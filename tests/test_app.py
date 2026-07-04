from app import parse_user_request


def test_parse_user_request_extracts_target_constraints_and_count() -> None:
    target, constraints, count = parse_user_request(
        "Цель: повысить жаропрочность сплава на 15%.\n"
        "Ограничения: Nb до 0.3%, бюджет 2 недели.\n"
        "Количество гипотез: 4"
    )

    assert target == "повысить жаропрочность сплава на 15%."
    assert constraints == "Nb до 0.3%, бюджет 2 недели."
    assert count == 4


def test_parse_user_request_caps_count() -> None:
    _, _, count = parse_user_request("Цель: X\nКоличество гипотез: 99")

    assert count == 5

