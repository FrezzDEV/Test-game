from app.giveaways.planner import action_key


def test_range_number_action_has_same_key_for_different_assigned_numbers():
    base = {
        "code": 6,
        "type": "number_guess",
        "min_number": 1,
        "max_number": 100,
    }

    assert action_key({**base, "number_value": 17}) == action_key(
        {**base, "number_value": 83}
    )


def test_exact_number_action_has_different_key_when_answer_changes():
    base = {
        "code": 6,
        "type": "number_guess",
        "min_number": 42,
        "max_number": 42,
    }

    assert action_key({**base, "number_value": 42}) != action_key(
        {**base, "number_value": 43}
    )
