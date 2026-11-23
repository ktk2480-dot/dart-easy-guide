"""보고서 모델과 화면/PDF 공용 렌더러."""
from __future__ import annotations

import html
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import (
    QAbstractTextDocumentLayout, QColor, QFont, QImage, QPageSize, QPainter, QPdfWriter, QTextCursor, QTextDocument,
    QTextFormat,
)

from . import APP_NAME

DISCLAIMER = (
    "이 자료는 회사가 제출한 공시를 OpenDART API로 불러와 정리한 것으로, 투자 권유나 투자 판단의 근거가 아닙니다. "
    "금융감독원은 정보의 정확성과 완전성을 보장하지 않으며, 중요한 내용은 공시 원문을 확인해 주세요."
)

_CSS = """
body { color:#1b1f24; font-family:'Malgun Gothic'; font-size:10pt; }
h1 { font-size:19pt; font-weight:700; color:#10233f; margin-top:0; margin-bottom:2px; }
h2 { font-size:12.5pt; font-weight:700; color:#173b6c; margin-top:18px; margin-bottom:6px; }
p { margin-top:2px; margin-bottom:5px; line-height:145%; }
li { margin-bottom:3px; }
a { color:#1a5fb4; text-decoration:none; }
.sub { color:#5b6470; font-size:9.5pt; }
.small { color:#5b6470; font-size:8.5pt; }
.code { color:#34404f; font-family:'Consolas','Malgun Gothic'; font-size:8pt; }
.mlabel { color:#5b6470; font-size:8.5pt; }
.mvalue { color:#10233f; font-size:14pt; font-weight:700; }
.mnote { color:#5b6470; font-size:8pt; }
th { background-color:#edf1f6; color:#26364b; font-size:9pt; font-weight:700; text-align:left; }
td { font-size:9pt; }
"""


@dataclass
class Metric:
    label: str
    value: str
    note: str = ""


@dataclass
class Table:
    columns: list[str]
    rows: list[list[str]]
    links: dict[tuple[int, int], str] = field(default_factory=dict)
    right: set[int] = field(default_factory=set)
    widths: list[int] | None = None
    note: str = ""


@dataclass
class Section:
    title: str
    paragraphs: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    chart: bytes | None = None
    table: Table | None = None


