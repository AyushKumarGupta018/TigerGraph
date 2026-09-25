# Unit tests for policy routing and R1-R10 predicates
import pytest

from agent.policy_engine import ACTIONS, PolicyEngine


def test_unknown_action_is_rejected():
    with pytest.raises(ValueError):
        PolicyEngine().route("WIRE_FUNDS_TO_RECOVERY_ACCOUNT")



def test_block_card_routing_depends_on_exposure():
    pe = PolicyEngine()
    assert pe.route("BLOCK_CARD", exposure_usd=2000) == "L1"   # <= $2,500: team lead
    assert pe.route("BLOCK_CARD", exposure_usd=3000) == "L2"   # above: fraud manager


def test_always_l2_actions():
    pe = PolicyEngine()
    assert pe.route("FILE_REPORT") == "L2"
    assert pe.route("BLOCK_ALL_CARDS") == "L2"
    assert pe.route("DECLINE_TRANSACTION") == "L1"


def test_evidence_gathering_is_auto():
    pe = PolicyEngine()
    for action in ("VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "MONITOR_CARD",
                   "CREATE_CASE", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD"):
        assert pe.may_execute(action), f"{action} must be executable without approval"
    assert not pe.may_execute("BLOCK_CARD", exposure_usd=100)


def test_r1_demands_verification_on_thin_signals():
    pe = PolicyEngine()
    assert pe.r1_verify_first(fraud_probability=0.55, independent_signals=1)
    assert not pe.r1_verify_first(fraud_probability=0.75, independent_signals=1)
    assert not pe.r1_verify_first(fraud_probability=0.55, independent_signals=3)


def test_sar_thresholds_follow_3a():
    pe = PolicyEngine()
    # Fraud + exposure over $1,000 -> file.
    assert pe.sar_required("fraud", 0.9, 1500, shared_link=False, undocumented_or_coordinated=False)
    # Fraud + shared origin, small exposure -> still file.
    assert pe.sar_required("fraud", 0.8, 250, shared_link=True, undocumented_or_coordinated=False)
    # Fraud but small and isolated -> case only, no report.
    assert not pe.sar_required("fraud", 0.8, 250, shared_link=False, undocumented_or_coordinated=False)
    # Legitimate never files.
    assert not pe.sar_required("legitimate", 0.1, 99999, shared_link=True, undocumented_or_coordinated=True)


def test_case_opening_threshold():
    pe = PolicyEngine()
    assert pe.case_required(0.35, requested_evidence=False, customer_dispute=False)
    assert pe.case_required(0.10, requested_evidence=True, customer_dispute=False)
    assert pe.case_required(0.10, requested_evidence=False, customer_dispute=True)
    assert not pe.case_required(0.10, requested_evidence=False, customer_dispute=False)


def test_r10_guards_block_all_cards():
    pe = PolicyEngine()
    assert not pe.r10_block_all_allowed(cards_with_confirmed_fraud=1, credentials_compromised=False)
    assert pe.r10_block_all_allowed(cards_with_confirmed_fraud=2, credentials_compromised=False)
    assert pe.r10_block_all_allowed(cards_with_confirmed_fraud=0, credentials_compromised=True)
