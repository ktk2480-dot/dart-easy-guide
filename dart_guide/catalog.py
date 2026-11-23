"""공식 개발가이드에서 정리한 85개 API 명세와 입력 선택지."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

GUIDE_URL = "https://opendart.fss.or.kr/guide/detail.do"
KEY_APPLY_URL = "https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do"
BINARY_ENDPOINTS = {"document", "corpCode", "fnlttXbrl"}
MULTI_COMPANY_ENDPOINTS = {"fnlttMultiAcnt", "fnlttCmpnyIndx"}

REPORT_CHOICES = [
    ("사업보고서 (1년 전체)", "11011"),
    ("3분기보고서", "11014"),
    ("반기보고서 (상반기)", "11012"),
    ("1분기보고서", "11013"),
]
PARAM_CHOICES = {
    "reprt_code": [(label, code) for label, code in REPORT_CHOICES],
    "fs_div": [("연결재무제표 (자회사 포함)", "CFS"), ("개별재무제표 (회사 단독)", "OFS")],
    "idx_cl_code": [("수익성 지표", "M210000"), ("안정성 지표", "M220000"), ("성장성 지표", "M230000"), ("활동성 지표", "M240000")],
    "pblntf_ty": [
        ("정기공시", "A"), ("주요사항보고", "B"), ("발행공시", "C"), ("지분공시", "D"), ("기타공시", "E"),
        ("외부감사관련", "F"), ("펀드공시", "G"), ("자산유동화", "H"), ("거래소공시", "I"), ("공정위공시", "J"),
    ],
    "corp_cls": [("코스피(유가증권)", "Y"), ("코스닥", "K"), ("코넥스", "N"), ("기타법인", "E")],
    "sort": [("접수일자", "date"), ("회사명", "crp"), ("보고서명", "rpt")],
    "sort_mth": [("최신순(내림차순)", "desc"), ("오래된순(오름차순)", "asc")],
    "last_reprt_at": [("정정 포함 전체", "N"), ("최종보고서만", "Y")],
}


@lru_cache(maxsize=1)
def load_catalog() -> tuple[dict, ...]:
    path = Path(__file__).parent / "data" / "api_catalog.json"
    return tuple(json.loads(path.read_text(encoding="utf-8")))


def find_api(endpoint: str) -> dict | None:
    return next((item for item in load_catalog() if item["endpoint"] == endpoint), None)


def field_labels(endpoint: str) -> dict[str, str]:
    entry = find_api(endpoint)
    return {field["key"]: field["name"] for field in entry["fields"]} if entry else {}


def guide_link(entry: dict) -> str:
    return f"{GUIDE_URL}?apiGrpCd={entry['group_code']}&apiId={entry['api_id']}"
