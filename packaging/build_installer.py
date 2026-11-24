"""release/DartGuide 폴더로 Windows 설치 파일을 만든다.

먼저 build_release.py를 실행한 뒤:  python packaging/build_installer.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import winreg
from pathlib import Path

from build_release import RELEASE, ROOT, make_icon
from dart_guide import __version__


def _registered_compilers() -> list[tuple[tuple[int, ...], Path]]:
    found = []
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive, view in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        try:
            root = winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, index)) as item:
                        if "Inno Setup" not in winreg.QueryValueEx(item, "DisplayName")[0]:
                            continue
                        location = Path(winreg.QueryValueEx(item, "InstallLocation")[0])
                        version = winreg.QueryValueEx(item, "DisplayVersion")[0]
                except OSError:
                    continue
                if (location / "ISCC.exe").is_file():
                    found.append((tuple(int(n) for n in re.findall(r"\d+", version)), location / "ISCC.exe"))
    return found


def find_compiler() -> Path:
    override = os.environ.get("ISCC")
    if override and Path(override).is_file():
        return Path(override)
    compilers = _registered_compilers()
    if not compilers:
        raise SystemExit("Inno Setup을 찾지 못했습니다. https://jrsoftware.org/isdl.php 에서 설치해 주세요.")
    return max(compilers)[1]


def _copy_as_windows_text(source: Path, target: Path) -> None:
    text = source.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    target.write_text(text, encoding="utf-8-sig", newline="")


def main() -> int:
    source = RELEASE / "DartGuide"
    if not (source / "DartGuide.exe").is_file():
        raise SystemExit("release/DartGuide가 없습니다. 먼저 python packaging/build_release.py를 실행해 주세요.")
    work = Path(tempfile.mkdtemp(prefix="dartguide_setup_"))
    try:
        make_icon(work / "app.ico")
        _copy_as_windows_text(ROOT / "packaging" / "설치판_사용방법.txt", work / "guide.txt")
        _copy_as_windows_text(ROOT / "packaging" / "DartGuide.iss", work / "DartGuide.iss")
        subprocess.run([
            str(find_compiler()), "/Q", f"/DAppVersion={__version__}", f"/DSourceDir={source}",
            f"/DIconFile={work / 'app.ico'}", f"/DGuideFile={work / 'guide.txt'}", f"/DOutputDir={RELEASE}",
            str(work / "DartGuide.iss"),
        ], check=True)
        output = RELEASE / f"DartGuide_Setup_v{__version__}.exe"
        print(f"installer {output.stat().st_size / 1048576:.0f} MB: {output}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
