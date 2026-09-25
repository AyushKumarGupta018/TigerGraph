"""Simulated action execution + evidence-response simulation.

The policy is explicit (section 5): customer and analyst replies are
not provided - we simulate them in our own system and state the
assumption in evidence_requests. The simulators are DETERMINISTIC
functions of the case (hash-seeded), so every benchmark run produces
identical answer files.

Execution stubs return receipts for the audit trail. Only auto-route
actions are ever dispatched; L1/L2 actions are recorded as
recommendations awaiting a human.
"""
import hashlib
from datetime import datetime, timezone


def _receipt(system: str, detail: dict) -> dict:
    return {"system": system, "status": "simulated_ok",
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **detail}


# ---- auto-route execution stubs ---------------------------------------
def execute(action: str, target: str) -> dict:
    """One dispatcher for every auto action - swap for real APIs here."""
    systems = {
        "ALLOW_TRANSACTION": "payments-core", "MONITOR_CARD": "fraud-monitoring",
        "MONITOR_CONNECTED_CARDS": "fraud-monitoring", "WARN_CUSTOMER": "customer-messaging",
        "VERIFY_WITH_CUSTOMER": "customer-messaging", "STEP_UP_AUTH": "auth-service",
        "GENERATE_REPORT": "case-management", "CREATE_CASE": "case-management",
        "ESCALATE_TO_ANALYST": "case-management", "CLOSE_NO_FRAUD": "case-management",
    }
    return _receipt(systems.get(action, "unknown"), {"action": action, "target": target})


# ---- deterministic evidence simulation --------------------------------
def _stable_coin(seed: str, threshold: float) -> bool:
    """Same case, same answer, every run - reproducibility matters when
    twenty graded files depend on it."""
    h = int(hashlib.sha1(seed.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < threshold


def simulate_customer_response(txn_id: str, current_probability: float) -> tuple[str, str]:
    """Simulate VERIFY_WITH_CUSTOMER. Correlated with our assessed
    probability but noisy in both directions, so the agent must handle
    a customer confirming a hot transaction and disowning a cool one.
    Returns (response, assumed_response_text for evidence_requests)."""
    p_deny = min(0.92, max(0.08, current_probability))
    if _stable_coin(f"resp:{txn_id}", p_deny):
        return "denied", "Customer states they did not make this transaction and still has the card"
    return "confirmed", "Customer confirms they made this transaction themselves"


def simulate_step_up(customer_id: str, txn_id: str, current_probability: float) -> tuple[str, str]:
    """Simulate STEP_UP_AUTH: fraudsters usually fail, owners usually pass."""
    p_fail = min(0.88, max(0.08, current_probability - 0.08))
    if _stable_coin(f"stepup:{customer_id}:{txn_id}", p_fail):
        return "failed", "Step-up challenge sent; assumed no valid completion within the window"
    return "passed", "Step-up challenge sent; assumed completed successfully by the account holder"
