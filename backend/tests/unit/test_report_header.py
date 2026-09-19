import pytest

from backend.application.report_header import (
    apply_header_patch,
    effective_production_presentation,
    validate_header,
    validate_production_codes,
)


def code(identity: str, plan: str = "", actual: str = "") -> dict[str, object]:
    return {
        "id": identity,
        "label": "Код " + identity,
        "plans": {"2026-01": plan},
        "actuals": {"2026-01": actual},
    }


def test_breakdown_totals_drive_production_but_do_not_mutate_legacy() -> None:
    source = {
        "plans": {"2025-12": "4", "2026-01": "999"},
        "actuals": {"2026-01": "999"},
        "production_codes": [code("A", "0.1", "0"), code("B", "0.2", "0")],
    }
    result = effective_production_presentation(source, 2026)
    assert result["plans"] == {"2025-12": "4", "2026-01": "0.3"}
    assert result["actuals"] == {"2026-01": "0"}
    assert result["production_code_annual"] == {"A": "0", "B": "0"}
    assert result["annual"] == {
        "plan": "0.3",
        "actual": "0",
        "completion": "0.00",
        "plan_months": 1,
        "actual_months": 1,
    }
    assert source["plans"]["2026-01"] == "999"  # type: ignore[index]


def test_partial_code_data_stays_missing_and_never_falls_back_to_legacy() -> None:
    source = {
        "actuals": {"2026-01": "80"},
        "production_codes": [code("A", "0", "10"), code("B", "0")],
    }
    result = effective_production_presentation(source, 2026)
    assert result["actuals"]["2026-01"] == ""
    assert result["plans"]["2026-01"] == "0"
    assert result["annual"]["completion"] == ""
    assert result["annual"]["actual_months"] == 0
    assert result["production_code_annual"] == {"A": "10", "B": ""}


def test_no_codes_preserve_aggregate_reports() -> None:
    source = {"plans": {"2026-01": "15"}, "actuals": {"2026-01": "12"}}
    result = effective_production_presentation(source, 2026)
    assert result["plans"] == source["plans"]
    assert result["annual"]["completion"] == "80.00"


def test_new_code_totals_conflict_requires_explicit_acknowledgment() -> None:
    current = {"plans": {"2026-01": "15"}}
    request = {"production_codes": [code("A", "10")]}
    with pytest.raises(ValueError, match="Подтвердите"):
        apply_header_patch(current, request, "HEAD_SITE")
    patch = apply_header_patch(current, {**request, "confirm_production_totals": True}, "HEAD_SITE")
    assert "plans" not in patch
    assert current["plans"]["2026-01"] == "15"


def test_code_edits_preserve_omitted_historical_months() -> None:
    old = code("A", "3", "2")
    old["plans"] = {"2025-12": "8", "2026-01": "3"}
    current = {"production_codes": [old]}
    patch = apply_header_patch(current, {"production_codes": [code("A", "4", "")]}, "HEAD_SITE")
    assert patch["production_codes"][0]["plans"]["2025-12"] == "8"
    assert patch["production_codes"][0]["actuals"]["2026-01"] == ""


def test_adding_blank_code_requires_acknowledgment_for_prior_year_totals() -> None:
    old = {"id": "A", "label": "A", "plans": {"2025-12": "8"}, "actuals": {}}
    new = {"id": "B", "label": "B", "plans": {}, "actuals": {}}
    request = {"production_codes": [old, new]}
    with pytest.raises(ValueError, match="2025-12"):
        apply_header_patch({"production_codes": [old]}, request, "HEAD_SITE")
    patch = apply_header_patch(
        {"production_codes": [old]},
        {**request, "confirm_production_totals": True},
        "HEAD_SITE",
    )
    assert effective_production_presentation(patch)["plans"]["2025-12"] == ""
    assert patch["production_codes"][0]["plans"]["2025-12"] == "8"


def test_cannot_remove_code_with_confirmed_zero_or_any_old_year_data() -> None:
    with pytest.raises(ValueError, match="Код содержит данные"):
        apply_header_patch(
            {"production_codes": [code("A", "0")]}, {"production_codes": []}, "HEAD_SITE"
        )


@pytest.mark.parametrize(
    "raw",
    [
        [code("A", "-1")],
        [code("A", "NaN")],
        [code("A"), code("A")],
        [{"id": "A", "label": "x", "plans": {"2026-13": "1"}}],
    ],
)
def test_rejects_invalid_production_codes(raw: object) -> None:
    with pytest.raises(ValueError):
        validate_production_codes(raw)


def test_rejects_codes_outside_head_site() -> None:
    with pytest.raises(ValueError, match="головной площадки"):
        apply_header_patch({}, {"production_codes": [code("A")]}, "SUBSIDIARY")


def test_partial_header_update_preserves_other_fields_and_image_validation() -> None:
    patch = apply_header_patch(
        {"header": {"factory_name": "Завод"}},
        {"header": {"product_designation": " X "}},
        "SUBSIDIARY",
    )
    assert patch["header"]["factory_name"] == "Завод"
    assert patch["header"]["product_designation"] == "X"
    with pytest.raises(ValueError):
        validate_header({"product_image": "data:image/png;base64,bm90IGEgcG5n"})
    with pytest.raises(ValueError):
        validate_header({"unknown": "x"})
