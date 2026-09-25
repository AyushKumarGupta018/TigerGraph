"""Case lifecycle and audit trail.

A fraud case is a legal document as much as a data record: every piece
of evidence, every decision and every action needs a timestamped entry
that survives review. The Case object here is that record, and the
answer-file exporter emits exactly the structure the benchmark asks
for - including the next-best-action snapshots taken BEFORE any extra
evidence was requested and AFTER it arrived.
"""
import itertools
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

_counter = itertools.count(1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Case:
    case_id: str
    trigger: dict                       # what started this: signal / report / analyst
    customer_id: str = ""
    txn_ids: list = field(default_factory=list)
    status: str = "open"                # open | evidence_requested | resolved_fraud | resolved_cleared | escalated
    pattern: str = ""
    risk_level: str = ""
    confidence: float = 0.0
    summary: str = ""
    outcome: str = "pending"
    timeline: list = field(default_factory=list)      # full audit trail
    evidence: list = field(default_factory=list)      # evidence items with provenance
    decisions: list = field(default_factory=list)     # every decision + reasoning
    actions: list = field(default_factory=list)       # executed / queued actions
    nba_history: list = field(default_factory=list)   # NBA snapshots (before/after evidence)
    sar: dict | None = None
    opened_at: str = field(default_factory=_now)

    # ---- audit helpers: everything goes through these so nothing ----
    # ---- can happen to a case without leaving a timeline entry.   ----
    def _log(self, entry_type: str, detail: dict) -> None:
        self.timeline.append({"ts": _now(), "type": entry_type, **detail})

    def add_evidence(self, kind: str, detail: str, data: dict | None = None) -> None:
        item = {"kind": kind, "detail": detail, "data": data or {}, "added_at": _now()}
        self.evidence.append(item)
        self._log("evidence_added", {"kind": kind, "detail": detail})

    def add_decision(self, decision: str, reasoning: str) -> None:
        self.decisions.append({"decision": decision, "reasoning": reasoning, "at": _now()})
        self._log("decision", {"decision": decision})

    def add_action(self, action: str, tier: str, route: str, status: str, receipt: dict | None = None) -> None:
        self.actions.append({
            "action": action, "tier": tier, "approval_route": route,
            "status": status, "receipt": receipt or {}, "at": _now(),
        })
        self._log("action", {"action": action, "status": status, "route": route})

    def record_nba(self, stage: str, actions: list[dict], reasoning: str) -> None:
        """Snapshot the next best action at a named stage.

        stage is 'before_additional_evidence' or 'after_additional_evidence'
        (or 'final') - the benchmark grades exactly these snapshots.
        """
        self.nba_history.append({"stage": stage, "at": _now(), "actions": actions, "reasoning": reasoning})
        self._log("next_best_action", {"stage": stage, "actions": [a["action"] for a in actions]})

    def update_assessment(self, risk_level: str, confidence: float, pattern: str) -> None:
        self.risk_level, self.confidence, self.pattern = risk_level, confidence, pattern
        self._log("assessment", {"risk_level": risk_level, "confidence": confidence, "pattern": pattern})

    def set_status(self, status: str, outcome: str | None = None) -> None:
        self.status = status
        if outcome:
            self.outcome = outcome
        self._log("status_change", {"status": status, "outcome": self.outcome})


class CaseManager:
    """Creates cases, persists them to the graph, exports answer files."""

    def __init__(self, graph_client):
        self.graph = graph_client

    def new_case(self, trigger: dict) -> Case:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        case = Case(case_id=f"CASE-{stamp}-{next(_counter):04d}", trigger=trigger)
        case._log("case_opened", {"trigger": trigger.get("type", "unknown")})
        return case

    def persist(self, case: Case) -> None:
        """Write the case into TigerGraph so it becomes queryable memory."""
        self.graph.upsert_case(self.to_dict(case))

    @staticmethod
    def to_dict(case: Case) -> dict:
        return asdict(case)

    @staticmethod
    def to_answer_file(case: Case) -> dict:
        """The per-case answer file in the shape the submission requires."""
        d = asdict(case)
        return {
            "case": d,
            "suspicious_activity_report": case.sar,  # None when policy does not require one
            "next_best_action": {
                "before_additional_evidence": [s for s in case.nba_history if s["stage"] == "before_additional_evidence"],
                "after_additional_evidence": [s for s in case.nba_history if s["stage"] == "after_additional_evidence"],
                "final": [s for s in case.nba_history if s["stage"] == "final"],
            },
        }
