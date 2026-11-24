"""공시 표에서 파생 계약의 종류, 액면, 통화, 환율, 만기, 거래상대방을 읽는다.

액면은 계약금액·매도/매입금액처럼 액면을 뜻하는 열에서만 읽고 장부금액이나 공정가치로 대신하지 않는다.
숫자, 통화, 집계 여부가 불분명하면 합계에 넣지 않고 공시 원문 값 그대로 '원문 확인'으로 남긴다.
"""
from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable

from .filing import FilingSection, FilingTable, clean, squash
from .formatting import to_number

_CCY = "USD|EUR|JPY|CNY|CNH|GBP|AUD|CAD|CHF|HKD|SGD|VND|THB|INR|IDR|MXN|BRL|PLN|CZK|HUF|NOK|SEK|DKK|NZD|TWD|MYR|PHP|RUB|TRY|ZAR|SAR|AED|KRW"
_CCY_ONLY = re.compile(rf"^(?:{_CCY})$")
_CCY_FIND = re.compile(rf"(?<![A-Za-z])({_CCY})(?![A-Za-z])")
_CCY_AMOUNT = re.compile(rf"^({_CCY})\s*(\(?-?[\d,.]+\)?)$")
_CCY_IN_TEXT = re.compile(rf"(?<![A-Za-z])({_CCY})\s*(\d[\d,.]*)(?![\d%])")
_NUMBER = re.compile(r"^\(?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?$")
_YEAR_ONLY = re.compile(r"^20\d{2}$")
_DATE_YEAR = re.compile(r"(20\d{2})\s*[.\-/년]\s*\d{1,2}")
_TERM = re.compile(r"제(\d+)기")
_PERIOD = re.compile(r"(20\d{2}[.\-]\d{1,2}[.\-]\d{1,2})\s*[-~]\s*(20\d{2}[.\-]\d{1,2}[.\-]\d{1,2})")
_DASHES = {"-", "–", "—"}
_TOTALS = {"합계", "소계", "총계", "계", "총합계"}
_NOT_CONTRACT = re.compile(r"신용위험|최대노출|계약상현금흐름|유동성|만기분석|민감도|3개월미만|3개월이내|6개월이내|1년이내|1년미만|수준1|이연법인세")
_EVIDENCE = re.compile(r"파생|통화선도|선물환|스왑|옵션|선도|선물|TRF|KIKO|키코", re.I)
_NOT_DERIVATIVE_TABLE = re.compile(r"차입금|사채발행|회사채|리스부채|담보|지급보증")
_AGGREGATE = re.compile(r"총계약금액|통화별|계약건수|건수")

_ROLES = (
    ("sell_ccy", r"매도통화|(외화구분|통화구분|통화).*매도$"),
    ("buy_ccy", r"매입통화|매수통화|(외화구분|통화구분|통화).*(매입|매수)$"),
    ("sell", r"매도금액|매도액"), ("buy", r"매입금액|매입액|매수금액|매수액"),
    ("remaining", r"잔여금액"),
    ("notional", r"계약금액|계약액|약정금액|미결제약정|계약원금|명목금액|액면금액|거래금액"),
    ("currency", r"^통화$|^통화구분$|^계약통화$|^외화구분$"),
    ("rate", r"약정환율|계약환율|행사환율|행사금액|행사가|선도환율|선도금리|약정이자율|계약이자율|(?i:strike)"),
    ("start", r"계약시작일|계약실행일|계약발효일|계약체결일|계약일자|계약일|거래일"),
    ("maturity", r"만기|만료일|계약종료일|종료일"),
    ("counterparty", r"계약처|계약상대|거래상대|금융기관|거래은행|거래처"),
    ("purpose", r"거래목적|계약체결목적|보유목적"),
    ("description", r"계약내용"),
    ("pl", r"이익\(손실\)|평가손익|손익현황|거래손익|관련손익|누적평가"),
    ("gain", r"평가이익"), ("loss", r"평가손실"),
    ("asset", r"자산"), ("liability", r"부채"),
    ("instrument", r"거래구분|파생상품종류|파생상품|상품명|상품|명칭|종류|구분"),
)

