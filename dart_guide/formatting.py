"""공시 값(문자열)을 숫자와 읽기 쉬운 한국어 표기로 바꾼다."""
from __future__ import annotations

import re
from datetime import date

_EMPTY = {"", "-", "–", "—", "N/A", "NA", "해당없음", "해당사항없음", "None", "null"}


def to_number(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace(" ", "")
    if text in _EMPTY:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text[:1] in ("△", "▲"):
        negative, text = True, text[1:]
    text = text.rstrip("%")
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def won(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "-" if value < 0 else ""
    size = abs(value)
    if size >= 1e12:
        return f"{sign}{size / 1e12:,.1f}조원"
    if size >= 1e8:
        return f"{sign}{size / 1e8:,.0f}억원"
    if size >= 1e4:
        return f"{sign}{size / 1e4:,.0f}만원"
    return f"{sign}{size:,.0f}원"


def compact_won(value: float | None) -> str:
    return won(value).removesuffix("원") if value is not None else "-"


def count(value: float | None, unit: str = "") -> str:
    return "-" if value is None else f"{value:,.0f}{unit}"


def percent(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:,.{digits}f}%"


def signed(value: float | None, unit: str = "") -> str:
    return "-" if value is None else f"{value:+,.0f}{unit}"


def ratio(top: float | None, bottom: float | None) -> float | None:
    if top is None or bottom in (None, 0):
        return None
    return top / bottom * 100


def change_text(current: float | None, previous: float | None) -> str:
    """전년 대비 변화를 평이한 문장 조각으로 만든다."""
    if current is None or previous is None:
        return "전년 비교 불가"
    if previous > 0 and current < 0:
        return "적자 전환"
    if previous < 0 and current > 0:
        return "흑자 전환"
    if previous < 0 and current < 0:
        return "적자 지속"
    if previous == 0:
        return "전년 비교 불가"
    rate = (current - previous) / abs(previous) * 100
    if abs(rate) < 0.05:
        return "전년과 비슷"
    return f"전년보다 {abs(rate):.1f}% {'증가' if rate > 0 else '감소'}"


def digits_only(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def ymd(value) -> str:
    text = digits_only(value)
    return f"{text[:4]}.{text[4:6]}.{text[6:8]}" if len(text) >= 8 else (str(value) if value else "-")


def months_ago(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    year, month = index // 12, index % 12 + 1
    next_index = index + 1
    next_first = date(next_index // 12, next_index % 12 + 1, 1)
    last_day = (next_first - date.resolution).day
    return date(year, month, min(day.day, last_day))


def text_or_dash(value) -> str:
    text = str(value).strip() if value is not None else ""
    return text if text else "-"
