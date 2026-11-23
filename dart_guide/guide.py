"""용어 사전과 PDF로 내려받는 전체 이용 가이드."""
from __future__ import annotations

from .catalog import load_catalog
from .client import FRIENDLY_ERRORS
from .examples import CATEGORIES, EXAMPLES
from .report import Report, Section, Table

GLOSSARY = [
    ("공시", "기업이 투자자에게 알려야 할 중요한 정보를 공개하는 일입니다."),
    ("DART", "금융감독원이 운영하는 전자공시시스템입니다. 기업의 공시를 누구나 볼 수 있습니다."),
    ("OpenDART", "DART의 공시 정보를 프로그램으로 가져올 수 있게 제공하는 서비스입니다."),
    ("API", "프로그램끼리 정보를 주고받는 약속입니다. 이 앱이 대신 호출해 줍니다."),
    ("인증키(API 키)", "OpenDART를 쓸 수 있는 40자리 개인 열쇠입니다. 다른 사람과 공유하지 마세요."),
    ("고유번호", "DART에서 회사를 구분하는 8자리 번호입니다. 이 앱에서는 회사명으로 찾을 수 있습니다."),
    ("종목코드", "주식시장에서 상장 회사를 구분하는 6자리 번호입니다."),
    ("접수번호", "공시 한 건마다 붙는 14자리 번호입니다."),
    ("사업보고서", "회사가 1년 동안의 사업과 재무 상태를 담아 제출하는 보고서입니다."),
    ("반기·분기보고서", "1년 중간에 6개월 또는 3개월 단위로 제출하는 보고서입니다."),
    ("연결재무제표", "자회사까지 하나의 회사처럼 합쳐서 작성한 재무제표입니다."),
    ("개별재무제표", "자회사를 합치지 않고 그 회사만 기준으로 작성한 재무제표입니다."),
    ("재무상태표", "특정 시점에 회사가 가진 자산, 부채, 자본을 보여 줍니다."),
    ("손익계산서", "일정 기간 동안 얼마를 벌고 얼마를 썼는지 보여 줍니다."),
    ("매출액", "상품이나 서비스를 팔아 벌어들인 총액입니다."),
    ("영업이익", "매출에서 원가와 판매·관리비를 뺀, 본업으로 번 이익입니다."),
    ("당기순이익", "이자와 세금 등을 모두 반영한 뒤 최종적으로 남은 이익입니다."),
    ("자산", "회사가 가진 현금, 건물, 설비 등 모든 재산입니다. 자산은 부채와 자본의 합입니다."),
    ("부채", "회사가 앞으로 갚아야 할 돈입니다."),
    ("자본", "자산에서 부채를 뺀, 주주 몫의 순수한 재산입니다."),
    ("부채비율", "부채를 자본으로 나눈 값입니다. 업종에 따라 적정 수준이 크게 다릅니다."),
    ("영업이익률", "매출 중 영업이익이 차지하는 비율입니다."),
    ("ROE", "자기자본이익률. 주주의 돈으로 얼마나 이익을 냈는지 나타냅니다."),
    ("배당", "회사가 번 이익의 일부를 주주에게 나눠 주는 것입니다."),
    ("배당수익률", "주가 대비 1주당 배당금의 비율입니다."),
    ("배당성향", "순이익 중 배당으로 지급한 비율입니다."),
    ("최대주주", "회사 주식을 가장 많이 가진 주주와 그 특수관계인입니다."),
    ("특수관계인", "최대주주의 가족, 계열사 등 가까운 관계에 있는 사람이나 회사입니다."),
    ("등기임원", "법인 등기부에 올라 이사회 의결에 참여하는 임원입니다."),
    ("사외이사", "회사 외부 전문가로서 경영진을 감시하고 조언하는 이사입니다."),
    ("감사의견", "외부 회계사가 재무제표를 검토한 뒤 내는 의견입니다. 적정, 한정, 부적정, 의견거절이 있습니다."),
    ("유상증자", "새 주식을 발행해 투자자에게 돈을 받고 자본을 늘리는 것입니다."),
    ("무상증자", "주주에게 돈을 받지 않고 새 주식을 나눠 주는 것입니다."),
    ("감자", "자본금을 줄이는 것입니다. 재무 구조 개선을 위해 하는 경우가 많습니다."),
    ("전환사채(CB)", "일정 조건에서 주식으로 바꿀 수 있는 회사채입니다."),
    ("신주인수권부사채(BW)", "새 주식을 살 권리가 붙은 회사채입니다."),
    ("교환사채(EB)", "회사가 보유한 다른 주식으로 바꿀 수 있는 회사채입니다."),
    ("자기주식(자사주)", "회사가 스스로 사들여 보유하는 자기 회사의 주식입니다."),
]

