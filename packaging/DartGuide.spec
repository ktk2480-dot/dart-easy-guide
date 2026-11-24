# PyInstaller 설정. build_release.py로 실행한다.
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
BUILD = Path(os.environ["DARTGUIDE_BUILD_DIR"])

# AI 에이전트(claude-agent-sdk → mcp)가 cryptography와 pywin32(pywintypes, win32job)를 import하므로 빼지 않는다.
EXCLUDES = [
    "tkinter", "PyQt5", "PyQt6", "PySide2", "pandas", "scipy", "IPython", "jupyter", "notebook", "pytest", "sphinx",
    "openpyxl", "win32com", "pythoncom", "bs4", "lxml", "sqlalchemy", "numba", "pyarrow",
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtWebEngineCore",
    "matplotlib.backends.backend_qt", "matplotlib.backends.backend_qtagg", "matplotlib.backends.backend_tkagg",
]
# Qt 위젯 앱에 필요 없는 큰 파일을 뺀다. SDK에 든 Claude Code 실행 파일(약 400MB)은 쓰지 않는다.
# 앱은 사용자가 설치해 로그인한 Claude Code를 실행한다.
DROP = ("opengl32sw", "qt6quick", "qt6qml", "qt6pdf", "qpdf", "qt6virtualkeyboard", "qtvirtualkeyboard", "qt6network",
        "/translations/", "\\translations\\", "qtuiotouch", "qtga", "qtiff", "qwbmp", "qwebp", "qicns",
        "claude_agent_sdk/_bundled", "claude_agent_sdk\\_bundled")


def keep(entry):
    name = entry[0].lower()
    return not any(word in name for word in DROP)


a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "dart_guide" / "data"), "dart_guide/data")],
    # anyio는 실행 시점에 백엔드를 이름으로 불러와서 PyInstaller가 찾지 못한다.
    hiddenimports=["anyio._backends._asyncio"],
    excludes=EXCLUDES,
)
a.binaries = [entry for entry in a.binaries if keep(entry)]
a.datas = [entry for entry in a.datas if keep(entry)]
# 오픈소스 고지에 넣을 패키지를 정하도록, 실제로 들어간 최상위 모듈 이름을 남긴다.
(BUILD / "modules.txt").write_text("\n".join(sorted(
    {name.split(".")[0] for name, *_ in a.pure} | {Path(dest).parts[0] for dest, *_ in a.binaries + a.datas}
)), encoding="utf-8")
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="DartGuide",
    console=False,
    upx=False,
    icon=str(BUILD / "app.ico"),
    version=str(BUILD / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="DartGuide")
