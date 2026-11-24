"""회사명으로 DART 고유번호를 찾기 위한 로컬 색인."""
from __future__ import annotations

import io
import os
import sqlite3
import time
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .client import DartClient, DartError
from .keystore import app_data_dir

REFRESH_DAYS = 7


@dataclass(frozen=True)
class Company:
    corp_code: str
    corp_name: str
    stock_code: str = ""
    eng_name: str = ""

    @property
    def label(self) -> str:
        return f"{self.corp_name} ({self.stock_code})" if self.stock_code else f"{self.corp_name} (비상장)"


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class CorpIndex:
    def __init__(self, path: Path | None = None):
        self.path = path or app_data_dir() / "corp_index.sqlite"

    @contextmanager
    def _connect(self):
        # sqlite3의 with 문은 연결을 닫지 않는다. Windows에서는 열린 파일을 교체할 수 없으므로 직접 닫는다.
        connection = sqlite3.connect(self.path)
        try:
            yield connection
        finally:
            connection.close()

    def is_ready(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            with self._connect() as db:
                return db.execute("SELECT COUNT(*) FROM corp").fetchone()[0] > 0
        except sqlite3.Error:
            return False

    def is_stale(self) -> bool:
        return not self.path.is_file() or time.time() - self.path.stat().st_mtime > REFRESH_DAYS * 86400

    def count(self) -> int:
        if not self.is_ready():
            return 0
        with self._connect() as db:
            return db.execute("SELECT COUNT(*) FROM corp").fetchone()[0]

    def refresh(self, client: DartClient) -> int:
        archive = client.get_binary("corpCode")
        rows = []
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                name = next(n for n in zipped.namelist() if n.lower().endswith(".xml"))
                with zipped.open(name) as handle:
                    for _, element in ET.iterparse(handle, events=("end",)):
                        if element.tag == "list":
                            code = (element.findtext("corp_code") or "").strip()
                            if code:
                                rows.append((
                                    code,
                                    (element.findtext("corp_name") or "").strip(),
                                    (element.findtext("stock_code") or "").strip(),
                                    (element.findtext("corp_eng_name") or "").strip(),
                                ))
                            element.clear()
        except (zipfile.BadZipFile, StopIteration, ET.ParseError):
            raise DartError("PARSE", "회사 목록 파일을 읽지 못했습니다. 잠시 후 다시 시도해 주세요.") from None
        if not rows:
            raise DartError("PARSE", "회사 목록이 비어 있습니다. 잠시 후 다시 시도해 주세요.")

        temp = self.path.with_suffix(".tmp")
        temp.unlink(missing_ok=True)
        db = sqlite3.connect(temp)
        try:
            db.execute("CREATE TABLE corp (corp_code TEXT PRIMARY KEY, corp_name TEXT, stock_code TEXT, eng_name TEXT)")
            db.executemany("INSERT OR REPLACE INTO corp VALUES (?,?,?,?)", rows)
            db.execute("CREATE INDEX idx_stock ON corp(stock_code)")
            db.commit()
        finally:
            db.close()
        for attempt in range(5):
            try:
                os.replace(temp, self.path)
                break
            except PermissionError:
                if attempt == 4:
                    temp.unlink(missing_ok=True)
                    raise DartError("APP", "회사 목록 파일을 교체하지 못했습니다. 잠시 후 다시 시도해 주세요.") from None
                time.sleep(0.2)
        return len(rows)

    def search(self, query: str, limit: int = 30, listed_only: bool = False) -> list[Company]:
        query = query.strip()
        if not query or not self.is_ready():
            return []
        contains, prefix = f"%{_like(query)}%", f"{_like(query)}%"
        listed = "AND stock_code <> ''" if listed_only else ""
        sql = f"""
            SELECT corp_code, corp_name, stock_code, eng_name FROM corp
            WHERE (corp_name LIKE ? ESCAPE '\\' OR eng_name LIKE ? ESCAPE '\\' OR stock_code LIKE ? ESCAPE '\\') {listed}
            ORDER BY (corp_name = ?) DESC, (stock_code = ?) DESC, (stock_code <> '') DESC,
                     (corp_name LIKE ? ESCAPE '\\') DESC, LENGTH(corp_name), corp_name
            LIMIT ?"""
        with self._connect() as db:
            rows = db.execute(sql, (contains, contains, prefix, query, query, prefix, limit)).fetchall()
        return [Company(*row) for row in rows]

    def by_stock_code(self, stock_code: str) -> Company | None:
        if not self.is_ready():
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT corp_code, corp_name, stock_code, eng_name FROM corp WHERE stock_code = ?", (stock_code,),
            ).fetchone()
        return Company(*row) if row else None


PROFILE_DAYS = 30


class ProfileCache:
    """대표이사와 주소처럼 회사 목록 파일에 없는 정보를 기업개황 API로 받아 둔다.

    회사 목록을 새로 받을 때 corp_index.sqlite는 통째로 바뀌므로, 지워지지 않게 파일을 따로 둔다.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or app_data_dir() / "corp_profile.sqlite"

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS profile (corp_code TEXT PRIMARY KEY, ceo TEXT, address TEXT, fetched_at REAL)")
            yield connection
        finally:
            connection.close()

    def get_many(self, codes: list[str]) -> dict[str, tuple[str, str]]:
        """저장한 지 30일이 안 된 값만 돌려준다. 대표이사는 바뀔 수 있기 때문이다."""
        codes = list(dict.fromkeys(codes))
        if not codes:
            return {}
        fresh = time.time() - PROFILE_DAYS * 86400
        marks = ",".join("?" * len(codes))
        try:
            with self._connect() as db:
                rows = db.execute(f"SELECT corp_code, ceo, address FROM profile WHERE fetched_at >= ? AND corp_code IN ({marks})",
                                  (fresh, *codes)).fetchall()
        except sqlite3.Error:
            return {}
        return {code: (ceo or "", address or "") for code, ceo, address in rows}

    def put(self, corp_code: str, ceo: str, address: str) -> None:
        try:
            with self._connect() as db:
                db.execute("INSERT OR REPLACE INTO profile VALUES (?,?,?,?)", (corp_code, ceo, address, time.time()))
                db.commit()
        except sqlite3.Error:
            pass
