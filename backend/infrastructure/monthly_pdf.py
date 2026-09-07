"""A4 landscape monthly matrix, repeating headers and vertical pagination."""

from __future__ import annotations

import calendar
import importlib
import io
from pathlib import Path
from typing import Any

MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


def render_monthly_pdf(
    snapshot: dict[str, Any], verification: dict[str, Any], font_path: Path
) -> bytes:
    pdfmetrics = importlib.import_module("reportlab.pdfbase.pdfmetrics")
    ttfonts = importlib.import_module("reportlab.pdfbase.ttfonts")
    canvas_module = importlib.import_module("reportlab.pdfgen.canvas")
    pdfmetrics.registerFont(ttfonts.TTFont("ReportingSerif", str(font_path)))
    stream = io.BytesIO()
    width, height = 841.8898, 595.2756
    canvas = canvas_module.Canvas(stream, pagesize=(width, height), pageCompression=1)
    canvas.setTitle(f"{snapshot['title']} - {snapshot['period']}")
    margin = 18.0
    usable = width - 2 * margin
    year, month = map(int, snapshot["period"].split("-"))
    labels = (
        ["Сводные данные по году"]
        + [f"{calendar.monthrange(year, m)[1]:02d}.{m:02d}.{year}" for m in range(1, month)]
        + [f"{d[-2:]}.{month:02d}.{year}" for d in snapshot["days"]]
    )
    fixed = [72.0, 64.0]
    number_width = (usable - sum(fixed)) / len(labels)
    col_widths = fixed + [number_width] * len(labels)
    page = 0

    def text(
        value: str, x: float, y: float, max_width: float, size: float = 6.5, center: bool = False
    ) -> None:
        measured = pdfmetrics.stringWidth(value, "ReportingSerif", size)
        if measured > max_width and measured:
            size *= max_width / measured
        canvas.setFont("ReportingSerif", size)
        if center:
            canvas.drawCentredString(x + max_width / 2, y, value)
        else:
            canvas.drawString(x, y, value)

    def wrap(value: str, w: float) -> list[str]:
        words = value.split()
        lines: list[str] = []
        line = ""
        for word in words:
            candidate = (line + " " + word).strip()
            if line and pdfmetrics.stringWidth(candidate, "ReportingSerif", 6) > w - 6:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        return lines

    def paragraph(value: str, x: float, y: float, w: float, h: float) -> None:
        lines = wrap(value, w)
        step = min(8.0, (h - 4) / max(1, len(lines)))
        size = min(6.0, step)
        for i, line in enumerate(lines):
            text(line, x + 3, y - 3 - (i + 1) * step, w - 6, size)

    def footer() -> None:
        if verification["status"] == "VERIFIED":
            status = (
                f"Данные подтверждены: {verification['signer_name']} | "
                f"{verification['signed_at']} (локальное подтверждение)"
            )
        elif verification["status"] == "STALE":
            status = "Данные изменены после подтверждения. Требуется повторная проверка."
        else:
            status = "Данные не подтверждены"
        text(status, margin, 28, usable - 80, 7)
        text(f"Лист {page}", width - 75, 28, 55, 7)
        text(f"Версия данных SHA-256: {verification['snapshot_sha256']}", margin, 17, usable, 5.5)

    def start_page(section: str) -> float:
        nonlocal page
        if page:
            footer()
            canvas.showPage()
        page += 1
        text(
            f"{snapshot['title']} | {snapshot['organization']} | "
            f"{MONTHS[month - 1]} {year} | {section}",
            margin,
            height - 16,
            usable,
            8,
        )
        top, header = height - 25, 58.0
        canvas.setLineWidth(0.5)
        canvas.setFillGray(1)
        canvas.rect(margin, top - header, sum(fixed), header, fill=1)
        canvas.setFillGray(0)
        paragraph("Изготовитель / поставщик (изделие)", margin, top, sum(fixed), header)
        x = margin + sum(fixed)
        for index, label in enumerate(labels):
            weekend = index >= month and calendar.weekday(year, month, index - month + 1) >= 5
            canvas.setFillGray(0.91 if index < month or weekend else 1)
            canvas.rect(x, top - header, number_width, header, fill=1)
            canvas.setFillGray(0)
            canvas.saveState()
            canvas.translate(x + number_width / 2 + 2, top - header + 4)
            canvas.rotate(90)
            if index == 0:
                text("Сводные данные", 0, 0, header - 8, 6)
                text("по году", 0, -7, header - 8, 6)
            else:
                text(label, 0, 0, header - 8, 6.2)
            canvas.restoreState()
            x += number_width
        canvas.setFillGray(0)
        return top - header

    groups: list[list[dict[str, Any]]] = []
    for row in snapshot["rows"]:
        if not groups or groups[-1][0]["group_id"] != row["group_id"]:
            groups.append([])
        groups[-1].append(row)
    y = start_page("Движение и остатки")
    production_started = False
    for group_index, group in enumerate(groups):
        is_production = any(
            "ASSEMBLY" in r["metric_code"] or "PRODUCT" in r["metric_code"] for r in group
        )
        if is_production and not production_started:
            y = start_page("Выпуск и движение изделий")
            production_started = True
        chunks: list[list[tuple[dict[str, Any], float]]] = [[]]
        chunk_height = 0.0
        for row in group:
            row_height = max(12.0, len(wrap(row["label"], fixed[1])) * 8.0 + 4)
            if chunk_height + row_height > 440 and chunks[-1]:
                chunks.append([])
                chunk_height = 0
            chunks[-1].append((row, row_height))
            chunk_height += row_height
        for chunk in chunks:
            block_height = sum(h for _, h in chunk)
            if y - block_height < 48:
                y = start_page("Продолжение")
            canvas.setFillGray(0.92 if group_index % 2 == 0 else 1)
            canvas.rect(margin, y - block_height, fixed[0], block_height, fill=1)
            canvas.setFillGray(0)
            paragraph(
                f"{group[0]['party']} / {group[0]['position']}", margin, y, fixed[0], block_height
            )
            for row, row_height in chunk:
                x = margin + fixed[0]
                values = [row["label"], row["summary"], *row["prior"], *row["days"]]
                for index, value in enumerate(values):
                    cell_width = col_widths[index + 1]
                    canvas.setFillGray(0.94 if group_index % 2 == 0 else 1)
                    canvas.rect(x, y - row_height, cell_width, row_height, fill=1)
                    canvas.setFillGray(0)
                    if index == 0:
                        paragraph(str(value), x, y, cell_width, row_height)
                    else:
                        text(str(value), x + 1.5, y - 8, cell_width - 3, 6.1, center=True)
                    x += cell_width
                y -= row_height
            canvas.setLineWidth(0.8)
            canvas.rect(margin, y, usable, block_height)
            canvas.setLineWidth(0.5)

    # Source sheet 3: monthly received/used/balance table, in batches of 3 positions.
    summaries = []
    for group in groups:
        by_code = {r["metric_code"]: r for r in group}
        codes = ["WRK_DAILY_RECEIVED", "WRK_DAILY_USED", "WRK_DAILY_BALANCE"]
        if all(code in by_code for code in codes):
            summaries.append([by_code[code] for code in codes])
    for offset in range(0, len(summaries), 3):
        block = summaries[offset : offset + 3]
        if y - 220 < 48:
            y = start_page("Месячная сводка")
        y -= 15
        summary_width = min(620.0, usable)
        sx = margin + usable - summary_width
        cw = summary_width / (1 + len(block) * 3)
        text("Получение изделий — месячная сводка", sx, y, summary_width, 8, center=True)
        y -= 6
        canvas.setFillGray(0.92)
        canvas.rect(sx, y - 34, cw, 34, fill=1)
        for i, trio in enumerate(block):
            x = sx + cw * (1 + i * 3)
            canvas.setFillGray(0.92)
            canvas.rect(x, y - 16, cw * 3, 16, fill=1)
            canvas.setFillGray(0)
            text(trio[0]["position"], x + 2, y - 11, cw * 3 - 4, 6)
            for j, label in enumerate(["Получено", "Использовано", "Остаток"]):
                canvas.rect(x + j * cw, y - 34, cw, 18)
                text(label, x + j * cw + 2, y - 28, cw - 4, 6)
        y -= 34
        for m in range(12):
            values = [MONTHS[m]] + [r["monthly"][m] for trio in block for r in trio]
            for i, value in enumerate(values):
                canvas.rect(sx + i * cw, y - 11, cw, 11)
                text(value, sx + i * cw + 2, y - 8, cw - 4, 6)
            y -= 11
        values = ["Итого"] + [r["summary"] for trio in block for r in trio]
        for i, value in enumerate(values):
            canvas.rect(sx + i * cw, y - 11, cw, 11)
            text(value, sx + i * cw + 2, y - 8, cw - 4, 6)
        y -= 11
    footer()
    canvas.save()
    return stream.getvalue()
