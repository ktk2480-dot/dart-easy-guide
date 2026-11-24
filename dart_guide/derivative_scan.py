"""DART 전체 공시 기업의 파생거래 기재 내역을 호출량과 데이터양을 제한하며 조사한다.

진행 상태와 결과만 SQLite에 저장하고 공시 원문은 저장하지 않는다. 앱을 껐다 켜도 남은 건부터 이어서 실행한다.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
import threading
import time
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from .analysis import _AUDIT_NAME, YearDoc, _load_year, derivative_for_doc
from .client import DartClient, DartError, KEY_ERROR_CODES
from .corp_index import Company
from .derivative_contracts import FX_INSTRUMENTS, notional_summary
from .examples import Api, viewer_url
from .filing import squash
from .formatting import digits_only
from .keystore import app_data_dir
from .latest import _REPORT_NAME, Filing

KST = timezone(timedelta(hours=9))
_MAX_NETWORK_FAILURES = 8
_MAX_DETAIL_TABLES = 4
_MAX_DETAIL_ROWS = 20
_MAX_DETAIL_CONTRACTS = 500
_DEFAULT_MB = {"사업보고서": 0.35, "감사보고서": 0.03}  # 실제 표본 평균. 20건 이상 받으면 실측값으로 바꿔 계산한다.


@dataclass(frozen=True)
class Scope:
    label: str
    codes: tuple[str, ...]
    corp_cls: str
    order: int


SCOPES = {
    "Y": Scope("코스피", ("A001",), "Y", 1),
    "K": Scope("코스닥", ("A001",), "K", 2),
    "N": Scope("코넥스", ("A001",), "N", 3),
    "E": Scope("사업보고서 제출 비상장", ("A001",), "E", 4),
    "AUDIT": Scope("외부감사 비상장", ("F002", "F001"), "E", 5),
}
_PRIORITY = {"A001": 3, "F002": 2, "F001": 1}  # 같은 회사·같은 결산기는 정보가 많은 보고서 하나만 읽는다.

INSTRUMENTS = (
    ("통화선도", r"통화선도|선물환", True),
    ("통화스왑", r"통화스왑|(?<![A-Za-z])CRS(?![A-Za-z])", True),
    ("통화옵션", r"통화옵션|KIKO|키코", True),
    ("이자율스왑", r"이자율스왑|금리스왑|(?<![A-Za-z])IRS(?![A-Za-z])", False),
    ("이자율선도·옵션", r"이자율선도|이자율옵션", False),
    ("상품파생", r"상품선물|상품스왑", False),
    ("주식파생", r"주식선도|주식옵션", False),
    ("풋·콜옵션", r"풋옵션|콜옵션|풋백옵션", False),
)


_FX_PURPOSE = re.compile(r"(환율|외화|환위험|외환)[^.。]{0,60}파생|파생[^.。]{0,60}(환율|외화|환위험|외환)")


@dataclass(frozen=True)
class Policy:
    name: str
    calls_per_minute: int
    mb_per_minute: float
    calls_per_day: int
    mb_per_day: float
    skip_office_hours: bool = False

    @property
    def label(self) -> str:
        return f"{self.name} (분당 {self.calls_per_minute}건·{self.mb_per_minute:.0f}MB, 하루 {self.calls_per_day:,}건·{self.mb_per_day / 1000:.1f}GB)"


# OpenDART의 하루 한도(일반적으로 20,000건)를 앱의 다른 기능과 나눠 쓰도록 여유를 둔다.
PRESETS = (
    Policy("보통", 60, 20, 10000, 3000),
    Policy("안전", 30, 10, 5000, 1500),
    Policy("빠름", 120, 40, 15000, 5000),
)


class ScanCancelled(Exception):
    pass


def next_reset(now: datetime) -> datetime:
    """OpenDART 하루 한도가 풀린 뒤 여유를 두고 다시 시작할 시각(한국시간 00:05)."""
    return (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)


class ScanStore:
    def __init__(self, path: Path | None = None):
        self.path = path or app_data_dir() / "derivative_scan.sqlite"
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS target (
                    corp_code TEXT, period_end TEXT, rcept_no TEXT, corp_name TEXT, stock_code TEXT, corp_cls TEXT,
                    scope TEXT, source TEXT, year INTEGER, report_nm TEXT, rcept_dt TEXT, priority INTEGER, sort_order INTEGER,
                    state TEXT DEFAULT 'pending', status TEXT DEFAULT '', instruments TEXT DEFAULT '', has_fx INTEGER DEFAULT 0,
                    table_count INTEGER DEFAULT 0, detail BLOB, used_rcept TEXT DEFAULT '', zip_bytes INTEGER DEFAULT 0,
                    updated_at TEXT DEFAULT '', PRIMARY KEY (corp_code, period_end)
                );
                CREATE INDEX IF NOT EXISTS idx_queue ON target(state, year, sort_order);
                CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, calls INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0);
            """)
            existing = {row["name"] for row in db.execute("PRAGMA table_info(target)")}
            if "notionals" not in existing:
                db.execute("ALTER TABLE target ADD COLUMN notionals TEXT DEFAULT ''")
            if "contract_count" not in existing:
                db.execute("ALTER TABLE target ADD COLUMN contract_count INTEGER DEFAULT 0")
            db.commit()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    # ------------------------------------------------- 사용량
    def add_usage(self, day: str, calls: int = 0, data: int = 0) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO usage(day, calls, bytes) VALUES (?,?,?) "
                       "ON CONFLICT(day) DO UPDATE SET calls = calls + excluded.calls, bytes = bytes + excluded.bytes", (day, calls, data))
            db.commit()

    def usage(self, day: str) -> tuple[int, int]:
        with self._connect() as db:
            row = db.execute("SELECT calls, bytes FROM usage WHERE day = ?", (day,)).fetchone()
        return (row["calls"], row["bytes"]) if row else (0, 0)

    # ------------------------------------------------- 대상
    def add_targets(self, rows: Iterable[dict]) -> None:
        sql = """INSERT INTO target (corp_code, period_end, rcept_no, corp_name, stock_code, corp_cls, scope, source, year, report_nm,
                                     rcept_dt, priority, sort_order)
                 VALUES (:corp_code, :period_end, :rcept_no, :corp_name, :stock_code, :corp_cls, :scope, :source, :year, :report_nm,
                         :rcept_dt, :priority, :sort_order)
                 ON CONFLICT(corp_code, period_end) DO UPDATE SET
                     rcept_no = excluded.rcept_no, corp_name = excluded.corp_name, stock_code = excluded.stock_code,
                     corp_cls = excluded.corp_cls, scope = excluded.scope, source = excluded.source, report_nm = excluded.report_nm,
                     rcept_dt = excluded.rcept_dt, priority = excluded.priority, sort_order = excluded.sort_order,
                     state = 'pending', status = '', instruments = '', has_fx = 0, table_count = 0, detail = NULL, used_rcept = '',
                     notionals = '', contract_count = 0
                 WHERE (excluded.priority, excluded.rcept_no) > (target.priority, target.rcept_no)"""
        with self._connect() as db:
            db.executemany(sql, list(rows))
            db.commit()

    @staticmethod
    def _where(years: Iterable[int], scopes: Iterable[str]) -> tuple[str, list]:
        years, scopes = list(years), list(scopes)
        return (f"year IN ({','.join('?' * len(years))}) AND scope IN ({','.join('?' * len(scopes))})", [*years, *scopes])

    def next_pending(self, years, scopes) -> dict | None:
        where, args = self._where(years, scopes)
        with self._connect() as db:
            row = db.execute(f"SELECT * FROM target WHERE state = 'pending' AND {where} ORDER BY year DESC, sort_order, corp_code LIMIT 1", args).fetchone()
        return dict(row) if row else None

    def finish(self, target: dict, result: dict) -> None:
        with self._connect() as db:
            db.execute("""UPDATE target SET state = :state, status = :status, instruments = :instruments, has_fx = :has_fx,
                          table_count = :table_count, detail = :detail, used_rcept = :used_rcept, zip_bytes = :zip_bytes,
                          notionals = :notionals, contract_count = :contract_count, updated_at = :updated_at
                          WHERE corp_code = :corp_code AND period_end = :period_end""",
                       {"notionals": "", "contract_count": 0, **result, "corp_code": target["corp_code"], "period_end": target["period_end"],
                        "updated_at": datetime.now(KST).isoformat(timespec="seconds")})
            db.commit()

    def mark(self, target: dict, state: str) -> None:
        self.finish(target, {"state": state, "status": "", "instruments": "", "has_fx": 0, "table_count": 0, "detail": None,
                             "used_rcept": "", "zip_bytes": 0})

    def retry_failed(self, years, scopes) -> int:
        where, args = self._where(years, scopes)
        with self._connect() as db:
            count = db.execute(f"UPDATE target SET state = 'pending' WHERE state IN ('failed', 'nofile') AND {where}", args).rowcount
            db.commit()
        return count

    def stats(self, years, scopes) -> dict:
        out = {"total": 0, "pending": 0, "done": 0, "failed": 0, "nofile": 0, "있음": 0, "없음": 0, "확인 필요": 0, "기재 없음": 0, "fx": 0}
        if not list(years) or not list(scopes):
            return out
        where, args = self._where(years, scopes)
        with self._connect() as db:
            for row in db.execute(f"SELECT state, status, COUNT(*) n, SUM(has_fx) fx FROM target WHERE {where} GROUP BY state, status", args):
                out["total"] += row["n"]
                out[row["state"]] = out.get(row["state"], 0) + row["n"]
                if row["state"] == "done":
                    out[row["status"]] = out.get(row["status"], 0) + row["n"]
                    out["fx"] += row["fx"] or 0
        return out

    def estimate(self, years, scopes, policy: Policy) -> dict:
        where, args = self._where(years, scopes)
        with self._connect() as db:
            averages = {r["source"]: r["mean"] for r in db.execute(
                "SELECT source, AVG(zip_bytes) mean, COUNT(*) n FROM target WHERE state = 'done' AND zip_bytes > 0 GROUP BY source HAVING n >= 20")}
            rows = db.execute(f"SELECT source, COUNT(*) n FROM target WHERE state = 'pending' AND {where} GROUP BY source", args).fetchall()
        calls = sum(r["n"] for r in rows)
        mb = sum(r["n"] * (averages[r["source"]] / 1e6 if r["source"] in averages else _DEFAULT_MB.get(r["source"], 0.26)) for r in rows)
        hours = max(calls / policy.calls_per_minute, mb / policy.mb_per_minute) / 60
        days = max(math.ceil(calls / policy.calls_per_day), math.ceil(mb / policy.mb_per_day)) if calls else 0
        return {"calls": calls, "mb": mb, "hours": hours, "days": days}

    # ------------------------------------------------- 결과
    @staticmethod
    def _result_filter(years, scopes, text="", status="", fx_only=False) -> tuple[str, list]:
        where, args = ScanStore._where(years, scopes)
        where += " AND state = 'done'"
        if text.strip():
            where += " AND (corp_name LIKE ? OR stock_code LIKE ?)"
            like = "%" + text.strip().replace("%", "") + "%"
            args += [like, like]
        if status:
            where += " AND status = ?"
            args.append(status)
        if fx_only:
            where += " AND has_fx = 1"
        return where, args

    def results(self, years, scopes, text="", status="", fx_only=False, limit=500) -> tuple[list[dict], int]:
        if not list(years) or not list(scopes):
            return [], 0
        where, args = self._result_filter(years, scopes, text, status, fx_only)
        with self._connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM target WHERE {where}", args).fetchone()[0]
            rows = db.execute(f"""SELECT corp_code, period_end, corp_name, stock_code, scope, year, status, instruments, has_fx, table_count,
                                         notionals, contract_count
                                  FROM target WHERE {where}
                                  ORDER BY (status = '있음') DESC, has_fx DESC, year DESC, sort_order, corp_name LIMIT ?""", [*args, limit]).fetchall()
        return [dict(r) for r in rows], total

    def detail(self, corp_code: str, period_end: str) -> tuple[dict, dict]:
        with self._connect() as db:
            row = dict(db.execute("SELECT * FROM target WHERE corp_code = ? AND period_end = ?", (corp_code, period_end)).fetchone())
        blob = row.pop("detail", None)
        return row, json.loads(zlib.decompress(blob).decode("utf-8")) if blob else {"statements": [], "tables": []}

    def export_csv(self, path: str | Path, years, scopes, text="", status="", fx_only=False) -> tuple[Path, Path, Path, int]:
        """요약, 계약 상세, 근거 표를 Excel에서 바로 열리는 CSV 세 개로 저장한다."""
        summary = Path(path)
        tables = summary.with_name(f"{summary.stem}_표내용.csv")
        contracts = summary.with_name(f"{summary.stem}_계약상세.csv")
        where, args = self._result_filter(years, scopes, text, status, fx_only)
        count = 0
        with self._connect() as db, summary.open("w", newline="", encoding="utf-8-sig") as first, \
                tables.open("w", newline="", encoding="utf-8-sig") as second, contracts.open("w", newline="", encoding="utf-8-sig") as third:
            head, body, deals = csv.writer(first), csv.writer(second), csv.writer(third)
            head.writerow(["회사", "종목코드", "고유번호", "구분", "사업연도", "보고서", "접수번호", "기재 상태", "상품", "외환파생", "상품별 액면 합계",
                           "계약 행 수", "근거 표 수", "원문"])
            body.writerow(["회사", "종목코드", "사업연도", "접수번호", "표 제목", "단위", "행", "내용(열별)"])
            deals.writerow(["회사", "종목코드", "사업연도", "접수번호", "상품", "공시 상품명", "통화", "액면(실제 금액)", "액면 공시 원문", "포지션", "매도", "매입",
                            "거래목적", "약정환율·금리", "계약일", "만기", "거래상대방", "평가·장부", "출처", "표 제목", "원문 확인 필요", "기말 전 만기"])
            for row in db.execute(f"SELECT * FROM target WHERE {where} ORDER BY year DESC, sort_order, corp_name", args):
                number = row["used_rcept"] or row["rcept_no"]
                head.writerow([_safe_cell(v) for v in (
                    row["corp_name"], row["stock_code"], row["corp_code"], SCOPES[row["scope"]].label, row["year"], row["report_nm"], number,
                    row["status"], row["instruments"], "Y" if row["has_fx"] else "N", row["notionals"], row["contract_count"], row["table_count"],
                    viewer_url(number) or "")])
                count += 1
                detail = json.loads(zlib.decompress(row["detail"]).decode("utf-8")) if row["detail"] else {"tables": []}
                for table in detail["tables"]:
                    for index, cells in enumerate(table["rows"], 1):
                        body.writerow([_safe_cell(v) for v in (row["corp_name"], row["stock_code"], row["year"], number, table["title"], table["unit"], index, *cells)])
                for c in detail.get("contracts", []):
                    deals.writerow([_safe_cell(v) for v in (
                        row["corp_name"], row["stock_code"], row["year"], number, c["instrument"], c["label"], c["currency"],
                        "" if c["notional"] is None else f"{c['notional']:.2f}".rstrip("0").rstrip("."), c["notional_text"], c["side"], c["sell"], c["buy"],
                        c["purpose"], c["rate"], c["start"], c["maturity"], c["counterparty"], c["valuation"], c["source"], c["title"],
                        "Y" if c["check"] else "N", "Y" if c.get("expired") else "N")])
        return summary, tables, contracts, count


