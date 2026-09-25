# Fraud investigation pipeline - graph traversal, uncertainty scoring, and policy actions
import time

from . import actions as act
from .case_manager import Case, CaseManager
from .graph_client import GraphClient
from .graphrag import PolicyRetriever, build_context
from .llm import synthesize
from .memory import CaseMemory
from .policy_engine import PolicyEngine
from .uncertainty import Assessment, assess, should_stop

MAX_ROUNDS = 2  # Max verification rounds (customer verification, then step-up auth)


class Investigator:
    def __init__(self, graph: GraphClient | None = None):
        self.graph = graph or GraphClient()
        self.cases = CaseManager(self.graph)
        self.memory = CaseMemory(self.graph)
        self.policy = PolicyEngine()
        self.retriever = PolicyRetriever()
        self._tokens = 0

    def _gather(self, ctx: dict, row: dict, case: Case) -> tuple[dict, list, dict]:
        # Gather signals across graph neighborhoods and build evidentiary trail
        sig: dict = {
            "model_score": ctx["risk_score"],
            "channel": ctx["channel"],
            "new_device": ctx["device_new"],
            "proxy": ctx["proxy"]
        }
        evidence: list = []
        txn_lookup: dict = {ctx["txn_id"]: (ctx["ts"], ctx["amount"])}

        def claim(text: str, ref: str, entity_ids: list, source: str = "graph"):
            evidence.append({"claim": text, "source": source, "ref": ref, "entity_ids": entity_ids})
            case.add_evidence(source, text)

        card_id = ctx["card_id"]
        claim(
            f"Flagged transaction {ctx['txn_id']}: ${ctx['amount']:.2f}, {ctx['channel']}, "
            f"product {ctx['product_cd']}, region {ctx['addr1']}, model score {ctx['risk_score']:.2f} "
            f"(treated as a reason to look, not a verdict)",
            f"query:case_context(txn_id={ctx['txn_id']})",
            [ctx["txn_id"], card_id]
        )

        # Check device familiarity on online transactions
        if ctx["channel"] == "online" and ctx["device_profile"]:
            claim(
                f"Device profile '{ctx['device_profile']}' is marked "
                f"{'NEW' if ctx['device_new'] else 'previously seen (Found)'} for this account (id_15)"
                + ("; connection via anonymising proxy (id_23)" if ctx["proxy"] else ""),
                f"query:case_context(txn_id={ctx['txn_id']})",
                [ctx["txn_id"]]
            )

        # 48-hour card window to detect card-testing or micro-auth bursts
        window = self.graph.card_window(card_id, ctx["ts"], hours=48)
        for t in window:
            txn_lookup[t["txn_id"]] = (t["ts"], t["amount"])
        hour_win = [t for t in window if abs((t["ts"] - ctx["ts"]).total_seconds()) <= 3600]
        small = [t for t in hour_win if t["amount"] < 5 and t["channel"] == "online"]
        larger = [t for t in hour_win if t["amount"] >= 20]
        if len(small) >= 3:
            big = max(larger, key=lambda t: t["amount"], default=None)
            sig["testing_sequence"] = {
                "small_count": len(small),
                "large_amount": big["amount"] if big else None,
                "txn_ids": [t["txn_id"] for t in small] + ([big["txn_id"]] if big else [])
            }
            claim(
                f"{len(small)} authorisations under $5 within one hour on {card_id}"
                + (f", followed by a ${big['amount']:.2f} purchase" if big else ""),
                f"query:card_window(card_id={card_id}, hours=2)",
                sig["testing_sequence"]["txn_ids"]
            )

        # Compare against customer baseline spending history
        hist = self.graph.card_history(card_id, ctx["ts"])
        if hist["txn_count"] >= 3:
            anomalies = []
            if ctx["amount"] > 3 * max(hist["avg_amount"], 1):
                anomalies.append(f"amount ${ctx['amount']:.2f} vs average ${hist['avg_amount']:.2f}")
            if ctx["product_cd"] and ctx["product_cd"] not in hist["product_codes"]:
                anomalies.append(f"first-ever use of product code {ctx['product_cd']}")
            if anomalies:
                sig["amount_anomaly"] = True
                claim(
                    f"Out of profile for {card_id}: " + "; ".join(anomalies)
                    + f" (baseline: {hist['txn_count']} txns)",
                    f"query:card_history(card_id={card_id})",
                    [ctx["txn_id"]]
                )
            if ctx["channel"] and ctx["channel"] not in hist["channels"]:
                sig["mixed_channel_anomaly"] = True
                claim(
                    f"Channel shift: card has only ever transacted {'/'.join(hist['channels'])}, this is {ctx['channel']}",
                    f"query:card_history(card_id={card_id})",
                    [ctx["txn_id"]]
                )
        burst = [t for t in window if t["channel"] == "online" and t["txn_id"] != ctx["txn_id"]
                 and t["amount"] > 2 * max(hist["avg_amount"], 1)] if hist["txn_count"] >= 3 else []
        if burst and sig.get("amount_anomaly"):
            sig["cnp_burst"] = [t["txn_id"] for t in burst] + [ctx["txn_id"]]
            claim(
                f"Burst: {len(burst) + 1} out-of-profile online purchases within 48h",
                f"query:card_window(card_id={card_id}, hours=48)",
                sig["cnp_burst"]
            )

        # Card-present out of region checks
        if ctx["channel"] == "in_person" and hist["txn_count"] >= 3 and ctx["addr1"] \
                and ctx["addr1"] not in hist["regions"]:
            reg = self.graph.region_activity(card_id, ctx["addr1"], ctx["ts"])
            sig["out_of_region"] = reg
            sig["trip_pattern"] = reg["days_active_in_region"] >= 3
            sig["home_activity_continues"] = reg["home_activity_continues"]
            claim(
                f"Card-present use in region {ctx['addr1']} with no prior history there; "
                f"{reg['days_active_in_region']} active day(s) in that region this week; "
                f"home-region activity {'continued in parallel' if reg['home_activity_continues'] else 'paused'}",
                f"query:region_activity(card_id={card_id}, region={ctx['addr1']})",
                reg["region_txn_ids"]
            )

        # Recurring billing cadence check (Rule R7)
        if self.graph.recurring_pattern(card_id, ctx["amount"], ctx["ts"]):
            sig["recurring_match"] = True
            claim(
                f"Charge matches this card's own recurring rhythm: similar amount roughly monthly",
                f"query:recurring_pattern(card_id={card_id})",
                [ctx["txn_id"]]
            )

        # Multi-card shared device fingerprint (Rule R6)
        shared_cards = []
        if ctx["device_profile"]:
            neighbors = self.graph.device_neighbors(ctx["device_profile"], ctx["ts"])
            shared_cards = [n["card_id"] for n in neighbors if n["card_id"] != card_id]
            if shared_cards:
                sig["shared_device_cards"] = shared_cards
                claim(
                    f"Device profile also used by {len(shared_cards)} other card(s) in a 30-day window: {', '.join(shared_cards[:5])}",
                    f"query:device_neighbors(device_profile=...)",
                    shared_cards[:10]
                )

        # Query past case memory for similar patterns/outcomes
        sims = self.memory.find_similar(ctx["customer_id"], card_id, "", shared_cards)
        sig["prior_fraud_cases"] = [s["case_id"] for s in sims if s["outcome"] == "confirmed_fraud"]
        sig["prior_cleared_cases"] = [s["case_id"] for s in sims if s["outcome"] == "cleared"]
        sig["prior_fraud_on_shared"] = any(
            s["outcome"] == "confirmed_fraud" and any("shared card" in w for w in s["why_similar"]) for s in sims
        )
        sig["_similar_cases"] = sims
        for s in sims:
            claim(
                f"Closed case {s['case_id']} ({s['outcome']}, pattern {s['pattern']}) is similar: "
                f"{', '.join(s['why_similar'])}. Notes: {s['notes'][:150]}",
                f"memory:closed_cases(case_id={s['case_id']})",
                [s["case_id"]],
                source="document"
            )

        # Initial trigger signal
        if row.get("trigger_type") == "customer_report":
            sig["customer_response"] = "denied"
            claim(
                "Customer reported the transaction as unrecognised (trigger message)",
                "case_pack:trigger_text",
                [ctx["txn_id"]],
                source="customer"
            )
        return sig, evidence, txn_lookup

    def _recommend(self, a: Assessment, sig: dict, exposure: float, stage: str) -> list[dict]:
        # Map assessment and policy rules to actionable recommendations
        recs: list[dict] = []
        seen = set()

        def add(action: str, reason: str):
            if action in seen:
                return
            seen.add(action)
            recs.append({"action": action, "route": self.policy.route(action, exposure), "reason": reason})

        shared = bool(sig.get("shared_device_cards"))
        denied = sig.get("customer_response") == "denied"
        confirmed = sig.get("customer_response") == "confirmed"

        if a.verdict == "fraud":
            if sig.get("testing_sequence") and not self.policy.r5_block_after_testing(
                    bool((sig["testing_sequence"].get("large_amount") or 0) > 100)):
                add("DECLINE_TRANSACTION", "R5: card-testing sequence observed")
                add("STEP_UP_AUTH", "R5: challenge before further activity")
            add(
                "BLOCK_CARD",
                ("R2: customer denied the transaction" if denied else
                 "R5: testing sequence with a cleared purchase over $100" if sig.get("testing_sequence") else
                 f"Probability {a.fraud_probability} with {a.independent_evidence} independent signals supports containment")
            )
            add("CREATE_CASE", "3a: fraud probability above 0.30 requires an internal case")
            if self.policy.sar_required(a.verdict, a.fraud_probability, exposure, shared or sig.get("prior_fraud_on_shared", False),
                                        a.pattern == "undocumented"):
                add(
                    "FILE_REPORT",
                    "R2/3a: confirmed or strongly suspected fraud with "
                    + ("a shared-origin link" if shared else f"exposure ${exposure:.2f} over $1,000")
                )
            if shared:
                add("MONITOR_CONNECTED_CARDS", "R6: shared device profile - protect every card that shares it")
            if a.pattern == "undocumented":
                add("ESCALATE_TO_ANALYST", "R9: undocumented coordinated pattern needs human review")
            add("WARN_CUSTOMER", "Inform the cardholder of the compromise and the protective steps")
        elif a.verdict == "legitimate":
            if denied and sig.get("recurring_match"):
                # Handle customer dispute on established recurring payment pattern (Rule R7)
                add("CREATE_CASE", "R7: customer disputed a charge matching their own recurring pattern")
                add("VERIFY_WITH_CUSTOMER", "R7: confirm with a recurring-charge reminder before anything else")
                add("WARN_CUSTOMER", "R7: send a recurring-charge reminder. Do not block")
            else:
                add("ALLOW_TRANSACTION", f"Probability {a.fraud_probability} is low"
                    + ("; customer confirmed the transaction (R3)" if confirmed else ""))
                add("CLOSE_NO_FRAUD", "R3: close as legitimate" if confirmed else
                    "Evidence supports closing the alert as legitimate")
        else:  # uncertain
            if stage == "initial" and self.policy.r1_verify_first(a.fraud_probability, a.independent_evidence):
                add("VERIFY_WITH_CUSTOMER", f"R1: probability {a.fraud_probability} on a thin signal - verify before any block")
                add("MONITOR_CARD", "Raise monitoring while verification is pending")
            elif stage == "initial":
                add("VERIFY_WITH_CUSTOMER", "R1: assessed probability below 0.70 - confirm before blocking")
                add("STEP_UP_AUTH", "Require a challenge before further activity")
                add("MONITOR_CARD", "Raise monitoring while evidence is pending")
            else:
                add("MONITOR_CARD", "Residual uncertainty: enhanced monitoring for 72 hours")
                if self.policy.r8_escalate(a.verdict, exposure, evidence_conflicts=(denied and sig.get("step_up_result") == "passed")):
                    add("ESCALATE_TO_ANALYST", f"R8: verdict uncertain with exposure ${exposure:.2f} over $500")
                add("CREATE_CASE", "3a: evidence was requested, so a case must exist")
        return recs

    @staticmethod
    def _episode(a: Assessment, sig: dict, ctx: dict, txn_lookup: dict) -> tuple[list, str, float]:
        # Calculates all affected transaction IDs and total dollar exposure
        if a.verdict == "legitimate":
            return [], "", 0.0
        ids = {ctx["txn_id"]}
        if sig.get("testing_sequence"):
            ids |= set(sig["testing_sequence"]["txn_ids"])
        if sig.get("cnp_burst"):
            ids |= set(sig["cnp_burst"])
        if sig.get("out_of_region"):
            ids |= set(sig["out_of_region"]["region_txn_ids"])
        known = [i for i in ids if i in txn_lookup]
        known.sort(key=lambda i: txn_lookup[i][0])
        exposure = round(sum(abs(txn_lookup[i][1]) for i in known), 2)
        return known, (known[0] if known else ""), exposure

    def _narrate(self, instruction: str, evidence: list, sims: list, fallback: str) -> str:
        context = build_context(instruction, evidence, sims, self.retriever)
        out = synthesize(instruction, context)
        if out.startswith("[deterministic"):
            return fallback
        self._tokens += (len(context) + len(out)) // 4
        return out

    def run_case(self, row: dict) -> tuple[dict, Case]:
        # Main investigation loop
        t0 = time.time()
        self.graph.reset_metrics()
        self._tokens = 0
        case = self.cases.new_case({
            "type": row.get("trigger_type"),
            "txn_id": row.get("flagged_txn_id"),
            "text": row.get("trigger_text", "")
        })
        case.add_decision("open_investigation", f"Trigger '{row.get('trigger_type')}': {row.get('trigger_text', '')[:160]}")

        ctx = self.graph.case_context(str(row["flagged_txn_id"]))
        if not ctx.get("found"):
            case.set_status("escalated", "pending")
            answer = self._empty_answer(row, "Flagged transaction not found in the graph; escalated to an analyst.")
            return answer, case
        case.customer_id = ctx["customer_id"]
        case.txn_ids = [ctx["txn_id"]]

        # Phase 1: Initial graph evidence & risk estimation
        sig, evidence, txn_lookup = self._gather(ctx, row, case)
        a = assess(sig)
        case.add_decision("assessment", f"p={a.fraud_probability}, verdict={a.verdict}, pattern={a.pattern}. "
                                         f"Drivers: {' | '.join(a.drivers[:6])}")
        _, _, exposure = self._episode(a, sig, ctx, txn_lookup)

        # Phase 2: Initial Next Best Action snapshot (before evidence request)
        initial = self._recommend(a, sig, exposure, stage="initial")
        case.record_nba("initial", initial, f"Initial view at p={a.fraud_probability} ({a.verdict}).")

        # Phase 3: Interactive evidence collection loop
        evidence_requests: list = []
        settled = sig.get("customer_response") in ("denied", "confirmed") and sig.get("recurring_match") is not True
        rounds = 0
        stop, stop_reason = should_stop(a, settled, rounds, MAX_ROUNDS)
        step_counter = len(evidence)
        while not stop:
            rounds += 1
            if sig.get("customer_response") is None or (sig.get("recurring_match") and rounds == 1
                                                        and row.get("trigger_type") == "customer_report"):
                resp, assumed = act.simulate_customer_response(ctx["txn_id"], a.fraud_probability)
                sig["customer_response"] = resp
                evidence_requests.append({
                    "type": "customer_validation",
                    "asked_after_step": step_counter,
                    "assumed_response": assumed
                })
                evidence.append({
                    "claim": assumed,
                    "source": "customer",
                    "ref": f"evidence_request:{len(evidence_requests)}",
                    "entity_ids": []
                })
                case.add_evidence("customer", f"VERIFY_WITH_CUSTOMER (simulated): {assumed}")
                sig["recurring_match"] = sig.get("recurring_match") if resp == "denied" else sig.get("recurring_match")
            else:
                res, assumed = act.simulate_step_up(ctx["customer_id"], ctx["txn_id"], a.fraud_probability)
                sig["step_up_result"] = res
                evidence_requests.append({
                    "type": "step_up_auth",
                    "asked_after_step": step_counter,
                    "assumed_response": assumed
                })
                evidence.append({
                    "claim": assumed,
                    "source": "customer",
                    "ref": f"evidence_request:{len(evidence_requests)}",
                    "entity_ids": []
                })
                case.add_evidence("step_up_auth", f"STEP_UP_AUTH (simulated): {assumed}")
            a = assess(sig)
            case.add_decision("re-assessment", f"After evidence round {rounds}: p={a.fraud_probability}, verdict={a.verdict}")
            settled = True
            stop, stop_reason = should_stop(a, settled, rounds, MAX_ROUNDS)

        # Phase 4: Final NBA and disposition
        affected, first_suspicious, exposure = self._episode(a, sig, ctx, txn_lookup)
        final = self._recommend(a, sig, exposure, stage="final")
        what_changed = "nothing"
        if evidence_requests:
            i_set, f_set = {r["action"] for r in initial}, {r["action"] for r in final}
            if i_set != f_set:
                what_changed = (
                    f"The assumed evidence ({evidence_requests[-1]['type']}) moved probability to "
                    f"{a.fraud_probability} ({a.verdict}); "
                    f"added {sorted(f_set - i_set) or 'nothing'}, dropped {sorted(i_set - f_set) or 'nothing'}."
                )
            else:
                what_changed = "The evidence confirmed the initial view; the actions stand."
        case.record_nba("final", final, what_changed)

        status = {"fraud": "closed_fraud", "legitimate": "closed_legitimate"}.get(
            a.verdict, "escalated" if any(r["action"] == "ESCALATE_TO_ANALYST" for r in final) else "open")
        case.set_status(status, a.verdict)
        case.confidence = a.fraud_probability
        case.pattern = a.pattern
        case.risk_level = (
            "critical" if a.fraud_probability >= 0.85 else
            "high" if a.fraud_probability >= 0.65 else
            "medium" if a.fraud_probability > 0.30 else "low"
        )

        # Phase 5: Draft FinCEN SAR filing if criteria met
        sims = sig.get("_similar_cases", [])
        file_sar = any(r["action"] == "FILE_REPORT" for r in final)
        connected_cards = sorted(set(sig.get("shared_device_cards", [])))
        sar = self._build_sar(file_sar, a, ctx, row, affected, exposure, connected_cards, evidence, sims, txn_lookup)
        case.sar = sar if file_sar else None

        # Phase 6: Synthesize executive case summary
        fallback_summary = (
            f"{row.get('trigger_type')} alert on {ctx['txn_id']} (${ctx['amount']:.2f}, {ctx['channel']}). "
            f"Assessed p={a.fraud_probability} ({a.verdict}), pattern {a.pattern}. "
            f"Key evidence: {'; '.join(d for d in a.drivers[1:4])}. "
            f"Exposure ${exposure:.2f} across {len(affected)} transaction(s). "
            f"Final actions: {', '.join(r['action'] for r in final)}."
        )
        summary = self._narrate(
            f"Write a 2-6 sentence case summary for verdict '{a.verdict}' (p={a.fraud_probability}, "
            f"pattern {a.pattern}) an analyst could read.",
            evidence,
            sims,
            fallback_summary
        )
        case.summary = summary

        # Phase 7: Persist closed investigation back to TigerGraph case memory
        graph_case_id = f"CASE-2016-{row['case_id']}"
        self.graph.upsert_case({
            "graph_case_id": graph_case_id,
            "case_id": row["case_id"],
            "customer_id": ctx["customer_id"],
            "card_id": ctx["card_id"],
            "pattern": a.pattern,
            "verdict": a.verdict,
            "status": status,
            "fraud_probability": a.fraud_probability,
            "affected_txn_ids": affected,
            "connected_card_ids": connected_cards,
            "summary": summary,
            "outcome": a.verdict
        })

        # Phase 8: Assemble final benchmark JSON schema
        answer = {
            "case_id": row["case_id"],
            "case": {
                "status": status,
                "verdict": a.verdict,
                "fraud_probability": a.fraud_probability,
                "pattern": a.pattern,
                "pattern_description": a.pattern_description,
                "affected_txn_ids": affected,
                "first_suspicious_txn_id": first_suspicious,
                "connected_card_ids": connected_cards,
                "connected_device_profiles": [ctx["device_profile"]] if (ctx["device_profile"] and connected_cards) else [],
                "exposure_usd": exposure,
                "evidence": evidence,
                "similar_prior_cases": [s["case_id"] for s in sims],
                "summary": summary,
                "written_to_graph": True,
                "graph_case_id": graph_case_id,
            },
            "evidence_requests": evidence_requests,
            "next_best_actions": {"initial": initial, "final": final, "what_changed": what_changed},
            "sar": sar,
            "stop_reason": stop_reason,
            "tool_calls": self.graph.tool_calls,
            "tokens": self._tokens,
            "latency_s": round(time.time() - t0, 2),
        }
        case.answer = answer
        return answer, case

    def _build_sar(self, file_sar: bool, a: Assessment, ctx: dict, row: dict, affected: list,
                   exposure: float, connected_cards: list, evidence: list, sims: list,
                   txn_lookup: dict) -> dict:
        if not file_sar:
            reason = (
                "No filing: " + ("verdict is not fraud" if a.verdict != "fraud" else
                "exposure under $1,000 with no shared-origin link (3a)")
            )
            return {
                "file": False,
                "reason": reason,
                "narrative": "",
                "subjects": [],
                "total_amount_usd": 0,
                "activity_dates": []
            }
        dates = sorted(str(txn_lookup[i][0].date()) for i in affected if i in txn_lookup)
        subjects = [ctx["customer_id"], row.get("card_id", ctx["card_id"])] + connected_cards
        shared = bool(connected_cards)
        reason = (
            "R2/3a: strongly suspected fraud with "
            + ("a shared device profile linking additional cards" if shared else f"exposure ${exposure:.2f} over $1,000")
        )
        fallback = (
            f"Between {dates[0] if dates else 'n/a'} and {dates[-1] if dates else 'n/a'}, card "
            f"{row.get('card_id', ctx['card_id'])} belonging to customer {ctx['customer_id']} showed "
            f"{len(affected)} suspicious transaction(s) totalling ${exposure:.2f}, including the flagged "
            f"${ctx['amount']:.2f} {ctx['channel']} transaction {ctx['txn_id']} (region {ctx['addr1']}). "
            f"The activity is consistent with {a.pattern.replace('_', ' ')}. "
            + (f"The device profile '{ctx['device_profile']}' also transacted on {', '.join(connected_cards)} "
               f"in the same window, indicating a common actor across cardholders. " if shared else "")
            + (f"Assumed customer contact: {evidence[-1]['claim']}. " if evidence and evidence[-1]["source"] == "customer" else "")
            + (f"Related closed cases: {', '.join(s['case_id'] for s in sims[:2])}. " if sims else "")
            + f"Assessed fraud probability {a.fraud_probability}. The pattern of activity, its inconsistency "
            + f"with the cardholder's history, and the supporting evidence above are the basis for suspicion."
        )
        narrative = self._narrate(
            "Write the SAR narrative (6-12 sentences) per FinCEN guidance: who, what, when, where, "
            "how, and why it is suspicious. It must stand on its own.",
            evidence,
            sims,
            fallback
        )
        return {
            "file": True,
            "reason": reason,
            "narrative": narrative,
            "subjects": subjects,
            "total_amount_usd": exposure,
            "activity_dates": [dates[0], dates[-1]] if dates else []
        }

    @staticmethod
    def _empty_answer(row: dict, why: str) -> dict:
        return {
            "case_id": row["case_id"],
            "case": {
                "status": "escalated", "verdict": "uncertain", "fraud_probability": 0.5,
                "pattern": "none", "pattern_description": "", "affected_txn_ids": [],
                "first_suspicious_txn_id": "", "connected_card_ids": [],
                "connected_device_profiles": [], "exposure_usd": 0,
                "evidence": [], "similar_prior_cases": [], "summary": why,
                "written_to_graph": False, "graph_case_id": ""
            },
            "evidence_requests": [],
            "next_best_actions": {
                "initial": [{"action": "ESCALATE_TO_ANALYST", "route": "auto", "reason": "R8"}],
                "final": [{"action": "ESCALATE_TO_ANALYST", "route": "auto", "reason": "R8"}],
                "what_changed": "nothing"
            },
            "sar": {
                "file": False, "reason": why, "narrative": "", "subjects": [],
                "total_amount_usd": 0, "activity_dates": []
            },
            "stop_reason": why, "tool_calls": 1, "tokens": 0, "latency_s": 0.0
        }

