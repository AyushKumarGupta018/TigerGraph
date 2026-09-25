"""Uncertainty model tests: probability must move for the right
reasons, patterns must match the official enum, and the section-6
stopping rule must hold."""
from agent.uncertainty import PATTERNS, assess, classify_pattern, should_stop


def test_model_score_alone_is_a_weak_prior():
    # README: above 0.7 most flagged txns are STILL legitimate. A bare
    # high score must land in the uncertain band, not at 'fraud'.
    a = assess({"model_score": 0.88, "channel": "online"})
    assert a.verdict == "uncertain"
    assert 0.30 < a.fraud_probability < 0.70


def test_testing_sequence_classifies_and_raises():
    sig = {"model_score": 0.6, "channel": "online",
           "testing_sequence": {"small_count": 4, "large_amount": 189.0, "txn_ids": ["a", "b", "c", "d", "e"]}}
    assert classify_pattern(sig)[0] == "card_testing"
    a = assess(sig)
    assert a.fraud_probability > 0.6


def test_new_device_upgrades_cnp_pattern():
    base = {"channel": "online", "cnp_burst": ["t1", "t2"], "amount_anomaly": True}
    assert classify_pattern({**base, "new_device": False})[0] == "card_not_present_fraud"
    assert classify_pattern({**base, "new_device": True})[0] == "card_not_present_new_device"


def test_trip_pattern_is_not_out_of_region_fraud():
    # Several days in one new region is a trip, not a clone (pattern 4).
    sig = {"channel": "in_person", "out_of_region": {"days": 4}, "trip_pattern": True}
    assert classify_pattern(sig)[0] != "out_of_region_use"
    a = assess({**sig, "model_score": 0.6})
    assert a.fraud_probability < 0.6  # the trip signal pushes DOWN


def test_customer_testimony_dominates():
    sig = {"model_score": 0.75, "channel": "online", "new_device": True, "amount_anomaly": True}
    before = assess(sig)
    denied = assess({**sig, "customer_response": "denied"})
    confirmed = assess({**sig, "customer_response": "confirmed"})
    assert denied.fraud_probability > before.fraud_probability
    assert confirmed.fraud_probability < before.fraud_probability
    assert confirmed.verdict in ("legitimate", "uncertain")


def test_undocumented_pattern_has_description():
    sig = {"channel": "online", "shared_device_cards": ["C1-K1", "C2-K1"], "prior_fraud_on_shared": True}
    pattern, desc = classify_pattern(sig)
    assert pattern == "undocumented"
    assert len(desc) > 50, "undocumented requires a written pattern_description"


def test_all_emitted_patterns_are_official():
    for sig in ({}, {"channel": "online", "cnp_burst": ["x"], "new_device": True},
                {"testing_sequence": {"small_count": 3, "txn_ids": []}}):
        assert classify_pattern(sig)[0] in PATTERNS


def test_stopping_rule_section_6():
    hot = assess({"model_score": 0.9, "channel": "online", "new_device": True, "proxy": True,
                  "testing_sequence": {"small_count": 4, "large_amount": 200, "txn_ids": []},
                  "customer_response": "denied"})
    stop, reason = should_stop(hot, verification_settled=False, rounds_used=0, max_rounds=2)
    assert stop and "0.85" in reason  # extreme + multi-evidence stops early

    murky = assess({"model_score": 0.55, "channel": "online"})
    stop, _ = should_stop(murky, verification_settled=False, rounds_used=0, max_rounds=2)
    assert not stop  # the murky middle must keep investigating

    stop, reason = should_stop(murky, verification_settled=True, rounds_used=1, max_rounds=2)
    assert stop and "settled" in reason
