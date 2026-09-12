"""Weekly subsidiary report on landscape A4 with repeating column headers."""

from __future__ import annotations

import base64
import importlib
import io
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


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

    left = snapshot["left_columns"]
    columns = snapshot["columns"]
    # Identify the common stock and variance fields separately from supplier receipts.
    ordering = [0, 1, 2, 3, 4, 6, 8, 5, 7, 9] + list(range(10, len(left) + len(columns)))
    if snapshot.get("head_site"):
        ordering = list(range(len(left) + len(columns)))
    labels = [c["label"] for c in left] + [
        c["label"]
        if c.get("kind") != "USED" or snapshot.get("head_site")
        else "Расход " + c["label"]
        for c in columns
    ]
    widths: list[float] = [29, 78, 95, 62, 90, 53, 51, 55, 53, 60] + [45.0] * (len(columns) - 4)
    total = sum(widths)
    widths = [w * (841.89 - 36) / total for w in widths]
    data = [[p(labels[i], header_style) for i in ordering]]
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
        values = [row["left_values"].get(c["id"], "") for c in left] + row["values"]
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
            position = next(i for i, column in enumerate(left) if column["id"] == "position")
            scale = min((widths[position] - 6) / picture.imageWidth, 42 / picture.imageHeight)
            picture.drawWidth = picture.imageWidth * scale
            picture.drawHeight = picture.imageHeight * scale
            picture.hAlign = "LEFT"
            rendered[ordering.index(position)] = [
                p(values[position], name_style),
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
    shared = [ordering.index(i) for i, column in enumerate(left) if column.get("shared")] + [
        ordering.index(len(left) + i)
        for i, column in enumerate(columns)
        if column.get("kind")
        in {"OPENING", "STOCK", "VARIANCE", *(["USED"] if snapshot.get("head_site") else [])}
    ]
    group_first: dict[str, int] = {}
    for index, row in enumerate(snapshot["rows"], 1):
        group_first.setdefault(row["group_id"], index)
    story: list[Any] = []
    start = 1
    while start < len(data) or (start == 1 and len(data) == 1):
        end = start
        used_height = heights[0]
        while end < len(data) and used_height + heights[end] <= 400:
            used_height += heights[end]
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
        if story:
            story.append(layout.PageBreak())
        story.extend(
            [
                layout.Paragraph(
                    escape(
                        f"{snapshot['title']} | {snapshot['organization']} | {snapshot['period']}"
                    ),
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
                table,
            ]
        )
        if end == start:
            break
        start = end

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("SubsidiaryFont", 9)
        caption = (
            f"Проверено: {verification.get('signer_name', '')}, {verification.get('signed_at', '')}"
            if verification.get("status") == "VERIFIED"
            else "Данные не подтверждены"
        )
        canvas.drawString(18, 24, caption)
        canvas.drawRightString(824, 24, f"Лист {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
