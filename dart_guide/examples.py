"""초보자용 예제 15종. 각 예제는 API를 호출해 쉬운 말로 정리한 Report를 만든다."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Callable

from . import charts
from .catalog import field_labels, find_api
from .client import DartClient, DartError, describe_call
from .corp_index import Company
from .formatting import (
    change_text, compact_won, count, digits_only, months_ago, percent, ratio, signed, text_or_dash, to_number, won, ymd,
)
from .latest import PERIOD_NAMES, Filing, find_filings, first_with_data, months_between, rows_for
from .report import Metric, Report, Section, Table

CORP_CLS = {"Y": "코스피", "K": "코스닥", "N": "코넥스", "E": "기타법인"}
TOTAL_WORDS = {"합계", "총계", "전체", "계", "총합계", "소계"}
VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


def default_year(today: date | None = None) -> int:
    """사업보고서는 보통 다음 해 3월 말까지 제출되므로 4월부터 전년도를 기본값으로 쓴다."""
    today = today or date.today()
    return today.year - 1 if today.month >= 4 else today.year - 2


def viewer_url(rcept_no) -> str | None:
    number = digits_only(rcept_no)
    return f"{VIEWER}{number}" if len(number) == 14 else None


def _norm(value) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _no_data(message: str) -> DartError:
    return DartError("013", message)


def _stem(company: Company | None, title: str) -> str:
    base = f"{company.corp_name}_{title}" if company else title
    return re.sub(r'[\\/:*?"<>|\s]+', "_", base)


@dataclass
class RunInput:
    company: Company | None = None
    companies: list[Company] = field(default_factory=list)
    year: int = 0  # 0이면 최신 공시를 자동으로 찾는다.
    report_code: str = "11011"
    months: int = 12
    days: int = 1
    market: str = ""
    disclosure_type: str = ""


class Api:
    """호출 내역을 기록해 '이 결과를 만든 API 호출'에 보여 준다."""

    def __init__(self, client: DartClient):
        self.client = client
        self.calls: list[str] = []
        self._responses: dict[tuple, object] = {}

    def _once(self, kind: str, endpoint: str, params: dict, fetch: Callable):
        """한 번의 조회 안에서 같은 요청을 반복하지 않아 호출 횟수를 아낀다."""
        key = (kind, endpoint, tuple(sorted(params.items())))
        if key not in self._responses:
            self.calls.append(describe_call(endpoint, params))
            self._responses[key] = fetch(endpoint, **params)
        return self._responses[key]

    def rows(self, endpoint: str, **params) -> list[dict]:
        return list(self._once("rows", endpoint, params, self.client.get_list))

    def obj(self, endpoint: str, **params) -> dict:
        return self._once("obj", endpoint, params, self.client.get_json)


# ---------------------------------------------------------------- 재무 공통
ACCOUNTS = {
    "매출액": ("매출액", "수익(매출액)", "영업수익", "매출", "보험영업수익", "순영업수익"),
    "영업이익": ("영업이익", "영업이익(손실)"),
    "당기순이익": ("당기순이익", "당기순이익(손실)", "연결당기순이익", "당기순손익"),
    "자산총계": ("자산총계",),
    "부채총계": ("부채총계",),
    "자본총계": ("자본총계",),
}


@dataclass
class Annual:
    years: list[int]
    fs_name: str
    values: dict[str, list[float | None]]
    rcept_no: str = ""

    def latest(self, name: str) -> float | None:
        return self.values[name][-1]

    def previous(self, name: str) -> float | None:
        return self.values[name][-2]


def _annual_from_rows(rows: list[dict], year: int) -> Annual:
    consolidated = [r for r in rows if r.get("fs_div") == "CFS"]
    separate = [r for r in rows if r.get("fs_div") == "OFS"]
    chosen = consolidated or separate or rows
    fs_name = "연결재무제표" if consolidated else "개별재무제표"
    values: dict[str, list[float | None]] = {}
    for name, aliases in ACCOUNTS.items():
        row = next((r for r in chosen if _norm(r.get("account_nm")) in aliases), None)
        values[name] = [
            to_number(row.get(key)) if row else None
            for key in ("bfefrmtrm_amount", "frmtrm_amount", "thstrm_amount")
        ]
    return Annual([year - 2, year - 1, year], fs_name, values, str(chosen[0].get("rcept_no", "")))


def _load_annual(api: Api, company: Company, year: int) -> Annual:
    rows = api.rows("fnlttSinglAcnt", corp_code=company.corp_code, bsns_year=str(year), reprt_code="11011")
    if not rows:
        raise _no_data(f"{company.corp_name}의 {year}년 사업보고서 재무 데이터가 없습니다. 기준 시점을 '최신 공시 자동 반영'이나 "
                       "이전 연도로 바꿔 보세요. 비상장 소규모 회사는 제공되지 않을 수 있습니다.")
    return _annual_from_rows(rows, year)


def _source_section(rcept_no, label: str = "공시 원문 열기") -> Section | None:
    url = viewer_url(rcept_no)
    if not url:
        return None
    return Section("원문 확인", table=Table(["구분", "바로가기"], [["DART 공시 뷰어", label]], links={(0, 1): url}, widths=[35, 65]))


def _with_source(sections: list[Section], rcept_no) -> list[Section]:
    source = _source_section(rcept_no)
    return sections + ([source] if source else [])


# ---------------------------------------------------------------- 기업 파악
def ex_profile(api: Api, inp: RunInput) -> Report:
    c = inp.company
    d = api.obj("company", corp_code=c.corp_code)
    if not d:
        raise _no_data("이 회사의 기업개황 정보가 없습니다.")
    market = CORP_CLS.get(d.get("corp_cls"), "-")
    home = str(d.get("hm_url") or "").strip()
    if home and not home.lower().startswith(("http://", "https://")):
        home = f"http://{home}"
    rows = [
        ["정식 회사명", text_or_dash(d.get("corp_name"))],
        ["영문 회사명", text_or_dash(d.get("corp_name_eng"))],
        ["종목코드", text_or_dash(d.get("stock_code"))],
        ["대표자", text_or_dash(d.get("ceo_nm"))],
        ["주소", text_or_dash(d.get("adres"))],
        ["전화번호", text_or_dash(d.get("phn_no"))],
        ["홈페이지", home or "-"],
        ["업종코드", text_or_dash(d.get("induty_code"))],
    ]
    links = {(6, 1): home} if home else {}
    return Report(
        f"{d.get('corp_name', c.corp_name)} 회사 프로필", "기업개황 API로 불러온 기본 정보",
        [f"{d.get('stock_name') or c.corp_name}은(는) {market}에 속한 회사이고, 대표자는 {text_or_dash(d.get('ceo_nm'))}입니다."],
        [Metric("시장", market), Metric("설립일", ymd(d.get("est_dt"))), Metric("결산월", f"{d.get('acc_mt', '-')}월")],
        [Section("기본 정보", table=Table(["항목", "내용"], rows, links=links, widths=[24, 76]))],
        ["결산월은 회사의 회계연도가 끝나는 달입니다. 12월 결산 회사의 사업보고서는 보통 다음 해 3월에 나옵니다.",
         "종목코드는 주식시장에서 쓰는 6자리 번호이고, DART 고유번호는 공시 시스템에서 쓰는 8자리 번호입니다."],
        file_stem=_stem(c, "회사프로필"),
    )


def _fetch_disclosures(api: Api, **params) -> tuple[list[dict], int]:
    """공시검색 결과를 마지막 페이지까지 모두 가져온다."""
    rows: list[dict] = []
    total, page = 0, 1
    while True:
        data = api.obj("list", page_no=str(page), page_count="100", **params)
        if not data:
            break
        rows.extend(data.get("list", []))
        total = int(data.get("total_count") or len(rows))
        if page >= int(data.get("total_page") or 1):
            break
        page += 1
    return rows, total


def _disclosure_table(rows: list[dict], with_company: bool) -> Table:
    table_rows, links = [], {}
    report_col = 2 if with_company else 1
    for index, row in enumerate(rows):
        cells = [ymd(row.get("rcept_dt"))]
        if with_company:
            cells.append(text_or_dash(row.get("corp_name")))
        cells += [text_or_dash(row.get("report_nm")).strip(), text_or_dash(row.get("flr_nm"))]
        table_rows.append(cells)
        url = viewer_url(row.get("rcept_no"))
        if url:
            links[(index, report_col)] = url
    columns = ["접수일"] + (["회사"] if with_company else []) + ["보고서명 (누르면 원문)", "제출인"]
    widths = [14, 20, 46, 20] if with_company else [15, 60, 25]
    return Table(columns, table_rows, links=links, widths=widths)


def ex_recent(api: Api, inp: RunInput) -> Report:
    c = inp.company
    end = date.today()
    begin = months_ago(end, inp.months)
    rows, total = _fetch_disclosures(api, corp_code=c.corp_code, bgn_de=begin.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"))
    if not rows:
        raise _no_data(f"최근 {inp.months}개월 동안 {c.corp_name}의 공시가 없습니다. 기간을 늘려 보세요.")
    by_month = Counter(digits_only(r.get("rcept_dt"))[:6] for r in rows)
    labels, values = [], []
    cursor = date(begin.year, begin.month, 1)
    while cursor <= end:
        key = cursor.strftime("%Y%m")
        labels.append(f"{cursor.year % 100}.{cursor.month:02d}")
        values.append(by_month.get(key, 0))
        cursor = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
    corrected = sum(1 for r in rows if "정정" in str(r.get("report_nm", "")))
    table = _disclosure_table(rows, False)
    return Report(
        f"{c.corp_name} 최근 공시 살펴보기", f"최근 {inp.months}개월 · {ymd(begin.strftime('%Y%m%d'))} ~ {ymd(end.strftime('%Y%m%d'))}",
        [f"최근 {inp.months}개월 동안 공시 {total:,}건이 나왔고, 가장 최근 공시는 {ymd(rows[0].get('rcept_dt'))}의 '{str(rows[0].get('report_nm', '')).strip()}'입니다."],
        [Metric("전체 공시", f"{total:,}건"), Metric("정정 공시", f"{corrected:,}건"), Metric("최근 공시일", ymd(rows[0].get("rcept_dt")))],
        [Section("월별 공시 건수", chart=charts.month_columns("월별 공시 건수", labels, values)), Section(f"공시 목록 (전체 {len(rows):,}건)", table=table)],
        ["공시 건수가 많다고 좋거나 나쁜 것은 아닙니다. 대기업은 계열사 거래 등으로 공시가 많은 편입니다.",
         "보고서명 앞의 [기재정정]은 이미 낸 보고서의 내용을 고쳐 다시 제출했다는 뜻입니다."],
        file_stem=_stem(c, "최근공시"),
    )


def ex_market(api: Api, inp: RunInput) -> Report:
    end = date.today()
    begin = end - timedelta(days=max(inp.days - 1, 0))
    rows, total = _fetch_disclosures(
        api, bgn_de=begin.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"), corp_cls=inp.market, pblntf_ty=inp.disclosure_type,
    )
    if not rows:
        raise _no_data("이 기간에는 조건에 맞는 공시가 없습니다. 주말이나 휴일이라면 기간을 '최근 7일'로 늘려 보세요.")
    top = Counter(str(r.get("corp_name", "-")) for r in rows).most_common(8)
    table = _disclosure_table(rows, True)
    market = CORP_CLS.get(inp.market, "전체 시장")
    return Report(
        "오늘의 시장 공시", f"{market} · {ymd(begin.strftime('%Y%m%d'))} ~ {ymd(end.strftime('%Y%m%d'))}",
        [f"선택한 기간에 공시 {total:,}건이 접수되었고, 공시가 가장 많은 회사는 {top[0][0]}({top[0][1]:,}건)입니다."],
        [Metric("전체 공시", f"{total:,}건"), Metric("공시 회사 수", f"{len({r.get('corp_code') for r in rows}):,}곳"), Metric("최근 접수일", ymd(rows[0].get("rcept_dt")))],
        [Section("공시가 많은 회사", chart=charts.bar_rows("회사별 공시 건수 상위 8곳", [n for n, _ in top], [v for _, v in top], lambda v: f"{v:,.0f}건")),
         Section(f"공시 목록 (전체 {len(rows):,}건)", table=table)],
        ["공시는 평일 업무 시간에 주로 접수되고, 장 마감 뒤에 몰리는 경우도 많습니다.",
         "회사 고유번호 없이 전체를 조회할 때 OpenDART의 검색 기간은 3개월로 제한됩니다."],
        file_stem=_stem(None, "오늘의시장공시"),
    )


# ---------------------------------------------------------------- 재무
def ex_performance(api: Api, inp: RunInput) -> Report:
    c = inp.company
    a = _load_annual(api, c, inp.year)
    labels = [str(y) for y in a.years]
    names = ["매출액", "영업이익", "당기순이익"]
    chart = charts.column_panels([(n, labels, a.values[n]) for n in names], compact_won)
    metrics = [Metric(f"{inp.year}년 {n}", won(a.latest(n)), change_text(a.latest(n), a.previous(n))) for n in names]
    rows = [[n] + [won(v) for v in a.values[n]] for n in names]
    return Report(
        f"{c.corp_name} 3년 실적", f"{inp.year}년 사업보고서 · {a.fs_name} 기준",
        [f"{inp.year}년 매출은 {won(a.latest('매출액'))}으로 {change_text(a.latest('매출액'), a.previous('매출액'))}했고, "
         f"영업이익은 {won(a.latest('영업이익'))}입니다.".replace("전년 비교 불가했고", "전년 비교가 어렵고")],
        metrics,
        _with_source([Section("3년 추이", chart=chart), Section("숫자로 보기", table=Table(["구분"] + [f"{y}년" for y in a.years], rows, right={1, 2, 3}, widths=[28, 24, 24, 24]))], a.rcept_no),
        ["매출액은 물건이나 서비스를 팔아 벌어들인 총액입니다.", "영업이익은 매출에서 원가와 판매·관리비를 뺀, 본업으로 번 돈입니다.",
         "당기순이익은 이자, 세금 등까지 모두 반영한 최종 이익입니다."],
        file_stem=_stem(c, "3년실적"),
    )


def ex_health(api: Api, inp: RunInput) -> Report:
    c = inp.company
    a = _load_annual(api, c, inp.year)
    indicators = {
        "부채비율": [ratio(l, e) for l, e in zip(a.values["부채총계"], a.values["자본총계"])],
        "자기자본비율": [ratio(e, t) for e, t in zip(a.values["자본총계"], a.values["자산총계"])],
        "영업이익률": [ratio(o, r) for o, r in zip(a.values["영업이익"], a.values["매출액"])],
        "순이익률": [ratio(n, r) for n, r in zip(a.values["당기순이익"], a.values["매출액"])],
        "ROE(자기자본이익률)": [ratio(n, e) for n, e in zip(a.values["당기순이익"], a.values["자본총계"])],
    }
    formulas = {
        "부채비율": "부채총계 ÷ 자본총계", "자기자본비율": "자본총계 ÷ 자산총계", "영업이익률": "영업이익 ÷ 매출액",
        "순이익률": "당기순이익 ÷ 매출액", "ROE(자기자본이익률)": "당기순이익 ÷ 기말 자본총계",
    }
    rows = [[n, formulas[n]] + [percent(v) for v in vals] for n, vals in indicators.items()]
    labels = [str(y) for y in a.years]
    chart = charts.column_panels(
        [("부채비율", labels, indicators["부채비율"]), ("영업이익률", labels, indicators["영업이익률"]), ("ROE", labels, indicators["ROE(자기자본이익률)"])],
        lambda v: percent(v),
    )
    return Report(
        f"{c.corp_name} 재무 건강검진", f"{inp.year}년 사업보고서 · {a.fs_name} 기준 · 공시 금액으로 직접 계산",
        [f"{inp.year}년 부채비율은 {percent(indicators['부채비율'][-1])}, 영업이익률은 {percent(indicators['영업이익률'][-1])}입니다."],
        [Metric("부채비율", percent(indicators["부채비율"][-1]), "빚 ÷ 자기 돈"), Metric("영업이익률", percent(indicators["영업이익률"][-1]), "매출 중 본업 이익"),
         Metric("ROE", percent(indicators["ROE(자기자본이익률)"][-1]), "자기 돈으로 번 이익")],
        _with_source([Section("3년 추이", chart=chart),
                      Section("지표 계산표", table=Table(["지표", "계산식"] + [f"{y}년" for y in a.years], rows, right={2, 3, 4}, widths=[24, 28, 16, 16, 16]))], a.rcept_no),
        ["적정 수준은 업종마다 크게 다릅니다. 은행이나 보험사는 구조적으로 부채비율이 높으므로 같은 업종끼리 비교하세요.",
         "한 해의 숫자보다 여러 해의 방향을 함께 보는 편이 안전합니다.", "이 앱의 ROE는 이해하기 쉽도록 평균이 아닌 기말 자본으로 계산했습니다."],
        file_stem=_stem(c, "재무건강검진"),
    )


def ex_compare(api: Api, inp: RunInput) -> Report:
    companies = inp.companies
    codes = ",".join(x.corp_code for x in companies)
    rows = api.rows("fnlttMultiAcnt", corp_code=codes, bsns_year=str(inp.year), reprt_code="11011")
    if not rows:
        raise _no_data(f"{inp.year}년 사업보고서 재무 데이터가 없습니다. 이전 연도로 바꿔 보세요.")
    by_key: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_key[str(row.get("corp_code") or row.get("stock_code") or "").strip()].append(row)
    table_rows, names, series = [], [], {"매출액": [], "영업이익": [], "당기순이익": []}
    missing = []
    for company in companies:
        own = by_key.get(company.corp_code) or by_key.get(company.stock_code)
        if not own:
            missing.append(company.corp_name)
            continue
        a = _annual_from_rows(own, inp.year)
        names.append(company.corp_name)
        for name in series:
            series[name].append(a.latest(name))
        table_rows.append([
            company.corp_name, won(a.latest("매출액")), won(a.latest("영업이익")), won(a.latest("당기순이익")),
            percent(ratio(a.latest("영업이익"), a.latest("매출액"))), percent(ratio(a.latest("부채총계"), a.latest("자본총계"))),
        ])
    if not names:
        raise _no_data("선택한 회사들의 재무 데이터를 찾지 못했습니다.")
    short = [n if len(n) <= 6 else f"{n[:5]}…" for n in names]
    best = max(((v, n) for v, n in zip(series["매출액"], names) if v is not None), default=(None, "-"))
    note = f"데이터가 없어 제외한 회사: {', '.join(missing)}" if missing else ""
    return Report(
        "라이벌 회사 비교", f"{inp.year}년 사업보고서 · 회사별 연결재무제표 우선",
        [f"비교한 {len(names)}개 회사 중 {inp.year}년 매출이 가장 큰 곳은 {best[1]}({won(best[0])})입니다."],
        [Metric("비교 회사", f"{len(names)}곳"), Metric("기준 연도", f"{inp.year}년"), Metric("매출 1위", best[1])],
        [Section("규모 비교", chart=charts.column_panels([(n, short, series[n]) for n in series], compact_won)),
         Section("비교표", table=Table(["회사", "매출액", "영업이익", "순이익", "영업이익률", "부채비율"], table_rows, right={1, 2, 3, 4, 5}, widths=[24, 17, 16, 16, 14, 13], note=note))],
        ["같은 업종끼리 비교해야 의미가 있습니다.", "회사마다 연결과 개별 재무제표 기준이 다를 수 있습니다.", "규모가 달라도 영업이익률과 부채비율은 비율이라 비교하기 쉽습니다."],
        file_stem=_stem(None, f"회사비교_{inp.year}"),
    )


def ex_indicators(api: Api, inp: RunInput) -> Report:
    c = inp.company
    groups = [("수익성", "M210000", "얼마나 효율적으로 돈을 버는가"), ("안정성", "M220000", "빚을 감당할 체력이 있는가"),
              ("성장성", "M230000", "회사가 커지고 있는가"), ("활동성", "M240000", "자산을 얼마나 활발히 쓰는가")]
    sections, total = [], 0
    for name, code, description in groups:
        rows = api.rows("fnlttSinglIndx", corp_code=c.corp_code, bsns_year=str(inp.year), reprt_code=inp.report_code, idx_cl_code=code)
        shown = [[text_or_dash(r.get("idx_nm")), text_or_dash(r.get("idx_val"))] for r in rows]
        total += len(shown)
        if shown:
            sections.append(Section(f"{name} 지표", [description], table=Table(["지표명", "공시 시스템 제공값"], shown, right={1}, widths=[65, 35])))
    if not sections:
        raise _no_data("이 조건의 재무지표가 없습니다. 재무지표는 2023년 3분기 이후 자료부터 제공됩니다.")
    return Report(
        f"{c.corp_name} 공식 재무지표", f"{inp.year}년 · 보고서 코드 {inp.report_code}",
        [f"OpenDART가 제공하는 재무지표 {total}개를 수익성, 안정성, 성장성, 활동성으로 나눠 정리했습니다."],
        [Metric("지표 수", f"{total}개"), Metric("분류", f"{len(sections)}개"), Metric("기준 연도", f"{inp.year}년")],
        sections,
        ["값은 OpenDART가 제공한 그대로 표시했습니다. 지표별 단위는 원문을 함께 확인하세요.", "이 API는 2023년 3분기 이후 자료만 제공합니다."],
        file_stem=_stem(c, "공식재무지표"),
    )


# ---------------------------------------------------------------- 주주·배당
def ex_dividend(api: Api, inp: RunInput) -> Report:
    c = inp.company
    rows = api.rows("alotMatter", corp_code=c.corp_code, bsns_year=str(inp.year), reprt_code=inp.report_code)
    partial = inp.report_code != "11011"
    if not rows:
        raise _no_data(f"{inp.year}년 배당 정보가 없습니다. 이전 연도로 바꿔 보세요.")

    def pick(word: str, common_only: bool = True) -> dict | None:
        for row in rows:
            if word in _norm(row.get("se")) and (not common_only or "보통" in str(row.get("stock_knd", ""))):
                return row
        return next((r for r in rows if word in _norm(r.get("se"))), None)

    keys = ("lwfr", "frmtrm", "thstrm")
    per_share = pick("주당현금배당금")
    yield_row = pick("현금배당수익률")
    payout = pick("현금배당성향", common_only=False)
    amounts = [to_number(per_share.get(k)) if per_share else None for k in keys]
    current_label = f"{inp.year} {PERIOD_NAMES[inp.report_code]}" if partial else str(inp.year)
    labels = [str(inp.year - 2), str(inp.year - 1), current_label]
    latest = amounts[-1]
    table_rows = [[text_or_dash(r.get("se")), text_or_dash(r.get("stock_knd")), text_or_dash(r.get("lwfr")), text_or_dash(r.get("frmtrm")), text_or_dash(r.get("thstrm"))] for r in rows]
    period_text = f"{inp.year}년 {PERIOD_NAMES[inp.report_code]}까지" if partial else f"{inp.year}년"
    summary = f"{period_text} 보통주 1주당 현금배당은 {count(latest, '원')}입니다." if latest else f"{period_text} 보통주 현금배당이 확인되지 않습니다."
    sections = []
    if any(v is not None for v in amounts):
        sections.append(Section("주당 배당금 추이", chart=charts.column_panels([("보통주 1주당 현금배당", labels, amounts)], lambda v: count(v, "원"))))
    current_header = f"{labels[2]} 누적" if partial else f"{labels[2]}년"
    sections.append(Section("배당 상세", table=Table(["항목", "주식", f"{labels[0]}년", f"{labels[1]}년", current_header], table_rows, right={2, 3, 4}, widths=[36, 12, 16, 16, 20])))
    return Report(
        f"{c.corp_name} 배당 내역", f"{inp.year}년 사업보고서 기준", [summary],
        [Metric("주당 현금배당", count(latest, "원"), "연간 확정 전 누적" if partial else change_text(amounts[-1], amounts[-2])),
         Metric("현금배당수익률", percent(to_number(yield_row.get("thstrm")), 2) if yield_row else "-", "주가 대비 배당"),
         Metric("현금배당성향", percent(to_number(payout.get("thstrm"))) if payout else "-", "이익 중 배당 비율")],
        _with_source(sections, rows[0].get("rcept_no")),
        ["배당수익률은 주가에 따라 달라지므로 과거 값이 앞으로도 유지된다는 뜻은 아닙니다.", "배당성향이 지나치게 높으면 이익보다 많이 나눠 준 해일 수 있습니다."],
        file_stem=_stem(c, "배당내역"),
    )


def ex_holders(api: Api, inp: RunInput) -> Report:
    c = inp.company
    rows = api.rows("hyslrSttus", corp_code=c.corp_code, bsns_year=str(inp.year), reprt_code=inp.report_code)
    people = [r for r in rows if _norm(r.get("nm")) not in TOTAL_WORDS]
    if not people:
        raise _no_data(f"{inp.year}년 최대주주 정보가 없습니다. 이전 연도로 바꿔 보세요.")
    common = [r for r in people if "보통" in str(r.get("stock_knd", ""))] or people
    common.sort(key=lambda r: to_number(r.get("trmend_posesn_stock_qota_rt")) or 0, reverse=True)
    total_rate = sum(to_number(r.get("trmend_posesn_stock_qota_rt")) or 0 for r in common)
    top = common[:10]
    chart = charts.bar_rows("보통주 지분율 상위", [str(r.get("nm", "-")) for r in top],
                            [to_number(r.get("trmend_posesn_stock_qota_rt")) or 0 for r in top], lambda v: f"{v:.2f}%")
    table = [[text_or_dash(r.get("nm")), text_or_dash(r.get("relate")), text_or_dash(r.get("stock_knd")),
              count(to_number(r.get("trmend_posesn_stock_co")), "주"), percent(to_number(r.get("trmend_posesn_stock_qota_rt")), 2)] for r in common]
    return Report(
        f"{c.corp_name} 최대주주", f"{inp.year}년 · 기말 보유 기준",
        [f"최대주주와 특수관계인의 보통주 지분은 합계 {total_rate:.2f}%이고, 가장 많이 가진 주주는 {top[0].get('nm')}입니다."],
        [Metric("지분 합계", percent(total_rate, 2), "보통주, 특수관계인 포함"), Metric("1위 주주", str(top[0].get("nm", "-"))), Metric("명단 인원", f"{len({r.get('nm') for r in people})}명")],
        _with_source([Section("지분율", chart=chart), Section("주주 명단", table=Table(["이름", "관계", "주식", "보유 주식", "지분율"], table, right={3, 4}, widths=[26, 22, 14, 22, 16]))], rows[0].get("rcept_no")),
        ["특수관계인은 최대주주의 가족이나 계열사처럼 가까운 관계에 있는 주주입니다.", "지분이 높으면 경영권이 안정적일 수 있지만, 그 자체가 좋고 나쁨을 뜻하지는 않습니다."],
        file_stem=_stem(c, "최대주주"),
    )


def ex_major(api: Api, inp: RunInput) -> Report:
    c = inp.company
    rows = api.rows("majorstock", corp_code=c.corp_code)
    if not rows:
        raise _no_data("이 회사의 5% 이상 대량보유 보고가 없습니다.")
    rows.sort(key=lambda r: digits_only(r.get("rcept_dt")), reverse=True)
    table_rows, links = [], {}
    for index, r in enumerate(rows):
        table_rows.append([ymd(r.get("rcept_dt")), text_or_dash(r.get("repror")), f"{text_or_dash(r.get('stkrt'))}%", text_or_dash(r.get("stkrt_irds")), text_or_dash(r.get("report_resn"))])
        url = viewer_url(r.get("rcept_no"))
        if url:
            links[(index, 1)] = url
    return Report(
        f"{c.corp_name} 5% 이상 큰손", "대량보유 상황보고 전체 이력",
        [f"지금까지 대량보유 보고가 {len(rows):,}건 있었고, 가장 최근 보고자는 {rows[0].get('repror')}({ymd(rows[0].get('rcept_dt'))})입니다."],
        [Metric("보고 건수", f"{len(rows):,}건"), Metric("보고자 수", f"{len({r.get('repror') for r in rows}):,}곳"), Metric("최근 보고일", ymd(rows[0].get("rcept_dt")))],
        [Section(f"전체 보고 ({len(rows):,}건)", table=Table(["접수일", "보고자 (누르면 원문)", "보유비율", "증감", "보고 사유"], table_rows, links=links, right={2, 3}, widths=[14, 28, 12, 10, 36]))],
        ["상장사 지분을 5% 이상 보유하면 보고해야 하고, 이후 1% 이상 변하면 다시 보고합니다.", "국민연금이나 자산운용사처럼 기관 투자자가 자주 등장합니다."],
        file_stem=_stem(c, "대량보유"),
    )


def ex_insider(api: Api, inp: RunInput) -> Report:
    c = inp.company
    rows = api.rows("elestock", corp_code=c.corp_code)
    since = months_ago(date.today(), inp.months).strftime("%Y%m%d")
    recent = [r for r in rows if digits_only(r.get("rcept_dt"))[:8] >= since]
    if not recent:
        raise _no_data(f"최근 {inp.months}개월 동안 임원·주요주주 소유 보고가 없습니다. 기간을 늘려 보세요.")
    recent.sort(key=lambda r: digits_only(r.get("rcept_dt")), reverse=True)
    changes = [to_number(r.get("sp_stock_lmp_irds_cnt")) or 0 for r in recent]
    table_rows, links = [], {}
    for index, r in enumerate(recent):
        table_rows.append([ymd(r.get("rcept_dt")), text_or_dash(r.get("repror")), text_or_dash(r.get("isu_exctv_ofcps")),
                           signed(to_number(r.get("sp_stock_lmp_irds_cnt")), "주"), count(to_number(r.get("sp_stock_lmp_cnt")), "주")])
        url = viewer_url(r.get("rcept_no"))
        if url:
            links[(index, 1)] = url
    increased, decreased = sum(v > 0 for v in changes), sum(v < 0 for v in changes)
    return Report(
        f"{c.corp_name} 내부자 지분 변동", f"최근 {inp.months}개월 임원·주요주주 소유 보고",
        [f"최근 {inp.months}개월간 보고 {len(recent):,}건 중 보유 증가 {increased:,}건, 감소 {decreased:,}건입니다."],
        [Metric("보고 건수", f"{len(recent):,}건"), Metric("증가 / 감소", f"{increased:,} / {decreased:,}건"), Metric("합산 증감", signed(sum(changes), "주"), "보고 수량 단순 합계")],
        [Section(f"전체 보고 ({len(recent):,}건)", table=Table(["접수일", "보고자 (누르면 원문)", "직위", "증감", "보고 후 보유"], table_rows, links=links, right={3, 4}, widths=[14, 28, 18, 18, 22]))],
        ["증감은 장내 매매뿐 아니라 상속, 증여, 주식보상 때문에도 생깁니다. 이유는 원문에서 확인하세요.", "감소가 곧 회사 전망이 나쁘다는 뜻은 아닙니다. 세금이나 개인 사정일 수 있습니다."],
        file_stem=_stem(c, "내부자지분"),
    )


# ---------------------------------------------------------------- 사람
def ex_employees(api: Api, inp: RunInput) -> Report:
    c = inp.company
    rows = api.rows("empSttus", corp_code=c.corp_code, bsns_year=str(inp.year), reprt_code=inp.report_code)
    if not rows:
        raise _no_data(f"{inp.year}년 직원 현황이 없습니다. 이전 연도로 바꿔 보세요.")
    details = [r for r in rows if _norm(r.get("fo_bbm")) not in TOTAL_WORDS and _norm(r.get("sexdstn")) in ("남", "여")]
    totals = [r for r in rows if _norm(r.get("fo_bbm")) in TOTAL_WORDS or _norm(r.get("sexdstn")) in TOTAL_WORDS]
    base = details or totals or rows
    headcount = sum(to_number(r.get("sm")) or 0 for r in base)
    male = sum(to_number(r.get("sm")) or 0 for r in details if _norm(r.get("sexdstn")) == "남")
    female = sum(to_number(r.get("sm")) or 0 for r in details if _norm(r.get("sexdstn")) == "여")
    paid = [(to_number(r.get("sm")), to_number(r.get("jan_salary_am"))) for r in base]
    paid = [(n, s) for n, s in paid if n and s]
    avg_pay = sum(n * s for n, s in paid) / sum(n for n, _ in paid) if paid else None
    regular = sum(to_number(r.get("rgllbr_co")) or 0 for r in base)
    by_unit: Counter = Counter()
    for row in details:
        by_unit[text_or_dash(row.get("fo_bbm"))] += to_number(row.get("sm")) or 0
    top_units = by_unit.most_common(8)
    sections = []
    if len(top_units) > 1:
        sections.append(Section("사업부문별 인원", chart=charts.bar_rows("사업부문별 직원 수 상위 8개" if len(by_unit) > 8 else "사업부문별 직원 수", [n for n, _ in top_units], [v for _, v in top_units], lambda v: f"{v:,.0f}명")))
    table = [[text_or_dash(r.get("fo_bbm")), text_or_dash(r.get("sexdstn")), count(to_number(r.get("rgllbr_co"))), count(to_number(r.get("cnttk_co"))), count(to_number(r.get("sm"))),
              text_or_dash(r.get("avrg_cnwk_sdytrn")), count(to_number(r.get("jan_salary_am")))] for r in rows]
    sections.append(Section("상세", table=Table(["부문", "성별", "정규직", "계약직", "합계", "평균 근속", "1인 평균 급여"], table, right={2, 3, 4, 5, 6}, widths=[24, 8, 13, 12, 13, 13, 17],
                                               note="급여 단위는 회사가 공시한 기준(원, 천원, 백만원 등)을 따르므로 원문을 함께 확인하세요.")))
    return Report(
        f"{c.corp_name} 직원과 급여", f"{inp.year}년 · 회사 공시 기준",
        [f"직원은 약 {headcount:,.0f}명이고, 이 중 정규직 비율은 {percent(ratio(regular, headcount))}입니다."],
        [Metric("전체 직원", count(headcount, "명")), Metric("남 / 여", f"{male:,.0f} / {female:,.0f}" if details else "-"), Metric("1인 평균 급여", count(avg_pay), "인원 가중 평균, 공시 단위")],
        _with_source(sections, rows[0].get("rcept_no")),
        ["평균 급여는 사업부문과 직급 구성에 따라 크게 달라집니다.", "기간제 등 고용 형태의 표기는 회사마다 조금씩 다를 수 있습니다."],
        file_stem=_stem(c, "직원급여"),
    )


def ex_executives(api: Api, inp: RunInput) -> Report:
    c = inp.company
    rows = api.rows("exctvSttus", corp_code=c.corp_code, bsns_year=str(inp.year), reprt_code=inp.report_code)
    if not rows:
        raise _no_data(f"{inp.year}년 임원 현황이 없습니다. 이전 연도로 바꿔 보세요.")
    registered = [r for r in rows if "미등기" not in str(r.get("rgist_exctv_at", "")) and str(r.get("rgist_exctv_at", "")).strip()]
    outside = [r for r in registered if "사외" in str(r.get("rgist_exctv_at", ""))]
    women = sum(1 for r in rows if _norm(r.get("sexdstn")) == "여")
    ordered = registered + [r for r in rows if r not in registered]
    table = [[text_or_dash(r.get("nm")), text_or_dash(r.get("ofcps")), text_or_dash(r.get("rgist_exctv_at")), text_or_dash(r.get("fte_at")),
              text_or_dash(r.get("chrg_job")), text_or_dash(r.get("tenure_end_on"))] for r in ordered]
    return Report(
        f"{c.corp_name} 임원 현황", f"{inp.year}년 · 회사 공시 기준",
        [f"임원은 총 {len(rows)}명이고, 이사회 구성원인 등기임원은 {len(registered)}명, 그중 사외이사는 {len(outside)}명입니다."],
        [Metric("전체 임원", f"{len(rows)}명"), Metric("등기임원", f"{len(registered)}명", f"사외이사 {len(outside)}명"), Metric("여성 임원", f"{women}명", percent(ratio(women, len(rows))))],
        _with_source([Section(f"임원 명단 (전체 {len(rows):,}명, 등기임원 먼저)", table=Table(["이름", "직위", "등기 여부", "상근", "담당", "임기 만료"], table, widths=[13, 16, 15, 9, 33, 14]))], rows[0].get("rcept_no")),
        ["등기임원은 법인 등기부에 오른 이사로 이사회 의결에 참여합니다.", "사외이사는 회사 밖 전문가로서 경영진을 견제하는 역할을 합니다."],
        file_stem=_stem(c, "임원현황"),
    )


# ---------------------------------------------------------------- 위험 신호
def ex_audit(api: Api, inp: RunInput) -> Report:
    c = inp.company
    rows = api.rows("accnutAdtorNmNdAdtOpinion", corp_code=c.corp_code, bsns_year=str(inp.year), reprt_code="11011")
    if not rows:
        raise _no_data(f"{inp.year}년 감사의견 정보가 없습니다. 이전 연도로 바꿔 보세요.")
    with_opinion = [r for r in rows if str(r.get("adt_opinion", "")).strip() not in ("", "-")]
    current = with_opinion[0] if with_opinion else rows[0]
    opinion = text_or_dash(current.get("adt_opinion"))
    concerning = [r for r in with_opinion if any(w in str(r.get("adt_opinion")) for w in ("부적정", "한정", "거절"))]
    table = [[text_or_dash(r.get("bsns_year")), text_or_dash(r.get("adtor")), text_or_dash(r.get("adt_opinion")), text_or_dash(r.get("emphs_matter")), text_or_dash(r.get("core_adt_matter"))] for r in rows]
    summary = (f"표시된 감사의견 중 {len(concerning)}건이 '적정'이 아닙니다. 원문에서 사유를 꼭 확인해 보세요." if concerning
               else f"가장 최근 감사의견은 '{opinion}'이고, 감사인은 {text_or_dash(current.get('adtor'))}입니다.")
    return Report(
        f"{c.corp_name} 감사의견", f"{inp.year}년 사업보고서 기준", [summary],
        [Metric("감사의견", opinion), Metric("감사인", text_or_dash(current.get("adtor"))), Metric("주의 의견", f"{len(concerning)}건", "한정·부적정·의견거절")],
        _with_source([Section("연도별 감사 내용", table=Table(["사업연도", "감사인", "의견", "강조사항", "핵심감사사항"], table, widths=[16, 18, 12, 27, 27]))], rows[0].get("rcept_no")),
        ["적정: 재무제표가 회계기준에 맞게 작성되었다는 의견입니다. 회사가 좋다는 보증은 아닙니다.",
         "한정·부적정·의견거절: 재무제표를 믿기 어려운 부분이 있다는 뜻이며, 상장 유지에도 영향을 줄 수 있습니다."],
        file_stem=_stem(c, "감사의견"),
    )


EVENT_GROUPS = [
    ("위기·법적 사건", [("부도 발생", "dfOcr"), ("영업정지", "bsnSp"), ("회생절차 개시신청", "ctrcvsBgrq"), ("해산 사유 발생", "dsRsOcr"),
                   ("채권은행 관리절차 개시", "bnkMngtPcbg"), ("소송 등의 제기", "lwstLg")]),
    ("자금 조달", [("유상증자", "piicDecsn"), ("전환사채", "cvbdIsDecsn"), ("신주인수권부사채", "bdwtIsDecsn"), ("교환사채", "exbdIsDecsn")]),
    ("자본·구조 변화", [("감자", "crDecsn"), ("무상증자", "fricDecsn"), ("자기주식 취득", "tsstkAqDecsn"), ("회사합병", "cmpMgDecsn"), ("회사분할", "cmpDvDecsn")]),
]


def ex_events(api: Api, inp: RunInput) -> Report:
    c = inp.company
    end = date.today()
    begin = months_ago(end, inp.months)
    table, links, group_counts = [], {}, Counter()
    for group, items in EVENT_GROUPS:
        for label, endpoint in items:
            rows = api.rows(endpoint, corp_code=c.corp_code, bgn_de=begin.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"))
            rows.sort(key=lambda r: digits_only(r.get("rcept_no")), reverse=True)
            group_counts[group] += len(rows)
            latest = ymd(digits_only(rows[0].get("rcept_no"))[:8]) if rows else "-"
            table.append([group, label, f"{len(rows)}건" if rows else "없음", latest, "원문 보기" if rows else "-"])
            url = viewer_url(rows[0].get("rcept_no")) if rows else None
            if url:
                links[(len(table) - 1, 4)] = url
    total = sum(group_counts.values())
    crisis = group_counts["위기·법적 사건"]
    summary = (f"최근 {inp.months}개월 동안 위기·법적 사건 공시가 {crisis}건 있습니다. 원문에서 내용을 확인해 보세요." if crisis
               else f"최근 {inp.months}개월 동안 점검한 위기·법적 사건 공시는 없습니다.")
    return Report(
        f"{c.corp_name} 주요 사건 점검", f"최근 {inp.months}개월 · 주요사항보고서 {sum(len(i) for _, i in EVENT_GROUPS)}종 조회",
        [summary, f"점검 항목 전체에서 공시 {total}건이 확인되었습니다."],
        [Metric("위기·법적 사건", f"{crisis}건"), Metric("자금 조달", f"{group_counts['자금 조달']}건"), Metric("자본·구조 변화", f"{group_counts['자본·구조 변화']}건")],
        [Section("항목별 결과", table=Table(["분류", "사건", "건수", "최근 공시", "바로가기"], table, links=links, widths=[22, 30, 12, 18, 18]))],
        ["'없음'은 조회 기간과 항목에 한해 공시가 없다는 뜻이며, 회사에 위험이 없다는 보증이 아닙니다.",
         "유상증자나 전환사채는 성장 투자에도 쓰입니다. 자금의 사용 목적을 원문에서 확인하세요."],
        file_stem=_stem(c, "주요사건점검"),
    )


# ---------------------------------------------------------------- 최신 공시 자동 반영
IS_NAMES = ("매출액", "영업이익", "당기순이익")
PARTIAL_TIP = "분기·반기 보고서의 손익, 급여, 배당은 해당 시점까지의 누적이므로 연간 금액과 직접 비교하면 안 됩니다."
LTM_TIP = "최근 12개월은 이번 누적에 직전 연간 실적을 더하고 작년 같은 기간 누적을 뺀 값으로, 연간 실적과 같은 12개월 기준으로 비교할 수 있습니다."


@dataclass
class PeriodFinancials:
    filing: Filing
    fs_name: str
    current: dict[str, float | None]
    prior_same: dict[str, float | None]
    annual: Annual | None = None
    ltm: dict[str, float | None] = field(default_factory=dict)


def _period_from_rows(rows: list[dict], filing: Filing) -> PeriodFinancials:
    consolidated = [r for r in rows if r.get("fs_div") == "CFS"]
    separate = [r for r in rows if r.get("fs_div") == "OFS"]
    chosen = consolidated or separate or rows
    plain_is_cumulative = filing.reprt_code in ("11011", "11013")
    current, prior = {}, {}
    for name, aliases in ACCOUNTS.items():
        row = next((r for r in chosen if _norm(r.get("account_nm")) in aliases), None)
        if not row:
            current[name], prior[name] = None, None
        elif name in IS_NAMES:
            # 손익의 thstrm_amount는 분기 3개월치일 수 있으므로 누적 필드를 우선한다.
            current[name] = to_number(row.get("thstrm_add_amount"))
            prior[name] = to_number(row.get("frmtrm_add_amount"))
            if plain_is_cumulative:
                current[name] = current[name] if current[name] is not None else to_number(row.get("thstrm_amount"))
                prior[name] = prior[name] if prior[name] is not None else to_number(row.get("frmtrm_amount"))
        else:
            current[name], prior[name] = to_number(row.get("thstrm_amount")), to_number(row.get("frmtrm_amount"))
    return PeriodFinancials(filing, "연결재무제표" if consolidated else "개별재무제표", current, prior)


def _load_latest(api: Api, company: Company) -> tuple[PeriodFinancials, list[Filing]]:
    filings = find_filings(api, company)
    rows, filing, skipped = first_with_data(api, company, filings, "fnlttSinglAcnt")
    if not rows or not filing:
        raise _no_data(f"{company.corp_name}의 최근 정기보고서 재무 데이터를 찾지 못했습니다. 비상장 소규모 회사는 제공되지 않을 수 있습니다.")
    data = _period_from_rows(rows, filing)
    if filing.is_annual:
        data.annual = _annual_from_rows(rows, filing.bsns_year)
        return data, skipped
    previous = next((f for f in filings if f.is_annual and 0 < months_between(f.period_end, filing.period_end) < 12), None)
    if previous:
        annual_rows, resolved = rows_for(api, company, previous, "fnlttSinglAcnt")
        if annual_rows:
            data.annual = _annual_from_rows(annual_rows, resolved.bsns_year)
            if data.annual.fs_name == data.fs_name:
                for name in IS_NAMES:
                    now, year, same = data.current[name], data.annual.latest(name), data.prior_same[name]
                    data.ltm[name] = None if None in (now, year, same) else now + year - same
    return data, skipped


def _attach_basis(api: Api, report: Report, company: Company, filing: Filing, skipped: list[Filing]) -> Report:
    """보고서 앞에 기준 보고서를, 뒤에 그 이후 나온 공시를 붙인다."""
    today = date.today().strftime("%Y%m%d")
    disclosures, _ = _fetch_disclosures(api, corp_code=company.corp_code, bgn_de=filing.rcept_dt, end_de=today, last_reprt_at="Y")
    newer = [r for r in disclosures if digits_only(r.get("rcept_no")) > filing.rcept_no]
    provisional = [r for r in newer if "잠정" in str(r.get("report_nm", ""))]
    rows = [["기준 보고서", f"{filing.label} (누르면 원문)"], ["공시 접수일", ymd(filing.rcept_dt)],
            ["자료 기준 시점", filing.period_text], ["이후 나온 공시", f"{len(newer):,}건"]]
    url = viewer_url(filing.rcept_no)
    note = ""
    if skipped:
        names = ", ".join(f.label for f in skipped)
        note = f"{names}에는 이 항목이 없거나 API 제공 전이라 그 직전 보고서를 사용했습니다."
    basis = Section("최신 공시 반영 현황", table=Table(["항목", "내용"], rows, links={(0, 1): url} if url else {}, widths=[28, 72], note=note))
    report.subtitle = f"{filing.label} 기준 · {ymd(filing.rcept_dt)} 공시 · 최신 공시 자동 반영"
    report.sections = [basis] + [s for s in report.sections if s.title != "원문 확인"]
    if provisional:
        report.summary.append(f"기준 보고서 이후 잠정실적 공시가 {len(provisional)}건 있습니다. 확정 전 수치라 표에 넣지 않았으니 원문을 확인해 주세요.")
    if newer:
        report.sections.append(Section("기준 보고서 이후 나온 공시", table=_disclosure_table(newer, False)))
    if not filing.is_annual and PARTIAL_TIP not in report.tips:
        report.tips.insert(0, PARTIAL_TIP)
    return report


def _auto(function: Callable[[Api, RunInput], Report], endpoint: str, annual_only: bool = False, **extra) -> Callable[[Api, RunInput], Report]:
    """연도를 고르지 않으면 해당 항목이 들어 있는 가장 최근 보고서로 예제를 실행한다."""
    def run(api: Api, inp: RunInput) -> Report:
        if inp.year:
            return function(api, inp)
        company = inp.company
        filings = find_filings(api, company)
        if annual_only:
            filings = [f for f in filings if f.is_annual]
        rows, filing, skipped = first_with_data(api, company, filings, endpoint, **extra)
        if not rows or not filing:
            raise _no_data("최근 정기보고서에서 이 항목을 찾지 못했습니다. 기준 시점을 특정 연도로 바꿔 보세요.")
        report = function(api, replace(inp, year=filing.bsns_year, report_code=filing.reprt_code))
        return _attach_basis(api, report, company, filing, skipped)
    return run


def _same_period(current: float | None, previous: float | None) -> str:
    return change_text(current, previous).replace("전년보다", "전년 동기보다").replace("전년과", "전년 동기와").replace("전년 비교", "전년 동기 비교")


def ex_performance_latest(api: Api, inp: RunInput) -> Report:
    if inp.year:
        return ex_performance(api, inp)
    c = inp.company
    d, skipped = _load_latest(api, c)
    f = d.filing
    if f.is_annual:
        return _attach_basis(api, ex_performance(api, replace(inp, year=f.bsns_year)), c, f, skipped)
    previous_label = replace(f, period_end=f"{int(f.period_end[:4]) - 1}{f.period_end[4:]}").short
    current_label = f.short
    sections = [Section("이번 누적과 작년 같은 기간", chart=charts.column_panels(
        [(n, [previous_label, current_label], [d.prior_same[n], d.current[n]]) for n in IS_NAMES], compact_won))]
    has_ltm = any(d.ltm.get(n) is not None for n in IS_NAMES)
    if d.annual and has_ltm:
        labels = [str(y) for y in d.annual.years] + ["최근1년"]
        sections.append(Section("연간 흐름과 최근 12개월", chart=charts.column_panels(
            [(n, labels, d.annual.values[n] + [d.ltm.get(n)]) for n in IS_NAMES], compact_won)))
    rows = [[n, won(d.prior_same[n]), won(d.current[n]), _same_period(d.current[n], d.prior_same[n]), won(d.ltm.get(n))] for n in IS_NAMES]
    sections.append(Section("숫자로 보기", table=Table(["구분", "작년 같은 기간", "이번 누적", "변화", "최근 12개월"], rows, right={1, 2, 4}, widths=[18, 20, 20, 24, 18])))
    change = _same_period(d.current["매출액"], d.prior_same["매출액"])
    summary = (f"{f.label} 기준 누적 매출은 {won(d.current['매출액'])}으로 {change}했고, 영업이익은 {won(d.current['영업이익'])}입니다."
               .replace("전년 동기 비교 불가했고", "전년 동기 비교가 어렵고"))
    metrics = [Metric(f"누적 {n}", won(d.current[n]), _same_period(d.current[n], d.prior_same[n])) for n in IS_NAMES]
    if has_ltm:
        metrics += [Metric(f"최근 12개월 {n}", won(d.ltm.get(n))) for n in IS_NAMES]
    report = Report(
        f"{c.corp_name} 최신 실적", "", [summary], metrics, sections,
        [PARTIAL_TIP, LTM_TIP, "영업이익은 본업으로 번 돈, 당기순이익은 이자와 세금까지 반영한 최종 이익입니다."],
        file_stem=_stem(c, "최신실적"),
    )
    return _attach_basis(api, report, c, f, skipped)


def ex_health_latest(api: Api, inp: RunInput) -> Report:
    if inp.year:
        return ex_health(api, inp)
    c = inp.company
    d, skipped = _load_latest(api, c)
    f = d.filing
    if f.is_annual:
        return _attach_basis(api, ex_health(api, replace(inp, year=f.bsns_year)), c, f, skipped)
    a = d.annual
    def history(top: str, bottom: str) -> list[float | None]:
        return [ratio(x, y) for x, y in zip(a.values[top], a.values[bottom])] if a else []
    indicators = {
        "부채비율": history("부채총계", "자본총계") + [ratio(d.current["부채총계"], d.current["자본총계"])],
        "자기자본비율": history("자본총계", "자산총계") + [ratio(d.current["자본총계"], d.current["자산총계"])],
        "영업이익률": history("영업이익", "매출액") + [ratio(d.current["영업이익"], d.current["매출액"])],
        "순이익률": history("당기순이익", "매출액") + [ratio(d.current["당기순이익"], d.current["매출액"])],
        "ROE(자기자본이익률)": history("당기순이익", "자본총계") + [ratio(d.ltm.get("당기순이익"), d.current["자본총계"])],
    }
    formulas = {"부채비율": "부채총계 ÷ 자본총계", "자기자본비율": "자본총계 ÷ 자산총계", "영업이익률": "영업이익 ÷ 매출액(누적)",
                "순이익률": "순이익 ÷ 매출액(누적)", "ROE(자기자본이익률)": "최근 12개월 순이익 ÷ 자본총계"}
    labels = ([str(y) for y in a.years] if a else []) + [f.short]
    headers = ([f"{y}년" for y in a.years] if a else []) + [f"{f.short} (최신)"]
    count_columns = len(headers)
    rows = [[n, formulas[n]] + [percent(v) for v in values] for n, values in indicators.items()]
    chart = charts.column_panels([("부채비율", labels, indicators["부채비율"]), ("영업이익률", labels, indicators["영업이익률"]),
                                  ("ROE", labels, indicators["ROE(자기자본이익률)"])], lambda v: percent(v))
    column_width = int(52 / count_columns)
    report = Report(
        f"{c.corp_name} 재무 건강검진", "",
        [f"{f.period_text} 부채비율은 {percent(indicators['부채비율'][-1])}, 누적 영업이익률은 {percent(indicators['영업이익률'][-1])}입니다."],
        [Metric("부채비율", percent(indicators["부채비율"][-1]), f"{f.period_text} 기준"), Metric("영업이익률", percent(indicators["영업이익률"][-1]), "이번 누적 기준"),
         Metric("ROE", percent(indicators["ROE(자기자본이익률)"][-1]), "최근 12개월 순이익 기준")],
        [Section("추이", chart=chart),
         Section("지표 계산표", table=Table(["지표", "계산식"] + headers, rows, right=set(range(2, 2 + count_columns)), widths=[22, 26] + [column_width] * count_columns))],
        ["적정 수준은 업종마다 크게 다릅니다. 같은 업종끼리 비교하세요.", "이익률은 계절성이 있는 업종에서 분기마다 크게 달라질 수 있습니다.", LTM_TIP],
        file_stem=_stem(c, "재무건강검진"),
    )
    return _attach_basis(api, report, c, f, skipped)


def ex_compare_latest(api: Api, inp: RunInput) -> Report:
    if inp.year:
        return ex_compare(api, inp)
    filings = {c: find_filings(api, c) for c in inp.companies}
    shared = set.intersection(*[{(f.period_end, f.reprt_code) for f in items} for items in filings.values()])
    loaded: dict[Company, PeriodFinancials] = {}
    chosen = None
    for key in sorted(shared, reverse=True)[:4]:
        attempt = {}
        for company, items in filings.items():
            filing = next(f for f in items if (f.period_end, f.reprt_code) == key)
            rows, resolved = rows_for(api, company, filing, "fnlttSinglAcnt")
            if not rows:
                break
            attempt[company] = _period_from_rows(rows, resolved)
        if len(attempt) == len(filings):
            loaded, chosen = attempt, key
            break
    if not chosen:
        raise _no_data("선택한 회사들이 공통으로 제출한 최근 보고서를 찾지 못했습니다. 결산월이 다르면 연도를 직접 선택해 주세요.")
    names = [c.corp_name for c in inp.companies]
    series = {n: [loaded[c].current[n] for c in inp.companies] for n in IS_NAMES}
    sample = next(iter(loaded.values())).filing
    table_rows = [[
        c.corp_name, won(d.current["매출액"]), won(d.current["영업이익"]), won(d.current["당기순이익"]),
        percent(ratio(d.current["영업이익"], d.current["매출액"])), percent(ratio(d.current["부채총계"], d.current["자본총계"])),
    ] for c, d in loaded.items()]
    basis_rows, basis_links = [], {}
    for index, (company, data) in enumerate(loaded.items()):
        newest = filings[company][0]
        basis_rows.append([company.corp_name, f"{data.filing.label} (원문)", ymd(data.filing.rcept_dt), newest.label])
        url = viewer_url(data.filing.rcept_no)
        if url:
            basis_links[(index, 1)] = url
    behind = any(filings[c][0].period_end > chosen[0] for c in inp.companies)
    note = "회사마다 최신 보고서 시점이 달라, 모두가 제출한 가장 최근 보고서로 맞춰 비교했습니다." if behind else ""
    best = max(((v, n) for v, n in zip(series["매출액"], names) if v is not None), default=(None, "-"))
    short = [n if len(n) <= 6 else f"{n[:5]}…" for n in names]
    period = "연간" if sample.is_annual else "누적"
    return Report(
        "라이벌 회사 비교", f"{sample.label} 기준 · 최신 공시 자동 반영",
        [f"비교한 {len(names)}개 회사 중 {sample.label} 기준 {period} 매출이 가장 큰 곳은 {best[1]}({won(best[0])})입니다."],
        [Metric("비교 회사", f"{len(names)}곳"), Metric("공통 기준", sample.short), Metric("매출 1위", best[1])],
        [Section("최신 공시 반영 현황", table=Table(["회사", "비교 기준 보고서", "접수일", "회사의 최신 보고서"], basis_rows, links=basis_links, widths=[24, 32, 16, 28], note=note)),
         Section("규모 비교", chart=charts.column_panels([(n, short, series[n]) for n in IS_NAMES], compact_won)),
         Section("비교표", table=Table(["회사", f"매출액({period})", "영업이익", "순이익", "영업이익률", "부채비율"], table_rows, right={1, 2, 3, 4, 5}, widths=[24, 17, 16, 16, 14, 13]))],
        ["같은 업종끼리 비교해야 의미가 있습니다.", "회사마다 연결과 개별 재무제표 기준이 다를 수 있습니다."] + ([] if sample.is_annual else [PARTIAL_TIP]),
        file_stem=_stem(None, f"회사비교_{sample.period_end}"),
    )


def latest_for_endpoint(client: DartClient, company: Company, endpoint: str, **extra) -> Filing | None:
    """전체 API 화면의 '최신 공시로 채우기'에 쓴다."""
    api = Api(client)
    rows, filing, _ = first_with_data(api, company, find_filings(api, company), endpoint, **extra)
    return filing if rows else None


# ---------------------------------------------------------------- 예제 목록
@dataclass(frozen=True)
class Example:
    key: str
    category: str
    title: str
    question: str
    description: str
    endpoints: tuple[str, ...]
    inputs: tuple[str, ...]
    run: Callable[[Api, RunInput], Report]


CATEGORIES = ["기업 파악", "실적과 재무", "주주와 배당", "사람과 조직", "위험 신호"]

EXAMPLES: tuple[Example, ...] = (
    Example("profile", "기업 파악", "회사 프로필", "이 회사는 어떤 회사일까?", "대표자, 시장, 설립일, 주소를 한 장으로 정리합니다.", ("company",), ("company",), ex_profile),
    Example("recent", "기업 파악", "최근 공시 살펴보기", "요즘 이 회사에 무슨 일이 있었을까?", "기간별 공시 목록과 월별 건수를 보여 줍니다.", ("list",), ("company", "months"), ex_recent),
    Example("market", "기업 파악", "오늘의 시장 공시", "오늘 어떤 회사들이 공시를 냈을까?", "회사를 고르지 않아도 시장 전체 공시를 봅니다.", ("list",), ("days", "market", "dtype"), ex_market),
    Example("performance", "실적과 재무", "최신 실적 한눈에", "돈을 잘 벌고 있을까?", "올해 최신 분기 누적, 전년 동기, 최근 12개월을 봅니다.", ("fnlttSinglAcnt", "list"), ("company", "year"), ex_performance_latest),
    Example("health", "실적과 재무", "재무 건강검진", "빚은 많지 않을까?", "최신 분기까지 부채비율, 이익률, ROE를 계산해 줍니다.", ("fnlttSinglAcnt", "list"), ("company", "year"), ex_health_latest),
    Example("compare", "실적과 재무", "라이벌 회사 비교", "경쟁사와 비교하면 어떨까?", "최대 5개 회사를 공통 최신 보고서로 맞춰 비교합니다.", ("fnlttSinglAcnt", "fnlttMultiAcnt", "list"), ("companies", "year"), ex_compare_latest),
    Example("indicators", "실적과 재무", "공식 재무지표", "전문가가 보는 지표는 뭘까?", "수익성, 안정성, 성장성, 활동성 지표를 모아 줍니다.", ("fnlttSinglIndx", "list"), ("company", "year", "report"), _auto(ex_indicators, "fnlttSinglIndx", idx_cl_code="M210000")),
    Example("dividend", "주주와 배당", "배당 내역", "주주에게 얼마나 돌려줄까?", "최신 보고서까지 주당 배당금과 배당수익률을 봅니다.", ("alotMatter", "list"), ("company", "year"), _auto(ex_dividend, "alotMatter")),
    Example("holders", "주주와 배당", "최대주주 확인", "이 회사의 주인은 누구일까?", "최신 보고서의 최대주주와 특수관계인 지분을 봅니다.", ("hyslrSttus", "list"), ("company", "year", "report"), _auto(ex_holders, "hyslrSttus")),
    Example("major", "주주와 배당", "5% 이상 큰손", "기관이나 큰손이 들어왔을까?", "5% 이상 대량보유 보고 이력을 봅니다.", ("majorstock",), ("company",), ex_major),
    Example("insider", "주주와 배당", "내부자 지분 변동", "임원들이 주식을 사고 있을까?", "임원과 주요주주의 보유 변동을 모아 줍니다.", ("elestock",), ("company", "months"), ex_insider),
    Example("employees", "사람과 조직", "직원과 급여", "직원 수와 평균 급여는?", "인원, 고용 형태, 평균 급여, 부문별 인원을 봅니다.", ("empSttus", "list"), ("company", "year", "report"), _auto(ex_employees, "empSttus")),
    Example("executives", "사람과 조직", "임원 현황", "누가 회사를 이끌고 있을까?", "등기임원, 사외이사, 담당 업무를 정리합니다.", ("exctvSttus", "list"), ("company", "year", "report"), _auto(ex_executives, "exctvSttus")),
    Example("audit", "위험 신호", "감사의견 확인", "회계사는 재무제표를 믿을 만하다고 봤을까?", "가장 최근 사업보고서의 감사의견을 확인합니다.", ("accnutAdtorNmNdAdtOpinion", "list"), ("company", "year"), _auto(ex_audit, "accnutAdtorNmNdAdtOpinion", annual_only=True)),
    Example("events", "위험 신호", "주요 사건 점검", "부도, 소송, 증자 같은 일이 있었을까?", "주요사항보고서 15종을 한 번에 조회합니다.", tuple(ep for _, items in EVENT_GROUPS for _, ep in items), ("company", "months"), ex_events),
)


def run_example(example: Example, client: DartClient, inputs: RunInput) -> Report:
    api = Api(client)
    report = example.run(api, inputs)
    report.calls = api.calls
    return report


# ---------------------------------------------------------------- 전체 API 탐색기 결과
_META_KEYS = {"status", "message", "page_no", "page_count", "total_count", "total_page", "list"}
_HIDDEN = {"corp_cls", "corp_code", "reprt_code", "stlm_dt", "ord", "rm"}
_CARD_LIMIT = 30  # 이보다 많고 열도 많으면 카드 대신 표로 보여 준다.
_EXPLORER_MAX_PAGES = 100  # 회사 없이 3개월을 조회해도 하루 요청 한도를 크게 쓰지 않도록 10,000건에서 멈춘다.


def explorer_fetch(client: DartClient, endpoint: str, params: dict) -> dict:
    """공시검색에서 페이지를 지정하지 않으면 모든 페이지를 모아서 돌려준다."""
    if endpoint != "list" or params.get("page_no") or params.get("page_count"):
        return client.get_json(endpoint, allow_empty=False, **params)
    merged: dict = {}
    rows: list[dict] = []
    for page in range(1, _EXPLORER_MAX_PAGES + 1):
        data = client.get_json("list", allow_empty=False, **params, page_no=str(page), page_count="100")
        merged = data
        rows.extend(data.get("list", []))
        if page >= int(data.get("total_page") or 1):
            break
    return {**merged, "list": rows}


def explorer_report(endpoint: str, data: dict, call: str) -> Report:
    entry = find_api(endpoint) or {"name": endpoint, "summary": ""}
    labels = field_labels(endpoint)
    records = data.get("list") if isinstance(data.get("list"), list) else None
    if records is None:
        single = {k: v for k, v in data.items() if k not in _META_KEYS}
        records = [single] if single else []
    if not records:
        raise _no_data("조건에 맞는 데이터가 없습니다. 연도나 기간을 바꿔 보세요.")

    keys = [k for k in dict.fromkeys(k for r in records for k in r) if k not in _HIDDEN and any(str(r.get(k, "")).strip() not in ("", "-") for r in records)]
    if not keys:
        keys = list(dict.fromkeys(k for r in records for k in r))
    title = labels.get
    sections: list[Section] = []
    if len(keys) <= 6:
        table_rows, links = [], {}
        for index, record in enumerate(records):
            table_rows.append([text_or_dash(record.get(k)) for k in keys])
            if "rcept_no" in keys and viewer_url(record.get("rcept_no")):
                links[(index, keys.index("rcept_no"))] = viewer_url(record.get("rcept_no"))
        sections.append(Section("조회 결과", table=Table([title(k, k) for k in keys], table_rows, links=links)))
    elif len(records) > _CARD_LIMIT:
        # 열이 많은 대량 결과는 A4에 들어가도록 열을 나눠, 모든 행과 열을 표 여러 개로 보여 준다.
        anchor = next((k for k in ("rcept_dt", "repror", "nm", "account_nm", "corp_name", "se") if k in keys), keys[0])
        others = [k for k in keys if k != anchor]
        groups = [others[i:i + 4] for i in range(0, len(others), 4)]
        for group_index, group in enumerate(groups, 1):
            columns = [anchor] + group
            table_rows, links = [], {}
            for row_index, record in enumerate(records):
                table_rows.append([str(row_index + 1)] + [text_or_dash(record.get(k)) for k in columns])
                if "rcept_no" in group and viewer_url(record.get("rcept_no")):
                    links[(row_index, group.index("rcept_no") + 2)] = viewer_url(record.get("rcept_no"))
            sections.append(Section(f"조회 결과 ({group_index}/{len(groups)})", table=Table(["번호"] + [title(k, k) for k in columns], table_rows, links=links)))
    else:
        for index, record in enumerate(records, 1):
            pairs = [[title(k, k), text_or_dash(record.get(k))] for k in keys if str(record.get(k, "")).strip() not in ("", "-")]
            links = {}
            for row_index, key in enumerate(k for k in keys if str(record.get(k, "")).strip() not in ("", "-")):
                if key == "rcept_no" and viewer_url(record.get(key)):
                    links[(row_index, 1)] = viewer_url(record.get(key))
            sections.append(Section(f"결과 {index}", table=Table(["항목", "값"], pairs, links=links, widths=[38, 62])))
    total = data.get("total_count")
    count_text = f"{int(total):,}건" if str(total or "").isdigit() else f"{len(records):,}건"
    notice = []
    if str(total or "").isdigit() and int(total) > len(records):
        notice = [f"전체 {int(total):,}건 중 {len(records):,}건을 불러왔습니다. 나머지는 기간이나 조건을 좁혀 조회해 주세요."]
    return Report(
        entry["name"], f"{entry.get('group', '')} · {endpoint}",
        [f"조회 결과 {count_text}을 한글 항목명으로 정리했습니다."] + notice,
        [], sections, [], [call], file_stem=_stem(None, f"API_{entry['name']}"),
    )
