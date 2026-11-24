"""배포판(exe)의 시작점. 시작 오류를 창으로 알리고, --self-check로 설치 상태를 진단한다."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path


def _prepare_environment() -> Path:
    from dart_guide.keystore import app_data_dir

    home = app_data_dir()
    # 글꼴 캐시를 고정 위치에 두어 두 번째 실행부터 빠르게 시작한다.
    config = home / "matplotlib"
    config.mkdir(exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(config)
    return home


def _show_error(message: str) -> None:
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, message, "DART 쉬운 가이드", 0x10)


def _self_check(output: Path) -> int:
    """실제 키나 사용자 데이터를 건드리지 않고 배포판의 핵심 기능을 점검한다."""
    from PySide6.QtGui import QFont, QImage
    from PySide6.QtWidgets import QApplication

    from dart_guide import __version__, app as ui, charts
    from dart_guide.catalog import load_catalog
    from dart_guide.client import _new_session
    from dart_guide.corp_index import CorpIndex
    from dart_guide.derivative_scan import ScanStore
    from dart_guide.examples import EXAMPLES
    from dart_guide.guide import build_guide_report
    from dart_guide.keystore import KeyStore
    from dart_guide.report import Report, Section, export_pdf

    results: dict[str, object] = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False))}
    qt_app = QApplication(sys.argv[:1])
    qt_app.setFont(QFont("Malgun Gothic", 10))
    qt_app.setStyle("Fusion")
    qt_app.setStyleSheet(ui.QSS)

    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        store = KeyStore(folder / "check.bin")
        sample = "0123456789" * 4
        store.save(sample)
        results["key_encrypts"] = sample.encode() not in store.path.read_bytes() and store.load() == sample
        results["catalog"] = len(load_catalog())
        results["examples"] = len(EXAMPLES)
        chart = charts.column_panels([("매출액", ["2024", "2025"], [100.0, 120.0])], lambda v: f"{v:,.0f}")
        results["chart_png"] = chart[:4] == b"\x89PNG"
        results["report_pdf_pages"] = export_pdf(Report("점검", sections=[Section("차트", chart=chart)]), folder / "report.pdf")
        results["guide_pdf_pages"] = export_pdf(build_guide_report(), output.with_suffix(".pdf"))
        results["svg_plugin"] = not QImage(str(Path(ui.__file__).parent / "data" / "chevron.svg")).isNull()
        results.update(_feature_check(folder))
        window = ui.MainWindow(keys=KeyStore(folder / "none.bin"), index=CorpIndex(folder / "none.sqlite"),
                               scan_store=ScanStore(folder / "scan.sqlite"))
        window.resize(1320, 860)
        window.show()
        window.open_example(next(e for e in EXAMPLES if e.key == "performance"))
        qt_app.processEvents()
        window.grab().save(str(output.with_suffix(".png")))
        results["window"] = window.isVisible()
        window.close()
        qt_app.processEvents()

    try:
        results["tls_status"] = _new_session().get("https://opendart.fss.or.kr/", timeout=20).status_code
    except Exception as exc:
        results["tls_status"] = type(exc).__name__
    bundle = str(Path(getattr(sys, "_MEIPASS", "")).resolve()).lower()
    # PySide6의 내장 모듈은 실제 파일이 없는 가상 경로를 쓰므로 존재하는 파일만 확인한다.
    outside = sorted(
        name for name, module in sys.modules.items()
        if getattr(module, "__file__", None) and Path(module.__file__).is_file()
        and not str(Path(module.__file__).resolve()).lower().startswith(bundle)
    )
    results["modules_outside_bundle"] = outside[:10]
    output.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    required = (results["key_encrypts"], results["catalog"] == 85, results["chart_png"], results["svg_plugin"], results["window"],
                results["tls_status"] == 200, not outside, results["frozen"], results["contract_parse"], results["scan_store"],
                results["agent_sdk"], results["api_key_path"] is True)
    return 0 if all(required) else 1


def _feature_check(folder: Path) -> dict[str, object]:
    """업체 분석·파생 전수조사·AI 에이전트 구성요소가 배포판 안에서 불러와지는지 본다. 네트워크와 Claude는 쓰지 않는다."""
    from dart_guide.agent import DartTools, _build_server, find_cli
    from dart_guide.corp_index import CorpIndex
    from dart_guide.derivative_contracts import parse_table
    from dart_guide.derivative_scan import ScanStore
    from dart_guide.filing import parse_document

    results: dict[str, object] = {}
    sections = parse_document(
        "<DOCUMENT><BODY><SECTION-1><TITLE>5. 위험관리 및 파생거래</TITLE><P>(단위 : USD)</P><TABLE>"
        "<TR><TH>구분</TH><TH>계약처</TH><TH>계약금액</TH><TH>약정환율</TH><TH>만기일</TH></TR>"
        "<TR><TD>통화선도</TD><TD>은행</TD><TD>1,000,000</TD><TD>1,300.00</TD><TD>2099.12.31</TD></TR>"
        "</TABLE></SECTION-1></BODY></DOCUMENT>")
    table = next(b for s in sections for b in s.blocks if not isinstance(b, str))
    contracts = parse_table(table, 2025, "점검", sections[0].title)
    results["contract_parse"] = [(c.instrument, c.currency, c.notional) for c in contracts] == [("통화선도", "USD", 1_000_000)]
    store = ScanStore(folder / "check_scan.sqlite")
    results["scan_store"] = store.stats([2025], ["Y"])["total"] == 0
    try:
        import anyio._backends._asyncio  # noqa: F401  실행 시점에 이름으로 불러오는 모듈

        server = _build_server(DartTools(lambda: None, CorpIndex(folder / "none.sqlite")))
        results["agent_sdk"] = server.get("type") == "sdk"
    except Exception as exc:
        results["agent_sdk"] = f"{type(exc).__name__}: {exc}"
    try:
        # Claude Code 로그인이 없을 때 쓰는 API 키 경로. 실제 키나 네트워크는 쓰지 않는다.
        import anthropic

        from dart_guide.agent import _http_client
        from dart_guide.keystore import ClaudeKeyStore

        claude_store = ClaudeKeyStore(folder / "check_claude.bin")
        sample = "sk-ant-api03-" + "x" * 90
        claude_store.save(sample)
        anthropic.AsyncAnthropic(api_key=sample, http_client=_http_client())
        results["api_key_path"] = claude_store.load() == sample and sample.encode() not in claude_store.path.read_bytes()
    except Exception as exc:
        results["api_key_path"] = f"{type(exc).__name__}: {exc}"
    results["claude_code_found"] = bool(find_cli())
    return results


def _agent_check(output: Path) -> int:
    """설치된 Claude Code 로그인으로 짧은 질문 하나를 보내 도구 호출까지 확인한다. 구독 사용량이 조금 쓰인다."""
    import threading

    from dart_guide.agent import AgentError, ClaudeAgent, DartTools, check_login, test_api_key
    from dart_guide.client import DartClient
    from dart_guide.corp_index import CorpIndex
    from dart_guide.keystore import KeyStore

    status = check_login()
    events: list[tuple[str, str]] = []
    finished = threading.Event()

    def on_event(kind: str, text: str) -> None:
        events.append((kind, text))
        if kind in ("done", "error"):
            finished.set()

    if status.ready:
        agent = ClaudeAgent(DartTools(lambda: DartClient(KeyStore().load()), CorpIndex()), on_event)
        agent.send("search_company 도구로 '삼성전자'를 찾아, 그 고유번호 8자리 숫자만 답하세요.")
        finished.wait(240)
        agent.close()
    answer = "".join(text for kind, text in events if kind == "delta").strip()
    # API 키 경로는 일부러 틀린 키로 실제 서버까지 다녀와 거절 안내가 나오는지만 본다. 요금은 들지 않는다.
    try:
        api_check = test_api_key("sk-ant-api03-" + "0" * 90)
    except AgentError as exc:
        api_check = str(exc)
    results = {"login": status.message, "tools": [t for k, t in events if k == "tool"], "answer": answer[-300:],
               "errors": [t for k, t in events if k == "error"], "api_invalid_key": api_check}
    output.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    passed = status.ready and "00126380" in answer and results["tools"] and not results["errors"]
    return 0 if passed and "API 키가 올바르지" in api_check else 1


def main() -> int:
    home = None
    try:
        home = _prepare_environment()
        if "--self-check" in sys.argv:
            return _self_check(Path(sys.argv[sys.argv.index("--self-check") + 1]))
        if "--agent-check" in sys.argv:
            return _agent_check(Path(sys.argv[sys.argv.index("--agent-check") + 1]))
        from dart_guide.app import main as run_app

        return run_app()
    except Exception:
        if home:
            (home / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        _show_error("프로그램을 시작하지 못했습니다.\n압축을 완전히 푼 폴더에서 다시 실행해 주세요.\n"
                    + (f"\n오류 기록: {home / 'error.log'}" if home else ""))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
