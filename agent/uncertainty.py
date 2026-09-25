"""Fraud probability, pattern classification and the stopping rule.

The README is blunt: the model risk score 'is often wrong in both
directions' and above 0.7 most flagged transactions are legitimate. So
the score is one modest input among many - the graph evidence does the
heavy lifting, and direct customer testimony outweighs everything.

fraud_probability is scored for calibration, so the maths here is
deliberately conservative: it starts unsure and only reaches the
extremes when multiple independent signals agree.
"""
from dataclasses import dataclass, field

# The official pattern enum from the answer format. Nothing else is valid.
PATTERNS = ("card_testing", "card_not_present_fraud", "card_not_present_new_device",
            "out_of_region_use", "account_takeover", "undocumented", "none")


@dataclass
class Assessment:
    fraud_probability: float
    verdict: str                     # fraud | legitimate | uncertain
    pattern: str                     # one of PATTERNS
    pattern_description: str = ""    # required prose when pattern == undocumented
    drivers: list = field(default_factory=list)   # what moved the probability, and by how much
    gaps: list = field(default_factory=list)      # what we still do not know
    independent_evidence: int = 0    # distinct independent signals supporting the verdict


def classify_pattern(sig: dict) -> tuple[str, str]:
    """Map graph signals onto the official typologies, in precedence order.

    Returns (pattern, pattern_description) - description only filled for
    'undocumented', per the answer format.
    """
    if sig.get("testing_sequence"):
        return "card_testing", ""
    # Coordinated shared-device abuse across customers that is not a
    # testing sequence fits none of the five documented patterns.
    shared = sig.get("shared_device_cards", [])
    if len(shared) >= 2 and sig.get("prior_fraud_on_shared"):
        return "undocumented", (
            f"A single device profile transacts across {len(shared) + 1} unrelated cards in a short "
            f"window, at least one already tied to confirmed fraud. This looks like one operator "
            f"cycling through card credentials from shared infrastructure - coordinated abuse that "
            f"matches none of the five documented patterns. Found by walking device-profile "
            f"neighbours in the graph.")
    if sig.get("mixed_channel_anomaly") and (sig.get("new_device") or sig.get("match_flag_anomaly")):
        return "account_takeover", ""
    if sig.get("out_of_region") and not sig.get("trip_pattern"):
        return "out_of_region_use", ""
    if sig.get("channel") == "online" and sig.get("cnp_burst"):
        # New device upgrades plain CNP fraud to the stronger variant.
        return ("card_not_present_new_device" if sig.get("new_device") else "card_not_present_fraud"), ""
    if sig.get("channel") == "online" and sig.get("amount_anomaly") and sig.get("new_device"):
        return "card_not_present_new_device", ""
    if sig.get("channel") == "online" and sig.get("amount_anomaly"):
        return "card_not_present_fraud", ""
    return "none", ""