# 전환사채를 헤지하는 통화스왑이 내재파생으로 분류되지 않도록 거래 상품을 먼저 본다.
INSTRUMENT_RULES = (
    ("통화스왑", r"통화스왑|(?<![A-Za-z])CRS(?![A-Za-z])|crosscurrency"),
    ("통화옵션", r"통화옵션|(?<![A-Za-z])TRF(?![A-Za-z])|KIKO|키코|환변동옵션"),
    ("통화선도", r"통화선도|통화선물|선물환|외화선도|FXForward|(?<![A-Za-z])NDF(?![A-Za-z])"),
    ("이자율스왑", r"이자율스왑|금리스왑|(?<![A-Za-z])IRS(?![A-Za-z])"),
    ("이자율 파생", r"이자율선도|이자율선물|이자율옵션|국채선물|채권선도"),
    ("상품 파생", r"상품선물|상품스왑|상품선도|비철금속|동선물|금속선도|전기선도|원자재|유가"),
    ("주식 파생", r"주식선도|주식옵션|주식스왑|주가지수|총수익스왑|(?<![A-Za-z])TRS(?![A-Za-z])"),
    ("전환사채 등 내재파생", r"전환사채|교환사채|교환권|신주인수권|매도청구권|상환청구권|전환권"),
    ("풋·콜옵션", r"풋옵션|콜옵션|풋백옵션"),
)
FX_INSTRUMENTS = {"통화스왑", "통화옵션", "통화선도", "외환 파생(종류 미기재)"}


@dataclass
class Contract:
    year: int
    instrument: str
    label: str
    currency: str = ""
    notional: float | None = None
    notional_text: str = ""
    side: str = ""  # 매도·매수 잔액을 따로 공시한 경우
    purpose: str = ""
    sell: str = ""
    buy: str = ""
    rate: str = ""
    start: str = ""
    maturity: str = ""
    counterparty: str = ""
    valuation: str = ""
    source: str = ""
    title: str = ""
    check: bool = False  # 금액이나 기준이 불분명해 합계에서 제외한 행
    aggregate: bool = False  # 계약별 표와 겹칠 수 있는 집계표의 행
    expired: bool = False  # 보고기간 말 전에 만기가 지나 기말 잔액이 아닌 계약
    table_id: int = 0

    @property
    def notional_display(self) -> str:
        if self.notional is not None:
            return money_text(self.currency, self.notional)
        return self.notional_text or "미공시"

    def to_row(self) -> dict:
        data = asdict(self)
        data.pop("table_id", None)
        return data


def money_text(currency: str, value: float) -> str:
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{currency} {text}".strip()


def _instrument(text: str) -> str:
    compact = squash(text)
    for name, pattern in INSTRUMENT_RULES:
        if re.search(pattern, compact):
            return name
    return ""


def _is_number(text: str) -> bool:
    value = clean(text)
    return bool(_NUMBER.match(value)) and not _YEAR_ONLY.match(value) and to_number(value) is not None


def _is_data(text: str) -> bool:
    return _is_number(text) or bool(_CCY_AMOUNT.match(clean(text)))


def _header_rows(rows: list[list[str]]) -> int:
    """병합 머리글이 여러 줄이면 첫 칸이 반복되거나 윗줄이 날짜 같은 한 값으로만 채워져 있다."""
    size = 1
    for index in range(1, min(len(rows), 5)):
        values = [clean(c) for c in rows[index] if clean(c)]
        if not values or any(_is_data(c) or c in _DASHES for c in values):
            break
        above = [clean(c) for c in rows[index - 1] if clean(c)]
        first, previous = clean(rows[index][0]), clean(rows[index - 1][0])
        if first and first != previous and len(set(above)) != 1:
            break
        size = index + 1
    return size


def _is_prior(header: str, year: int) -> bool:
    if re.search(r"전기|전년", header) and "당기" not in header:
        return True
    years = {int(y) for y in _DATE_YEAR.findall(header)}
    return bool(years) and year not in years


