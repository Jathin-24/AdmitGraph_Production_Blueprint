from app.services.monitoring.service import is_material_change
from app.services.strategy.simulator import apply_scenario


def test_budget_minus_25_percent() -> None:
    out = apply_scenario({"total_budget_amount": 1800000}, "BUDGET_MINUS_25_PERCENT")
    assert out["total_budget_amount"] == 1800000 * 0.75


def test_ielts_lowered() -> None:
    out = apply_scenario({"ielts_overall": 7.5}, "IELTS_LOWERED")
    assert out["ielts_overall"] == 6.5


def test_remove_country_drops_first() -> None:
    out = apply_scenario({"preferred_countries": ["DE", "NL"]}, "REMOVE_COUNTRY")
    assert out["preferred_countries"] == ["NL"]


def test_material_change_detection() -> None:
    assert is_material_change({"a": 1}, {"a": 2}) is True
    assert is_material_change({"a": 1}, {"a": 1}) is False
    assert is_material_change(None, {"a": 1}) is False
