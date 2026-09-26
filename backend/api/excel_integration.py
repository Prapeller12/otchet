"""One reviewed import package shared by recognition, UI and atomic persistence."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, cast

from backend.application.excel_reports import (
    ExcelWorkbookValidationError,
    _preview,
    _sha256,
    _source_context_hash,
)
from backend.application.import_recognition import apply_structure_overrides, recognize_document
from backend.application.import_workspace import (
    apply_structure,
    complete_package,
    export_exchange,
    get_package,
    project_structure,
    stage_package,
)
from backend.application.reference_transfer import validate_transfer
from backend.application.report_cells import ReportCellChange, ReportCellCoordinate, ReportCellValue
from backend.repositories.report_facts import ReportCellUnitOfWork


def exchange_matrix(app: Any, report: str, organization: int, year: int | None) -> dict[str, Any]:
    matrix: dict[str, Any] = app._build_matrix(report, organization, year)
    matrix.update(export_exchange(app._database_path, organization, report))
    return matrix


def project(
    app: Any,
    organization: int,
    report: str,
    year: int | None,
    actions: list[dict[str, Any]],
    mode: str,
    preview_changes: list[ReportCellChange] | None = None,
) -> dict[str, Any]:
    def build(path: Path) -> dict[str, Any]:
        from backend.api.working_reference_bridge import WorkingReferenceApplicationBridge

        shadow = WorkingReferenceApplicationBridge(
            path,
            migrations_directory=app._migrations_directory,
            definitions_directory=app._definitions_directory,
            initialize_workspace=False,
        )
        if preview_changes:
            shadow._service.save_cells(
                preview_changes,
                idempotency_key="reconciliation-preview",
                actor_ref="isolated-import-preview",
            )
        return exchange_matrix(shadow, report, organization, year)

    return project_structure(
        app._database_path,
        organization,
        report,
        app._group_templates(report),
        actions,
        build,
        mode=mode,
    )


def coordinate_keys(matrix: dict[str, Any]) -> list[str]:
    return [
        json.dumps(c["coordinate"], sort_keys=True, separators=(",", ":"))
        for row in matrix["rows"]
        for c in row["cells"]
        if c["state"]["access"] == "editable"
    ]


def repeated_preview(
    app: Any, original_hash: str, matrix: dict[str, Any], mode: str, profile_version: str
) -> dict[str, Any] | None:
    context = {
        "report_type": matrix["report_type"],
        "organization_id": int(matrix["organization_id"]),
        "year": matrix.get("year"),
        "mode": mode,
        "profile_version": profile_version,
        "target_identity": matrix.get("exchange_identity", {}),
    }
    previous = app._excel._imports.find_committed_source(
        report_type=matrix["report_type"],
        organization_id=int(matrix["organization_id"]),
        source_sha256=_source_context_hash(original_hash, context),
    )
    return None if previous is None else _preview(previous, already_imported=True).to_dict()


def value_review(app: Any, batch_id: str, matrix: dict[str, Any]) -> list[dict[str, Any]]:
    batch = app._excel._imports.get_batch(batch_id)
    if batch is None:
        return []
    lookup = {}
    columns = {column["id"]: column for column in matrix["time_columns"]}
    for row in matrix["rows"]:
        for cell in row["cells"]:
            key = json.dumps(cell["coordinate"], sort_keys=True, separators=(",", ":"))
            column = columns.get(cell["column_id"], {})
            lookup[key] = {
                "target": " · ".join(
                    str(value)
                    for value in row.get("left_values", {}).values()
                    if value not in (None, "")
                ),
                "period": " · ".join(
                    str(column.get(field, "")) for field in ("group_label", "label")
                ),
                "before": cell["value"],
            }
    return [
        {
            "source_cell": row.source_cell,
            **lookup.get(
                row.coordinate_json,
                {"target": "Новая позиция", "period": "", "before": {"kind": "DATA_NOT_PROVIDED"}},
            ),
            "after": {
                "kind": row.value_kind,
                **({"quantity": row.quantity} if row.quantity is not None else {}),
            },
            "classification": row.classification,
        }
        for row in batch.rows
    ]


def prepare_transfer(app: Any, request: dict[str, Any], organization: int) -> dict[str, Any]:
    for field in ("id", "report_type"):
        if not isinstance(request.get(field), str) or not request[field].strip():
            raise ValueError("Укажите исходную книгу и тип рабочего отчёта")
    if not isinstance(request.get("mappings", []), list) or not all(
        isinstance(item, dict) for item in request.get("mappings", [])
    ):
        raise ValueError("Сопоставления должны быть списком объектов")
    for field in ("period_rules", "sheet_decisions", "structure_overrides"):
        if not isinstance(request.get(field, {}), dict):
            raise ValueError("Уточнения периодов, листов и структуры должны быть объектами")
    identity = request["id"]
    document = app._references.get(identity, organization, staged=True, original=True)
    report = request["report_type"]
    year = request.get("year")
    mode = str(request.get("mode", "update")).upper()
    if mode not in {"CREATE", "APPEND", "UPDATE"}:
        raise ValueError("Выберите режим создания, дополнения или обновления")
    recognition = recognize_document(document)
    from backend.application.import_profiles import apply_reusable_profile, reusable_profile

    saved_profile = reusable_profile(app._database_path, organization, report, year, recognition)
    request = apply_reusable_profile(request, saved_profile)
    recognition = apply_structure_overrides(recognition, request.get("structure_overrides"))
    decision_context = {
        key: request.get(key, {} if key != "mappings" else [])
        for key in ("mappings", "period_rules", "sheet_decisions", "structure_overrides")
    }
    decision_context["mappings"] = sorted(
        [{k: v for k, v in item.items() if k != "method"} for item in decision_context["mappings"]],
        key=lambda item: item.get("source", ""),
    )
    decision_hash = hashlib.sha256(
        json.dumps(
            decision_context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    profile_version = (
        f"{recognition['profile_id']}:{recognition['profile_version']}:{decision_hash}"
    )
    current_matrix = exchange_matrix(app, report, organization, year)
    previous = repeated_preview(app, document["sha256"], current_matrix, mode, profile_version)
    if previous is not None:
        return {
            **previous,
            "reference_workbook": document,
            "mode": mode.lower(),
            "transfer_validated": True,
            "validation_pending": False,
            "position_count": 0,
            "dictionary_count": 0,
            "structural_actions": [],
        }
    decisions = request.get("sheet_decisions", {})
    if not isinstance(decisions, dict):
        raise ValueError("Решения по листам должны быть объектом")
    for decision in decisions.values():
        if not isinstance(decision, dict) or not isinstance(decision.get("include"), bool):
            raise ValueError("Укажите решение о включении листа")
    actions = [
        a
        for a in recognition["structural_actions"]
        if decisions.get(str(a.get("sheet_index", "")), {}).get("include") is not False
    ]
    structural_issues = [
        {"source_cell": action.get("source_key", ""), **error}
        for action in actions
        for error in action.get("errors", [])
    ]
    reviewed: list[dict[str, Any]] = actions
    projected = None
    try:
        if structural_issues:
            raise ValueError("Уточните отмеченные реквизиты исходных позиций")
        projected = project(app, organization, report, year, actions, mode)
        matrix = projected["matrix"]
        reviewed = projected["structural_actions"]
    except ValueError as error:
        matrix = exchange_matrix(app, report, organization, year)
        if not structural_issues:
            structural_issues.append(
                {"source_cell": "", "code": "STRUCTURE_REQUIRED", "message": str(error)}
            )
    checked = validate_transfer(
        document,
        matrix,
        request.get("mappings", []),
        request.get("period_rules"),
        decisions,
        recognition,
    )
    issues = [*structural_issues, *checked["issues"]]
    # Every counter represents the same reviewed result, never raw non-empty Excel cells.
    result = {
        **app._references.preview(document),
        **checked,
        "issues": issues,
        "error_count": len(issues),
        "status": "INVALID" if issues else "STAGED",
        "new_count": 0,
        "changed_count": 0,
        "same_count": 0,
        "reference_workbook": document,
        "target_matrix": matrix,
        "structural_actions": reviewed,
        "position_count": sum(a.get("kind") == "CREATE_POSITION" for a in reviewed),
        "dictionary_count": sum(a.get("suppliers_added", 0) for a in reviewed),
        "mode": mode.lower(),
        "transfer_validated": not issues,
        "validation_pending": False,
    }
    from backend.application.import_reconciliation import reconcile_controls

    result["reconciliation"] = reconcile_controls(recognition, matrix, values_applied=False)
    if issues or projected is None:
        return result
    staged = app._excel.stage_transfer(
        source_document=document,
        report_type=report,
        organization_id=organization,
        changes=checked["changes"],
        mode=mode,
        profile_version=profile_version,
        year=matrix["year"],
        target_identity=matrix["exchange_identity"],
        metadata={
            "recognition": {
                "profile_id": recognition["profile_id"],
                "profile_version": recognition["profile_version"],
            },
            "decisions": decision_context,
        },
    )
    result.update(staged)
    result["skipped_count"] = int(staged.get("skipped_count", 0)) + checked["skipped_count"]
    result["transfer_validated"] = not staged.get("error_count")
    result["profile_reused"] = saved_profile is not None and not request.get("reset_profile")
    result["applied_period_rules"] = request.get("period_rules", {})
    result["applied_sheet_decisions"] = decisions
    result["reference_workbook"] = document
    if not staged.get("error_count") and not staged.get("already_imported"):
        batch = app._excel._imports.get_batch(str(staged["batch_id"]))
        preview_changes = [
            ReportCellChange(
                coordinate=ReportCellCoordinate.from_mapping(json.loads(row.coordinate_json)),
                value=ReportCellValue(kind=row.value_kind, quantity=row.quantity),
                expected_revision=row.expected_revision,
            )
            for row in batch.rows
            if row.classification != "SAME"
        ]
        calculated = project(app, organization, report, year, actions, mode, preview_changes)[
            "matrix"
        ]
        result["reconciliation"] = reconcile_controls(recognition, calculated, values_applied=True)
    if not staged.get("already_imported"):
        package = {
            "plan": projected["plan"],
            "allowed_coordinates": coordinate_keys(matrix),
            "workbook_id": document["id"],
            "mode": mode,
            "year": matrix["year"],
            "profile_id": recognition["profile_id"],
            "profile_version": recognition["profile_version"],
            "structure_fingerprint": recognition["structure_fingerprint"],
            "mappings": request.get("mappings", []),
            "period_rules": request.get("period_rules", {}),
            "sheet_decisions": decisions,
            "structure_overrides": request.get("structure_overrides", {}),
            "structural_actions": reviewed,
        }
        stage_package(
            app._database_path,
            str(staged["batch_id"]),
            organization,
            report,
            document["sha256"],
            package,
        )
    return result


def commit_package(app: Any, identity: str, year: int | None) -> dict[str, Any]:
    batch = app._excel._imports.get_batch(identity)
    if batch is None:
        raise ValueError("Пакет импорта не найден")
    if batch.status == "INVALID" or batch.error_count:
        raise ExcelWorkbookValidationError("Импорт содержит ошибки и не может быть проведён")
    try:
        package = get_package(app._database_path, identity, batch.organization_id)
    except ValueError:
        package = None
    if package is None:
        if (
            batch.metadata.get("structural_changes")
            or batch.metadata.get("schema_version") == 2
            or "recognition" in batch.metadata
        ):
            raise ValueError("Проверенный пакет структуры отсутствует; повторите проверку импорта")
        return dict(
            app._excel.commit_import(
                identity,
                allowed_coordinates=lambda report, org: app._editable_coordinate_keys(
                    report, int(org), year
                ),
            )
        )

    def persist(unit: ReportCellUnitOfWork) -> None:
        connection = cast(Any, unit).connection
        apply_structure(connection, package["plan"])
        if package.get("workbook_id"):
            connection.execute(
                "UPDATE reference_workbooks SET committed=1 WHERE id=?", (package["workbook_id"],)
            )
            connection.execute(
                "INSERT INTO reference_transfer_decisions(batch_id,workbook_id,decisions) "
                "VALUES(?,?,?)",
                (
                    identity,
                    package["workbook_id"],
                    json.dumps(
                        {
                            "mappings": package["mappings"],
                            "period_rules": package["period_rules"],
                            "sheet_decisions": package["sheet_decisions"],
                            "structure_overrides": package.get("structure_overrides", {}),
                            "profile_id": package["profile_id"],
                            "profile_version": package["profile_version"],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        complete_package(connection, identity)

    result = dict(
        app._excel.commit_import(
            identity,
            allowed_coordinates=lambda _report, _org: set(package["allowed_coordinates"]),
            before_commit=persist,
        )
    )
    # Return to the working matrix; source archive is evidence, not the destination report.
    result["working_report"] = True
    return result


def prepare_canonical(
    app: Any,
    source: Path,
    report: str,
    organization: int,
    year: int | None,
    mode: str,
    sheet_decisions: object = None,
    source_file_name: str | None = None,
) -> dict[str, Any]:
    from openpyxl import load_workbook

    from backend.application.excel_reports import _validate_source_file
    from backend.application.import_workspace import coordinate_remap, exchange_actions
    from backend.infrastructure.excel.matrix_exchange_v2 import (
        METADATA_SHEET,
        read_exchange_snapshot,
        validate_metadata,
    )

    _validate_source_file(source)
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 10000
                or sum(entry.file_size for entry in entries) > 100 * 1024 * 1024
            ):
                raise ExcelWorkbookValidationError(
                    "Распакованная книга слишком велика: предел 100 МБ"
                )
    except zipfile.BadZipFile as error:
        raise ExcelWorkbookValidationError("Повреждённый файл Excel") from error
    matrix = exchange_matrix(app, report, organization, year)
    if sheet_decisions is None:
        sheet_decisions = {}
    if not isinstance(sheet_decisions, dict):
        raise ExcelWorkbookValidationError("Решения по листам должны быть объектом")
    profile_version = "canonical-v2"
    if sheet_decisions:
        profile_version += (
            ":"
            + hashlib.sha256(
                json.dumps(
                    sheet_decisions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
    previous = repeated_preview(app, _sha256(source), matrix, mode, profile_version)
    if previous is not None:
        return {**previous, "mode": mode.lower(), "position_count": 0, "dictionary_count": 0}
    snapshot = read_exchange_snapshot(source)
    projected = None
    if snapshot is not None:
        workbook = load_workbook(source, keep_links=False)
        try:
            if METADATA_SHEET in workbook:
                metadata, _issues = validate_metadata(workbook, matrix)
                actions = exchange_actions(
                    snapshot,
                    metadata["structural_changes"],
                    matrix["exchange_identity"],
                    target_structure=matrix["exchange_structure"],
                )
                projected = project(app, organization, report, year, actions, mode)
                matrix = projected["matrix"]
                matrix["exchange_source_dataset_id"] = snapshot["exchange_identity"].get(
                    "dataset_id"
                )
                matrix["exchange_coordinate_remap"] = coordinate_remap(snapshot, matrix)
        finally:
            workbook.close()
    matrix["exchange_sheet_decisions"] = sheet_decisions
    result = app._excel.stage_import(
        source,
        report_type=report,
        organization_id=organization,
        matrix=matrix,
        mode=mode,
        profile_version=profile_version,
        source_file_name=source_file_name,
    ).to_dict()
    result["mode"] = mode.lower()
    result["value_changes"] = value_review(app, str(result["batch_id"]), matrix)
    if projected is not None:
        result["structural_actions"] = projected["structural_actions"]
        result["position_count"] = sum(
            a.get("kind") == "CREATE_POSITION" for a in projected["structural_actions"]
        )
        result["dictionary_count"] = sum(
            a.get("suppliers_added", 0) for a in projected["structural_actions"]
        )
        if not result["error_count"] and not result.get("already_imported"):
            stage_package(
                app._database_path,
                str(result["batch_id"]),
                organization,
                report,
                str(result["source_sha256"]),
                {
                    "plan": projected["plan"],
                    "allowed_coordinates": coordinate_keys(matrix),
                    "mode": mode,
                    "year": matrix["year"],
                    "structural_actions": projected["structural_actions"],
                },
            )
    return dict(result)