def _columns(table: FilingTable, size: int, year: int) -> dict[str, list[tuple[int, str]]]:
    width = max(len(r) for r in table.rows)
    headers = {c: " ".join(dict.fromkeys(r[c] for r in table.rows[:size] if c < len(r) and r[c])) for c in range(width)}
    terms = {c: int(m.group(1)) for c, h in headers.items() if (m := _TERM.search(squash(h)))}
    newest = max(terms.values()) if terms else None
    out: dict[str, list[tuple[int, str]]] = {}
    for column, header in headers.items():
        compact = squash(header)
        if not compact or _is_prior(compact, year) or (column in terms and terms[column] != newest):
            continue
        for role, pattern in _ROLES:
            if re.search(pattern, compact):
                if role == "notional" and "매도" in compact:
                    role = "sell"
                elif role == "notional" and re.search("매입|매수", compact):
                    role = "buy"
                out.setdefault(role, []).append((column, header))
                break
    return out


def _unit_parts(unit: str) -> list[str]:
    return [squash(p) for p in re.split(r"[,/;]", unit or "") if p.strip()]


def _krw_only(unit: str) -> bool:
    parts = _unit_parts(unit)
    return bool(parts) and all(p.endswith("원") for p in parts)


def _is_expired(maturity: str, period_end: str) -> bool:
    found = re.findall(r"(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})", maturity or "")
    if not found or len(period_end or "") != 6:
        return False
    try:
        year, month = int(period_end[:4]), int(period_end[4:])
        closing = date(year, month, monthrange(year, month)[1])
        return date(*(int(x) for x in found[-1])) < closing
    except ValueError:
        return False


def _unit_scale(currency: str, header: str, unit: str) -> float:
    def scale_of(text: str) -> float | None:
        for word, value in (("십억", 1e9), ("백만", 1e6), ("억", 1e8), ("천", 1e3)):
            if word in text:
                return value
        return None

    found = scale_of(squash(header))
    if found:
        return found
    parts = _unit_parts(unit)
    for part in parts:
        if (currency and currency in part) or (currency == "KRW" and part.endswith("원")):
            return scale_of(part) or 1.0
    for part in parts:  # '백만', '외화-천'처럼 통화 없이 배수만 적은 단위
        if not _CCY_FIND.search(part) and not part.endswith("원") and not (currency == "KRW" and "외화" in part):
            found = scale_of(part)
            if found:
                return found
    return 1.0


def _unit_currency(unit: str) -> str:
    """통화가 적히지 않은 금액의 통화. 여러 통화가 섞인 단위에서는 추측하지 않는다."""
    parts = _unit_parts(unit)
    if _krw_only(unit) or any("외화단위" in p for p in parts):
        return "KRW"
    if any(p.endswith("원") for p in parts):
        return ""
    codes = {c for p in parts for c in _CCY_FIND.findall(p)}
    return codes.pop() if len(codes) == 1 else ""


def _amount(text: str, header: str, unit: str, known: str) -> tuple[str, float | None, str]:
    """(통화, 실제 금액, 공시 원문). 형식이 맞지 않는 숫자는 금액을 None으로 둔다."""
    raw = clean(text)
    if not raw or raw in _DASHES:
        return "", None, ""
    match = _CCY_AMOUNT.match(raw)
    currency, number = (match.group(1), match.group(2)) if match else ("", raw)
    if not currency:
        codes = _CCY_FIND.findall(header)
        currency = known or (codes[0] if len(set(codes)) == 1 else "") or _unit_currency(unit)
    if not _NUMBER.match(number):
        return currency, None, raw
    value = to_number(number)
    if value is None or not currency:
        return currency, None, raw
    return currency, value * _unit_scale(currency, header, unit), raw