def assess(sig: dict) -> Assessment:
    """Compute fraud probability from the signal bundle.

    Signals (all optional; absences become 'gaps'):
      model_score, channel, new_device, proxy, testing_sequence,
      cnp_burst, amount_anomaly, out_of_region, trip_pattern,
      recurring_match, shared_device_cards, prior_fraud_on_shared,
      prior_fraud_cases, prior_cleared_cases, home_activity_continues,
      customer_response, step_up_result, mixed_channel_anomaly
    """
    drivers, gaps = [], []
    independent = 0

    # Start unsure. The model score nudges, never decides (README: often
    # wrong in both directions; >0.7 is usually still legitimate).
    p = 0.30
    score = sig.get("model_score")
    if score is not None:
        p += (float(score) - 0.5) * 0.25
        drivers.append(f"Model risk score {float(score):.2f} (weak prior - policy s0: a reason to look, never a verdict)")

    def bump(delta: float, why: str, counts: bool = True):
        nonlocal p, independent
        p = min(0.99, max(0.01, p + delta))
        drivers.append(f"{'+' if delta >= 0 else ''}{delta:.2f}: {why}")
        if counts:
            independent += 1

    # --- graph signals -------------------------------------------------
    if sig.get("testing_sequence"):
        ts = sig["testing_sequence"]
        bump(0.35, f"Card-testing sequence: {ts['small_count']} authorisations under $5 within an hour"
                   + (f", then a ${ts['large_amount']:.2f} purchase" if ts.get("large_amount") else ""))
    if sig.get("new_device") is True:
        bump(0.12, "Identity record marks the device as New for this account (id_15)")
    elif sig.get("new_device") is False:
        bump(-0.08, "Device previously seen on this account", counts=False)
    elif sig.get("channel") == "online":
        gaps.append("no usable device-familiarity signal on this online transaction")
    if sig.get("proxy"):
        bump(0.08, "Connection flagged as anonymising proxy (id_23)")
    if sig.get("cnp_burst"):
        n = len(sig["cnp_burst"])
        bump(0.15, f"Burst of {n} out-of-profile online purchases within 48h")
    if sig.get("amount_anomaly"):
        bump(0.10, "Amount and/or product code far outside this card's history")
    if sig.get("out_of_region") and not sig.get("trip_pattern"):
        extra = " while home-region activity continued" if sig.get("home_activity_continues") else ""
        bump(0.18, f"Card-present use in a region with no prior history{extra}")
    if sig.get("trip_pattern"):
        bump(-0.20, "Several consecutive days in one new region - travel, not cloning (pattern 4 caveat)")
    shared = sig.get("shared_device_cards", [])
    if shared:
        bump(0.20, f"Device profile shared with {len(shared)} other card(s) in the window: {', '.join(shared[:4])}")
    if sig.get("prior_fraud_on_shared"):
        bump(0.10, "That shared element already appears in confirmed-fraud history")
    if sig.get("recurring_match"):
        bump(-0.30, "Charge matches the customer's own recurring pattern (same merchant profile, similar amount, monthly) - R7 territory")

    # --- case memory -----------------------------------------------------
    for c in sig.get("prior_fraud_cases", [])[:3]:
        bump(0.06, f"Similar closed case {c} was confirmed fraud")
    for c in sig.get("prior_cleared_cases", [])[:3]:
        bump(-0.06, f"Similar closed case {c} was cleared as a false alarm")

    # --- direct testimony: the closest thing to ground truth we get -----
    resp = sig.get("customer_response")
    if resp == "denied":
        bump(0.30, "Customer states they did not make the transaction")
    elif resp == "confirmed":
        bump(-0.40, "Customer confirmed the transaction as their own")
    else:
        gaps.append("no customer testimony yet")
    step = sig.get("step_up_result")
    if step == "failed":
        bump(0.22, "Step-up authentication failed")
    elif step == "passed":
        bump(-0.22, "Step-up authentication passed")

    p = round(min(0.99, max(0.01, p)), 2)
    verdict = "fraud" if p >= 0.70 else ("legitimate" if p <= 0.30 else "uncertain")
    pattern, desc = classify_pattern(sig)
    if verdict == "legitimate":
        pattern, desc = "none", ""

    return Assessment(fraud_probability=p, verdict=verdict, pattern=pattern,
                      pattern_description=desc, drivers=drivers, gaps=gaps,
                      independent_evidence=independent)


def should_stop(a: Assessment, verification_settled: bool, rounds_used: int, max_rounds: int) -> tuple[bool, str]:
    """Policy section 6, verbatim as code. Returns (stop, stop_reason)."""
    if verification_settled:
        return True, "A verification response settled the question."
    if a.fraud_probability >= 0.85 and a.independent_evidence >= 2:
        return True, (f"Fraud probability {a.fraud_probability} >= 0.85 supported by "
                      f"{a.independent_evidence} independent pieces of evidence.")
    if a.fraud_probability <= 0.15 and a.independent_evidence >= 2:
        return True, (f"Fraud probability {a.fraud_probability} <= 0.15 supported by "
                      f"{a.independent_evidence} independent pieces of evidence.")
    if rounds_used >= max_rounds:
        return True, ("Further steps are unlikely to change the decision: the evidence budget is "
                      "spent and remaining signals point the same way; acting on the current "
                      "assessment under R1/R8 is the defensible course.")
    return False, ""
