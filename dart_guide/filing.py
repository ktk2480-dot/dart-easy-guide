"""사업보고서 원문(document API)을 목차와 표 단위로 읽는다.

원문 XML은 형식이 엄격하지 않은 경우가 있어 관대한 HTML 파서로 읽는다.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .client import DartClient, DartError

_CELL_TAGS = {"td", "th", "te", "tu"}
_BLOCK_TAGS = {"p", "span"}
_SPACES = re.compile(r"\s+")


def clean(text: str) -> str:
    return _SPACES.sub(" ", text.replace("\xa0", " ")).strip()


def squash(text: str) -> str:
    """띄어쓰기가 보고서마다 달라 비교할 때는 공백을 모두 없앤다."""
    return _SPACES.sub("", text or "")


@dataclass
class FilingTable:
    rows: list[list[str]]
    caption: str = ""  # 표 바로 앞의 문단. 표가 무엇인지 알려 주는 경우가 많다.
    unit: str = ""

    def text(self) -> str:
        return "\n".join(" | ".join(row) for row in self.rows)


@dataclass
class FilingSection:
    title: str
    level: int
    blocks: list[str | FilingTable] = field(default_factory=list)

    @property
    def tables(self) -> list[FilingTable]:
        return [b for b in self.blocks if isinstance(b, FilingTable)]

    @property
    def paragraphs(self) -> list[str]:
        return [b for b in self.blocks if isinstance(b, str)]

    def text(self, limit: int | None = None) -> str:
        parts = [b.text() if isinstance(b, FilingTable) else b for b in self.blocks]
        joined = "\n".join(parts)
        return joined if limit is None else joined[:limit]


_UNIT = re.compile(r"\(?\s*(?:단위|Unit)\s*[:：]\s*([^)\n]{1,24})\)?", re.I)


def find_unit(*texts: str) -> str:
    for text in texts:
        match = _UNIT.search(text or "")
        if match:
            return clean(match.group(1)).rstrip(",.;)")
    return ""


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections: list[FilingSection] = []
        self.current: FilingSection | None = None
        self.level = 0
        self._title: list[str] | None = None
        self._text: list[str] = []
        self._table_depth = 0
        self._grid: list[list[tuple[str, int, int]]] = []
        self._row: list[tuple[str, int, int]] | None = None
        self._cell: list[str] | None = None
        self._span = (1, 1)
        self._last_para = ""
        self._pending_unit = ""

    def handle_starttag(self, tag, attrs):
        if tag.startswith("section-"):
            self._flush()
            self.level = int(tag.split("-")[1]) if tag.split("-")[1].isdigit() else self.level
        elif tag == "title" and self._table_depth == 0:
            self._flush()
            self._title = []
        elif tag == "table":
            if self._table_depth == 0:
                self._flush()
                self._grid = []
            self._table_depth += 1
        elif self._table_depth == 1 and tag == "tr":
            self._row = []
        elif self._table_depth == 1 and tag in _CELL_TAGS and self._row is not None:
            values = dict(attrs)
            self._span = (_span(values.get("colspan")), _span(values.get("rowspan")))
            self._cell = []
        elif tag in _BLOCK_TAGS and self._table_depth == 0:
            self._flush()

    def handle_endtag(self, tag):
        if tag == "title" and self._title is not None:
            self.current = FilingSection(clean("".join(self._title)), self.level)
            self.sections.append(self.current)
            self._title = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._finish_table()
        elif self._table_depth == 1 and tag in _CELL_TAGS and self._cell is not None:
            if self._row is not None:
                self._row.append((clean(" ".join(self._cell)), *self._span))
            self._cell = None
        elif self._table_depth == 1 and tag == "tr" and self._row is not None:
            self._grid.append(self._row)
            self._row = None
        elif tag in _BLOCK_TAGS and self._table_depth == 0:
            self._flush()

    def handle_data(self, data):
        if self._title is not None:
            self._title.append(data)
        elif self._table_depth:
            if self._cell is not None:
                self._cell.append(data)
        else:
            self._text.append(data)

    def _flush(self):
        text = clean("".join(self._text))
        self._text = []
        if text and self.current is not None:
            self.current.blocks.append(text)
            self._last_para = text

    def _finish_table(self):
        rows = _expand(self._grid)
        self._grid = []
        if not rows or self.current is None:
            return
        caption = self._last_para
        # 단위만 적은 한 줄짜리 표는 다음 표의 단위로 넘긴다.
        if len(rows) == 1 and len(set(rows[0])) <= 2:
            unit = find_unit(" ".join(dict.fromkeys(rows[0])))
            if unit:
                self._pending_unit = unit
                return
        unit = ""
        if len(set(rows[0])) == 1:
            unit = find_unit(rows[0][0])
            if unit and len(rows) > 1:
                rows = rows[1:]
        unit = unit or self._pending_unit or find_unit(caption)
        self._pending_unit = ""
        self.current.blocks.append(FilingTable(rows, caption[:160], unit))

    def close(self):
        super().close()
        self._flush()


def _span(value) -> int:
    try:
        return max(1, min(int(value), 60))
    except (TypeError, ValueError):
        return 1


def _expand(grid: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    """병합 셀을 풀어 모든 행의 열 위치를 맞춘다."""
    out: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}
    for source in grid:
        row: list[str] = []
        cells = iter(source)
        column = 0
        current = next(cells, None)
        while current is not None or column in pending:
            if column in pending:
                text, remaining = pending[column]
                row.append(text)
                if remaining <= 1:
                    del pending[column]
                else:
                    pending[column] = (text, remaining - 1)
                column += 1
                continue
            text, colspan, rowspan = current
            for _ in range(colspan):
                row.append(text)
                if rowspan > 1:
                    pending[column] = (text, rowspan - 1)
                column += 1
            current = next(cells, None)
        if any(row):
            out.append(row)
    return out


def parse_document(xml: bytes | str) -> list[FilingSection]:
    if isinstance(xml, bytes):
        head = xml[:200].lower()
        encoding = "euc-kr" if b"euc-kr" in head else "utf-8"
        xml = xml.decode(encoding, "replace")
    # DART 전용 줄바꿈 표기를 공백으로 바꾼다.
    xml = xml.replace("&cr;", " ").replace("&amp;cr;", " ")
    parser = _Parser()
    parser.feed(xml)
    parser.close()
    return parser.sections


def load_filing(client: DartClient, rcept_no: str) -> list[FilingSection]:
    """접수번호의 본문을 읽는다. 첨부(감사보고서 등)보다 큰 본문 파일을 고른다."""
    archive = client.get_binary("document", rcept_no=rcept_no)
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            names = [n for n in zipped.namelist() if n.lower().endswith(".xml")]
            if not names:
                raise DartError("PARSE", "공시 원문에서 본문을 찾지 못했습니다.")
            main = max(names, key=lambda n: zipped.getinfo(n).file_size)
            data = zipped.read(main)
    except zipfile.BadZipFile:
        raise DartError("PARSE", "공시 원문 파일을 읽지 못했습니다.") from None
    return parse_document(data)


def find_sections(sections: list[FilingSection], *patterns: str) -> list[FilingSection]:
    compiled = [re.compile(p) for p in patterns]
    return [s for s in sections if any(p.search(squash(s.title)) for p in compiled)]
