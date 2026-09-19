"""Regressions found by independent adversarial review, beyond happy-path tests."""

from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from backend.application.monthly_report import aggregate
from backend.tests.integration.test_subsidiary_consumption import (
    QUERY,
    app_at,
    data,
    setup,
    value,
    write,
)


def request_for(matrix: dict[str, Any], quantity: str) -> dict[str, Any]:
    cell = next(c for c in matrix["rows"][0]["cells"] if c["column_id"] == "2026-09-OPENING")
    return {
        **QUERY,
        "base_revision": matrix["matrix_revision"],
        "idempotency_key": uuid4().hex,
        "changes": [
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": quantity}}
        ],
    }


def test_intervening_save_cannot_be_silently_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = app_at(tmp_path)
    matrix = setup(app)
    competing = request_for(matrix, "200")
    stale = request_for(matrix, "100")
    original = app._editable_coordinate_keys
    fired = False

    def intervening_save(*args: Any) -> Any:
        nonlocal fired
        if not fired:
            fired = True
            data(app.save_report_cells(competing))
        return original(*args)

    # Place a successful competing write between the old revision check and
    # the old read of per-cell revisions: this previously overwrote 200 with 100.
    monkeypatch.setattr(app, "_editable_coordinate_keys", intervening_save)
    rejected = app.save_report_cells(stale)
    assert not rejected["ok"]
    assert cast(dict[str, Any], rejected["error"])["code"] == "REVISION_CONFLICT"
    assert value(data(app.get_report_matrix(QUERY)), "2026-09-OPENING") == "200"


def test_bridge_retry_is_idempotent_and_changed_payload_is_rejected(tmp_path: Path) -> None:
    app = app_at(tmp_path)
    matrix = setup(app)
    request = request_for(matrix, "100")
    first = data(app.save_report_cells(request))
    retry = data(app.save_report_cells(request))
    assert retry == first
    changed = {
        **request,
        "changes": [{**request["changes"][0], "value": {"kind": "QUANTITY", "quantity": "999"}}],
    }
    rejected = app.save_report_cells(changed)
    assert not rejected["ok"]
    assert cast(dict[str, Any], rejected["error"])["code"] == "IDEMPOTENCY_CONFLICT"
    assert value(data(app.get_report_matrix(QUERY)), "2026-09-OPENING") == "100"


def test_subsidiary_stock_preserves_all_accepted_decimal_digits(tmp_path: Path) -> None:
    app = app_at(tmp_path)
    matrix = setup(app)
    matrix = write(
        app,
        matrix,
        [
            (0, "2026-09-OPENING", "10000000000000000000000000000"),
            (0, "2026-09-RECEIVED", "1"),
            (1, "2026-09-RECEIVED", "0"),
        ],
    )
    assert value(matrix, "2026-09-STOCK") == "10000000000000000000000000001"
    assert value(matrix, "2026-09-VARIANCE") == "9999999999999999999999996001"
    matrix = write(app, matrix, [(0, "2026-09-01", "1")])
    assert value(matrix, "2026-09-STOCK") == "10000000000000000000000000000"


def test_head_stock_and_variance_preserve_all_accepted_digits(tmp_path: Path) -> None:
    app = app_at(tmp_path)
    query = {**QUERY, "report_type": "HEAD_SITE"}
    settings = {k: v for k, v in query.items() if k != "year"}
    layout = data(app.get_report_layout(settings))
    group = layout["rows"][0]
    group["configuration"]["norm"] = "1"
    data(app.save_report_layout({**settings, "rows": [group]}))
    matrix = data(app.get_report_matrix(query))
    data(
        app.save_report_presentation(
            {
                **settings,
                "expected_revision": matrix["matrix_revision"],
                "actuals": {"2026-09": "1"},
            }
        )
    )
    matrix = data(app.get_report_matrix(query))
    changes = []
    for col, number in [("OPENING", "10000000000000000000000000000"), ("FACT", "1"), ("PLAN", "0")]:
        cell = next(c for c in matrix["rows"][0]["cells"] if c["column_id"] == "2026-09-" + col)
        changes.append(
            {"coordinate": cell["coordinate"], "value": {"kind": "QUANTITY", "quantity": number}}
        )
    data(
        app.save_report_cells(
            {
                **query,
                "base_revision": matrix["matrix_revision"],
                "idempotency_key": uuid4().hex,
                "changes": changes,
            }
        )
    )
    matrix = data(app.get_report_matrix(query))
    assert value(matrix, "2026-09-STOCK") == "10000000000000000000000000000"
    assert value(matrix, "2026-09-VARIANCE") == "10000000000000000000000000001"


def test_daily_pdf_aggregation_preserves_small_increment() -> None:
    cells = [
        {"value": {"kind": "QUANTITY", "quantity": number}}
        for number in ("10000000000000000000000000000", "1")
    ]
    assert aggregate(cells, calculated=False) == "10000000000000000000000000001"


@pytest.mark.parametrize("field", ["plans", "actuals"])
def test_intervening_presentation_save_cannot_overwrite_new_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    app = app_at(tmp_path)
    matrix = setup(app)
    request = {
        "report_type": "SUBSIDIARY",
        "organization_id": "1",
        "expected_revision": matrix["matrix_revision"],
        field: {"2026-09": "100"},
    }
    competing = {**request, field: {"2026-09": "200"}}
    original = app._workspace.save_presentation
    fired = False

    def intervening_save(*args: Any, **kwargs: Any) -> Any:
        nonlocal fired
        if not fired:
            fired = True
            data(app.save_report_presentation(competing))
        return original(*args, **kwargs)

    # A second request commits after the bridge parsed/validated its request,
    # but before the original repository opens its write transaction.
    monkeypatch.setattr(app._workspace, "save_presentation", intervening_save)
    rejected = app.save_report_presentation(request)
    assert not rejected["ok"]
    assert "Форма изменилась" in cast(dict[str, Any], rejected["error"])["message"]
    assert app._workspace.get_presentation(1, "SUBSIDIARY")[field] == {"2026-09": "200"}


def test_visual_presentation_can_still_save_without_expected_revision(tmp_path: Path) -> None:
    app = app_at(tmp_path)
    setup(app)
    saved = data(
        app.save_report_presentation(
            {
                "report_type": "SUBSIDIARY",
                "organization_id": "1",
                "title": "Новое название",
                "widths": {"party": 190},
            }
        )
    )
    assert saved["title"] == "Новое название"
    assert saved["widths"] == {"party": 190}
    assert saved["plans"] == {"2026-09": "1000"}
