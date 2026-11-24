"""업체 분석: 업체정보, 3개년 재무, 수출/수입, 파생거래를 공시에서 모아 보고서로 만든다.

수출입과 파생거래는 전용 API가 없어 사업보고서 원문의 표를 읽는다. 합계가 맞는지 확인되지 않는 표는
숫자를 추정하거나 합산하지 않고 원문 표 그대로 보여 준다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from . import charts
from .client import DartClient, DartError, describe_call
from .corp_index import Company
from .derivative_contracts import Contract, collect_contracts, money_text, summarize_contracts
from .examples import ACCOUNTS, CORP_CLS, TOTAL_WORDS, Api, _annual_from_rows, _fetch_disclosures, _norm, _stem, viewer_url
from .filing import FilingSection, FilingTable, clean, find_sections, find_unit, load_filing, squash
from .formatting import change_text, compact_won, count, digits_only, months_ago, percent, ratio, text_or_dash, to_number, won, ymd
from .latest import _REPORT_NAME, Filing, _fiscal_month, find_filings, rows_for
from .report import Metric, Report, Section, Table

YEARS = 3
_LOOKBACK_MONTHS = 48
_MAX_ROWS = 18
_MAX_COLUMNS = 7

# 재무제표 주석의 '매출채권' 같은 목차가 섞이지 않도록 끝까지 맞춘다.
_SALES = (r"매출및수주상황", r"^\d+\.(\(.*?\))?매출(에관한사항|실적|현황)?$")
_MATERIALS = (r"원재료및생산설비", r"^\d+\.(\(.*?\))?원재료(에관한사항|현황)?$")
_DERIVATIVES = (r"위험관리및파생거래", r"파생상품거래현황", r"파생상품등", r"시장위험과위험관리")

_DOMESTIC = re.compile(r"^(내수|내수\(.*\)|국내|국내매출)$")
_EXPORT = re.compile(r"^(수출|수출\(.*\)|직수출|해외매출)$")
_LOCAL_BUY = re.compile(r"^(내자|국내|국내구매|국내매입)$")
_IMPORT_BUY = re.compile(r"^(수입|외자|해외|해외구매|해외매입|수입\(.*\))$")
_IMPORT_MARK = re.compile(r"^(수입|외자)$")
_INSTRUMENT = re.compile(
    r"통화선도|선물환|통화스왑|통화옵션|이자율스왑|금리스왑|이자율선도|이자율옵션|상품선물|상품스왑|주식선도|주식옵션|"
    r"파생상품|파생금융|풋옵션|콜옵션|풋백옵션|KIKO|키코|(?<![A-Za-z])(?:CRS|IRS)(?![A-Za-z])"  # FIRST 같은 영문 단어 제외
)
_NO_DERIVATIVE = re.compile(
    r"(파생|통화선도|선물환|스왑|옵션)[^.。]{0,40}?"
    r"(해당\s*사항\s*(이\s*)?없|해당\s*없|보유하고\s*있지\s*않|체결하고\s*있지\s*않|거래(가|는|내역이)?\s*없|내역이\s*없)"
)
# '투기 목적의 거래를 하지 않는다' 같은 정책 문구는 보유 근거로 세지 않는다.
_HAS_DERIVATIVE = re.compile(
    r"(통화선도|선물환|통화스왑|통화옵션|이자율스왑|금리스왑|파생상품|파생금융\w*)[^.。]{0,40}?"
    r"(체결하고\s*있|체결하였|보유하고\s*있|평가손익|평가이익|평가손실|거래이익|거래손실|장부가)"
)
# 신용위험·유동성 표에도 '파생상품' 행이 있지만 계약 내역은 아니다.
_NOT_CONTRACT = re.compile(r"신용위험|최대노출|계약상현금흐름|유동성|만기분석|민감도|3개월미만|3개월이내|6개월이내|1년이내|1년미만|수준1|이연법인세")
_DERIVATIVE_NOTES = (r"파생", r"금융상품", r"위험관리", r"공정가치")
_NOTES = (r"^주석$", r"재무제표.*주석")
_AUDIT_NAME = re.compile(r"(연결)?감사보고서\s*\((\d{4})\.(\d{2})\)")
_PERIOD_END = re.compile(r"당기\s*말|보고기간\s*말|기말\s*현재")
_STATEMENTS = {"BS": r"재무상태표|대차대조표", "IS": r"손익계산서", "CF": r"현금흐름표"}
_AUDIT_ACCOUNTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "매출액": ("IS", ("매출액", "매출", "영업수익", "수익")),
    "영업이익": ("IS", ("영업이익", "영업손실", "영업손익")),
    "당기순이익": ("IS", ("당기순이익", "당기순손실", "당기순손익", "연결당기순이익", "연결당기순손실", "연결당기순손익")),
    "자산총계": ("BS", ("자산총계",)),
    "부채총계": ("BS", ("부채총계",)),
    "자본총계": ("BS", ("자본총계",)),
    "유동자산": ("BS", ("유동자산",)),
    "유동부채": ("BS", ("유동부채",)),
    "영업활동현금흐름": ("CF", ("영업활동현금흐름", "영업활동으로인한현금흐름")),
}
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_TERM = re.compile(r"제\s*(\d+)\s*기")
_NOT_VALUE = re.compile(r"증감|증가|감소|비율|비중|구성|%|단가|수량")
_UNIT_SCALE = {"원": 1, "천원": 1e3, "백만원": 1e6, "십억원": 1e9, "억원": 1e8, "조원": 1e12}

FINANCIAL_ACCOUNTS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "매출액": (("ifrs-full_Revenue",), ACCOUNTS["매출액"], ("IS", "CIS")),
    "영업이익": (("dart_OperatingIncomeLoss",), ACCOUNTS["영업이익"], ("IS", "CIS")),
    "당기순이익": (("ifrs-full_ProfitLoss",), ACCOUNTS["당기순이익"], ("IS", "CIS")),
    "자산총계": (("ifrs-full_Assets",), ACCOUNTS["자산총계"], ("BS",)),
    "부채총계": (("ifrs-full_Liabilities",), ACCOUNTS["부채총계"], ("BS",)),
    "자본총계": (("ifrs-full_Equity",), ACCOUNTS["자본총계"], ("BS",)),
    "유동자산": (("ifrs-full_CurrentAssets",), ("유동자산",), ("BS",)),
    "유동부채": (("ifrs-full_CurrentLiabilities",), ("유동부채",), ("BS",)),
    "영업활동현금흐름": (("ifrs-full_CashFlowsFromUsedInOperatingActivities",), ("영업활동현금흐름", "영업활동으로인한현금흐름"), ("CF",)),
}
_AMOUNT_KEYS = ("bfefrmtrm_amount", "frmtrm_amount", "thstrm_amount")


@dataclass
class YearDoc:
    filing: Filing
    rcept_no: str
    sections: list[FilingSection] = field(default_factory=list)
    found: bool = True  # 원문 파일을 받지 못했으면 False

    @property
    def year(self) -> int:
        return int(self.filing.period_end[:4])


@dataclass
class Financials:
    years: list[int]
    basis: str
    currency: str
    values: dict[str, list[float | None]]
    derivative_accounts: list[tuple[str, list[float | None]]] = field(default_factory=list)
    rcept_no: str = ""


@dataclass
class Split:
    """내수/수출 또는 국내/수입처럼 둘로 나뉜 3개년 금액."""
    title: str
    unit: str
    years: list[int]
    near: list[float | None]
    far: list[float | None]
    total: list[float | None]
    note: str = ""
    rcept_no: str = ""

    def share(self) -> list[float | None]:
        return [ratio(f, t) for f, t in zip(self.far, self.total)]

    def other(self) -> list[float | None]:
        out: list[float | None] = []
        for n, f, t in zip(self.near, self.far, self.total):
            out.append(None if None in (n, f, t) else t - n - f)
        return out


@dataclass
class ImportYear:
    year: int
    rcept_no: str
    items: list[str] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)
    purchase: FilingTable | None = None


@dataclass
class DerivativeYear:
    year: int
    rcept_no: str
    status: str  # 있음 / 없음 / 확인 필요
    statements: list[str] = field(default_factory=list)
    tables: list[tuple[str, FilingTable]] = field(default_factory=list)
    contracts: list[Contract] = field(default_factory=list)


@dataclass
class CompanyAnalysis:
    company: Company
    info: dict
    filings: list[YearDoc]
    holder: str = ""
    holder_rate: float | None = None
    employees: float | None = None
    financials: Financials | None = None
    exports: list[Split] = field(default_factory=list)
    export_tables: list[tuple[str, FilingTable]] = field(default_factory=list)
    imports: list[Split] = field(default_factory=list)
    import_years: list[ImportYear] = field(default_factory=list)
    derivatives: list[DerivativeYear] = field(default_factory=list)
    derivative_losses: list[dict] = field(default_factory=list)
    fx_tables: list[tuple[str, FilingTable]] = field(default_factory=list)
    source: str = ""  # 사업보고서 / 감사보고서 / 빈 값(정기 공시 없음)
    calls: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 표 해석
def _is_total(label: str) -> bool:
    text = squash(label)
    return text in TOTAL_WORDS or text.endswith(("합계", "총계", "소계"))


def _header_size(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows[:4]):
        numbers = sum(1 for c in row if to_number(c) is not None and not _YEAR.fullmatch(c.strip()))
        if numbers >= 2:
            return max(index, 1)
    return 1


def _header_text(table: FilingTable, column: int, size: int) -> str:
    return " ".join(dict.fromkeys(r[column] for r in table.rows[:size] if column < len(r) and r[column]))


def _value_columns(table: FilingTable, base_year: int, single: str = "") -> tuple[dict[int, int], int] | None:
    """금액이 든 열과 그 열의 연도를 찾는다. 연도를 확정할 수 없으면 None."""
    size = _header_size(table.rows)
    width = max(len(r) for r in table.rows)
    found: dict[int, tuple[str, int | None, int | None]] = {}
    for column in range(1, width):
        text = _header_text(table, column, size)
        if _NOT_VALUE.search(text):
            continue
        year = _YEAR.search(text)
        term = _TERM.search(text)
        relative = 2 if "전전기" in text else 1 if "전기" in text else 0 if "당기" in text else None
        if year or term or relative is not None:
            found[column] = (text, int(year.group(1)) if year else None, int(term.group(1)) if term else None)
            if not year and not term:
                found[column] = (text, base_year - relative, None)
    if not found:
        if single:
            for column in range(1, width):
                if re.search(single, squash(_header_text(table, column, size))):
                    return {column: base_year}, size
        return None
    terms = [v[2] for v in found.values() if v[1] is None and v[2] is not None]
    newest = max(terms) if terms else 0
    by_year: dict[int, list[int]] = {}
    for column, (_, year, term) in found.items():
        resolved = year if year is not None else base_year - (newest - term)
        by_year.setdefault(resolved, []).append(column)
    columns: dict[int, int] = {}
    for year, candidates in by_year.items():
        if len(candidates) > 1:
            # 한 연도에 수량·금액처럼 여러 열이 있으면 금액 열만 고른다.
            candidates = [c for c in candidates if re.search(r"금액|매출", _header_text(table, c, size))]
            if len(candidates) != 1:
                return None
        columns[candidates[0]] = year
    return columns, size


def _kind(label: str, near: re.Pattern, far: re.Pattern) -> str:
    text = squash(label)
    return "near" if near.match(text) else "far" if far.match(text) else ""


def _extract_split(table: FilingTable, base_year: int, near: re.Pattern, far: re.Pattern, title: str,
                   note: str, rcept_no: str, single: str = "") -> Split | None:
    mapped = _value_columns(table, base_year, single)
    if not mapped:
        return None
    columns, header = mapped
    first_value = min(columns)
    ordered = sorted(columns, key=columns.get)[-YEARS:]
    leaves = {"near": [], "far": []}
    explicit: dict[str, list[float | None]] = {}
    totals: list[list[float | None]] = []
    for row in table.rows[header:]:
        if len(row) <= max(ordered):
            continue
        labels = [c for c in row[:first_value] if c]
        if not labels:
            continue
        values = [to_number(row[c]) for c in ordered]
        kinds = {_kind(l, near, far) for l in labels} - {""}
        if len(kinds) > 1:
            continue
        rest = [l for l in labels if not _kind(l, near, far)]
        if kinds:
            kind = next(iter(kinds))
            if rest and all(_is_total(l) for l in rest):
                explicit[kind] = values  # 마지막 합계 행이 전체 합계다.
            elif not any(_is_total(l) for l in rest):
                leaves[kind].append(values)
        elif all(_is_total(l) for l in labels):
            totals.append(values)

    years = [columns[c] for c in ordered]
    if "near" in explicit and "far" in explicit:
        near_values, far_values = explicit["near"], explicit["far"]
        total = totals[-1] if totals else [None if None in (n, f) else n + f for n, f in zip(near_values, far_values)]
        for n, f, t in zip(near_values, far_values, total):
            if None not in (n, f, t) and t and n + f > t * 1.005:
                return None
        return Split(title, table.unit, years, near_values, far_values, total, note, rcept_no)

    if not leaves["near"] or not leaves["far"]:
        return None
    summed = {k: [sum((v[i] or 0) for v in rows) for i in range(len(ordered))] for k, rows in leaves.items()}
    combined = [n + f for n, f in zip(summed["near"], summed["far"])]
    for candidate in reversed(totals):
        if all(t is not None and t > 0 and abs(c - t) / t <= 0.005 for c, t in zip(combined, candidate)):
            return Split(title, table.unit, years, summed["near"], summed["far"], candidate, note, rcept_no)
    return None


def _short_caption(table: FilingTable, fallback: str) -> str:
    """표 앞 문단이 제목이 아니라 긴 설명이면 목차 제목을 쓴다."""
    return table.caption if table.caption and len(table.caption) <= 40 else fallback


def _note_after(section: FilingSection, table: FilingTable) -> str:
    """표 바로 뒤의 ※ 주석(별도 기준 등)을 찾는다."""
    index = next((i for i, b in enumerate(section.blocks) if b is table), -1)
    notes = []
    for block in section.blocks[index + 1:index + 3]:
        text = block if isinstance(block, str) else (block.rows[0][0] if len(block.rows) == 1 else "")
        if text.startswith(("※", "주", "(주")) and len(text) <= 200:
            notes.append(clean(text))
    return " ".join(notes)


def display_table(source: FilingTable, max_rows: int = _MAX_ROWS, max_columns: int = _MAX_COLUMNS) -> Table:
    """병합 셀을 정리해 보고서 표로 바꾼다. 잘린 부분은 표 아래에 알린다."""
    rows = source.rows
    size = min(_header_size(rows), max(len(rows) - 1, 1))
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    keep = [c for c in range(width) if c == 0 or any(r[c] != r[c - 1] for r in padded)]
    hidden_columns = max(0, len(keep) - max_columns)
    keep = keep[:max_columns]
    columns = [" ".join(dict.fromkeys(padded[i][c] for i in range(size) if padded[i][c])) or " " for c in keep]
    body = [[r[c] for c in keep] for r in padded[size:]]
    numeric = {i for i in range(len(keep)) if body and sum(to_number(r[i]) is not None for r in body) >= len(body) / 2}
    notes = []
    if source.unit:
        notes.append(f"단위: {source.unit}")
    if len(body) > max_rows:
        notes.append(f"전체 {len(body)}행 중 {max_rows}행만 표시했습니다. 나머지는 원문을 확인해 주세요.")
    if hidden_columns:
        notes.append(f"오른쪽 {hidden_columns}개 열은 생략했습니다.")
    return Table(columns, body[:max_rows], right=numeric, note=" · ".join(notes))


# ---------------------------------------------------------------- 데이터 수집
def _find_audit_filings(api: Api, company: Company, info: dict) -> list[Filing]:
    """사업보고서가 없는 외감 비상장사의 감사보고서를 같은 기준(연결 또는 개별)으로 고른다."""
    today = date.today()
    data = api.obj(
        "list", corp_code=company.corp_code, bgn_de=months_ago(today, _LOOKBACK_MONTHS).strftime("%Y%m%d"),
        end_de=today.strftime("%Y%m%d"), last_reprt_at="Y", pblntf_ty="F", sort="date", sort_mth="desc", page_no="1", page_count="100",
    )
    fiscal_month = _fiscal_month(info.get("acc_mt"))
    found: dict[tuple[bool, str], Filing] = {}
    for row in data.get("list", []):
        match = _AUDIT_NAME.search(str(row.get("report_nm", "")))
        if not match or "철" in str(row.get("rm", "")):
            continue
        key = (bool(match.group(1)), f"{match.group(2)}{match.group(3)}")
        number = digits_only(row.get("rcept_no"))
        if key not in found or number > found[key].rcept_no:
            found[key] = Filing(key[1], "11011", int(match.group(2)), str(row.get("report_nm", "")).strip(), number,
                                digits_only(row.get("rcept_dt")), fiscal_month)
    if not found:
        return []
    consolidated = sum(k[0] for k in found) >= sum(not k[0] for k in found)
    chosen = [f for k, f in found.items() if k[0] == consolidated]
    return sorted(chosen, key=lambda f: f.period_end, reverse=True)[:YEARS]


def _account_label(text: str) -> str:
    value = re.sub(r"\(.*?\)", "", text)
    return re.sub(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVXivx\d]+[.．]?", "", squash(value))


def _period_columns(table: FilingTable) -> dict[int, list[int]]:
    """재무제표 표에서 당기(0)와 전기(1)의 열을 찾는다. 합계가 오른쪽 열에 있는 양식도 있어 열 목록으로 돌려준다."""
    size = min(_header_size(table.rows), 3)
    width = max(len(r) for r in table.rows)
    headers = {c: squash(_header_text(table, c, size)) for c in range(1, width)}
    current = [c for c, t in headers.items() if re.search(r"당기|\(당\)", t) and not re.search(r"전기|\(전\)", t)]
    prior = [c for c, t in headers.items() if re.search(r"전기|\(전\)", t)]
    if current:
        return {0: current, 1: prior}
    terms = {c: int(m.group(1)) for c, t in headers.items() if (m := _TERM.search(t))}
    if not terms:
        return {}
    newest = max(terms.values())
    return {0: [c for c, v in terms.items() if v == newest], 1: [c for c, v in terms.items() if v == newest - 1]}


def _row_value(row: list[str], columns: list[int]) -> float | None:
    for column in reversed(columns):
        if column < len(row):
            value = to_number(row[column])
            if value is not None:
                return value
    return None


def _audit_financials(docs: list[YearDoc]) -> Financials | None:
    """감사보고서의 재무제표 표에서 주요 계정을 읽는다. 단위를 확인할 수 없으면 쓰지 않는다."""
    if not docs:
        return None
    merged: dict[str, dict[int, float]] = {name: {} for name in _AUDIT_ACCOUNTS}
    for doc in docs:  # 최신 보고서의 비교 수치를 우선한다.
        for key, pattern in _STATEMENTS.items():
            section = next((s for s in doc.sections if re.search(pattern, squash(s.title)) and s.tables), None)
            if section is None:
                continue
            table = max(section.tables, key=lambda t: len(t.rows))
            periods = _period_columns(table)
            # 감사보고서는 날짜와 단위를 재무제표 위의 작은 표에 따로 적는다.
            heads = [" ".join(r) for t in section.tables if len(t.rows) <= 4 for r in t.rows]
            scale = _UNIT_SCALE.get(squash(table.unit or find_unit(*heads, *section.paragraphs[:3])))
            if not periods or not scale:
                continue
            for row in table.rows:
                label = _account_label(row[0])
                for name, (statement, labels) in _AUDIT_ACCOUNTS.items():
                    if statement != key or label not in labels:
                        continue
                    sign = -1 if "손실" in label and "이익" not in label else 1
                    for offset, columns in periods.items():
                        value = _row_value(row, columns)
                        if value is not None:
                            merged[name].setdefault(doc.year - offset, (abs(value) * -1 if sign < 0 else value) * scale)
    if not any(merged.values()):
        return None
    latest = docs[0].year
    years = [latest - 2, latest - 1, latest]
    values = {name: [merged.get(name, {}).get(y) for y in years] for name in FINANCIAL_ACCOUNTS}
    basis = "연결재무제표" if "연결" in docs[0].filing.report_nm else "개별재무제표"
    return Financials(years, basis, "KRW", values, [], docs[0].rcept_no)


def _load_year(client: DartClient, api: Api, company: Company, filing: Filing, source: str = "사업보고서") -> YearDoc:
    """[첨부정정]처럼 본문이 없는 보고서는 같은 사업연도의 이전 제출본을 읽는다."""
    candidates = [filing.rcept_no]
    for attempt in range(2):
        for number in candidates:
            api.calls.append(describe_call("document", {"rcept_no": number}, "xml"))
            try:
                sections = load_filing(client, number)
            except DartError as exc:
                if exc.code != "014":
                    raise
                continue
            if source == "감사보고서":
                keep = [s for s in sections if squash(s.title) != "목차"]
            else:
                keep = find_sections(sections, *_SALES, *_MATERIALS, *_DERIVATIVES, *_DERIVATIVE_NOTES)
            return YearDoc(filing, number, keep)
        if attempt or source != "사업보고서":
            break
        data = api.obj(
            "list", corp_code=company.corp_code, bgn_de=f"{filing.period_end[:4]}0101", end_de=date.today().strftime("%Y%m%d"),
            last_reprt_at="N", pblntf_ty="A", page_no="1", page_count="100",
        )
        same_period = []
        for row in data.get("list", []):
            match = _REPORT_NAME.search(str(row.get("report_nm", "")))
            number = str(row.get("rcept_no", ""))
            if match and match.group(1) == "사업" and f"{match.group(2)}{match.group(3)}" == filing.period_end and number != filing.rcept_no:
                same_period.append(number)
        candidates = sorted(same_period, reverse=True)
    return YearDoc(filing, filing.rcept_no, found=False)


def _find_account(rows: list[dict], ids: tuple[str, ...], names: tuple[str, ...], statements: tuple[str, ...]) -> dict | None:
    for statement in statements:
        scoped = [r for r in rows if r.get("sj_div") == statement]
        found = next((r for r in scoped if r.get("account_id") in ids), None) or \
            next((r for r in scoped if _norm(r.get("account_nm")) in names), None)
        if found:
            return found
    return None


def _load_financials(api: Api, company: Company, filing: Filing) -> Financials | None:
    year = int(filing.period_end[:4])
    for fs_div, basis in (("CFS", "연결재무제표"), ("OFS", "개별재무제표")):
        rows, resolved = rows_for(api, company, filing, "fnlttSinglAcntAll", fs_div=fs_div)
        if not rows:
            continue
        values = {}
        for name, (ids, names, statements) in FINANCIAL_ACCOUNTS.items():
            row = _find_account(rows, ids, names, statements)
            values[name] = [to_number(row.get(k)) if row else None for k in _AMOUNT_KEYS]
        seen, derivative = set(), []
        for row in rows:
            account = clean(str(row.get("account_nm", "")))
            if row.get("sj_div") in ("BS", "IS", "CIS") and "파생" in account and account not in seen:
                seen.add(account)
                derivative.append((account, [to_number(row.get(k)) for k in _AMOUNT_KEYS]))
        currency = str(rows[0].get("currency") or "KRW").upper()
        return Financials([year - 2, year - 1, year], basis, currency, values, derivative, str(rows[0].get("rcept_no", "")))
    rows, _ = rows_for(api, company, filing, "fnlttSinglAcnt")
    if not rows:
        return None
    annual = _annual_from_rows(rows, year)
    values = {name: annual.values.get(name, [None] * 3) for name in FINANCIAL_ACCOUNTS}
    return Financials(annual.years, annual.fs_name, str(rows[0].get("currency") or "KRW").upper(), values, [], annual.rcept_no)


def _merge_splits(splits: list[Split], title: str) -> Split:
    """보고서마다 2개년씩 실린 값을 최신 보고서 우선으로 이어 붙인다."""
    base = splits[0]
    by_year: dict[int, tuple] = {}
    for split in (s for s in splits if s.unit == base.unit):
        for index, year in enumerate(split.years):
            by_year.setdefault(year, (split.near[index], split.far[index], split.total[index]))
    years = sorted(by_year)[-YEARS:]
    return Split(title, base.unit, years, [by_year[y][0] for y in years], [by_year[y][1] for y in years],
                 [by_year[y][2] for y in years], base.note, base.rcept_no)


def _collect_audit_exports(result: CompanyAnalysis) -> None:
    found: list[Split] = []
    raw: list[tuple[str, FilingTable]] = []
    for doc in result.filings:
        for section in find_sections(doc.sections, *_NOTES):
            for table in section.tables:
                if not any(_kind(c, _DOMESTIC, _EXPORT) for r in table.rows for c in r):
                    continue
                split = _extract_split(table, doc.year, _DOMESTIC, _EXPORT, _short_caption(table, "감사보고서 주석"),
                                       _note_after(section, table), doc.rcept_no)
                if split:
                    found.append(split)
                elif doc is result.filings[0] and re.search(r"매출|수익|지역", squash(table.caption)) and _value_columns(table, doc.year):
                    raw.append((_short_caption(table, "감사보고서 주석"), table))
    if found:
        result.exports = [_merge_splits(found, "감사보고서 주석의 매출 구분")]
    else:
        result.export_tables = raw[:2]


def _collect_exports(result: CompanyAnalysis) -> None:
    if result.source == "감사보고서":
        _collect_audit_exports(result)
        return
    for doc in result.filings:  # 최신 보고서에 3개년 표가 함께 실린다.
        found: list[Split] = []
        fallback: list[tuple[str, FilingTable]] = []
        for section in find_sections(doc.sections, *_SALES):
            for table in section.tables:
                if not any(_kind(c, _DOMESTIC, _EXPORT) for r in table.rows for c in r):
                    continue
                title = _short_caption(table, section.title)
                split = _extract_split(table, doc.year, _DOMESTIC, _EXPORT, title, _note_after(section, table), doc.rcept_no)
                if split:
                    found.append(split)
                elif _value_columns(table, doc.year):
                    fallback.append((title, table))
        if found or fallback:
            result.exports, result.export_tables = found[:3], fallback[:2]
            return


def _item_column(table: FilingTable) -> int | None:
    for column, text in enumerate(table.rows[0]):
        if re.search(r"품목|품명|원재료|제품", squash(text)):
            return column
    return None


def _collect_imports(result: CompanyAnalysis) -> None:
    if result.source != "사업보고서":  # 감사보고서에는 원재료 매입 항목이 없다.
        return
    for doc in result.filings:
        entry = ImportYear(doc.year, doc.rcept_no)
        for section in find_sections(doc.sections, *_MATERIALS):
            for table in section.tables:
                head = squash(" ".join(table.rows[0]) + table.caption)
                is_price = bool(re.search(r"가격|단가|변동", head))
                if not is_price and re.search(r"매입", head) and entry.purchase is None and len(table.rows) >= 2:
                    entry.purchase = table
                if not is_price and not result.imports and re.search(r"매입|금액", head):
                    split = _extract_split(table, doc.year, _LOCAL_BUY, _IMPORT_BUY, _short_caption(table, section.title),
                                           _note_after(section, table), doc.rcept_no, single=r"매입액|매입금액|금액")
                    if split:
                        result.imports.append(split)
                column = _item_column(table)
                if column is None:
                    continue
                for row in table.rows[1:]:
                    if any(_IMPORT_MARK.match(squash(c)) for c in row) and column < len(row):
                        item = clean(row[column])
                        if item and item not in entry.items and not _IMPORT_MARK.match(squash(item)):
                            entry.items.append(item)
            for paragraph in section.paragraphs:
                if re.search(r"수입|외자|해외.{0,6}(구매|매입|조달)", paragraph) and len(entry.statements) < 2:
                    entry.statements.append(paragraph[:220] + ("…" if len(paragraph) > 220 else ""))
        entry.items = entry.items[:10]
        result.import_years.append(entry)


def _is_derivative_table(table: FilingTable, use_caption: bool = True) -> bool:
    """파생 계약이나 잔액을 담은 표만 고른다. 금액이 모두 0이거나 비어 있으면 보유 근거로 보지 않는다."""
    size = _header_size(table.rows)
    header = squash(" ".join(c for r in table.rows[:size] for c in r))
    caption = squash(table.caption)
    if _NOT_CONTRACT.search(header + caption) or _NO_DERIVATIVE.search(table.caption):
        return False
    body = table.rows[size:]
    nonzero = lambda row: any((to_number(c) or 0) != 0 for c in row)
    if _INSTRUMENT.search(header) or (use_caption and _INSTRUMENT.search(caption)):
        return any(nonzero(r) for r in body)
    return any(nonzero(r) and any(to_number(c) is None and _INSTRUMENT.search(squash(c)) for c in r) for r in body)


def _derivative_evidence(sections: list[FilingSection], strict: bool) -> tuple[list, list[str], bool, bool]:
    """strict는 회계정책 설명이 많은 재무제표 주석용으로, 기말 현재 보유를 말하는 문장만 인정한다."""
    tables, statements, says_none, says_some = [], [], False, False
    for section in sections:
        prefix = re.sub(r"^\d+\.\s*", "", section.title)
        for table in section.tables:
            if _is_derivative_table(table, use_caption=not strict):
                tables.append((f"{prefix} · {_short_caption(table, '')}".rstrip(" ·"), table))
        for paragraph in section.paragraphs:
            if _NO_DERIVATIVE.search(paragraph):
                says_none = True
            elif _HAS_DERIVATIVE.search(paragraph) and (not strict or _PERIOD_END.search(paragraph)):
                says_some = True
                if len(statements) < 3:
                    statements.append(paragraph[:240] + ("…" if len(paragraph) > 240 else ""))
    return tables, statements, says_none, says_some


def derivative_for_doc(doc: YearDoc, audit: bool) -> DerivativeYear:
    """보고서 한 건의 파생거래 기재 상태와 근거를 판정한다. 업체 분석과 전수조사가 같은 기준을 쓴다."""
    sections = find_sections(doc.sections, *(_NOTES if audit else _DERIVATIVES))
    tables, statements, says_none, says_some = _derivative_evidence(sections, audit)
    if not audit and not (tables or says_none or says_some):
        # 사업의 내용에 없으면 재무제표 주석에서 보유 근거를 찾는다.
        used = {id(s) for s in sections}
        notes = [s for s in find_sections(doc.sections, *_DERIVATIVE_NOTES) if id(s) not in used]
        tables, statements, _, says_some = _derivative_evidence([s for s in notes if "연결" in s.title] or notes, True)
    if audit:
        contracts = collect_contracts([], sections, doc.year, doc.filing.period_end)
    else:
        used = {id(s) for s in sections}
        candidates = [s for s in find_sections(doc.sections, *_DERIVATIVE_NOTES) if id(s) not in used]
        contracts = collect_contracts(sections, [s for s in candidates if "연결" in s.title] or candidates, doc.year, doc.filing.period_end)
    if tables or says_some or any(c.notional for c in contracts):
        status = "있음"
    elif says_none:
        status = "없음"
        statements = [p[:240] for s in sections for p in s.paragraphs if _NO_DERIVATIVE.search(p)][:1]
    elif audit and not any("파생" in s.text() for s in sections):
        status = "기재 없음"
    else:
        status = "확인 필요"
    return DerivativeYear(doc.year, doc.rcept_no, status, statements, tables[:6], contracts if status == "있음" else [])


def _collect_derivatives(result: CompanyAnalysis) -> None:
    audit = result.source == "감사보고서"
    result.derivatives = [derivative_for_doc(doc, audit) for doc in result.filings]


def _collect_fx(result: CompanyAnalysis) -> None:
    """감사보고서만 있는 회사는 외화자산·부채가 수출입 규모를 가늠할 간접 자료다."""
    if result.source != "감사보고서" or not result.filings:
        return
    for section in find_sections(result.filings[0].sections, *_NOTES):
        for table in section.tables:
            head = squash(table.caption + " ".join(table.rows[0]))
            if "외화" in head and any((to_number(c) or 0) != 0 for r in table.rows[1:] for c in r):
                result.fx_tables.append((_short_caption(table, "외화자산·부채"), table))
    result.fx_tables = result.fx_tables[:2]


def _collect_losses(api: Api, result: CompanyAnalysis) -> None:
    if result.info.get("corp_cls") not in ("Y", "K", "N"):
        return
    today = date.today()
    rows, _ = _fetch_disclosures(api, corp_code=result.company.corp_code, bgn_de=months_ago(today, YEARS * 12).strftime("%Y%m%d"),
                                 end_de=today.strftime("%Y%m%d"), pblntf_ty="I")
    result.derivative_losses = [r for r in rows if "파생상품거래손실" in squash(str(r.get("report_nm", "")))]


def analyze_company(client: DartClient, company: Company) -> CompanyAnalysis:
    api = Api(client)
    info = api.obj("company", corp_code=company.corp_code)
    if not info:
        raise DartError("013", "이 회사의 기업개황 정보가 없습니다.")
    filings = [f for f in find_filings(api, company, _LOOKBACK_MONTHS) if f.is_annual][:YEARS]
    result = CompanyAnalysis(company, info, [])
    if filings:
        result.source = "사업보고서"
        latest = filings[0]
        holders = [r for r in rows_for(api, company, latest, "hyslrSttus")[0] if _norm(r.get("nm")) not in TOTAL_WORDS]
        common = [r for r in holders if "보통" in str(r.get("stock_knd", ""))] or holders
        if common:
            top = max(common, key=lambda r: to_number(r.get("trmend_posesn_stock_qota_rt")) or 0)
            result.holder, result.holder_rate = str(top.get("nm", "")), to_number(top.get("trmend_posesn_stock_qota_rt"))
        staff = rows_for(api, company, latest, "empSttus")[0]
        detail = [r for r in staff if _norm(r.get("fo_bbm")) not in TOTAL_WORDS and _norm(r.get("sexdstn")) in ("남", "여")]
        if detail:
            result.employees = sum(to_number(r.get("sm")) or 0 for r in detail)
        result.financials = _load_financials(api, company, latest)
        result.filings = [_load_year(client, api, company, f) for f in filings]
    else:
        audits = _find_audit_filings(api, company, info)
        if audits:
            result.source = "감사보고서"
            result.filings = [_load_year(client, api, company, f, "감사보고서") for f in audits]
            result.financials = _audit_financials(result.filings)
    if result.filings:
        _collect_exports(result)
        _collect_imports(result)
        _collect_derivatives(result)
        _collect_fx(result)
    _collect_losses(api, result)
    result.calls = api.calls
    return result


# ---------------------------------------------------------------- 표기
def _money(value: float | None, currency: str = "KRW") -> str:
    if value is None:
        return "-"
    return won(value) if currency == "KRW" else f"{value:,.0f} {currency}"


def _table_money(value: float | None, unit: str) -> str:
    if value is None:
        return "-"
    scale = _UNIT_SCALE.get(squash(unit))
    return won(value * scale) if scale else f"{value:,.0f}"


def _unit_note(unit: str) -> str:
    return "" if squash(unit) in _UNIT_SCALE else (f"공시 단위: {unit}" if unit else "원문에 단위 표시가 없어 숫자를 그대로 표시했습니다.")


def _ratios(fin: Financials) -> dict[str, list[float | None]]:
    v = fin.values
    sales = v["매출액"]
    growth: list[float | None] = [None]
    for before, now in zip(sales, sales[1:]):
        growth.append((now - before) / abs(before) * 100 if before not in (None, 0) and now is not None else None)
    return {
        "매출 증가율": growth,
        "영업이익률": [ratio(a, b) for a, b in zip(v["영업이익"], sales)],
        "순이익률": [ratio(a, b) for a, b in zip(v["당기순이익"], sales)],
        "부채비율": [ratio(a, b) for a, b in zip(v["부채총계"], v["자본총계"])],
        "유동비율": [ratio(a, b) for a, b in zip(v["유동자산"], v["유동부채"])],
        "ROE (기말자본 기준)": [ratio(a, b) for a, b in zip(v["당기순이익"], v["자본총계"])],
    }


def _company_rows(a: CompanyAnalysis) -> tuple[list[list[str]], dict]:
    d = a.info
    home = str(d.get("hm_url") or "").strip()
    if home and not home.lower().startswith(("http://", "https://")):
        home = f"http://{home}"
    holder = f"{a.holder} ({percent(a.holder_rate, 2)})" if a.holder else "-"
    rows = [
        ["정식 회사명", text_or_dash(d.get("corp_name"))],
        ["영문 회사명", text_or_dash(d.get("corp_name_eng"))],
        ["시장 / 종목코드", f"{CORP_CLS.get(d.get('corp_cls'), '-')} / {text_or_dash(d.get('stock_code'))}"],
        ["대표자", text_or_dash(d.get("ceo_nm"))],
        ["설립일 / 결산월", f"{ymd(d.get('est_dt'))} / {text_or_dash(d.get('acc_mt'))}월"],
        ["사업자등록번호", text_or_dash(d.get("bizr_no"))],
        ["법인등록번호", text_or_dash(d.get("jurir_no"))],
        ["업종코드", text_or_dash(d.get("induty_code"))],
        ["최대주주 (보통주 지분율)", holder],
        ["직원 수", count(a.employees, "명")],
        ["주소", text_or_dash(d.get("adres"))],
        ["전화번호", text_or_dash(d.get("phn_no"))],
        ["홈페이지", home or "-"],
        ["DART 공시 유형", disclosure_type(a)],
    ]
    return rows, ({(12, 1): home} if home else {})


def disclosure_type(a: CompanyAnalysis) -> str:
    if a.source == "사업보고서":
        return "상장사" if a.info.get("corp_cls") in ("Y", "K", "N") else "사업보고서 제출 비상장사"
    if a.source == "감사보고서":
        return "외부감사 비상장사"
    return "DART 정기 공시 없음"


def _source_rows(a: CompanyAnalysis) -> Table:
    rows, links = [], {}
    for index, doc in enumerate(a.filings):
        rows.append([f"{doc.year}년", doc.filing.report_nm, "원문 열기"])
        url = viewer_url(doc.rcept_no)
        if url:
            links[(index, 2)] = url
    return Table(["사업연도", "보고서", "바로가기"], rows, links=links, widths=[16, 62, 22])


def _financial_sections(a: CompanyAnalysis) -> list[Section]:
    fin = a.financials
    if not fin:
        return [Section("2. 3개년 재무정보", [f"{a.source}에서 재무제표 수치를 자동으로 읽지 못했습니다. '근거 보고서'의 원문을 확인해 주세요."])]
    labels = [str(y) for y in fin.years]
    money = lambda value: _money(value, fin.currency)
    chart = None
    if fin.currency == "KRW" and any(v is not None for v in fin.values["매출액"]):
        chart = charts.column_panels(
            [(name, labels, fin.values[name]) for name in ("매출액", "영업이익", "당기순이익")], compact_won)
    rows = [[name] + [money(v) for v in fin.values[name]] for name in FINANCIAL_ACCOUNTS]
    ratio_rows = [[name] + [percent(v) for v in values] for name, values in _ratios(fin).items()]
    columns = ["항목"] + [f"{y}년" for y in fin.years]
    return [
        Section("2. 3개년 재무정보", [f"{fin.basis} 기준입니다. 최신 {a.source}의 수치를 우선 사용했습니다."], chart=chart,
                table=Table(columns, rows, right={1, 2, 3}, widths=[34, 22, 22, 22])),
        Section("2-1. 주요 재무비율", table=Table(columns, ratio_rows, right={1, 2, 3}, widths=[34, 22, 22, 22],
                                            note="금융업은 업종 특성상 부채비율과 매출 관련 비율을 일반 기업과 같은 기준으로 비교하기 어렵습니다.")),
    ]


def _coverage(a: CompanyAnalysis, split: Split) -> list[float | None]:
    """표의 합계가 재무제표 매출의 몇 %인지. 별도 기준이나 일부 사업부문 표를 구분하는 근거다."""
    fin, scale = a.financials, _UNIT_SCALE.get(squash(split.unit))
    if not fin or not scale or fin.currency != "KRW":
        return [None] * len(split.years)
    sales = dict(zip(fin.years, fin.values["매출액"]))
    return [ratio(t * scale, sales.get(y)) if t is not None else None for y, t in zip(split.years, split.total)]


def _split_table(split: Split, near: str, far: str, far_share: str, coverage: list[float | None] | None = None) -> Table:
    rows = [[near] + [_table_money(v, split.unit) for v in split.near],
            [far] + [_table_money(v, split.unit) for v in split.far]]
    if any(v not in (None, 0) and abs(v) > 0.5 for v in split.other()):
        rows.append(["기타 (표의 다른 항목)"] + [_table_money(v, split.unit) for v in split.other()])
    rows.append(["합계"] + [_table_money(v, split.unit) for v in split.total])
    rows.append([far_share] + [percent(v) for v in split.share()])
    notes = [split.note, _unit_note(split.unit)]
    if coverage and any(v is not None for v in coverage):
        rows.append(["재무제표 매출 대비"] + [percent(v) for v in coverage])
        if any(v is not None and abs(v - 100) > 2 for v in coverage):
            notes.append("합계가 재무제표 매출과 다릅니다. 별도 기준, 일부 사업부문 또는 재작성 전 수치일 수 있습니다.")
    note = " ".join(x for x in notes if x)
    return Table(["구분"] + [f"{y}년" for y in split.years], rows, right=set(range(1, len(split.years) + 1)), note=note)


def _trade_sections(a: CompanyAnalysis) -> list[Section]:
    out: list[Section] = []
    if a.exports:
        first = a.exports[0]
        labels = [str(y) for y in first.years]
        scale = _UNIT_SCALE.get(squash(first.unit))
        chart = None
        if scale:
            chart = charts.column_panels(
                [("내수", labels, [v * scale if v is not None else None for v in first.near]),
                 ("수출", labels, [v * scale if v is not None else None for v in first.far])], compact_won)
        for index, split in enumerate(a.exports):
            title = "3. 3개년 수출/수입 분석" if index == 0 else f"3-{index}. 추가 매출 구분"
            paragraphs = [f"수출: {split.title}"]
            known = [v for v in split.far if v is not None]
            if index == 0 and known:
                paragraphs.append(f"{len(known)}개년 평균 수출은 {_table_money(sum(known) / len(known), split.unit)}입니다. 원화 매출 기준이며 통관 실적이 아닙니다.")
            out.append(Section(title, paragraphs, chart=chart if index == 0 else None,
                               table=_split_table(split, "내수", "수출", "수출 비중", _coverage(a, split))))
    elif a.export_tables:
        out.append(Section("3. 3개년 수출/수입 분석", ["내수·수출 표가 있지만 합계를 자동으로 검증할 수 없어 합산하지 않고 원문 표를 그대로 보여 드립니다."]))
        for title, table in a.export_tables:
            out.append(Section(f"수출 관련 원문 표: {title[:50]}", table=display_table(table, max_rows=40)))
    else:
        out.append(Section("3. 3개년 수출/수입 분석", [f"최근 {a.source}에서 내수·수출 구분 금액을 찾지 못했습니다. 회사가 구분해 공시하지 않았을 수 있습니다."]))

    if a.source == "감사보고서":
        out.append(Section("3-2. 수입", ["감사보고서에는 수입 금액 항목이 없어 표시하지 않습니다. '공시 한계와 추가 확인 자료' 항목을 참고해 주세요."]))
        for title, table in a.fx_tables:
            out.append(Section(f"외화자산·부채 (간접 자료): {title[:40]}", table=display_table(table)))
        return out
    if a.imports:
        split = a.imports[0]
        out.append(Section("3-2. 수입 (원재료 매입)", [f"수입: {split.title}"],
                           table=_split_table(split, "국내 매입", "수입 매입", "수입 비중")))
    else:
        paragraphs = ["사업보고서의 원재료 항목에서 수입 금액을 찾지 못해 금액을 표시하지 않습니다. 아래는 공시에서 확인된 수입 품목과 원재료 매입 정보입니다."]
        rows = [[f"{entry.year}년", ", ".join(entry.items) if entry.items else "공시된 수입 품목 없음"] for entry in a.import_years]
        table = Table(["사업연도", "수입으로 표시된 품목"], rows, widths=[18, 82]) if rows else None
        out.append(Section("3-2. 수입 (원재료 매입)", paragraphs, table=table))
    latest_purchase = next((e for e in a.import_years if e.purchase), None)
    if latest_purchase:
        out.append(Section(f"3-3. 주요 원재료 매입 현황 ({latest_purchase.year}년 사업보고서)", table=display_table(latest_purchase.purchase)))
    statements = next((e.statements for e in a.import_years if e.statements), [])
    if statements:
        out.append(Section("3-4. 수입·조달 관련 공시 내용", bullets=statements))
    return out


def contract_tables(contracts: list[Contract]) -> tuple[Table, Table]:
    """상품·통화별 액면 합계 표와 계약별 상세 표."""
    rows = []
    for item in summarize_contracts(contracts):
        amount = money_text(item["currency"], item["total"]) if item["count"] else "원문 확인"
        name = f"{item['instrument']} {item['side']}".strip()
        rows.append([f"{item['year']}년", name, amount, f"{item['count']}건",
                     f"{item['unconfirmed']}건" if item["unconfirmed"] else "-"])
    summary = Table(["사업연도", "상품", "액면 합계", "합산 계약", "원문 확인"], rows, right={2, 3, 4}, widths=[13, 29, 32, 13, 13],
                    note="공시된 계약금액 기준의 기말 미결제 잔액입니다. 통화가 다른 금액과 매도·매수 포지션은 합산하지 않았고, 금액이나 단위가 불분명한 행, 집계표, 기말 전에 만기가 지난 계약은 합계에서 뺐습니다. "
                         "여러 계열사 표를 더한 값에는 그룹 내부 거래가 포함될 수 있습니다.")
    limit = _MAX_ROWS * 2
    detail_rows = [[f"{c.year}", c.instrument, c.notional_display + (" (만기 경과)" if c.expired else " (확인)" if c.check else ""),
                    c.side or " → ".join(x for x in (c.sell, c.buy) if x) or "-",
                    c.rate or "-", c.maturity or "-", c.counterparty or "-", c.valuation or "-"] for c in contracts[:limit]]
    note = "포지션은 매도 → 매입 순서로 표시했습니다. (확인)은 금액·단위가 불분명하거나 집계표라 원문 대조가 필요한 행이고, (만기 경과)는 기말 잔액이 아닌 계약입니다."
    if len(contracts) > limit:
        note += f" 전체 {len(contracts)}건 중 {limit}건만 표시했습니다."
    detail = Table(["연도", "상품", "액면", "포지션", "약정환율·금리", "만기", "거래상대방", "평가·장부"], detail_rows, note=note,
                   widths=[6, 13, 17, 21, 11, 10, 10, 12])
    return summary, detail


def _contract_sections(a: CompanyAnalysis) -> tuple[list[Section], set[int]]:
    """표로 정리한 원문 표는 아래에서 다시 보여 주지 않는다."""
    contracts = [c for d in sorted(a.derivatives, key=lambda d: d.year, reverse=True) for c in d.contracts]
    if not contracts:
        return [Section("4-2. 상품별 액면", ["공시 표에서 액면(계약금액)을 찾지 못했습니다. 회사가 장부금액이나 평가손익만 공시했을 수 있습니다."])], set()
    summary, detail = contract_tables(contracts)
    return [Section("4-2. 상품별 액면 합계", table=summary), Section("4-2-1. 계약 상세", table=detail)], {c.table_id for c in contracts}


def _derivative_sections(a: CompanyAnalysis) -> list[Section]:
    out = [Section("4. 3개년 파생거래 내역", [f"{a.source}의 파생상품 기재 내용과 재무제표 계정을 정리했습니다. 공시는 기말 잔액과 평가손익 중심이며 타행별 거래 내역은 포함하지 않습니다."],
                   table=Table(["사업연도", "공시상 파생거래", "확인된 표"],
                               [[f"{d.year}년", d.status, f"{len(d.tables)}개"] for d in a.derivatives], widths=[24, 46, 30]))]
    fin = a.financials
    if fin and fin.derivative_accounts:
        rows = [[name] + [_money(v, fin.currency) for v in values] for name, values in fin.derivative_accounts]
        out.append(Section("4-1. 재무제표의 파생상품 계정", table=Table(["계정"] + [f"{y}년" for y in fin.years], rows, right={1, 2, 3}, widths=[40, 20, 20, 20],
                                                              note=f"{fin.basis} 기준. 재무상태표와 손익계산서에 별도 계정으로 표시된 금액만 포함합니다. "
                                                                   "전환사채의 옵션 등 환헤지가 아닌 파생상품이 섞일 수 있어 통화 파생 잔액과 다를 수 있습니다.")))
    details, merged = _contract_sections(a)
    out.extend(details)
    if a.info.get("corp_cls") in ("Y", "K", "N"):
        if a.derivative_losses:
            rows, links = [], {}
            for index, row in enumerate(a.derivative_losses):
                rows.append([ymd(row.get("rcept_dt")), text_or_dash(row.get("report_nm")).strip()])
                url = viewer_url(row.get("rcept_no"))
                if url:
                    links[(index, 1)] = url
            out.append(Section("4-3. 파생상품 거래손실 수시공시", table=Table(["접수일", "공시 (누르면 원문)"], rows, links=links, widths=[20, 80])))
        else:
            out.append(Section("4-3. 파생상품 거래손실 수시공시", ["최근 3년간 거래소 공시에서 '파생상품거래손실발생' 공시를 찾지 못했습니다. 이 공시는 일정 규모 이상의 손실에만 해당합니다."]))
    for entry in a.derivatives:
        if entry.statements:
            out.append(Section(f"{entry.year}년 공시 내용", bullets=entry.statements))
        for title, table in entry.tables:
            if id(table) not in merged:
                out.append(Section(f"{entry.year}년 · {title[:58]}", table=display_table(table)))
    return out


def _evidence_section(a: CompanyAnalysis, number: int = 5) -> Section:
    """공시로 확인되는 범위와 실제 심사에 필요한 자료를 구분한다."""
    export_basis = {"사업보고서": "사업보고서의 내수/수출 매출 (원화 매출 기준, 통관 기준 아님)",
                    "감사보고서": "감사보고서 주석의 간접 자료"}.get(a.source, "공개 자료로 확인 불가")
    derivative_basis = {"사업보고서": "사업보고서와 주석의 기말 잔액·평가손익",
                        "감사보고서": "감사보고서 주석의 기말 잔액·평가손익"}.get(a.source, "공개 자료로 확인 불가")
    rows = [
        ["수출 실적", export_basis, "무역협회 수출입실적증명서 또는 관세청 무역 마이데이터, 부가세 과세표준증명(영세율)과 수출실적명세서로 교차 확인"],
        ["수입 실적", "공시에 수입 금액이 없는 경우가 많음", "무역협회 수출입실적증명서, 수입세금계산서(매입처별 세금계산서합계표), 수입신고필증"],
        ["파생 잔액", derivative_basis, "기업 제출 거래 내역 및 재무제표 주석 대조"],
        ["타행 파생거래", "공시로 확인 불가 (거래 은행 대부분 미기재)", "고객 확인서, 은행 간 공유 인프라의 운영 여부는 사내 외환·파생 리스크 부서 확인"],
    ]
    return Section(f"{number}. 공시 한계와 추가 확인 자료", ["이 보고서의 수치는 공개 공시만 사용합니다. 외환파생상품 거래한도 산정의 증빙으로 쓰려면 아래 자료를 별도로 확인해 주세요."],
                   table=Table(["항목", "이 보고서의 근거", "추가 확인 자료"], rows, widths=[16, 34, 50]))


def build_report(a: CompanyAnalysis, news: Section | None = None) -> Report:
    """news: AI 웹 검색 뉴스 항목(news.py). 있으면 5번에 넣고 공시 한계는 6번이 된다."""
    name = str(a.info.get("corp_name") or a.company.corp_name)
    rows, links = _company_rows(a)
    sections = [Section("1. 업체정보", table=Table(["항목", "내용"], rows, links=links, widths=[28, 72]))]
    summary, metrics = [], [Metric("시장", CORP_CLS.get(a.info.get("corp_cls"), "-")), Metric("대표자", text_or_dash(a.info.get("ceo_nm")))]
    if not a.filings:
        summary.append(f"{name}의 사업보고서와 감사보고서를 DART에서 찾지 못해 업체정보만 표시합니다. 비외감 기업이면 수출입과 파생거래는 공개 경로로 확인할 수 없습니다.")
        sections += ([news] if news else []) + [_evidence_section(a, 6 if news else 5)]
        return Report(f"{name} 업체 분석", "기업개황 기준", summary, metrics, sections, file_stem=_stem(a.company, "업체분석"), calls=a.calls)

    sections += _financial_sections(a) + _trade_sections(a) + _derivative_sections(a)
    sections += ([news] if news else []) + [_evidence_section(a, 6 if news else 5)]
    sections.append(Section("근거 보고서", table=_source_rows(a)))
    fin = a.financials
    if fin:
        sales, profit = fin.values["매출액"], fin.values["영업이익"]
        profit_text = f"영업이익은 {_money(profit[-1], fin.currency)}({change_text(profit[-1], profit[-2])})입니다."
        if sales[-1] is None:
            summary.append(f"{fin.years[-1]}년 {profit_text} 재무제표에 매출액 항목이 없습니다(금융업 등).")
        else:
            summary.append(f"{fin.years[-1]}년 매출은 {_money(sales[-1], fin.currency)}({change_text(sales[-1], sales[-2])}), {profit_text}")
        metrics = [Metric("매출액", _money(sales[-1], fin.currency), f"{fin.years[-1]}년"),
                   Metric("영업이익", _money(profit[-1], fin.currency), f"{fin.years[-1]}년"),
                   Metric("부채비율", percent(_ratios(fin)["부채비율"][-1]), f"{fin.basis[:2]} 기준")]
    if a.exports:
        split = a.exports[0]
        covered = _coverage(a, split)[-1]
        scope = f", 재무제표 매출의 {covered:.0f}% 범위" if covered is not None and abs(covered - 100) > 2 else ""
        summary.append(f"{split.years[-1]}년 수출 비중은 {percent(split.share()[-1])}입니다. (기준: {split.title[:40]}{scope})")
        metrics.append(Metric("수출 비중", percent(split.share()[-1]), f"{split.years[-1]}년{' · 일부 범위' if scope else ''}"))
    else:
        summary.append("내수·수출 금액은 자동으로 확정하지 못했습니다. 3번 항목의 안내를 확인해 주세요.")
        metrics.append(Metric("수출 비중", "-", "확정 불가"))
    if not a.imports:
        summary.append(f"수입 금액은 {a.source}에서 찾지 못해 표시하지 않습니다.")
    states = ", ".join(f"{d.year}년 {d.status}" for d in sorted(a.derivatives, key=lambda d: d.year))
    if states:
        summary.append(f"파생거래 공시: {states}.")
    if a.derivative_losses:
        summary.append(f"최근 3년간 파생상품 거래손실 수시공시가 {len(a.derivative_losses)}건 있습니다.")
    latest = a.filings[0]
    return Report(
        f"{name} 업체 분석", f"{a.filings[-1].year}~{latest.year} {a.source} 기준 · {disclosure_type(a)}",
        summary, metrics, sections,
        [f"수출입과 파생거래는 전용 API가 없어 {a.source} 원문의 표를 읽어 정리했습니다. 중요한 숫자는 '근거 보고서'의 원문으로 확인해 주세요.",
         "내수·수출 표는 회사에 따라 별도 기준이거나 일부 사업부문만 포함할 수 있습니다. 표 제목과 주석의 기준을 함께 보세요.",
         "파생거래 '확인 필요'나 '기재 없음'은 거래가 없다는 확인이 아닙니다. 타행 거래는 공시만으로 알 수 없습니다."]
        + (["5번 뉴스는 공시가 아니라 AI가 웹에서 찾은 기사입니다. 숫자와 사실은 기사 원문으로 확인해 주세요."] if news else []),
        a.calls, _stem(a.company, "업체분석"),
    )


def analysis_text(a: CompanyAnalysis) -> str:
    """AI 에이전트에게 넘길 근거 자료. 보고서와 같은 데이터를 글로 정리한다."""
    report = build_report(a)
    lines = [f"# {report.title} ({report.subtitle})"]
    lines += [f"- {s}" for s in report.summary]
    for section in report.sections:
        lines.append(f"\n## {section.title}")
        lines += section.paragraphs
        lines += [f"- {b}" for b in section.bullets]
        if section.table:
            lines.append(" | ".join(section.table.columns))
            lines += [" | ".join(row) for row in section.table.rows]
            if section.table.note:
                lines.append(f"(주: {section.table.note})")
    lines.append("\n## 접수번호")
    lines += [f"- {d.year}년 {a.source}: {d.rcept_no}" for d in a.filings]
    return "\n".join(lines)