def _description(text: str, header: str, unit: str) -> dict:
    """'거래처 : A 계약기간 : … 원금 : KRW 100'처럼 한 칸에 모아 적은 계약 내용을 나눈다."""
    out = {"currency": "", "notional": None, "text": "", "counterparty": "", "start": "", "maturity": "", "rate": "", "check": False}
    party = re.search(r"거래처\s*[:：]\s*(.*?)(?=계약기간|이자율|통화|원금|계약금액|$)", text)
    if party:
        out["counterparty"] = clean(party.group(1))
    period = _PERIOD.search(text)
    if period:
        out["start"], out["maturity"] = period.group(1), period.group(2)
    terms = re.search(r"(?:이자율스왑|통화스왑|통화선도|약정환율)\s*[:：]\s*(.*?)(?=원금|계약금액|$)", text)
    if terms:
        out["rate"] = clean(terms.group(1))[:40]
    principal = re.search(r"(?:원금|계약금액|약정금액)\s*[:：]?\s*(.+)$", text)
    found = _CCY_IN_TEXT.findall(principal.group(1) if principal else text)
    if not found:
        return out
    if len(found) > 1 and not principal:
        out["text"], out["check"] = text[:60], True  # 어느 쪽이 액면인지 문장만으로 정할 수 없다.
        return out
    currency, number = next((f for f in found if f[0] != "KRW"), found[0])
    number = number.rstrip(".,")
    out["currency"], out["text"] = currency, f"{currency} {number}"
    if _NUMBER.match(number):
        out["notional"] = to_number(number) * _unit_scale(currency, header, unit)
    else:
        out["check"] = True
    return out


def _valuation(fields: dict[str, str], unit: str) -> str:
    parts = []
    for role, name in (("gain", "이익"), ("loss", "손실"), ("pl", "손익"), ("asset", "자산"), ("liability", "부채")):
        if fields.get(role) and fields[role] not in _DASHES:
            parts.append(f"{name} {fields[role]}")
    if not parts:
        return ""
    won = next((p.strip() for p in re.split(r"[,/;]", unit or "") if p.strip().endswith(("원", "KRW"))), "")
    return " / ".join(parts) + (f" ({won})" if won else "")


