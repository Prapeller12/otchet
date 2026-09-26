"""Weekly subsidiary report on landscape A4 with repeating column headers."""

from __future__ import annotations

import base64
import copy
import importlib
import io
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from backend.application.report_signature_caption import signature_caption


def render_subsidiary_pdf(
    snapshot: dict[str, Any], verification: dict[str, Any], font_path: Path
) -> bytes:
    metrics = importlib.import_module("reportlab.pdfbase.pdfmetrics")
    fonts = importlib.import_module("reportlab.pdfbase.ttfonts")
    layout = importlib.import_module("reportlab.platypus")
    styles = importlib.import_module("reportlab.lib.styles")
    colors = importlib.import_module("reportlab.lib.colors")
    metrics.registerFont(
        fonts.TTFont("SubsidiaryFont", str(font_path.with_name("ReportingSans.ttf")))
    )
    metrics.registerFont(
        fonts.TTFont("SubsidiaryBold", str(font_path.with_name("ReportingSansBold.ttf")))
    )
    stream = io.BytesIO()
    document = layout.SimpleDocTemplate(
        stream,
        pagesize=(841.89, 595.28),
        leftMargin=18,
        rightMargin=18,
        topMargin=24,
        bottomMargin=45,
        title=f"{snapshot['title']} - {snapshot['period']}",
    )
    style = styles.ParagraphStyle("cell", fontName="SubsidiaryFont", fontSize=9, leading=12)
    title_style = styles.ParagraphStyle(
        "title", parent=style, fontName="SubsidiaryBold", fontSize=14, leading=18, spaceAfter=10
    )
    header_style = styles.ParagraphStyle(
        "header", parent=style, fontName="SubsidiaryBold", fontSize=7, leading=10
    )
    number_style = styles.ParagraphStyle(
        "number", parent=style, fontName="SubsidiaryBold", alignment=2
    )
    name_style = styles.ParagraphStyle("name", parent=style, fontName="SubsidiaryBold")

    def p(value: object, cell_style: Any = style) -> Any:
        return layout.Paragraph(escape(str(value)), cell_style)

    left = [{"id": "picture", "label": "Фото", "shared": True}, *snapshot["left_columns"]]
    if snapshot.get("head_site"):
        left.append(
            {"id": "manufactured_total", "label": "Изготовлено за год (внесено)", "shared": False}
        )
    columns = snapshot["columns"]
    # Presentation ordering follows the reference; all values remain backend snapshots.
    identifiers = {column["id"]: index for index, column in enumerate(left)}
    kinds = {column.get("kind"): len(left) + index for index, column in enumerate(columns)}
    base = [
        identifiers[key]
        for key in ("picture", "number", "designation", "position", "norm", "party")
    ]
    if snapshot.get("head_site"):
        ordering = [
            *base,
            kinds["STOCK"],
            identifiers["manufactured_total"],
            kinds["FACT"],
            kinds["PLAN"],
            kinds["FACT"],
        ]
        extra_kinds = ["OPENING", "USED", "VARIANCE"]
    else:
        ordering = [
            *base,
            kinds["STOCK"],
            identifiers["contract"],
            kinds["RECEIVED"],
            kinds["VARIANCE"],
        ]
        ordering.extend(
            len(left) + i for i, column in enumerate(columns) if column.get("kind") == "USED"
        )
        extra_kinds = ["OPENING"]
    labels = [c["label"] for c in left] + [
        c["label"]
        if c.get("kind") != "USED" or snapshot.get("head_site")
        else "Расход " + c["label"]
        for c in columns
    ]
    if snapshot.get("head_site"):
        labels[kinds["FACT"]] = "Факт месяца"
    width_by_id = {
        "picture": 45,
        "number": 30,
        "designation": 70,
        "position": 100,
        "norm": 62,
        "party": 88,
        "contract": 60,
        "manufactured_total": 65,
    }
    widths: list[float] = [float(width_by_id.get(c["id"], 60)) for c in left] + [55.0] * len(
        columns
    )
    factor = (841.89 - 36) / sum(widths[i] for i in ordering)
    widths = [width * factor for width in widths]
    data = [[p(labels[i], header_style) for i in ordering]]
    if snapshot.get("head_site"):
        data[0][-3] = p("Изготовлено в месяце", header_style)
        data[0][-2] = p("План " + snapshot["period"], header_style)
        data[0][-1] = p("Факт " + snapshot["period"], header_style)
    commands: list[Any] = [
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#bbbbbb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for r, row in enumerate(snapshot["rows"], 1):
        values = [
            row.get("manufactured_total", "")
            if c["id"] == "manufactured_total"
            else row["left_values"].get(c["id"], "")
            for c in left
        ] + row["values"]
        rendered = [
            p(
                values[i],
                number_style
                if i >= len(left)
                else name_style
                if left[i]["id"] == "position"
                else style,
            )
            for i in ordering
        ]
        if row.get("image"):
            # Images are validated PNG/JPEG data URLs in the stored configuration.
            # Embed bytes directly: printing must not depend on files or network URLs.
            encoded = row["image"].split(",", 1)[1]
            picture = layout.Image(io.BytesIO(base64.b64decode(encoded, validate=True)))
            position = identifiers["picture"]
            scale = min((widths[position] - 6) / picture.imageWidth, 42 / picture.imageHeight)
            picture.drawWidth = picture.imageWidth * scale
            picture.drawHeight = picture.imageHeight * scale
            picture.hAlign = "LEFT"
            rendered[ordering.index(position)] = [
                layout.Spacer(1, 3),
                picture,
            ]
        data.append(rendered)
        for c, original in enumerate(ordering):
            if original >= len(left) and columns[original - len(left)].get("kind") in {
                "STOCK",
                "VARIANCE",
            }:
                commands.append(("BACKGROUND", (c, r), (c, r), colors.HexColor("#f2f3f4")))
                if columns[original - len(left)]["kind"] == "VARIANCE" and str(
                    values[original]
                ).startswith("-"):
                    commands.append(("BACKGROUND", (c, r), (c, r), colors.HexColor("#fde8e8")))
    # Measure unmerged rows, then merge within each page only. A detail with many
    # suppliers may continue on another page; its common totals and image repeat.
    measured = layout.LongTable(data, colWidths=[widths[i] for i in ordering])
    measured.setStyle(layout.TableStyle(commands))
    measured.wrap(805, 10000)
    heights = measured._rowHeights
    shared = [
        ordering.index(i) for i, column in enumerate(left) if column.get("shared") and i in ordering
    ] + [
        ordering.index(len(left) + i)
        for i, column in enumerate(columns)
        if len(left) + i in ordering
        and column.get("kind")
        in {"OPENING", "STOCK", "VARIANCE", *(["USED"] if snapshot.get("head_site") else [])}
    ]
    group_first: dict[str, int] = {}
    for index, row in enumerate(snapshot["rows"], 1):
        group_first.setdefault(row["group_id"], index)

    def header_blocks(include_header: bool) -> list[Any]:
        header = snapshot.get("header", {})
        header_story: list[Any] = []
        if include_header:
            for key, label in (
                ("product_designation", "Шифр изделия"),
                ("product_name", "Изделие"),
                ("factory_name", "Завод"),
            ):
                if header.get(key):
                    header_story.append(p(label + ": " + str(header[key])))
            if header.get("product_image"):
                picture = layout.Image(
                    io.BytesIO(
                        base64.b64decode(header["product_image"].split(",", 1)[1], validate=True)
                    )
                )
                ratio = min(120 / picture.imageWidth, 65 / picture.imageHeight)
                picture.drawWidth, picture.drawHeight = (
                    picture.imageWidth * ratio,
                    picture.imageHeight * ratio,
                )
                picture.hAlign = "LEFT"
                header_story.append(picture)
            codes = snapshot.get("production_codes", [])
            if codes:
                code_data = [
                    [
                        p(label, header_style)
                        for label in (
                            "Код / модификация",
                            "Факт за год (внесено)",
                            "План месяца",
                            "Факт месяца",
                        )
                    ]
                ]
                for code in codes:
                    code_data.append(
                        [
                            p(code["label"]),
                            p(snapshot.get("production_code_annual", {}).get(code["id"], "")),
                            p(code.get("plans", {}).get(snapshot["period"], "")),
                            p(code.get("actuals", {}).get(snapshot["period"], "")),
                        ]
                    )
                code_table = layout.LongTable(
                    code_data, colWidths=[355, 150, 150, 150], repeatRows=1
                )
                code_table.setStyle(layout.TableStyle(commands[:7]))
                header_story.extend([code_table, layout.Spacer(1, 8)])
        return header_story

    heading = [
        layout.Paragraph(
            escape(f"{snapshot['title']} | {snapshot['organization']} | {snapshot['period']}"),
            title_style,
        ),
        p(
            f"План выпуска: {snapshot['plan'] or 'не задан'} шт. "
            f"Выпущено: {snapshot.get('actual') or '—'} шт. "
            f"Выполнение: {snapshot.get('completion') or '—'} %. "
            + (
                f"Месячный план/факт. Остаток на {snapshot['as_of']}."
                if snapshot.get("head_site")
                else f"Недельные значения — расход. Остаток на {snapshot['as_of']}."
            )
        ),
        layout.Spacer(1, 10),
    ]
    detail_header = ["Позиция / производитель", *[labels[kinds[kind]] for kind in extra_kinds]]
    if snapshot.get("head_site"):
        detail_header.append("Объём по договору")
    detail_data = [[p(label, header_style) for label in detail_header]]
    for row in snapshot["rows"]:
        values = [
            row["left_values"].get("position", "") + " / " + row["left_values"].get("party", "")
        ]
        values.extend(row["values"][kinds[kind] - len(left)] for kind in extra_kinds)
        if snapshot.get("head_site"):
            values.append(row["left_values"].get("contract", ""))
        detail_data.append([p(value) for value in values])
    detail_widths = [300] + [505 / (len(detail_header) - 1)] * (len(detail_header) - 1)
    detail_measure = layout.LongTable(detail_data, colWidths=detail_widths)
    detail_measure.setStyle(layout.TableStyle(commands[:7]))
    detail_measure.wrap(805, 10000)
    detail_heights = detail_measure._rowHeights

    def flow_height(items: list[Any]) -> float:
        return float(
            sum(
                item.wrap(805, 10000)[1] + item.getSpaceBefore() + item.getSpaceAfter()
                for item in items
            )
        )

    story: list[Any] = []
    start = 1
    while start < len(data) or (start == 1 and len(data) == 1):
        header_story = header_blocks(start == 1)
        base_height = flow_height(heading) + heights[0] + detail_heights[0] + 30
        first_row_height = heights[start] + detail_heights[start] if start < len(data) else 0
        if (
            header_story
            and base_height + flow_height(header_story) + first_row_height > document.height - 12
        ):
            story.extend([*copy.deepcopy(heading), *header_story, layout.PageBreak()])
            header_story = []
        end = start
        used_height = base_height + flow_height(header_story)
        while (
            end < len(data)
            and used_height + heights[end] + detail_heights[end] <= document.height - 12
        ):
            used_height += heights[end] + detail_heights[end]
            end += 1
        if end == start and end < len(data):
            end += 1
        page_data = [[*data[0]]] + [[*row] for row in data[start:end]]
        page_commands = list(commands[:7])
        for command in commands[7:]:
            row_number = command[1][1]
            if start <= row_number < end:
                local = row_number - start + 1
                page_commands.append(
                    (command[0], (command[1][0], local), (command[2][0], local), *command[3:])
                )
        cursor = start
        while cursor < end:
            group_id = snapshot["rows"][cursor - 1]["group_id"]
            stop = cursor + 1
            while stop < end and snapshot["rows"][stop - 1]["group_id"] == group_id:
                stop += 1
            local = cursor - start + 1
            last = stop - start
            first = group_first[group_id]
            for column in shared:
                page_data[local][column] = data[first][column]
                if last > local:
                    page_commands.append(("SPAN", (column, local), (column, last)))
                # Repeat the deficit shading across the whole shared cell.
                for command in commands[7:]:
                    if command[1] == (column, first):
                        page_commands.append(
                            (command[0], (column, local), (column, last), *command[3:])
                        )
            cursor = stop
        table = layout.LongTable(
            page_data,
            colWidths=[widths[i] for i in ordering],
            rowHeights=[heights[0], *heights[start:end]],
            repeatRows=1,
            hAlign="LEFT",
        )
        table.setStyle(layout.TableStyle(page_commands))
        if story and not isinstance(story[-1], layout.PageBreak):
            story.append(layout.PageBreak())
        story.extend([*copy.deepcopy(heading), *header_story, table])
        if end > start:
            story.extend(
                [layout.Spacer(1, 8), p("Дополнительные исходные данные и расчёты", name_style)]
            )
            details = layout.LongTable(
                [detail_data[0], *detail_data[start:end]], colWidths=detail_widths, repeatRows=1
            )
            details.setStyle(layout.TableStyle(commands[:7]))
            story.append(details)
        if end == start:
            break
        start = end

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        first, second = signature_caption(verification)
        for line, y, available in ((first, 28, 726), (second, 17, 806)):
            size = 8.0
            measured = metrics.stringWidth(line, "SubsidiaryFont", size)
            canvas.setFont("SubsidiaryFont", min(size, size * available / max(measured, 1)))
            canvas.drawString(18, y, line)
        canvas.setFont("SubsidiaryFont", 9)
        canvas.drawRightString(824, 28, f"Лист {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