KEY_STEPS = [
    "OpenDART 사이트(opendart.fss.or.kr)에 접속합니다.",
    "상단의 '인증키 신청/관리'에서 인증키를 신청합니다.",
    "가입한 이메일의 안내에 따라 인증을 마치면 40자리 인증키를 확인할 수 있습니다.",
    "이 앱에서 처음 조회할 때 나타나는 입력창에 인증키를 붙여 넣습니다.",
]
USAGE_STEPS = [
    "왼쪽 메뉴에서 '예제로 시작'을 누르고 궁금한 질문을 고릅니다.",
    "회사 이름을 입력해 목록에서 선택합니다. 처음 한 번은 회사 목록을 내려받습니다.",
    "기준 시점은 '최신 공시 자동 반영'이 기본입니다. 올해 분기·반기 보고서와 그 이후 공시까지 찾아 주며, 특정 연도를 직접 고를 수도 있습니다.",
    "조건을 확인한 뒤 '결과 보기'를 누릅니다.",
    "결과 화면의 'PDF로 저장'을 누르면 정리된 내용을 파일로 받을 수 있습니다.",
]


def glossary_html(query: str = "") -> str:
    query = query.strip().lower()
    items = [(t, d) for t, d in GLOSSARY if not query or query in t.lower() or query in d.lower()]
    if not items:
        return '<p style="color:#5b6470;">찾는 용어가 없습니다. 다른 단어로 검색해 보세요.</p>'
    return "".join(
        f'<p style="margin-bottom:12px;"><span style="font-size:12pt;font-weight:700;color:#173b6c;">{t}</span><br>{d}</p>'
        for t, d in items
    )


def build_guide_report() -> Report:
    catalog = load_catalog()
    sections = [
        Section("1. OpenDART란", [
            "OpenDART는 금융감독원 전자공시시스템(DART)의 공시 정보를 프로그램으로 받아 볼 수 있는 공식 서비스입니다. "
            f"이 앱은 공식 개발가이드의 {len(catalog)}개 API를 코딩 없이 쓸 수 있게 정리했습니다.",
        ]),
        Section("2. 인증키 준비", bullets=KEY_STEPS, paragraphs=[
            "입력한 키는 Windows DPAPI로 암호화되어 이 PC의 현재 Windows 계정에서만 풀 수 있고, 화면과 PDF에는 표시되지 않습니다.",
        ]),
        Section("3. 프로그램 사용법", bullets=USAGE_STEPS),
    ]
    for number, category in enumerate(CATEGORIES, 1):
        rows = [[e.title, e.question, e.description, ", ".join(e.endpoints[:2]) + (" 외" if len(e.endpoints) > 2 else "")]
                for e in EXAMPLES if e.category == category]
        sections.append(Section(f"4-{number}. 예제: {category}", table=Table(["예제", "이럴 때", "알 수 있는 것", "사용 API"], rows, widths=[17, 27, 36, 20])))
    sections.append(Section("5. 자주 보는 오류와 해결법", table=Table(["코드", "뜻과 해결법"], [[c, m] for c, m in FRIENDLY_ERRORS.items()], widths=[10, 90])))
    groups: dict[str, list[dict]] = {}
    for item in catalog:
        groups.setdefault(item["group"], []).append(item)
    for number, (group, items) in enumerate(groups.items(), 1):
        rows = [[str(i), item["name"], item["endpoint"]] for i, item in enumerate(items, 1)]
        sections.append(Section(f"6-{number}. 전체 API: {group} ({len(items)}개)", table=Table(["번호", "API 이름", "호출 이름"], rows, widths=[8, 56, 36])))
    sections.append(Section("7. 용어 사전", table=Table(["용어", "뜻"], [[t, d] for t, d in GLOSSARY], widths=[24, 76])))
    return Report(
        "OpenDART 쉬운 이용 가이드", "코딩을 몰라도 기업 공시를 읽을 수 있도록 정리한 안내서",
        ["인증키를 한 번 입력한 뒤, 궁금한 질문을 고르고 회사를 선택하면 결과를 쉬운 말과 그래프로 볼 수 있습니다."],
        sections=sections, file_stem="OpenDART_쉬운_이용가이드",
    )