def parse_table(table: FilingTable, year: int, source: str, section_title: str = "", period_end: str = "") -> list[Contract]:
    if len(table.rows) < 2:
        return []
    caption = squash(table.caption)
    if re.search(r"전기", caption) and "당기" not in caption:
        return []
    size = _header_rows(table.rows)
    head = squash(" ".join(c for r in table.rows[:size] for c in r))
    if _NOT_CONTRACT.search(head + caption):
        return []
    body_text = " ".join(c for r in table.rows[size:] for c in r)
    if not _EVIDENCE.search(head + caption + squash(body_text) + squash(section_title)):
        return []
    if _NOT_DERIVATIVE_TABLE.search(head) and not _instrument(body_text):
        return []
    columns = _columns(table, size, year)
    if not any(role in columns for role in ("notional", "sell", "buy", "description")):
        return []
    width = max(len(r) for r in table.rows)
    aggregate = bool(_AGGREGATE.search(head + caption))

    # 병합 머리글 아래 통화 코드만 든 열은 금액 열이 아니라 통화 열이다.
    if "notional" in columns:
        amounts = []
        for column, header in columns["notional"]:
            cells = [clean(r[column]) for r in table.rows[size:] if column < len(r) and clean(r[column])]
            if cells and all(_CCY_ONLY.match(c) for c in cells):
                columns.setdefault("currency", []).append((column, header))
            else:
                amounts.append((column, header))
        columns["notional"] = amounts

    out: list[Contract] = []
    for source_row in table.rows[size:]:
        row = source_row + [""] * (width - len(source_row))
        labels = [clean(c) for c in row if clean(c) and not _is_data(c) and clean(c) not in _DASHES]
        compact_labels = {squash(c) for c in labels}
        if compact_labels & _TOTALS or (labels and squash(labels[0]) in ("전기", "전기말")):
            continue
        fields = {role: clean(row[items[0][0]]) for role, items in columns.items() if items}

        explicit = fields.get("currency", "") if _CCY_ONLY.match(fields.get("currency", "")) else ""
        lone = list({clean(c) for c in row if _CCY_ONLY.match(clean(c))})
        known = explicit or (lone[0] if len(lone) == 1 and not _krw_only(table.unit) else "")
        doubtful_unit = bool(explicit and explicit != "KRW" and _krw_only(table.unit))
        sell_ccy = fields.get("sell_ccy", "") if _CCY_ONLY.match(fields.get("sell_ccy", "")) else ""
        buy_ccy = fields.get("buy_ccy", "") if _CCY_ONLY.match(fields.get("buy_ccy", "")) else ""
        # 표 단위는 원인데 행에 외화 코드가 있으면 금액이 외화인지 원화 환산인지 알 수 없다. 추측하지 않고 원문 값만 남긴다.
        foreign_row = lone[0] if len(lone) == 1 and not explicit and lone[0] != "KRW" and _krw_only(table.unit) else ""

        def read(value: str, header: str, currency: str) -> tuple[str, float | None, str]:
            text = clean(value)
            if foreign_row and text and text not in _DASHES and not _CCY_AMOUNT.match(text):
                return foreign_row, None, f"{foreign_row} {text}"
            return _amount(value, header, table.unit, currency)

        entries: list[dict] = []
        extra: dict = {}
        if "sell" in columns or "buy" in columns:
            sell_header = columns.get("sell", [(0, "")])[0][1]
            buy_header = columns.get("buy", [(0, "")])[0][1]
            sc, sv, sr = read(fields.get("sell", ""), sell_header, sell_ccy or known)
            bc, bv, br = read(fields.get("buy", ""), buy_header, buy_ccy or known)
            sell_text = money_text(sc, sv) if sv is not None else sr
            buy_text = money_text(bc, bv) if bv is not None else br
            if sr and br and sc and bc and sc != bc:
                # 서로 다른 통화면 한 계약의 양쪽이다. 외화 쪽을 액면으로 삼는다.
                currency, value, text = (sc, sv, sr) if sc != "KRW" else (bc, bv, br)
                entries.append({"currency": currency, "notional": value, "text": text, "sell": sell_text, "buy": buy_text,
                                "check": sv is None or bv is None})
            else:
                # 같은 통화의 매도·매수 열은 서로 다른 포지션의 잔액이다.
                for side, currency, value, text in (("매도", sc, sv, sr), ("매수", bc, bv, br)):
                    if text:
                        entries.append({"currency": currency, "notional": value, "text": text, "side": side, "check": value is None})
        elif columns.get("notional"):
            filled = [(clean(row[c]), h) for c, h in columns["notional"] if clean(row[c]) not in ("", *_DASHES)]
            if len(filled) == 1:
                pair = {sell_ccy, buy_ccy} - {""}
                pair_known = next((c for c in pair if c != "KRW"), "") if len(pair) == 2 else ""
                currency, value, text = read(filled[0][0], filled[0][1], known or pair_known)
                entries.append({"currency": currency, "notional": value, "text": text, "check": value is None or bool(pair_known)})
            elif filled:
                entries.append({"currency": known, "notional": None, "text": " / ".join(v for v, _ in filled), "check": True})
        elif fields.get("description") and fields["description"] not in _DASHES:
            parsed = _description(fields["description"], columns["description"][0][1], table.unit)
            if parsed["text"]:
                entries.append({"currency": parsed["currency"], "notional": parsed["notional"], "text": parsed["text"], "check": parsed["check"]})
                extra = {k: parsed[k] for k in ("counterparty", "start", "maturity", "rate") if parsed[k]}
        if not entries:
            continue

        instrument_cells = [clean(row[c]) for c, _ in columns.get("instrument", [])]
        label = next((c for c in instrument_cells if _instrument(c)), "") or next((c for c in instrument_cells if c), "") \
            or next((c for c in labels if not _CCY_ONLY.match(c)), "")
        if lone and _krw_only(table.unit) and not explicit and lone[0] not in label:
            label = f"{label} {lone[0]}".strip()  # 원화로 환산해 공시한 외화 계약의 원래 통화
        words = " ".join(c for c in labels if not _CCY_ONLY.match(c))
        instrument = _instrument(f"{label} {fields.get('purpose', '')}") or _instrument(words)
        if not instrument:
            kinds = {name for name, pattern in INSTRUMENT_RULES if re.search(pattern, caption)}
            if len(kinds) == 1:
                instrument = kinds.pop()
            elif any((e["currency"] and e["currency"] != "KRW") or e.get("sell") for e in entries):
                instrument = "외환 파생(종류 미기재)"
            else:
                instrument = "기타 파생"
        common = {
            "rate": extra.get("rate") or fields.get("rate", ""), "start": extra.get("start") or fields.get("start", ""),
            "maturity": extra.get("maturity") or fields.get("maturity", ""),
            "counterparty": (extra.get("counterparty") or fields.get("counterparty", ""))[:40],
        }
        valuation = _valuation(fields, table.unit)
        expired = _is_expired(common["maturity"], period_end)
        for entry in entries:
            out.append(Contract(
                year=year, instrument=instrument, label=label[:40], currency=entry["currency"], notional=entry["notional"],
                notional_text=entry["text"], side=entry.get("side", ""), purpose=fields.get("purpose", "")[:24],
                sell=entry.get("sell", ""), buy=entry.get("buy", ""), valuation=valuation, source=source,
                title=(table.caption if len(table.caption) <= 40 else section_title)[:60],
                check=bool(entry["check"] or doubtful_unit or expired), aggregate=aggregate, expired=expired, table_id=id(table), **common,
            ))
    return out


