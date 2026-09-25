"""Case lifecycle tests - the audit trail is a legal record, so every
mutation must leave a timeline entry, and the answer file must carry
the before/after NBA snapshots the benchmark grades."""
from agent.case_manager import CaseManager


class FakeGraph:
    """Stub graph client - records what would have been upserted."""
    def __init__(self):
        self.upserts = []

    def upsert_case(self, case_dict):
        self.upserts.append(case_dict)


def _manager():
    return CaseManager(FakeGraph())


def test_every_mutation_hits_the_timeline():
    cm = _manager()
    case = cm.new_case({"type": "fraud_signal", "txn_id": "T1"})
    case.add_evidence("graph_finding", "something found")
    case.add_decision("assessment", "because reasons")
    case.add_action("hold_transaction", "auto", "agent", "executed")
    case.record_nba("before_additional_evidence", [{"action": "hold_transaction"}], "uncertain")
    case.set_status("evidence_requested")
    types = [t["type"] for t in case.timeline]
    for expected in ("case_opened", "evidence_added", "decision", "action", "next_best_action", "status_change"):
        assert expected in types, f"missing audit entry: {expected}"


def test_answer_file_separates_nba_stages():
    cm = _manager()
    case = cm.new_case({"type": "fraud_signal", "txn_id": "T1"})
    case.record_nba("before_additional_evidence", [{"action": "hold_transaction"}], "pending evidence")
    case.record_nba("after_additional_evidence", [{"action": "block_transaction"}], "customer disowned txn")
    case.record_nba("final", [{"action": "block_transaction"}], "disposition")
    answer = CaseManager.to_answer_file(case)
    nba = answer["next_best_action"]
    assert len(nba["before_additional_evidence"]) == 1
    assert len(nba["after_additional_evidence"]) == 1
    assert len(nba["final"]) == 1
    # The recommendation is allowed - expected, even - to CHANGE.
    assert nba["before_additional_evidence"][0]["actions"][0]["action"] == "hold_transaction"
    assert nba["after_additional_evidence"][0]["actions"][0]["action"] == "block_transaction"


def test_persist_writes_to_graph():
    fake = FakeGraph()
    cm = CaseManager(fake)
    case = cm.new_case({"type": "analyst_request", "txn_id": "T2"})
    cm.persist(case)
    assert len(fake.upserts) == 1
    assert fake.upserts[0]["case_id"] == case.case_id


def test_case_ids_are_unique():
    cm = _manager()
    ids = {cm.new_case({"type": "fraud_signal"}).case_id for _ in range(50)}
    assert len(ids) == 50
