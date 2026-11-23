"""OpenDART HTTP 클라이언트. 오류 메시지와 호출 기록에 인증키가 남지 않게 한다."""
from __future__ import annotations

import ssl
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://opendart.fss.or.kr/api"
_MIN_INTERVAL = 0.12
_throttle_lock = threading.Lock()
_last_request = 0.0

FRIENDLY_ERRORS = {
    "010": "등록되지 않은 인증키입니다. 키를 정확히 입력했는지 확인해 주세요.",
    "011": "사용할 수 없는 인증키입니다. OpenDART 마이페이지에서 키 상태를 확인해 주세요.",
    "012": "이 PC의 IP는 접근이 허용되지 않았습니다. OpenDART에 등록한 IP 설정을 확인해 주세요.",
    "013": "조건에 맞는 데이터가 없습니다. 연도나 기간을 바꿔 보세요.",
    "014": "요청한 파일이 없습니다. 접수번호를 확인해 주세요.",
    "020": "오늘 사용할 수 있는 요청 횟수를 넘었습니다(일반적으로 하루 20,000건). 내일 다시 시도해 주세요.",
    "021": "한 번에 조회할 수 있는 회사 수(최대 100개)를 넘었습니다.",
    "100": "입력값이 올바르지 않습니다. 날짜, 연도, 코드 형식을 확인해 주세요.",
    "101": "부적절한 접근으로 처리되었습니다. 잠시 후 다시 시도해 주세요.",
    "800": "OpenDART 시스템 점검 중입니다. 점검이 끝난 뒤 다시 시도해 주세요.",
    "900": "OpenDART에서 알 수 없는 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
    "901": "계정의 개인정보 보유기간이 만료되어 키를 쓸 수 없습니다. opendart@fss.or.kr로 문의해 주세요.",
}
KEY_ERROR_CODES = {"010", "011", "901"}


class DartError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _windows_trust_context() -> ssl.SSLContext:
    """Windows 인증서 저장소로 서버를 검증한다. 체인과 호스트명 검증은 항상 켜 둔다."""
    context = ssl.create_default_context()
    # Python 3.13의 추가 RFC 5280 검사만 해제한다. 회사 보안 장비의 루트 인증서와 호환하기 위함이다.
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


class _TrustAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = _windows_trust_context()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = _windows_trust_context()
        return super().proxy_manager_for(*args, **kwargs)


def _new_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=0.6, status_forcelist=(502, 503, 504), allowed_methods=("GET",))
    session.mount("https://", _TrustAdapter(max_retries=retry))
    session.headers["User-Agent"] = "dart-guide/1.0"
    return session


def _throttle() -> None:
    global _last_request
    with _throttle_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def describe_call(endpoint: str, params: dict, suffix: str = "json") -> str:
    """사용자에게 보여 줄 호출 주소. 인증키 자리는 안내 문구로 대체한다."""
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")}, safe=",")
    joined = f"&{query}" if query else ""
    return f"GET {BASE_URL}/{endpoint}.{suffix}?crtfc_key=(내 인증키){joined}"


class DartClient:
    def __init__(self, api_key: str, session=None):
        self._key = api_key
        self._session = session or _new_session()

    def _request(self, endpoint: str, suffix: str, params: dict):
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        clean["crtfc_key"] = self._key
        _throttle()
        try:
            response = self._session.get(
                f"{BASE_URL}/{endpoint}.{suffix}", params=clean, timeout=(10, 60), allow_redirects=False,
            )
        except requests.exceptions.SSLError:
            raise DartError("NET", "보안 연결을 확인하지 못했습니다. 회사망이라면 보안 인증서 설정을 확인해 주세요.") from None
        except requests.exceptions.Timeout:
            raise DartError("NET", "OpenDART 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.") from None
        except requests.exceptions.RequestException:
            raise DartError("NET", "OpenDART에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.") from None
        if response.status_code != 200:
            raise DartError("HTTP", f"OpenDART 서버가 요청을 처리하지 못했습니다(HTTP {response.status_code}).")
        return response

    @staticmethod
    def _raise_status(status: str, message: str = "") -> None:
        raise DartError(status, FRIENDLY_ERRORS.get(status, message or f"알 수 없는 응답입니다({status})."))

    def get_json(self, endpoint: str, allow_empty: bool = True, **params) -> dict:
        response = self._request(endpoint, "json", params)
        try:
            data = response.json()
        except ValueError:
            raise DartError("PARSE", "OpenDART 응답을 해석하지 못했습니다.") from None
        status = str(data.get("status", ""))
        if status == "000":
            return data
        if status == "013" and allow_empty:
            return {}
        self._raise_status(status, str(data.get("message", "")))
        return {}

    def get_list(self, endpoint: str, **params) -> list[dict]:
        return self.get_json(endpoint, **params).get("list", []) or []

    def get_binary(self, endpoint: str, **params) -> bytes:
        content = self._request(endpoint, "xml", params).content
        if content[:2] == b"PK":
            return content
        try:
            root = ET.fromstring(content)
            status, message = root.findtext("status", ""), root.findtext("message", "")
        except ET.ParseError:
            raise DartError("PARSE", "내려받은 파일 형식을 확인하지 못했습니다.") from None
        self._raise_status(status, message)
        return b""