@dataclass
class Report:
    title: str
    subtitle: str = ""
    summary: list[str] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    tips: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    file_stem: str = "DART_리포트"
    show_disclaimer: bool = True
    created_at: datetime = field(default_factory=datetime.now)


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _metrics_html(metrics: list[Metric]) -> str:
    if not metrics:
        return ""
    per_row = 4 if len(metrics) in (4, 7, 8) else 3
    cell_width = int(100 / per_row)
    out = ['<table width="100%" cellspacing="5" cellpadding="9" border="0" style="margin-top:8px;">']
    for start in range(0, len(metrics), per_row):
        out.append("<tr>")
        chunk = metrics[start:start + per_row]
        for item in chunk:
            note = f'<br><span class="mnote">{_e(item.note)}</span>' if item.note else ""
            out.append(
                f'<td width="{cell_width}%" bgcolor="#f2f5f9"><span class="mlabel">{_e(item.label)}</span><br>'
                f'<span class="mvalue">{_e(item.value)}</span>{note}</td>'
            )
        out.extend(f'<td width="{cell_width}%"></td>' for _ in range(per_row - len(chunk)))
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _table_html(table: Table) -> str:
    out = ['<table width="100%" cellspacing="0" cellpadding="5" border="0" '
           'style="border-collapse:collapse; margin-top:4px;"><thead><tr>']
    for index, column in enumerate(table.columns):
        width = f' width="{table.widths[index]}%"' if table.widths else ""
        align = ' align="right"' if index in table.right else ""
        out.append(f"<th{width}{align}>{_e(column)}</th>")
    out.append("</tr></thead><tbody>")
    for row_index, row in enumerate(table.rows):
        background = ' bgcolor="#f8f9fb"' if row_index % 2 else ""
        out.append(f"<tr{background}>")
        for col_index, value in enumerate(row):
            align = ' align="right"' if col_index in table.right else ""
            text = _e(value)
            url = table.links.get((row_index, col_index))
            if url:
                text = f'<a href="{_e(url)}">{text}</a>'
            out.append(f"<td{align}>{text}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    if table.note:
        out.append(f'<p class="small">{_e(table.note)}</p>')
    return "".join(out)


def render_html(report: Report, image_width: int = 660) -> tuple[str, dict[str, bytes]]:
    images: dict[str, bytes] = {}
    out = [f"<h1>{_e(report.title)}</h1>"]
    if report.subtitle:
        out.append(f'<p class="sub">{_e(report.subtitle)}</p>')
    if report.summary:
        out.append('<table width="100%" cellspacing="0" cellpadding="11" border="0" style="margin-top:8px;">'
                   '<tr><td bgcolor="#e9f1fb"><b>한눈에 보기</b><br>')
        out.append("<br>".join(_e(line) for line in report.summary))
        out.append("</td></tr></table>")
    out.append(_metrics_html(report.metrics))
    for section in report.sections:
        out.append(f"<h2>{_e(section.title)}</h2>")
        out.extend(f"<p>{_e(paragraph)}</p>" for paragraph in section.paragraphs)
        if section.bullets:
            out.append("<ul>" + "".join(f"<li>{_e(item)}</li>" for item in section.bullets) + "</ul>")
        if section.chart:
            name = f"chart-{len(images)}.png"
            images[name] = section.chart
            pixel_width, pixel_height = struct.unpack(">II", section.chart[16:24])
            shown_height = round(image_width * pixel_height / pixel_width)
            out.append(f'<p align="center" style="line-height:100%;"><img src="{name}" width="{image_width}" height="{shown_height}"></p>')
        if section.table:
            out.append(_table_html(section.table))
    if report.tips:
        out.append("<h2>이 결과를 읽는 법</h2><ul>")
        out.extend(f"<li>{_e(tip)}</li>" for tip in report.tips)
        out.append("</ul>")
    if report.calls:
        out.append("<h2>이 결과를 만든 API 호출</h2>")
        out.extend(f'<p class="code">{_e(call)}</p>' for call in report.calls[:16])
        if len(report.calls) > 16:
            out.append(f'<p class="small">외 {len(report.calls) - 16}건</p>')
    stamp = report.created_at.strftime("%Y-%m-%d %H:%M")
    if report.show_disclaimer:
        out.append(f'<p class="small" style="margin-top:16px;">{_e(DISCLAIMER)}</p>')
    out.append(f'<p class="small">작성 시각 {stamp} · {_e(APP_NAME)}</p>')
    return "".join(out), images


def apply_to_document(document: QTextDocument, report: Report, image_width: int = 660) -> None:
    body, images = render_html(report, image_width)
    document.setDefaultStyleSheet(_CSS)
    for name, data in images.items():
        document.addResource(QTextDocument.ImageResource, QUrl(name), QImage.fromData(data, "PNG"))
    document.setHtml(body)


def _keep_headings_with_content(document: QTextDocument, page_height: float) -> None:
    """제목만 쪽 끝에 남고 표나 차트가 다음 쪽으로 넘어가지 않게 한다."""
    layout = document.documentLayout()
    block = document.begin()
    while block.isValid():
        following = block.next()
        if block.blockFormat().headingLevel() == 2 and following.isValid():
            heading_page = int(layout.blockBoundingRect(block).top() // page_height)
            # 차트처럼 큰 줄은 블록 시작점이 아니라 끝점이 다음 쪽으로 넘어간다.
            content_page = int((layout.blockBoundingRect(following).bottom() - 1) // page_height)
            if content_page > heading_page:
                block_format = block.blockFormat()
                block_format.setPageBreakPolicy(QTextFormat.PageBreak_AlwaysBefore)
                QTextCursor(block).setBlockFormat(block_format)
        block = following


def export_pdf(report: Report, path: str | Path) -> int:
    """보고서를 A4 PDF로 저장하고 페이지 수를 돌려준다."""
    dpi_scale = 300 / 96
    page_width, page_height = 793.7, 1122.5
    margin, footer = 50.0, 24.0
    content = QSizeF(page_width - margin * 2, page_height - margin * 2 - footer)

    font = QFont("Malgun Gothic", 10)
    font.setHintingPreference(QFont.PreferNoHinting)
    document = QTextDocument()
    document.setDefaultFont(font)
    document.setDocumentMargin(0)
    apply_to_document(document, report)
    document.setPageSize(content)
    _keep_headings_with_content(document, content.height())
    pages = document.pageCount()

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    writer.setResolution(300)
    writer.setTitle(report.title)
    writer.setCreator(APP_NAME)

    painter = QPainter()
    if not painter.begin(writer):
        raise OSError("PDF 파일을 쓸 수 없습니다. 같은 이름의 파일이 열려 있다면 닫고 다시 시도해 주세요.")
    try:
        painter.scale(dpi_scale, dpi_scale)
        footer_font = QFont("Malgun Gothic")
        footer_font.setPixelSize(10)
        footer_font.setHintingPreference(QFont.PreferNoHinting)
        for page in range(pages):
            if page:
                writer.newPage()
            painter.save()
            painter.translate(margin, margin - page * content.height())
            clip = QRectF(0, page * content.height(), content.width(), content.height())
            painter.setClipRect(clip)
            context = QAbstractTextDocumentLayout.PaintContext()
            context.clip = clip
            document.documentLayout().draw(painter, context)
            painter.restore()

            painter.save()
            painter.setFont(footer_font)
            painter.setPen(QColor("#7a828e"))
            area = QRectF(margin, page_height - margin - footer + 8, content.width(), footer)
            painter.drawText(area, Qt.AlignLeft | Qt.AlignVCenter, report.title)
            painter.drawText(area, Qt.AlignRight | Qt.AlignVCenter, f"{page + 1} / {pages}")
            painter.restore()
    finally:
        painter.end()
    return pages
