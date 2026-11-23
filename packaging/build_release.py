"""Python 없이 실행되는 Windows 배포판을 만든다.  사용법: python packaging/build_release.py"""
from __future__ import annotations

import importlib.metadata as metadata
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dart_guide import APP_NAME, __version__  # noqa: E402

RELEASE = ROOT / "release"
PACKAGES = ("PySide6", "PySide6_Essentials", "shiboken6", "requests", "urllib3", "certifi", "idna", "charset-normalizer",
            "matplotlib", "numpy", "pillow", "python-dateutil", "pyparsing", "cycler", "kiwisolver", "fonttools",
            "contourpy", "packaging", "six")


def make_icon(path: Path) -> None:
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 248, 248), radius=54, fill="#1f66c1")
    for x, height in ((60, 64), (112, 108), (164, 152)):
        draw.rounded_rectangle((x, 200 - height, x + 36, 200), radius=8, fill="#ffffff")
    image.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def write_version_file(path: Path) -> None:
    numbers = [int(part) for part in __version__.split(".")] + [0]
    version = ", ".join(str(n) for n in numbers[:4])
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({version}), prodvers=({version}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041204B0', [
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'DartGuide'),
      StringStruct('OriginalFilename', 'DartGuide.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1042, 1200])])
  ]
)
""", encoding="utf-8")


def _license_name(meta) -> str:
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier", []) if c.startswith("License ::")]
    if classifiers:
        return ", ".join(classifiers)
    return (meta.get("License") or "패키지 라이선스 파일 참고").splitlines()[0][:80]


def write_notices(target: Path) -> None:
    licenses = target / "licenses"
    licenses.mkdir(exist_ok=True)
    lines = [f"{APP_NAME} {__version__} 오픈소스 고지", "",
             "이 배포판에는 아래 오픈소스가 포함되어 있습니다. 각 라이선스 원문은 licenses 폴더에 있습니다.", "",
             f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}  (PSF License)"]
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy(python_license, licenses / "Python_LICENSE.txt")
    for package in PACKAGES:
        try:
            dist = metadata.distribution(package)
        except metadata.PackageNotFoundError:
            continue
        lines.append(f"{dist.metadata['Name']} {dist.version}  ({_license_name(dist.metadata)})")
        for file in dist.files or []:
            text = str(file).replace("\\", "/")
            is_license = re.search(r"(^|/)(licen[cs]e|copying|notice)[^/]*$", text, re.I) or "/licenses/" in text.lower()
            # 상용 라이선스 안내문은 이 배포판에 해당하지 않으므로 넣지 않는다.
            if ".dist-info/" in text.lower() and is_license and "Qt-Commercial" not in text:
                source = Path(dist.locate_file(file))
                if source.is_file() and source.stat().st_size < 2_000_000:
                    name = re.sub(r"[^\w.-]+", "_", f"{dist.metadata['Name']}_{Path(str(file)).name}")
                    shutil.copy(source, licenses / name)
    for name in ("LGPL-3.0.txt", "GPL-3.0.txt"):
        shutil.copy(ROOT / "packaging" / "licenses" / name, licenses / f"Qt_PySide6_{name}")
    lines += ["", "Qt for Python(PySide6)은 LGPLv3로 사용하며, 교체할 수 있도록 별도 DLL 파일로 포함했습니다.",
              "라이선스 원문: licenses/Qt_PySide6_LGPL-3.0.txt, licenses/Qt_PySide6_GPL-3.0.txt",
              "Qt 소스 코드: https://code.qt.io/cgit/pyside/pyside-setup.git/"]
    (target / "THIRD_PARTY_NOTICES.txt").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8-sig")


def make_zip(folder: Path, archive: Path) -> None:
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                zipped.write(file, Path(folder.name) / file.relative_to(folder))


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="dartguide_build_"))
    try:
        make_icon(work / "app.ico")
        write_version_file(work / "version_info.txt")
        environment = {**os.environ, "DARTGUIDE_BUILD_DIR": str(work)}
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                   "--distpath", str(RELEASE), "--workpath", str(work / "pyi"), str(ROOT / "packaging" / "DartGuide.spec")]
        subprocess.run(command, check=True, env=environment, cwd=work)
        target = RELEASE / "DartGuide"
        guide = (ROOT / "packaging" / "사용방법.txt").read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
        (target / "사용방법.txt").write_text(guide, encoding="utf-8-sig", newline="")
        write_notices(target)
        archive = RELEASE / f"DartGuide_v{__version__}_Windows_x64.zip"
        make_zip(target, archive)
        folder_size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1048576
        print(f"folder {folder_size:.0f} MB, zip {archive.stat().st_size / 1048576:.0f} MB: {archive}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