def parse_sections(sections: Iterable[FilingSection], year: int, source: str, period_end: str = "") -> list[Contract]:
    """당기·전기 표가 같은 양식으로 이어지면 첫 표만 쓰고, 연결 표가 있으면 별도 표는 뺀다."""
    found: list[tuple[FilingTable, list[Contract]]] = []
    for section in sections:
        seen: set[tuple] = set()
        for table in section.tables:
            size = _header_rows(table.rows)
            key = (tuple(tuple(r) for r in table.rows[:size]), squash(table.caption))
            has_date = bool(_DATE_YEAR.search(squash(" ".join(c for r in table.rows[:size] for c in r))))
            if key in seen and not has_date:
                continue
            seen.add(key)
            contracts = parse_table(table, year, source, section.title, period_end)
            if contracts:
                found.append((table, contracts))
    if any("연결" in squash(t.caption) for t, _ in found):
        found = [(t, c) for t, c in found if "별도" not in squash(t.caption)]
    out: list[Contract] = []
    keys: set[tuple] = set()
    for _, contracts in found:
        own = []
        for item in contracts:
            key = (item.instrument, item.currency, item.notional, item.notional_text, item.side, item.rate, item.start, item.maturity, item.counterparty)
            if key not in keys:
                own.append((key, item))
        keys.update(key for key, _ in own)
        out.extend(item for _, item in own)
    # 계약별 표가 있으면 집계표는 겹칠 수 있어 합계에서 빼고 표시만 한다.
    if any(not c.aggregate for c in out):
        for item in out:
            if item.aggregate:
                item.check = True
    return out


def collect_contracts(primary: list[FilingSection], notes: list[FilingSection], year: int, period_end: str = "") -> list[Contract]:
    """사업의 내용을 먼저 읽고, 계약표가 없을 때만 재무제표 주석을 읽어 같은 계약을 두 번 세지 않는다."""
    return parse_sections(primary, year, "사업의 내용", period_end) or parse_sections(notes, year, "재무제표 주석", period_end)


def summarize_contracts(contracts: Iterable[Contract]) -> list[dict]:
    """연도·상품·통화·포지션별 액면 합계. 금액이 확정되지 않은 행은 건수만 따로 센다."""
    groups: dict[tuple, dict] = {}
    for item in contracts:
        key = (item.year, item.instrument, item.currency, item.side)
        entry = groups.setdefault(key, {"year": item.year, "instrument": item.instrument, "currency": item.currency, "side": item.side,
                                        "count": 0, "total": 0.0, "unconfirmed": 0})
        if item.notional is not None and not item.check:
            entry["count"] += 1
            entry["total"] += item.notional
        else:
            entry["unconfirmed"] += 1
    return sorted(groups.values(), key=lambda g: (-g["year"], g["instrument"], g["currency"], g["side"]))


def notional_summary(contracts: Iterable[Contract]) -> str:
    parts = []
    for entry in summarize_contracts(contracts):
        amount = money_text(entry["currency"], entry["total"]) if entry["count"] else "원문 확인"
        parts.append(f"{entry['instrument']}{' ' + entry['side'] if entry['side'] else ''} {amount}")
    return "; ".join(parts)
