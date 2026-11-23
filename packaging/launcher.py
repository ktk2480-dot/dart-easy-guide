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
        window = ui.MainWindow(keys=KeyStore(folder / "none.bin"), index=CorpIndex(folder / "none.sqlite"))
        window.resize(1320, 860)
        window.show()
        window.open_example(next(e for e in EXAMPLES if e.key == "performance"))
        qt_app.processEvents()
        window.grab().save(str(output.with_suffix(".png")))
        results["window"] = window.isVisible()
        window.close()

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
                results["tls_status"] == 200, not outside, results["frozen"])
    return 0 if all(required) else 1


def main() -> int:
    home = None
    try:
        home = _prepare_environment()
        if "--self-check" in sys.argv:
            return _self_check(Path(sys.argv[sys.argv.index("--self-check") + 1]))
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
