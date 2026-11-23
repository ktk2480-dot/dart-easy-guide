"""회사별 최신 정기보고서를 찾고, 해당 API에 데이터가 있는 가장 최근 보고서를 고른다."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date

from .formatting import digits_only, months_ago

REPORT_LABELS = {"11011": "사업보고서", "11012": "반기보고서", "11013": "1분기보고서", "11014": "3분기보고서"}
PERIOD_NAMES = {"11011": "연간", "11012": "반기", "11013": "1분기", "11014": "3분기"}
_REPORT_NAME = re.compile(r"(사업|반기|분기)보고서\s*\((\d{4})\.(\d{2})\)")
_MAX_FILINGS_TO_TRY = 6


@dataclass(frozen=True)
class Filing:
    period_end: str  # YYYYMM
    reprt_code: str
    bsns_year: int
    report_nm: str
    rcept_no: str
    rcept_dt: str
    fiscal_month: int = 12

    @property
    def is_annual(self) -> bool:
        return self.reprt_code == "11011"

    @property
    def period_text(self) -> str:
        return f"{self.period_end[:4]}.{self.period_end[4:]} 말"

    @property
    def label(self) -> str:
        if self.fiscal_month == 12:
            return f"{self.period_end[:4]}년 {REPORT_LABELS[self.reprt_code]}"
        return f"{self.period_end[:4]}.{self.period_end[4:]} {REPORT_LABELS[self.reprt_code]}"

    @property
    def short(self) -> str:
        if self.fiscal_month == 12:
            return f"{self.period_end[2:4]}년 {PERIOD_NAMES[self.reprt_code]}"
        return f"{self.period_end[2:4]}.{self.period_end[4:]}"

    def year_candidates(self) -> tuple[int, ...]:
        """12월 결산이 아니면 API의 사업연도 표기가 달라질 수 있어 앞뒤 연도도 확인한다."""
        year = int(self.period_end[:4])
        if self.fiscal_month == 12:
            return (year,)
        candidates = [year, year - 1]
        if year + 1 <= date.today().year:
            candidates.append(year + 1)
        return tuple(candidates)


def _fiscal_month(value) -> int:
    text = digits_only(value)
    month = int(text) if text else 12
    return month if 1 <= month <= 12 else 12


def _report_code(kind: str, month: int, fiscal_month: int) -> str | None:
    if kind == "사업":
        return "11011"
    if kind == "반기":
        return "11012"
    elapsed = (month - fiscal_month) % 12
    return {3: "11013", 9: "11014"}.get(elapsed)


def find_filings(api, company, lookback_months: int = 30) -> list[Filing]:
    """최종 정기보고서를 자료 기준 시점이 최신인 순서로 돌려준다."""
    info = api.obj("company", corp_code=company.corp_code)
    fiscal_month = _fiscal_month(info.get("acc_mt"))
    today = date.today()
    data = api.obj(
        "list", corp_code=company.corp_code, bgn_de=months_ago(today, lookback_months).strftime("%Y%m%d"),
        end_de=today.strftime("%Y%m%d"), last_reprt_at="Y", pblntf_ty="A", sort="date", sort_mth="desc",
        page_no="1", page_count="100",
    )
    found: dict[str, Filing] = {}
    for row in data.get("list", []):
        if "철" in str(row.get("rm", "")):
            continue
        match = _REPORT_NAME.search(str(row.get("report_nm", "")))
        if not match:
            continue
        kind, year, month = match.group(1), match.group(2), match.group(3)
        code = _report_code(kind, int(month), fiscal_month)
        if not code:
            continue
        period = f"{year}{month}"
        number = digits_only(row.get("rcept_no"))
        if period not in found or number > found[period].rcept_no:
            found[period] = Filing(period, code, int(year), str(row.get("report_nm", "")).strip(), number,
                                   digits_only(row.get("rcept_dt")), fiscal_month)
    return sorted(found.values(), key=lambda f: f.period_end, reverse=True)


def _period_matches(rows: list[dict], period_end: str) -> bool:
    dates = [digits_only(r.get("thstrm_dt") or r.get("stlm_dt")) for r in rows]
    dates = [d for d in dates if d]
    return not dates or any(period_end in d for d in dates)


def rows_for(api, company, filing: Filing, endpoint: str, **extra) -> tuple[list[dict], Filing]:
    for year in filing.year_candidates():
        rows = api.rows(endpoint, corp_code=company.corp_code, bsns_year=str(year), reprt_code=filing.reprt_code, **extra)
        if rows and (filing.fiscal_month == 12 or _period_matches(rows, filing.period_end)):
            return rows, replace(filing, bsns_year=year)
    return [], filing


def first_with_data(api, company, filings: list[Filing], endpoint: str, **extra) -> tuple[list[dict], Filing | None, list[Filing]]:
    """최신 보고서부터 확인해 데이터가 있는 첫 보고서와 건너뛴 보고서를 돌려준다."""
    skipped: list[Filing] = []
    for filing in filings[:_MAX_FILINGS_TO_TRY]:
        rows, resolved = rows_for(api, company, filing, endpoint, **extra)
        if rows:
            return rows, resolved, skipped
        skipped.append(filing)
    return [], None, skipped


def months_between(earlier: str, later: str) -> int:
    return (int(later[:4]) - int(earlier[:4])) * 12 + int(later[4:]) - int(earlier[4:])
