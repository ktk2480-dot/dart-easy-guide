"""API 인증키를 Windows DPAPI로 암호화해 저장한다.

DPAPI로 암호화한 값은 같은 PC의 같은 Windows 계정에서만 복호화할 수 있다.
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
from ctypes import wintypes
from pathlib import Path

_MAGIC = b"DGK1"
_ENTROPY = b"dart-guide/api-key/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9]{40}$")
# Claude Console에서 발급한 API 키만 받는다. 구독 로그인 토큰(sk-ant-oat…)이나 관리자 키는 받지 않는다.
_CLAUDE_KEY_PATTERN = re.compile(r"^sk-ant-api\d{2}-[A-Za-z0-9_-]{20,300}$")


class KeyStoreError(Exception):
    """저장된 키를 읽거나 쓸 수 없을 때 발생한다."""


def app_data_dir() -> Path:
    override = os.environ.get("DART_GUIDE_HOME")
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DartGuide"
    base.mkdir(parents=True, exist_ok=True)
    return base


def is_valid_key(value: str) -> bool:
    return bool(_KEY_PATTERN.match(value or ""))


def is_valid_claude_key(value: str) -> bool:
    return bool(_CLAUDE_KEY_PATTERN.match(value or ""))


def mask_key(value: str) -> str:
    return f"{value[:4]}{'•' * 8}{value[-4:]}" if value and len(value) >= 8 else "••••"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _to_blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi(data: bytes, encrypt: bool, entropy_bytes: bytes = _ENTROPY) -> bytes:
    if sys.platform != "win32":
        raise KeyStoreError("암호화 저장은 Windows에서만 지원합니다.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    function = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    function.restype = wintypes.BOOL

    source, _source_buffer = _to_blob(data)
    entropy, _entropy_buffer = _to_blob(entropy_bytes)
    output = _Blob()
    ok = function(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
    )
    if not ok:
        raise KeyStoreError("키를 암호화하거나 복호화하지 못했습니다.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


class KeyStore:
    """OpenDART 인증키 저장소."""
    filename = "api_key.bin"
    entropy = _ENTROPY
    invalid_message = "인증키는 영문과 숫자로 된 40자리여야 합니다."

    def __init__(self, path: Path | None = None):
        self.path = path or app_data_dir() / self.filename

    @staticmethod
    def is_valid(value: str) -> bool:
        return is_valid_key(value)

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, api_key: str) -> None:
        if not self.is_valid(api_key):
            raise KeyStoreError(self.invalid_message)
        payload = _MAGIC + _dpapi(api_key.encode("ascii"), encrypt=True, entropy_bytes=self.entropy)
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(payload)
        os.replace(temp, self.path)

    def load(self) -> str | None:
        if not self.exists():
            return None
        payload = self.path.read_bytes()
        if not payload.startswith(_MAGIC):
            raise KeyStoreError("저장된 키 파일 형식이 올바르지 않습니다. 키를 다시 입력해 주세요.")
        try:
            value = _dpapi(payload[len(_MAGIC):], encrypt=False, entropy_bytes=self.entropy).decode("ascii")
        except (KeyStoreError, UnicodeDecodeError) as exc:
            raise KeyStoreError("저장된 키를 풀 수 없습니다. 다른 PC나 계정에서 저장된 키일 수 있으니 다시 입력해 주세요.") from exc
        if not self.is_valid(value):
            raise KeyStoreError("저장된 키가 손상되었습니다. 키를 다시 입력해 주세요.")
        return value

    def delete(self) -> None:
        if self.exists():
            self.path.unlink()


class ClaudeKeyStore(KeyStore):
    """Claude Code 로그인이 없을 때만 쓰는 Claude API 키 저장소. OpenDART 키와 다른 값으로 암호화한다."""
    filename = "claude_api_key.bin"
    entropy = b"dart-guide/claude-api-key/v1"
    invalid_message = "Claude Console에서 발급한 API 키(sk-ant-api로 시작)를 입력해 주세요."

    @staticmethod
    def is_valid(value: str) -> bool:
        return is_valid_claude_key(value)