def _safe_cell(value) -> str:
    """공시 문구가 Excel 수식으로 실행되지 않게 막는다. 음수 같은 일반 숫자는 그대로 둔다."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "@", "\t", "\r") or (text[:1] == "-" and not re.fullmatch(r"-[\d,.]*", text)):
        return "'" + text
    return text


class RateLimiter:
    """분당 호출 수, 분당 데이터, 하루 한도, 업무시간 제외 조건을 모두 지키도록 다음 호출을 기다리게 한다."""

    def __init__(self, policy: Policy, store: ScanStore, cancel: threading.Event, clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], object] | None = None):
        self.policy, self.store, self.cancel, self.clock = policy, store, cancel, clock
        self.sleep = sleeper or cancel.wait
        self.message = ""
        self._call_free = 0.0
        self._data_free = 0.0
        self._blocked_until = 0.0
        self._blocked_reason = ""

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), KST)

    def block(self, until: float, reason: str) -> None:
        self._blocked_until, self._blocked_reason = max(self._blocked_until, until), reason

    def _delay(self) -> tuple[float, str]:
        moment = self.clock()
        now = datetime.fromtimestamp(moment, KST)
        p = self.policy
        if moment < self._blocked_until:
            return self._blocked_until - moment, self._blocked_reason
        if p.skip_office_hours and now.weekday() < 5 and 9 <= now.hour < 18:
            end = now.replace(hour=18, minute=0, second=0, microsecond=0)
            return (end - now).total_seconds(), "평일 업무시간이라 18:00까지 쉽니다."
        calls, data = self.store.usage(now.date().isoformat())
        if calls >= p.calls_per_day or data >= p.mb_per_day * 1e6:
            until = next_reset(now)
            reason = f"오늘 사용 한도에 도달해 {until:%m-%d %H:%M}에 이어서 실행합니다."
            self.block(until.timestamp(), reason)  # 자정 직후 바로 풀리지 않도록 재개 시각까지 묶어 둔다.
            return (until - now).total_seconds(), reason
        return max(self._call_free, self._data_free) - moment, "사용량 조절 중"

    def acquire(self) -> None:
        while True:
            if self.cancel.is_set():
                raise ScanCancelled
            delay, reason = self._delay()
            if delay <= 0:
                moment = self.clock()
                self._call_free = max(self._call_free, moment) + 60 / self.policy.calls_per_minute
                self.store.add_usage(self._now().date().isoformat(), calls=1)
                if reason == "사용량 조절 중":
                    self.message = ""
                return
            if delay > 2:
                self.message = reason
            self.sleep(min(delay, 2.0))

    def record_bytes(self, size: int) -> None:
        moment = self.clock()
        self._data_free = max(self._data_free, moment) + size / (self.policy.mb_per_minute * 1e6 / 60)
        self.store.add_usage(self._now().date().isoformat(), data=size)


class MeteredClient:
    """모든 OpenDART 호출이 사용량 제한을 거치게 한다."""

    def __init__(self, client: DartClient, limiter: RateLimiter):
        self.client, self.limiter = client, limiter
        self.total_bytes = 0

    def _count(self, size: int) -> None:
        self.total_bytes += size
        self.limiter.record_bytes(size)

    def get_json(self, endpoint: str, allow_empty: bool = True, **params) -> dict:
        self.limiter.acquire()
        data = self.client.get_json(endpoint, allow_empty=allow_empty, **params)
        self._count(len(json.dumps(data, ensure_ascii=False).encode("utf-8")))
        return data

    def get_list(self, endpoint: str, **params) -> list[dict]:
        return self.get_json(endpoint, **params).get("list", []) or []

    def get_binary(self, endpoint: str, **params) -> bytes:
        self.limiter.acquire()
        content = self.client.get_binary(endpoint, **params)
        self._count(len(content))
        return content


def _quarters(first: date, last: date) -> Iterable[tuple[date, date]]:
    """회사를 지정하지 않은 공시검색은 3개월까지만 되므로 분기로 나눈다."""
    cursor = date(first.year, (first.month - 1) // 3 * 3 + 1, 1)
    while cursor <= last:
        month = cursor.month + 3
        following = date(cursor.year + (month > 12), (month - 1) % 12 + 1, 1)
        yield cursor, min(following - timedelta(days=1), last)
        cursor = following


def _target_from(row: dict, scope: str, code: str, years: set[int]) -> dict | None:
    name = str(row.get("report_nm", "")).strip()
    if "철" in str(row.get("rm", "")):
        return None
    match = _REPORT_NAME.search(name) if code == "A001" else _AUDIT_NAME.search(name)
    if not match or (code == "A001" and match.group(1) != "사업"):
        return None
    year, month = match.group(2), match.group(3)
    if int(year) not in years:
        return None
    return {
        "corp_code": str(row.get("corp_code", "")), "period_end": f"{year}{month}", "rcept_no": digits_only(row.get("rcept_no")),
        "corp_name": str(row.get("corp_name", "")), "stock_code": str(row.get("stock_code") or "").strip(),
        "corp_cls": str(row.get("corp_cls", "")), "scope": scope, "source": "사업보고서" if code == "A001" else "감사보고서",
        "year": int(year), "report_nm": name, "rcept_dt": digits_only(row.get("rcept_dt")), "priority": _PRIORITY[code],
        "sort_order": SCOPES[scope].order,
    }


def scan_target(client: MeteredClient, target: dict) -> dict:
    before = client.total_bytes
    company = Company(target["corp_code"], target["corp_name"], target["stock_code"])
    filing = Filing(target["period_end"], "11011", int(target["period_end"][:4]), target["report_nm"], target["rcept_no"], target["rcept_dt"])
    doc: YearDoc = _load_year(client, Api(client), company, filing, target["source"])
    if not doc.found:
        return {"state": "nofile", "status": "", "instruments": "", "has_fx": 0, "table_count": 0, "detail": None,
                "used_rcept": "", "zip_bytes": client.total_bytes - before}
    entry = derivative_for_doc(doc, target["source"] == "감사보고서")
    tables = [{"title": title, "unit": table.unit, "rows": table.rows[:_MAX_DETAIL_ROWS]} for title, table in entry.tables[:_MAX_DETAIL_TABLES]]
    names, has_fx = [], False
    if entry.status == "있음":
        text = squash(" ".join(entry.statements) + " " + " ".join(table.text() for _, table in entry.tables))
        for name, pattern, fx in INSTRUMENTS:
            if re.search(pattern, text):
                names.append(name)
                has_fx = has_fx or fx
        # 상품명 없이 '환율 위험을 피하려 파생상품계약을 체결했다'고만 적는 회사도 외환 파생으로 분류한다.
        if not has_fx and any(_FX_PURPOSE.search(s) for s in entry.statements):
            names.insert(0, "외환 파생(종류 미기재)")
            has_fx = True
        names = names or ["기타 파생"]
        # 계약표에서 상품이 확인되면 문장에서 찾은 이름보다 그 결과를 우선한다.
        kinds = list(dict.fromkeys(c.instrument for c in entry.contracts))
        if kinds:
            names = kinds
            has_fx = has_fx or any(kind in FX_INSTRUMENTS for kind in kinds)
    contracts = [c.to_row() for c in entry.contracts[:_MAX_DETAIL_CONTRACTS]]
    detail = zlib.compress(json.dumps({"statements": entry.statements, "tables": tables, "contracts": contracts}, ensure_ascii=False).encode("utf-8"), 6)
    return {"state": "done", "status": entry.status, "instruments": ", ".join(names), "has_fx": int(has_fx), "table_count": len(entry.tables),
            "detail": detail, "used_rcept": doc.rcept_no, "zip_bytes": client.total_bytes - before,
            "notionals": notional_summary(entry.contracts), "contract_count": len(entry.contracts)}


class ScanRunner:
    """대상 확인과 조회를 전용 스레드에서 한 번에 하나만 실행한다."""

    def __init__(self, store: ScanStore):
        self.store = store
        self.message = ""
        self.error = ""
        self.mode = ""
        self.limiter: RateLimiter | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures = 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def status_text(self) -> str:
        waiting = self.limiter.message if self.limiter else ""
        return waiting or self.message

    def _start(self, mode: str, target: Callable, policy: Policy, client_factory: Callable[[], DartClient], *args) -> None:
        if self.running:
            return
        self.mode, self.error, self.message = mode, "", "시작하는 중입니다."
        self._failures = 0
        self._cancel = threading.Event()
        self.limiter = RateLimiter(policy, self.store, self._cancel)
        client = MeteredClient(client_factory(), self.limiter)
        self._thread = threading.Thread(target=self._guard, args=(target, client, *args), daemon=True, name=f"derivative-{mode}")
        self._thread.start()

    def _guard(self, target: Callable, *args) -> None:
        try:
            target(*args)
        except ScanCancelled:
            self.message = "일시정지했습니다. 다시 시작하면 남은 건부터 이어서 실행합니다."
        except Exception:
            # 예외 내용에 요청 주소(인증키 포함)가 섞일 수 있으므로 표시하지 않는다.
            self.error = "처리 중 문제가 발생해 멈췄습니다. 다시 시작하면 남은 건부터 이어서 실행합니다."
        finally:
            if self.limiter:
                self.limiter.message = ""

    def start_discovery(self, client_factory, years, scopes, policy: Policy) -> None:
        self._start("discover", self._discover, policy, client_factory, list(years), list(scopes))

    def start_scan(self, client_factory, years, scopes, policy: Policy) -> None:
        self._start("scan", self._scan, policy, client_factory, list(years), list(scopes))

    def stop(self, wait: float = 0) -> None:
        self._cancel.set()
        if wait and self._thread:
            self._thread.join(wait)

    def _retryable(self, error: DartError) -> bool:
        """기다리면 풀리는 오류면 대기를 걸고 True. 키 오류나 접근 제한은 바로 멈춘다."""
        now = time.time()
        if error.code == "020":
            until = next_reset(datetime.fromtimestamp(now, KST))
            self.limiter.block(until.timestamp(), f"OpenDART 하루 요청 한도에 도달해 {until:%m-%d %H:%M}에 이어서 실행합니다.")
            return True
        if error.code == "800":
            self.limiter.block(now + 1800, "OpenDART 시스템 점검 중이라 30분 뒤 다시 시도합니다.")
            return True
        if error.code in ("NET", "HTTP", "900"):
            self._failures += 1
            if self._failures >= _MAX_NETWORK_FAILURES:
                self.error = "네트워크 오류가 계속되어 멈췄습니다. 연결을 확인한 뒤 다시 시작해 주세요."
                return False
            delay = min(300, 15 * 2 ** (self._failures - 1))
            self.limiter.block(now + delay, f"연결 오류로 {delay}초 뒤 다시 시도합니다.")
            return True
        self.error = error.message if error.code in KEY_ERROR_CODES or error.code in ("012", "101") else f"조회를 멈췄습니다. {error.message}"
        return False

    def _discover(self, client: MeteredClient, years: list[int], scopes: list[str]) -> None:
        today = datetime.now(KST).date()
        first, last = date(min(years), 1, 1), min(date(max(years) + 1, 12, 31), today)
        wanted, found = set(years), {}
        for scope in scopes:
            spec = SCOPES[scope]
            for code in spec.codes:
                for begin, end in _quarters(first, last):
                    page = 1
                    while True:
                        self.message = f"{spec.label} 대상 확인 중 ({begin:%Y.%m}, {page}쪽) · 찾은 보고서 {len(found):,}건"
                        try:
                            data = client.get_json("list", bgn_de=begin.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"), last_reprt_at="Y",
                                                   pblntf_detail_ty=code, corp_cls=spec.corp_cls, page_no=str(page), page_count="100")
                        except DartError as error:
                            if self._retryable(error):
                                continue
                            return
                        self._failures = 0
                        for row in data.get("list", []):
                            item = _target_from(row, scope, code, wanted)
                            if not item:
                                continue
                            key = (item["corp_code"], item["period_end"])
                            if key not in found or (item["priority"], item["rcept_no"]) > (found[key]["priority"], found[key]["rcept_no"]):
                                found[key] = item
                        if page >= int(data.get("total_page") or 1):
                            break
                        page += 1
        self.store.add_targets(found.values())
        self.message = f"대상 확인을 마쳤습니다. 이번에 찾은 보고서 {len(found):,}건"

    def _scan(self, client: MeteredClient, years: list[int], scopes: list[str]) -> None:
        while True:
            if self._cancel.is_set():
                raise ScanCancelled
            target = self.store.next_pending(years, scopes)
            if target is None:
                self.message = "조회를 모두 마쳤습니다."
                return
            self.message = f"{target['year']}년 {target['corp_name']} 조회 중"
            try:
                result = scan_target(client, target)
            except ScanCancelled:
                raise
            except DartError as error:
                if error.code == "014":
                    self.store.mark(target, "nofile")
                elif error.code in ("PARSE", "APP"):
                    self.store.mark(target, "failed")
                elif not self._retryable(error):
                    return
                continue
            except Exception:
                self.store.mark(target, "failed")  # 형식이 특이한 보고서 한 건 때문에 전체를 멈추지 않는다.
                continue
            self._failures = 0
            self.store.finish(target, result)
