"""PySide6 데스크톱 화면."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QDate, QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap, QPainter, QColor, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from . import APP_NAME, __version__
from .agent import AgentError, ClaudeAgent, ClaudeStatus, DartTools, check_login, open_login
from .analysis import analyze_company, build_report, contract_tables, display_table
from .derivative_contracts import Contract
from .derivative_scan import KST, PRESETS, SCOPES, ScanRunner, ScanStore
from .filing import FilingTable
from .catalog import (
    BINARY_ENDPOINTS, KEY_APPLY_URL, MULTI_COMPANY_ENDPOINTS, PARAM_CHOICES, REPORT_CHOICES, guide_link, load_catalog,
)
from .client import DartClient, DartError, KEY_ERROR_CODES, describe_call
from .corp_index import Company, CorpIndex
from .examples import (
    CATEGORIES, EXAMPLES, Example, RunInput, default_year, explorer_fetch, explorer_report, latest_for_endpoint,
    run_example, viewer_url,
)
from .formatting import months_ago
from .guide import KEY_STEPS, build_guide_report, glossary_html
from .keystore import KeyStore, KeyStoreError, is_valid_key, mask_key
from .report import Report, Section, Table, apply_to_document, export_pdf

POPULAR = [("삼성전자", "005930"), ("SK하이닉스", "000660"), ("현대차", "005380"), ("NAVER", "035420"), ("카카오", "035720")]

QSS = """
* { font-family: 'Malgun Gothic'; }
QWidget { color: #1b1f24; font-size: 10pt; }
QMainWindow, #page { background: #f4f6f9; }
#nav { background: #12233b; }
#brand { color: #ffffff; font-size: 14pt; font-weight: 700; }
#brandSub { color: #a9b8cc; font-size: 8.5pt; }
#navButton { background: transparent; color: #d4deea; border: none; border-radius: 7px; padding: 10px 13px; text-align: left; font-size: 10.5pt; }
#navButton:hover { background: #1e3555; color: #ffffff; }
#navButton:checked { background: #2a78d6; color: #ffffff; font-weight: 700; }
#keyBadge { color: #c6d3e2; font-size: 8.5pt; }
#h1 { font-size: 18pt; font-weight: 700; color: #10233f; }
#h2 { font-size: 12pt; font-weight: 700; color: #173b6c; }
#muted { color: #5b6470; }
#panel { background: #ffffff; border: 1px solid #dfe4ea; border-radius: 10px; }
#exampleCard { background: #ffffff; border: 1px solid #dfe4ea; border-radius: 10px; text-align: left; }
#exampleCard:hover { border: 1px solid #2a78d6; background: #f5f9ff; }
#exampleCard:focus { border: 2px solid #2a78d6; }
#cardTitle { font-size: 11pt; font-weight: 700; color: #10233f; background: transparent; }
#cardQuestion { color: #1a5fb4; background: transparent; }
#cardDescription { color: #5b6470; font-size: 9pt; background: transparent; }
#stepNo { background: #2a78d6; color: white; border-radius: 13px; font-weight: 700; }
QPushButton { background: #ffffff; border: 1px solid #c9d1db; border-radius: 6px; padding: 7px 13px; }
QPushButton:hover { border-color: #2a78d6; background: #f5f9ff; }
QPushButton:disabled { color: #9aa3ae; background: #f0f2f5; border-color: #e0e4e9; }
#primary { background: #1f66c1; color: white; border: none; font-weight: 700; padding: 9px 16px; }
#primary:hover { background: #17549f; }
#primary:disabled { background: #b8c5d6; color: #eef2f7; }
#chip { background: #eaf2fc; color: #17457f; border: 1px solid #c6daf3; border-radius: 12px; padding: 3px 10px; font-size: 9pt; }
#quick { padding: 3px 9px; font-size: 9pt; border-radius: 11px; color: #34404f; }
QLineEdit, QComboBox, QDateEdit { background: #ffffff; border: 1px solid #c9d1db; border-radius: 6px; padding: 6px 8px; min-height: 18px; }
QLineEdit:focus, QComboBox:focus, QDateEdit:focus { border: 1px solid #2a78d6; }
QComboBox::drop-down, QDateEdit::drop-down { border: none; width: 24px; }
QComboBox::down-arrow, QDateEdit::down-arrow { image: url("__CHEVRON__"); width: 12px; height: 12px; }
QTextBrowser { background: #ffffff; border: 1px solid #dfe4ea; border-radius: 10px; padding: 14px; }
QTableWidget { background: #ffffff; border: 1px solid #dfe4ea; border-radius: 6px; gridline-color: #eef1f5; outline: none; }
QTableWidget::item:selected { background: #dceafb; color: #10233f; }
QHeaderView::section { background: #edf1f6; color: #26364b; border: none; padding: 5px; font-weight: 700; }
QListWidget, QTreeWidget { background: #ffffff; border: 1px solid #dfe4ea; border-radius: 6px; outline: none; }
QListWidget::item, QTreeWidget::item { padding: 4px; }
QListWidget::item:selected, QTreeWidget::item:selected { background: #dceafb; color: #10233f; }
QScrollArea { border: none; background: transparent; }
QStatusBar { background: #ffffff; border-top: 1px solid #dfe4ea; }
QProgressBar { border: none; background: #e6ebf1; max-height: 4px; min-width: 150px; }
QProgressBar::chunk { background: #2a78d6; }
""".replace("__CHEVRON__", (Path(__file__).parent / "data" / "chevron.svg").as_posix())


def safe_open(url: QUrl | str) -> None:
    target = url if isinstance(url, QUrl) else QUrl(url)
    if target.scheme().lower() in ("http", "https"):
        QDesktopServices.openUrl(target)


def downloads_dir() -> Path:
    folder = Path.home() / "Downloads"
    return folder if folder.is_dir() else Path.home()


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#1f66c1"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setBrush(QColor("#ffffff"))
    for x, height in ((15, 16), (28, 27), (41, 38)):
        painter.drawRoundedRect(x, 50 - height, 9, height, 2, 2)
    painter.end()
    return QIcon(pixmap)


class _Task(QRunnable):
    def __init__(self, owner: "MainWindow", task_id: int, function: Callable):
        super().__init__()
        self.owner, self.task_id, self.function = owner, task_id, function

    def run(self):
        result, error = None, None
        try:
            result = self.function()
        except DartError as exc:
            error = exc
        except OSError:
            error = DartError("APP", "파일을 저장하지 못했습니다. 저장 위치와 파일 사용 여부를 확인해 주세요.")
        except Exception:
            # 예외 내용에 요청 주소가 섞일 수 있으므로 기록하거나 표시하지 않는다.
            error = DartError("APP", "처리 중 문제가 발생했습니다. 조건을 바꿔 다시 시도해 주세요.")
        self.owner.task_finished.emit(self.task_id, result, error)


class KeyDialog(QDialog):
    def __init__(self, parent=None, message: str = ""):
        super().__init__(parent)
        self.setWindowTitle("OpenDART 인증키 입력")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(label("인증키를 입력해 주세요", "h2"))
        layout.addWidget(label(message or "OpenDART에서 발급받은 40자리 인증키가 필요합니다.", "muted", True))
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setPlaceholderText("40자리 인증키 붙여넣기")
        self.edit.setMaxLength(60)
        self.edit.textChanged.connect(self._validate)
        reveal = QPushButton("보기")
        reveal.setCheckable(True)
        reveal.toggled.connect(lambda on: (self.edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password), reveal.setText("숨기기" if on else "보기")))
        row.addWidget(self.edit, 1)
        row.addWidget(reveal)
        layout.addLayout(row)
        self.hint = label("", "muted")
        layout.addWidget(self.hint)
        self.remember = QCheckBox("이 PC에 암호화해서 저장 (다음부터 입력 생략)")
        self.remember.setChecked(True)
        layout.addWidget(self.remember)
        layout.addWidget(label("Windows DPAPI로 암호화되어 현재 Windows 계정에서만 풀 수 있습니다.", "muted", True))
        link = QPushButton("인증키가 없어요 (발급 사이트 열기)")
        link.clicked.connect(lambda: safe_open(KEY_APPLY_URL))
        layout.addWidget(link, 0, Qt.AlignLeft)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("확인")
        self.buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        self.buttons.button(QDialogButtonBox.Cancel).setText("취소")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._validate("")

    def _validate(self, text: str):
        value = text.strip()
        ok = is_valid_key(value)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)
        self.hint.setText("" if not value or ok else f"현재 {len(value)}자입니다. 영문과 숫자 40자리를 입력해 주세요.")

    @property
    def value(self) -> str:
        return self.edit.text().strip()


class ReportView(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.anchorClicked.connect(safe_open)
        self.report: Report | None = None
        self._image_width = 0
        self._resize_timer = QTimer(self, singleShot=True, interval=140)
        self._resize_timer.timeout.connect(self._fit_images)

    def _target_width(self) -> int:
        return max(420, min(660, self.viewport().width() - 34))

    def _render(self):
        self._image_width = self._target_width()
        self.clear()
        self.document().setDefaultFont(QFont("Malgun Gothic", 10))
        apply_to_document(self.document(), self.report, self._image_width)

    def _fit_images(self):
        if self.report and self._target_width() != self._image_width:
            position = self.verticalScrollBar().value()
            self._render()
            self.verticalScrollBar().setValue(position)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start()

    def show_report(self, report: Report):
        self.report = report
        self._render()
        self.verticalScrollBar().setValue(0)

    def show_message(self, title: str, body: str):
        self.report = None
        self.setHtml(
            f'<div style="margin-top:70px;" align="center"><p style="font-size:14pt;font-weight:700;color:#173b6c;">{title}</p>'
            f'<p style="color:#5b6470;">{body}</p></div>'
        )


class CompanyPicker(QWidget):
    changed = Signal()

    def __init__(self, main: "MainWindow", multiple=False, listed_only=False, limit=5, optional=False, compact=False):
        super().__init__()
        self.main, self.multiple, self.listed_only, self.limit, self.optional = main, multiple, listed_only, limit, optional
        self.selected: list[Company] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("회사 이름 또는 종목코드 입력 (예: 삼성전자)")
        self.edit.setClearButtonEnabled(True)
        self.edit.textEdited.connect(self._on_edit)
        self.edit.returnPressed.connect(self._pick_first)
        layout.addWidget(self.edit)
        self.results = QListWidget()
        self.results.setMaximumHeight(170)
        self.results.hide()
        self.results.itemClicked.connect(self._pick_item)
        self.results.itemActivated.connect(self._pick_item)
        layout.addWidget(self.results)
        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(5)
        self.chip_grid = QWidget()
        self.chip_layout = QVBoxLayout(self.chip_grid)
        self.chip_layout.setContentsMargins(0, 0, 0, 0)
        self.chip_layout.setSpacing(4)
        layout.addWidget(self.chip_grid)
        if not compact:
            layout.addWidget(label("자주 찾는 회사", "muted"))
            quick = QGridLayout()
            quick.setSpacing(4)
            for index, (name, code) in enumerate(POPULAR):
                button = QPushButton(name)
                button.setObjectName("quick")
                button.clicked.connect(lambda _=False, c=code: self._pick_stock(c))
                quick.addWidget(button, index // 3, index % 3)
            layout.addLayout(quick)
        self.timer = QTimer(self, singleShot=True, interval=180)
        self.timer.timeout.connect(self._search)

    def _on_edit(self, text: str):
        if not text.strip():
            self.results.hide()
            return
        self.main.with_index(self.timer.start)

    def _search(self):
        rows = self.main.index.search(self.edit.text(), limit=25, listed_only=self.listed_only)
        self.results.clear()
        for company in rows:
            item = QListWidgetItem(company.label)
            item.setData(Qt.UserRole, company)
            self.results.addItem(item)
        if not rows:
            self.results.addItem("검색 결과가 없습니다")
            self.results.item(0).setFlags(Qt.NoItemFlags)
        self.results.setVisible(True)

    def _pick_first(self):
        if self.timer.isActive():
            self.timer.stop()
            self._search()
        if self.results.isVisible() and self.results.count() and self.results.item(0).data(Qt.UserRole):
            self._pick_item(self.results.item(0))

    def _pick_item(self, item: QListWidgetItem):
        company = item.data(Qt.UserRole)
        if company:
            self.add(company)

    def _pick_stock(self, code: str):
        def choose():
            company = self.main.index.by_stock_code(code)
            if company:
                self.add(company)
        self.main.with_index(choose)

    def add(self, company: Company):
        if self.multiple:
            if company in self.selected:
                pass
            elif len(self.selected) >= self.limit:
                self.main.status(f"회사는 최대 {self.limit}개까지 선택할 수 있습니다.")
            else:
                self.selected.append(company)
        else:
            self.selected = [company]
            self.main.last_company = company
        self.edit.clear()
        self.results.hide()
        self._render()
        self.changed.emit()

    def remove(self, company: Company):
        self.selected = [c for c in self.selected if c != company]
        self._render()
        self.changed.emit()

    def _render(self):
        while self.chip_layout.count():
            child = self.chip_layout.takeAt(0).widget()
            if child:
                child.deleteLater()
        for company in self.selected:
            chip = QPushButton(f"{company.label}   ×")
            chip.setObjectName("chip")
            chip.setToolTip("누르면 선택을 해제합니다")
            chip.clicked.connect(lambda _=False, c=company: self.remove(c))
            self.chip_layout.addWidget(chip)

    def is_ready(self) -> bool:
        needed = 2 if self.multiple else (0 if self.optional else 1)
        return len(self.selected) >= needed


class HomePage(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 22)
        outer.setSpacing(14)
        outer.addWidget(label("기업 공시, 질문만 고르면 됩니다", "h1"))
        outer.addWidget(label("코딩이나 재무 지식이 없어도 금융감독원 OpenDART의 공시 정보를 쉬운 말과 그래프로 볼 수 있습니다.", "muted", True))

        steps = QHBoxLayout()
        steps.setSpacing(12)
        data = [("1", "인증키 입력", "처음 한 번만 입력하면 암호화해 저장합니다."),
                ("2", "질문 고르기", "'돈을 잘 벌까?'처럼 궁금한 질문을 고릅니다."),
                ("3", "결과와 PDF", "쉬운 설명을 읽고 PDF로 저장합니다.")]
        for number, title, text in data:
            panel = QFrame(objectName="panel")
            box = QHBoxLayout(panel)
            box.setContentsMargins(14, 13, 14, 13)
            mark = label(number, "stepNo")
            mark.setFixedSize(26, 26)
            mark.setAlignment(Qt.AlignCenter)
            box.addWidget(mark, 0, Qt.AlignTop)
            texts = QVBoxLayout()
            texts.setSpacing(2)
            texts.addWidget(label(title, "h2"))
            texts.addWidget(label(text, "muted", True))
            box.addLayout(texts, 1)
            steps.addWidget(panel, 1)
        outer.addLayout(steps)

        key_panel = QFrame(objectName="panel")
        key_row = QHBoxLayout(key_panel)
        key_row.setContentsMargins(16, 12, 16, 12)
        self.key_text = label("", "", True)
        key_row.addWidget(self.key_text, 1)
        self.key_button = QPushButton("인증키 입력")
        self.key_button.setObjectName("primary")
        self.key_button.clicked.connect(lambda: main.go("settings"))
        key_row.addWidget(self.key_button)
        outer.addWidget(key_panel)

        outer.addWidget(label("이것부터 해 보세요", "h2"))
        grid = QGridLayout()
        grid.setSpacing(12)
        for index, key in enumerate(("performance", "health", "dividend", "events")):
            example = next(e for e in EXAMPLES if e.key == key)
            grid.addWidget(ExampleCard(example, main.open_example), 0, index)
        outer.addLayout(grid)

        actions = QHBoxLayout()
        all_button = QPushButton("예제 15개 모두 보기")
        all_button.setObjectName("primary")
        all_button.clicked.connect(lambda: main.go("examples"))
        guide_button = QPushButton("이용 가이드 PDF 받기")
        guide_button.clicked.connect(main.save_guide)
        actions.addWidget(all_button)
        actions.addWidget(guide_button)
        actions.addStretch(1)
        outer.addLayout(actions)
        outer.addStretch(1)
        outer.addWidget(label("이 프로그램의 결과는 공시 정리 자료이며 투자 권유가 아닙니다. 중요한 내용은 공시 원문을 확인해 주세요.", "muted", True))

    def refresh(self):
        if self.main.has_key():
            self.key_text.setText(f"<b>인증키 준비 완료</b> &nbsp; {mask_key(self.main.peek_key())}")
            self.key_button.setText("키 관리")
        else:
            self.key_text.setText("<b>인증키가 아직 없습니다.</b> 첫 조회 때 입력해도 됩니다.")
            self.key_button.setText("인증키 입력")


class ExampleCard(QPushButton):
    def __init__(self, example: Example, on_open: Callable[[Example], None]):
        super().__init__()
        self.setObjectName("exampleCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(98)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setAccessibleName(f"{example.title}: {example.question}")
        self.clicked.connect(lambda: on_open(example))
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(4)
        for text, name in ((example.title, "cardTitle"), (example.question, "cardQuestion"), (example.description, "cardDescription")):
            widget = label(text, name, True)
            widget.setAttribute(Qt.WA_TransparentForMouseEvents)
            box.addWidget(widget)
        box.addStretch(1)


class ExamplesPage(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 12, 12)
        outer.setSpacing(4)
        outer.addWidget(label("무엇이 궁금하세요?", "h1"))
        outer.addWidget(label("질문을 누르면 회사만 고르고 바로 결과를 볼 수 있습니다.", "muted"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget(objectName="page")
        grid = QVBoxLayout(inner)
        grid.setContentsMargins(0, 10, 16, 10)
        grid.setSpacing(8)
        for category in CATEGORIES:
            grid.addWidget(label(category, "h2"))
            cards = QGridLayout()
            cards.setSpacing(10)
            items = [e for e in EXAMPLES if e.category == category]
            for index, example in enumerate(items):
                cards.addWidget(ExampleCard(example, main.open_example), index // 3, index % 3)
            for column in range(3):
                cards.setColumnStretch(column, 1)
            grid.addLayout(cards)
            grid.addSpacing(8)
        grid.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)


class RunnerPage(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        self.example: Example | None = None
        self.controls: dict[str, QWidget] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 14)
        outer.setSpacing(10)
        top = QHBoxLayout()
        back = QPushButton("← 예제 목록")
        back.clicked.connect(lambda: main.go("examples"))
        top.addWidget(back)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = label("", "h1")
        self.question = label("", "muted")
        titles.addWidget(self.title)
        titles.addWidget(self.question)
        top.addSpacing(10)
        top.addLayout(titles, 1)
        self.pdf_button = QPushButton("PDF로 저장")
        self.pdf_button.setObjectName("primary")
        self.pdf_button.setEnabled(False)
        self.pdf_button.clicked.connect(lambda: main.save_report(self.view.report))
        top.addWidget(self.pdf_button)
        outer.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(14)
        self.side = QFrame(objectName="panel")
        self.side.setFixedWidth(330)
        self.form = QVBoxLayout(self.side)
        self.form.setContentsMargins(15, 15, 15, 15)
        self.form.setSpacing(7)
        body.addWidget(self.side)
        self.view = ReportView()
        body.addWidget(self.view, 1)
        outer.addLayout(body, 1)

    def load(self, example: Example):
        self.example = example
        self.title.setText(example.title)
        self.question.setText(example.question)
        self.pdf_button.setEnabled(False)
        self.view.show_message("조건을 고른 뒤 결과를 확인하세요", example.description)
        while self.form.count():
            item = self.form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.controls = {}
        step = 1
        for key in example.inputs:
            widget, heading = self._control(key)
            self.controls[key] = widget
            self.form.addWidget(label(f"{step}. {heading}", "h2"))
            self.form.addWidget(widget)
            self.form.addSpacing(4)
            step += 1
        self.run_button = QPushButton("결과 보기")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self.run)
        self.form.addWidget(self.run_button)
        self.form.addStretch(1)
        self.form.addWidget(label("사용 API: " + ", ".join(example.endpoints[:3]) + (f" 외 {len(example.endpoints) - 3}개" if len(example.endpoints) > 3 else ""), "muted", True))
        year, report = self.controls.get("year"), self.controls.get("report")
        if year is not None and report is not None:
            sync = lambda: report.setEnabled(year.currentData() != 0)
            year.currentIndexChanged.connect(sync)
            sync()
        self._update_state()

    def _control(self, key: str) -> tuple[QWidget, str]:
        if key == "company":
            picker = CompanyPicker(self.main)
            if self.main.last_company:
                picker.add(self.main.last_company)
            picker.changed.connect(self._update_state)
            return picker, "회사 선택"
        if key == "companies":
            picker = CompanyPicker(self.main, multiple=True, listed_only=True)
            if self.main.last_company and self.main.last_company.stock_code:
                picker.add(self.main.last_company)
            picker.changed.connect(self._update_state)
            return picker, "비교할 회사 2~5개"
        combo = QComboBox()
        if key == "year":
            combo.addItem("최신 공시 자동 반영 (추천)", 0)
            latest = default_year()
            for year in range(latest + 1, 2014, -1):
                combo.addItem(f"{year}년", year)
            combo.setToolTip("가장 최근 분기·반기·사업보고서를 자동으로 찾습니다.")
            return combo, "기준 시점"
        if key == "report":
            for text, code in REPORT_CHOICES:
                combo.addItem(text, code)
            combo.setToolTip("기준 시점에서 특정 연도를 고르면 선택할 수 있습니다.")
            return combo, "보고서"
        if key == "months":
            for text, value in (("최근 3개월", 3), ("최근 6개월", 6), ("최근 1년", 12), ("최근 3년", 36), ("최근 5년", 60)):
                combo.addItem(text, value)
            combo.setCurrentIndex(2)
            return combo, "기간"
        if key == "days":
            for text, value in (("오늘", 1), ("최근 3일", 3), ("최근 7일", 7)):
                combo.addItem(text, value)
            combo.setCurrentIndex(2)
            return combo, "기간"
        if key == "market":
            for text, value in (("전체 시장", ""), ("코스피", "Y"), ("코스닥", "K"), ("코넥스", "N"), ("기타법인", "E")):
                combo.addItem(text, value)
            return combo, "시장"
        if key == "dtype":
            combo.addItem("전체 공시", "")
            for text, value in PARAM_CHOICES["pblntf_ty"]:
                combo.addItem(text, value)
            return combo, "공시 종류"
        raise KeyError(key)

    def _update_state(self):
        ready = all(w.is_ready() for w in self.controls.values() if isinstance(w, CompanyPicker))
        if hasattr(self, "run_button"):
            self.run_button.setEnabled(ready)

    def _inputs(self) -> RunInput:
        c = self.controls
        data = RunInput()
        if "company" in c:
            data.company = c["company"].selected[0]
        if "companies" in c:
            data.companies = list(c["companies"].selected)
        for key, attr in (("year", "year"), ("report", "report_code"), ("months", "months"), ("days", "days"), ("market", "market"), ("dtype", "disclosure_type")):
            if key in c:
                setattr(data, attr, c[key].currentData())
        return data

    def run(self):
        key = self.main.require_key()
        if not key or not self.example:
            return
        example, inputs = self.example, self._inputs()
        self.run_button.setEnabled(False)
        self.pdf_button.setEnabled(False)
        self.view.show_message("불러오는 중입니다", "OpenDART에서 공시를 가져와 정리하고 있습니다.")

        def finish(report: Report):
            self._update_state()
            if self.example is example:
                self.view.show_report(report)
                self.pdf_button.setEnabled(True)

        def fail(error: DartError):
            self._update_state()
            if self.example is example:
                self.view.show_message("결과를 가져오지 못했습니다", error.message)

        self.main.run_task(lambda: run_example(example, self.main.make_client(key), inputs), finish, fail, f"{example.title} 조회 중")


class ParamForm(QWidget):
    def __init__(self, main: "MainWindow", entry: dict):
        super().__init__()
        self.entry = entry
        self.widgets: dict[str, tuple[str, QWidget]] = {}
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setLabelAlignment(Qt.AlignLeft)
        layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        for param in entry["params"]:
            key = param["key"]
            if key == "crtfc_key":
                continue
            kind, widget = self._widget(main, param)
            self.widgets[key] = (kind, widget)
            star = " *" if param["required"] else ""
            caption = QLabel(f"{param['name']}{star}")
            caption.setToolTip(param["desc"])
            widget.setToolTip(param["desc"])
            layout.addRow(caption, widget)

    def _widget(self, main: "MainWindow", param: dict) -> tuple[str, QWidget]:
        key = param["key"]
        if key == "corp_code":
            multiple = self.entry["endpoint"] in MULTI_COMPANY_ENDPOINTS
            picker = CompanyPicker(main, multiple=multiple, listed_only=multiple, optional=not param["required"], compact=True)
            if main.last_company and (not multiple or main.last_company.stock_code):
                picker.add(main.last_company)
            return "company", picker
        if key == "bsns_year":
            combo = QComboBox()
            for year in range(default_year() + 1, 2014, -1):
                combo.addItem(f"{year}년", str(year))
            combo.setCurrentIndex(1)
            return "combo", combo
        if key in PARAM_CHOICES:
            combo = QComboBox()
            if not param["required"]:
                combo.addItem("선택 안 함", "")
            for text, value in PARAM_CHOICES[key]:
                combo.addItem(text, value)
            return "combo", combo
        if key in ("bgn_de", "end_de"):
            edit = QDateEdit(calendarPopup=True)
            edit.setDisplayFormat("yyyy-MM-dd")
            day = date.today() if key == "end_de" else months_ago(date.today(), 3 if self.entry["endpoint"] == "list" else 12)
            edit.setDate(QDate(day.year, day.month, day.day))
            return "date", edit
        edit = QLineEdit()
        edit.setPlaceholderText({"rcept_no": "14자리 접수번호", "page_no": "비우면 전체 조회", "page_count": "비우면 전체 조회 (최대 100)", "sj_div": "예: BS1", "pblntf_detail_ty": "예: A001"}.get(key, ""))
        return "text", edit

    def values(self) -> tuple[dict, str | None]:
        out = {}
        required = {p["key"]: p for p in self.entry["params"]}
        for key, (kind, widget) in self.widgets.items():
            if kind == "company":
                if not widget.is_ready():
                    return {}, "회사를 2개 이상 선택해 주세요." if widget.multiple else "회사를 선택해 주세요."
                value = ",".join(c.corp_code for c in widget.selected)
            elif kind == "combo":
                value = widget.currentData()
            elif kind == "date":
                value = widget.date().toString("yyyyMMdd")
            else:
                value = widget.text().strip()
            if required[key]["required"] and not value:
                return {}, f"'{required[key]['name']}'을(를) 입력해 주세요."
            if value:
                out[key] = value
        if out.get("bgn_de", "") > out.get("end_de", "99999999"):
            return {}, "시작일이 종료일보다 늦습니다."
        return out, None

    def supports_latest(self) -> bool:
        return {"corp_code", "bsns_year", "reprt_code"} <= set(self.widgets)

    def first_company(self) -> Company | None:
        kind_widget = self.widgets.get("corp_code")
        return kind_widget[1].selected[0] if kind_widget and kind_widget[1].selected else None

    def extra_params(self) -> dict:
        """보고서 탐색에 함께 보내야 하는 필수 선택값(재무제표 구분, 지표 분류)."""
        return {key: self.widgets[key][1].currentData() for key in ("fs_div", "idx_cl_code") if key in self.widgets}

    def set_period(self, year: int, report_code: str) -> None:
        year_combo = self.widgets["bsns_year"][1]
        index = year_combo.findData(str(year))
        if index < 0:
            year_combo.insertItem(0, f"{year}년", str(year))
            index = 0
        year_combo.setCurrentIndex(index)
        report_combo = self.widgets["reprt_code"][1]
        report_combo.setCurrentIndex(report_combo.findData(report_code))


class ExplorerPage(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        self.entry: dict | None = None
        self.form: ParamForm | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 14)
        outer.setSpacing(8)
        outer.addWidget(label(f"전체 API {len(load_catalog())}개 둘러보기", "h1"))
        outer.addWidget(label("예제에 없는 정보가 필요할 때 사용하세요. 입력값은 쉬운 선택지로 바뀌어 있습니다.", "muted"))
        splitter = QSplitter()
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 6, 0)
        self.search = QLineEdit(placeholderText="API 검색 (예: 배당, 합병)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        left_box.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        groups: dict[str, QTreeWidgetItem] = {}
        for entry in load_catalog():
            parent = groups.get(entry["group"])
            if parent is None:
                parent = QTreeWidgetItem([entry["group"]])
                parent.setFlags(Qt.ItemIsEnabled)
                groups[entry["group"]] = parent
                self.tree.addTopLevelItem(parent)
            child = QTreeWidgetItem([entry["name"]])
            child.setData(0, Qt.UserRole, entry)
            child.setToolTip(0, entry["summary"])
            parent.addChild(child)
        self.tree.topLevelItem(0).setExpanded(True)
        self.tree.currentItemChanged.connect(self._select)
        left_box.addWidget(self.tree, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(6, 0, 0, 0)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        self.name = label("왼쪽에서 API를 고르세요", "h2")
        self.summary = label("", "muted", True)
        titles.addWidget(self.name)
        titles.addWidget(self.summary)
        head.addLayout(titles, 1)
        self.guide_button = QPushButton("공식 가이드")
        self.guide_button.setEnabled(False)
        self.guide_button.clicked.connect(lambda: self.entry and safe_open(guide_link(self.entry)))
        head.addWidget(self.guide_button)
        self.latest_button = QPushButton("최신 공시로 채우기")
        self.latest_button.setToolTip("선택한 회사의 이 항목이 들어 있는 가장 최근 보고서로 연도와 보고서를 맞춥니다.")
        self.latest_button.setEnabled(False)
        self.latest_button.clicked.connect(self.fill_latest)
        head.addWidget(self.latest_button)
        self.run_button = QPushButton("조회")
        self.run_button.setObjectName("primary")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.run)
        head.addWidget(self.run_button)
        self.pdf_button = QPushButton("PDF로 저장")
        self.pdf_button.setEnabled(False)
        self.pdf_button.clicked.connect(lambda: main.save_report(self.view.report))
        head.addWidget(self.pdf_button)
        right_box.addLayout(head)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setMaximumHeight(235)
        right_box.addWidget(self.form_scroll)
        self.view = ReportView()
        self.view.show_message("API를 선택해 보세요", "필수 항목(*)만 채우면 조회할 수 있습니다.")
        right_box.addWidget(self.view, 1)
        splitter.addWidget(right)
        splitter.setSizes([300, 900])
        outer.addWidget(splitter, 1)

    def _filter(self, text: str):
        query = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            visible = 0
            for j in range(parent.childCount()):
                child = parent.child(j)
                entry = child.data(0, Qt.UserRole)
                match = not query or query in entry["name"].lower() or query in entry["endpoint"].lower() or query in entry["summary"].lower()
                child.setHidden(not match)
                visible += match
            parent.setHidden(visible == 0)
            parent.setExpanded(bool(query) and visible > 0 or (not query and i == 0))

    def _select(self, current, _previous):
        entry = current.data(0, Qt.UserRole) if current else None
        if not entry:
            return
        self.entry = entry
        self.name.setText(f"{entry['name']}  ·  {entry['endpoint']}")
        self.summary.setText(entry["summary"])
        self.form = ParamForm(self.main, entry)
        holder = QWidget(objectName="page")
        box = QVBoxLayout(holder)
        box.setContentsMargins(4, 4, 12, 4)
        box.addWidget(self.form)
        box.addStretch(1)
        self.form_scroll.setWidget(holder)
        self.run_button.setEnabled(True)
        self.guide_button.setEnabled(True)
        self.latest_button.setEnabled(self.form.supports_latest())
        self.pdf_button.setEnabled(False)
        binary = entry["endpoint"] in BINARY_ENDPOINTS
        self.run_button.setText("파일 받기" if binary else "조회")
        self.view.show_message(entry["name"], "ZIP 파일로 내려받는 API입니다." if binary else "값을 확인하고 조회를 눌러 주세요.")

    def run(self):
        if not self.entry or not self.form:
            return
        params, problem = self.form.values()
        if problem:
            self.view.show_message("입력을 확인해 주세요", problem)
            return
        key = self.main.require_key()
        if not key:
            return
        entry = self.entry
        endpoint = entry["endpoint"]
        if endpoint in BINARY_ENDPOINTS:
            self._download(key, endpoint, params)
            return
        self.run_button.setEnabled(False)
        self.view.show_message("불러오는 중입니다", endpoint)

        def job():
            data = explorer_fetch(self.main.make_client(key), endpoint, params)
            return explorer_report(endpoint, data, describe_call(endpoint, params))

        def finish(report: Report):
            self.run_button.setEnabled(True)
            if self.entry is entry:
                self.view.show_report(report)
                self.pdf_button.setEnabled(True)

        def fail(error: DartError):
            self.run_button.setEnabled(True)
            if self.entry is entry:
                self.view.show_message("결과를 가져오지 못했습니다", error.message)

        self.main.run_task(job, finish, fail, f"{entry['name']} 조회 중")

    def fill_latest(self):
        if not self.entry or not self.form or not self.form.supports_latest():
            return
        company = self.form.first_company()
        if not company:
            self.view.show_message("회사를 먼저 선택해 주세요", "선택한 회사의 최신 보고서를 찾아 연도와 보고서를 채웁니다.")
            return
        key = self.main.require_key()
        if not key:
            return
        entry, form = self.entry, self.form
        endpoint, extra = entry["endpoint"], form.extra_params()
        self.latest_button.setEnabled(False)

        def finish(filing):
            if self.form is not form:
                return
            self.latest_button.setEnabled(True)
            if filing:
                form.set_period(filing.bsns_year, filing.reprt_code)
                self.view.show_message("최신 보고서로 맞췄습니다", f"{filing.label} ({filing.rcept_dt[:4]}.{filing.rcept_dt[4:6]}.{filing.rcept_dt[6:8]} 공시). 조회를 눌러 주세요.")
            else:
                self.view.show_message("최신 보고서를 찾지 못했습니다", "최근 정기보고서에 이 항목이 없습니다. 연도를 직접 골라 보세요.")

        def fail(error: DartError):
            if self.form is form:
                self.latest_button.setEnabled(True)
                self.view.show_message("최신 보고서를 찾지 못했습니다", error.message)

        self.main.run_task(lambda: latest_for_endpoint(self.main.make_client(key), company, endpoint, **extra), finish, fail, "최신 보고서 찾는 중")

    def _download(self, key: str, endpoint: str, params: dict):
        suggested = re.sub(r"\W+", "_", f"{endpoint}_{params.get('rcept_no', 'file')}") + ".zip"
        path, _ = QFileDialog.getSaveFileName(self, "ZIP 파일 저장", str(downloads_dir() / suggested), "ZIP 파일 (*.zip)")
        if not path:
            return
        self.run_button.setEnabled(False)

        def job():
            content = self.main.make_client(key).get_binary(endpoint, **params)
            Path(path).write_bytes(content)
            return len(content)

        def finish(size: int):
            self.run_button.setEnabled(True)
            self.view.show_message("저장했습니다", f"{Path(path).name} ({size / 1024:,.0f} KB)")

        def fail(error: DartError):
            self.run_button.setEnabled(True)
            self.view.show_message("파일을 받지 못했습니다", error.message)

        self.main.run_task(job, finish, fail, "파일 내려받는 중")


class AnalysisPage(QWidget):
    """업체명을 고르면 업체정보, 3개년 재무, 수출입, 파생거래를 한 번에 보여 준다."""

    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        self.company: Company | None = None
        self._run_id = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 14)
        outer.setSpacing(10)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("업체 분석", "h1"))
        titles.addWidget(label("업체명을 입력하면 최근 3개년 사업보고서나 감사보고서를 읽어 한 번에 정리합니다.", "muted"))
        top.addLayout(titles, 1)
        self.ai_button = QPushButton("AI 종합 의견")
        self.ai_button.setToolTip("이 업체의 분석 자료를 AI 에이전트가 읽고 핵심과 위험 요인을 정리합니다.")
        self.ai_button.setEnabled(False)
        self.ai_button.clicked.connect(self._ask_ai)
        top.addWidget(self.ai_button)
        self.pdf_button = QPushButton("PDF로 저장")
        self.pdf_button.setObjectName("primary")
        self.pdf_button.setEnabled(False)
        self.pdf_button.clicked.connect(lambda: main.save_report(self.view.report))
        top.addWidget(self.pdf_button)
        outer.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(14)
        side = QFrame(objectName="panel")
        side.setFixedWidth(330)
        form = QVBoxLayout(side)
        form.setContentsMargins(15, 15, 15, 15)
        form.setSpacing(7)
        form.addWidget(label("업체명", "h2"))
        self.picker = CompanyPicker(main)
        self.picker.changed.connect(self._picked)
        form.addWidget(self.picker)
        self.run_button = QPushButton("다시 분석")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.run)
        form.addWidget(self.run_button)
        form.addSpacing(8)
        form.addWidget(label("분석 항목", "h2"))
        for text in ("1. 업체정보", "2. 3개년 재무정보", "3. 3개년 수출/수입 분석", "4. 3개년 파생거래 내역", "5. 공시 한계와 추가 확인 자료"):
            form.addWidget(label(text))
        form.addStretch(1)
        form.addWidget(label("공시 원문의 표를 읽어 만들며, 합계를 확인할 수 없는 값은 추정하지 않습니다. 거래한도 산정 증빙으로는 별도 자료가 필요합니다.", "muted", True))
        body.addWidget(side)
        self.view = ReportView()
        self.view.show_message("업체명을 입력해 주세요", "목록에서 회사를 고르면 바로 분석을 시작합니다.")
        body.addWidget(self.view, 1)
        outer.addLayout(body, 1)

    def _picked(self):
        self.company = self.picker.selected[0] if self.picker.selected else None
        self.run_button.setEnabled(bool(self.company))
        self.ai_button.setEnabled(False)
        self.pdf_button.setEnabled(False)
        if self.company:
            self.run()
        else:
            self._run_id += 1
            self.view.show_message("업체명을 입력해 주세요", "목록에서 회사를 고르면 바로 분석을 시작합니다.")

    def run(self):
        company = self.company
        key = self.main.require_key()
        if not company or not key:
            return
        self._run_id += 1
        run_id = self._run_id
        self.run_button.setEnabled(False)
        self.ai_button.setEnabled(False)
        self.pdf_button.setEnabled(False)
        self.view.show_message(f"{company.corp_name} 분석 중입니다", "최근 3개년 공시 원문을 읽고 있습니다. 보통 10초 안팎 걸립니다.")

        def job():
            return build_report(analyze_company(self.main.make_client(key), company))

        def finish(report: Report):
            if run_id != self._run_id:
                return
            self.run_button.setEnabled(True)
            self.view.show_report(report)
            self.ai_button.setEnabled(True)
            self.pdf_button.setEnabled(True)

        def fail(error: DartError):
            if run_id != self._run_id:
                return
            self.run_button.setEnabled(True)
            self.view.show_message("분석하지 못했습니다", error.message)

        self.main.run_task(job, finish, fail, f"{company.corp_name} 분석 중")

    def _ask_ai(self):
        if self.company:
            self.main.ask_agent(
                f"{self.company.corp_name}(고유번호 {self.company.corp_code})의 업체 분석 자료를 읽고 종합 의견을 정리해 줘. "
                "사업과 재무 흐름, 수출입 구조, 파생거래와 환위험, 눈여겨볼 위험 요인 순서로 써 줘."
            )


def fix_bold(text: str) -> str:
    """'**9.6%**로'처럼 문장부호와 한글이 붙으면 마크다운 굵게가 풀리므로 그 경계에 아주 좁은 공백을 넣는다."""
    def mark(match: re.Match) -> str:
        inner = match.group(1)
        before = " " if not inner[0].isalnum() and match.start() and text[match.start() - 1].isalnum() else ""
        after = " " if not inner[-1].isalnum() and match.end() < len(text) and text[match.end()].isalnum() else ""
        return f"{before}**{inner}**{after}"
    return re.sub(r"\*\*([^*\n]+?)\*\*", mark, text)


class AgentPage(QWidget):
    """Claude Code 로그인으로 동작하는 대화 화면."""
    agent_event = Signal(str, str)
    SUGGESTIONS = ("삼성전자 환위험과 파생거래 요약해 줘", "현대차와 기아 수출 비중 비교해 줘", "농심 최근 3년 재무 흐름 알려 줘")

    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        self.agent: ClaudeAgent | None = None
        self.messages: list[list[str]] = []
        self.busy = False
        self.activity = ""
        self.agent_event.connect(self._on_event, Qt.QueuedConnection)
        self._render_timer = QTimer(self, singleShot=True, interval=120)
        self._render_timer.timeout.connect(self._render)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 14)
        outer.setSpacing(9)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("AI 에이전트", "h1"))
        self.state = label("Claude Code 로그인을 확인하는 중입니다.", "muted")
        titles.addWidget(self.state)
        top.addLayout(titles, 1)
        self.login_button = QPushButton("Claude 로그인")
        self.login_button.setToolTip("Claude Code의 공식 로그인 창을 엽니다. 이 앱은 계정 정보를 받지 않습니다.")
        self.login_button.clicked.connect(self._login)
        self.login_button.hide()
        top.addWidget(self.login_button)
        self.new_button = QPushButton("새 대화")
        self.new_button.clicked.connect(self.reset)
        top.addWidget(self.new_button)
        outer.addLayout(top)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(safe_open)
        outer.addWidget(self.view, 1)
        self.quick = QWidget()
        quick_row = QHBoxLayout(self.quick)
        quick_row.setContentsMargins(0, 0, 0, 0)
        quick_row.setSpacing(6)
        for text in self.SUGGESTIONS:
            button = QPushButton(text, objectName="quick")
            button.clicked.connect(lambda _=False, t=text: self.submit(t))
            quick_row.addWidget(button)
        quick_row.addStretch(1)
        outer.addWidget(self.quick)
        row = QHBoxLayout()
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("공시에 대해 물어보세요. Enter로 보내고 Shift+Enter로 줄을 바꿉니다.")
        self.input.setFixedHeight(66)
        self.input.setStyleSheet("QPlainTextEdit { background:#ffffff; border:1px solid #c9d1db; border-radius:6px; padding:5px; }")
        self.input.installEventFilter(self)
        row.addWidget(self.input, 1)
        self.send_button = QPushButton("보내기")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self._send_clicked)
        row.addWidget(self.send_button)
        outer.addLayout(row)
        outer.addWidget(label("질문과 조회한 공시는 내 Claude 계정으로 전송됩니다. 고객정보나 회사 내부 자료는 입력하지 마세요. AI 답변은 원문으로 확인해 주세요.", "muted", True))
        self._render()

    def eventFilter(self, watched, event):
        if watched is self.input and event.type() == event.Type.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not event.modifiers() & Qt.ShiftModifier:
                self._send_clicked()
                return True
        return super().eventFilter(watched, event)

    def refresh_login(self):
        def done(status: ClaudeStatus):
            suffix = f" 로그인 만료 예정: {status.expires}" if status.ready and status.expires else ""
            self.state.setText(("● " if status.ready else "○ ") + status.message + suffix)
            self.login_button.setVisible(not status.ready)

        self.main.run_task(check_login, done, lambda _e: self.state.setText("○ Claude Code 상태를 확인하지 못했습니다."), "Claude 로그인 확인 중", handle_key_error=False)

    def _login(self):
        try:
            open_login()
        except (AgentError, OSError) as exc:
            QMessageBox.warning(self, "Claude 로그인", str(exc) if isinstance(exc, AgentError) else "로그인 창을 열지 못했습니다.")
            return
        QMessageBox.information(self, "Claude 로그인", "열린 창의 안내에 따라 브라우저에서 로그인을 마친 뒤 확인을 눌러 주세요.")
        self.refresh_login()

    def _send_clicked(self):
        if self.busy:
            if self.agent:
                self.activity = "멈추는 중"
                self.agent.stop()
                self._render()
            return
        text = self.input.toPlainText().strip()
        if text:
            self.input.clear()
            self.submit(text)

    def submit(self, text: str):
        if self.busy:
            self.main.status("이전 답변이 끝난 뒤 다시 보내 주세요.")
            return
        key = self.main.require_key()
        if not key:
            return
        self.main.with_index(lambda: self._start(text, key))

    def _start(self, text: str, key: str):
        if self.busy:
            return
        if self.agent is None:
            tools = DartTools(lambda: self.main.make_client(self.main.session_key or key), self.main.index)
            self.agent = ClaudeAgent(tools, self.agent_event.emit)
        self.messages += [["user", text], ["assistant", ""]]
        self._set_busy(True, "생각하는 중")
        self._render()
        self.agent.send(text)

    def _set_busy(self, busy: bool, activity: str = ""):
        self.busy, self.activity = busy, activity
        self.send_button.setText("멈추기" if busy else "보내기")
        self.send_button.setObjectName("" if busy else "primary")
        self.send_button.style().unpolish(self.send_button)
        self.send_button.style().polish(self.send_button)
        self.quick.setVisible(not busy and not self.messages)
        self.new_button.setEnabled(not busy)

    @Slot(str, str)
    def _on_event(self, kind: str, text: str):
        if not self.busy:
            return
        if kind == "delta":
            self.messages[-1][1] += text
            self.activity = "답변 작성 중"
            self._render_timer.start()
        elif kind == "tool":
            self.activity = text
            self._render_timer.start()
        elif kind == "done":
            if not self.messages[-1][1]:
                self.messages[-1][1] = "_답변을 멈췄습니다._"
            self._set_busy(False)
            self._render()
        elif kind == "error":
            self.messages[-1][1] += f"\n\n**오류:** {text}"
            self._set_busy(False)
            self._render()
            self.refresh_login()

    def _render(self):
        bar = self.view.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 40
        position = bar.value()
        if not self.messages:
            self.view.setHtml(
                '<div align="center" style="margin-top:70px;"><p style="font-size:14pt;font-weight:700;color:#173b6c;">무엇을 분석할까요?</p>'
                '<p style="color:#5b6470;">회사 이름과 궁금한 점을 적으면 공시를 찾아 읽고 근거와 함께 답합니다.</p></div>'
            )
            return
        parts = []
        for role, content in self.messages:
            parts.append(f"#### {'나' if role == 'user' else 'Claude'}\n\n{fix_bold(content) if role == 'assistant' else content}\n")
        if self.busy and self.activity:
            parts.append(f"\n_{self.activity}…_\n")
        features = QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML
        self.view.document().setMarkdown("\n".join(parts), features)
        bar.setValue(bar.maximum() if follow else position)

    def reset(self):
        if self.busy:
            return
        self.shutdown()
        self.messages = []
        self._set_busy(False)
        self._render()

    def shutdown(self):
        agent, self.agent = self.agent, None
        if agent:
            agent.close()


class ScanPage(QWidget):
    """전체 공시 기업의 파생거래 기재 내역을 사용량을 조절하며 조사한다."""
    COLUMNS = ("회사", "구분", "사업연도", "기재 상태", "상품", "액면 합계(통화별)")

    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        self.store: ScanStore = main.scan_store
        self.runner = ScanRunner(self.store)
        self._was_running = False
        self._last_done = -1
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 14)
        outer.setSpacing(10)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("파생거래 전수조사", "h1"))
        titles.addWidget(label("DART 공시 기업의 파생상품 기재 내역을 사용량을 조절하며 조사합니다. 중단해도 이어서 실행됩니다.", "muted"))
        top.addLayout(titles, 1)
        self.csv_button = QPushButton("CSV로 저장")
        self.csv_button.setObjectName("primary")
        self.csv_button.clicked.connect(self._export)
        top.addWidget(self.csv_button)
        outer.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(14)
        side = QFrame(objectName="panel")
        side.setFixedWidth(330)
        form = QVBoxLayout(side)
        form.setContentsMargins(15, 13, 15, 13)
        form.setSpacing(5)
        form.addWidget(label("사업연도", "h2"))
        year_row = QHBoxLayout()
        self.year_boxes: dict[int, QCheckBox] = {}
        latest = default_year()
        for year in (latest, latest - 1, latest - 2):
            box = QCheckBox(f"{year}")
            box.setChecked(year == latest)
            box.toggled.connect(self._selection_changed)
            self.year_boxes[year] = box
            year_row.addWidget(box)
        year_row.addStretch(1)
        form.addLayout(year_row)
        form.addWidget(label("조사 대상", "h2"))
        self.scope_boxes: dict[str, QCheckBox] = {}
        for key, scope in SCOPES.items():
            text = scope.label + (" (연 약 4만 건)" if key == "AUDIT" else "")
            box = QCheckBox(text)
            box.setChecked(key != "AUDIT")
            box.toggled.connect(self._selection_changed)
            self.scope_boxes[key] = box
            form.addWidget(box)
        form.addWidget(label("사용량", "h2"))
        self.speed = QComboBox()
        for policy in PRESETS:
            self.speed.addItem(policy.label, policy)
        self.speed.view().setMinimumWidth(400)
        self.speed.currentIndexChanged.connect(self._refresh)
        form.addWidget(self.speed)
        self.office = QCheckBox("평일 09~18시에는 쉬기")
        self.office.toggled.connect(self._refresh)
        form.addWidget(self.office)
        form.addSpacing(4)
        self.check_button = QPushButton("1. 대상 확인")
        self.check_button.setToolTip("공시 목록만 읽어 조사할 보고서 수와 예상 사용량을 계산합니다.")
        self.check_button.clicked.connect(self._discover)
        form.addWidget(self.check_button)
        self.run_button = QPushButton("2. 조회 시작")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self._toggle)
        form.addWidget(self.run_button)
        self.retry_button = QPushButton("실패·미제공 건 다시 조회")
        self.retry_button.clicked.connect(self._retry)
        form.addWidget(self.retry_button)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        form.addWidget(self.bar)
        self.state = label("", "", True)
        form.addWidget(self.state)
        self.counts = label("", "muted", True)
        form.addWidget(self.counts)
        self.usage = label("", "muted", True)
        form.addWidget(self.usage)
        form.addStretch(1)
        body.addWidget(side)
        right = QVBoxLayout()
        right.setSpacing(7)
        filters = QHBoxLayout()
        self.search = QLineEdit(placeholderText="결과에서 회사명·종목코드 검색")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda: self._filter_timer.start())
        filters.addWidget(self.search, 1)
        self.status_filter = QComboBox()
        for text, value in (("상태 전체", ""), ("있음", "있음"), ("없음", "없음"), ("확인 필요", "확인 필요"), ("기재 없음", "기재 없음")):
            self.status_filter.addItem(text, value)
        self.status_filter.currentIndexChanged.connect(self._load_results)
        filters.addWidget(self.status_filter)
        self.fx_only = QCheckBox("외환 파생만")
        self.fx_only.toggled.connect(self._load_results)
        filters.addWidget(self.fx_only)
        right.addLayout(filters)
        self.result_note = label("", "muted")
        right.addWidget(self.result_note)
        splitter = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._show_detail)
        splitter.addWidget(self.table)
        self.view = ReportView()
        self.view.show_message("회사를 선택하세요", "기재 내용과 근거 표, 원문 링크를 보여 줍니다.")
        splitter.addWidget(self.view)
        splitter.setSizes([360, 320])
        right.addWidget(splitter, 1)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)
        self._filter_timer = QTimer(self, singleShot=True, interval=220)
        self._filter_timer.timeout.connect(self._load_results)
        self.timer = QTimer(self, interval=1500)
        self.timer.timeout.connect(self._tick)

    def _selected(self) -> tuple[list[int], list[str]]:
        return ([y for y, box in self.year_boxes.items() if box.isChecked()],
                [k for k, box in self.scope_boxes.items() if box.isChecked()])

    def _policy(self):
        return replace(self.speed.currentData(), skip_office_hours=self.office.isChecked())

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        self._refresh()
        self._load_results()

    def hideEvent(self, event):
        super().hideEvent(event)
        if not self.runner.running:
            self.timer.stop()

    def _selection_changed(self):
        self._refresh()
        self._load_results()

    def _tick(self):
        running = self.runner.running
        if self._was_running and not running:
            self._was_running = False
            self._refresh()
            self._load_results()
            if self.runner.error:
                QMessageBox.warning(self, "파생거래 전수조사", self.runner.error)
            return
        self._was_running = running
        if self.isVisible():
            self._refresh()
            if running and self._last_done >= 0 and self.table.rowCount() < 500:
                self._load_results(keep=True)

    def _refresh(self):
        years, scopes = self._selected()
        running = self.runner.running
        stats = self.store.stats(years, scopes)
        finished = stats["done"] + stats["failed"] + stats["nofile"]
        self.bar.setRange(0, max(stats["total"], 1))
        self.bar.setValue(finished)
        self.check_button.setEnabled(not running and bool(years and scopes))
        self.retry_button.setEnabled(not running and bool(stats["failed"] + stats["nofile"]))
        scanning = running and self.runner.mode == "scan"
        self.run_button.setText("일시정지" if scanning else "멈추기" if running else ("이어서 조회" if finished and stats["pending"] else "2. 조회 시작"))
        self.run_button.setEnabled(running or bool(stats["pending"]))
        for box in (*self.year_boxes.values(), *self.scope_boxes.values(), self.speed, self.office):
            box.setEnabled(not running)
        if running:
            text = self.runner.status_text
        elif self.runner.error:
            text = self.runner.error
        elif stats["pending"]:
            text = "이어서 조회할 수 있습니다." if finished else "조회할 준비가 되었습니다."
        elif stats["total"]:
            text = "선택한 범위의 조회를 마쳤습니다."
        else:
            text = "먼저 대상 확인을 눌러 주세요."
        if not running and stats["pending"] and not self.runner.error:
            estimate = self.store.estimate(years, scopes, self._policy())
            text += f"\n남은 {estimate['calls']:,}건 · 약 {estimate['mb'] / 1000:.1f}GB · 실행 약 {estimate['hours']:.1f}시간 (하루 한도 기준 {estimate['days']}일)"
        self.state.setText(text)
        self.counts.setText(f"완료 {finished:,} / 전체 {stats['total']:,}\n있음 {stats['있음']:,} (외환 {stats['fx']:,}) · 없음 {stats['없음']:,} · "
                            f"확인 필요 {stats['확인 필요']:,} · 기재 없음 {stats['기재 없음']:,} · 실패·미제공 {stats['failed'] + stats['nofile']:,}")
        calls, data = self.store.usage(datetime.now(KST).date().isoformat())
        policy = self._policy()
        self.usage.setText(f"오늘 사용: {calls:,} / {policy.calls_per_day:,}건 · {data / 1e6:,.0f} / {policy.mb_per_day:,.0f}MB")
        self._last_done = finished

    def _discover(self):
        years, scopes = self._selected()
        key = self.main.require_key()
        if not key or not years or not scopes:
            return
        self.runner.start_discovery(lambda: self.main.make_client(key), years, scopes, self._policy())
        self._was_running = True
        self.timer.start()
        self._refresh()

    def _toggle(self):
        if self.runner.running:
            self.runner.stop()
            self.state.setText("현재 건을 마치고 멈추는 중입니다.")
            return
        years, scopes = self._selected()
        key = self.main.require_key()
        if not key:
            return
        estimate = self.store.estimate(years, scopes, self._policy())
        answer = QMessageBox.question(
            self, "파생거래 전수조사",
            f"{estimate['calls']:,}건을 조회합니다.\n\n예상 데이터: 약 {estimate['mb'] / 1000:.1f}GB\n예상 실행 시간: 약 {estimate['hours']:.1f}시간 "
            f"(하루 한도 기준 {estimate['days']}일)\n\n조회 중에는 앱을 켜 두어야 합니다. 시작할까요?")
        if answer != QMessageBox.Yes:
            return
        self.runner.start_scan(lambda: self.main.make_client(key), years, scopes, self._policy())
        self._was_running = True
        self.timer.start()
        self._refresh()

    def _retry(self):
        years, scopes = self._selected()
        count = self.store.retry_failed(years, scopes)
        self.main.status(f"{count:,}건을 다시 조회 대기열에 넣었습니다.")
        self._refresh()

    def _filters(self) -> tuple:
        years, scopes = self._selected()
        return years, scopes, self.search.text(), self.status_filter.currentData(), self.fx_only.isChecked()

    def _load_results(self, *_args, keep: bool = False):
        selected = self._current_key() if keep else None
        position = self.table.verticalScrollBar().value()
        rows, total = self.store.results(*self._filters())
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (f"{row['corp_name']} ({row['stock_code']})" if row["stock_code"] else row["corp_name"], SCOPES[row["scope"]].label,
                      f"{row['year']}", row["status"], row["instruments"] or "-",
                      row["notionals"] or ("액면 미공시" if row["status"] == "있음" else "-"))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, (row["corp_code"], row["period_end"]))
                if column >= 4:
                    item.setToolTip(value)
                self.table.setItem(index, column, item)
            if selected == (row["corp_code"], row["period_end"]):
                self.table.selectRow(index)
        self.table.blockSignals(False)
        if keep:
            self.table.verticalScrollBar().setValue(position)
        self.result_note.setText(f"결과 {total:,}건" + (f" 중 {len(rows):,}건 표시 (전체는 CSV로 저장)" if total > len(rows) else ""))

    def _current_key(self):
        items = self.table.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _show_detail(self):
        key = self._current_key()
        if not key:
            return
        row, detail = self.store.detail(*key)
        sections = []
        contracts = [Contract(**item) for item in detail.get("contracts", [])]
        if contracts:
            totals, deals = contract_tables(contracts)
            sections += [Section("상품별 액면 합계", table=totals), Section("계약 상세", table=deals)]
        elif row["status"] == "있음":
            sections.append(Section("상품별 액면", ["공시 표에서 액면(계약금액)을 찾지 못했습니다. 회사가 장부금액이나 평가손익만 공시했을 수 있습니다."]))
        if detail["statements"]:
            sections.append(Section("공시 내용", bullets=detail["statements"]))
        for table in detail["tables"]:
            sections.append(Section(table["title"][:60] or "파생 관련 표", table=display_table(FilingTable(table["rows"], "", table["unit"]))))
        number = row["used_rcept"] or row["rcept_no"]
        url = viewer_url(number)
        if url:
            sections.append(Section("원문 확인", table=Table(["보고서", "바로가기"], [[row["report_nm"], "원문 열기"]], links={(0, 1): url}, widths=[72, 28])))
        summary = f"기재 상태: {row['status']}" + (f" · 상품: {row['instruments']}" if row["instruments"] else "")
        self.view.show_report(Report(f"{row['corp_name']} {row['year']}년 파생거래", f"{row['source']} 기준 · 기말 잔액 중심이며 타행별 내역은 알 수 없습니다.",
                                     [summary], sections=sections, file_stem=f"{row['corp_name']}_파생거래", show_disclaimer=False))

    def _export(self):
        filters = self._filters()
        _, total = self.store.results(*filters, limit=1)
        if not total:
            QMessageBox.information(self, "CSV로 저장", "저장할 결과가 없습니다.")
            return
        default = downloads_dir() / f"파생거래_전수조사_{date.today():%Y%m%d}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "CSV로 저장", str(default), "CSV 파일 (*.csv)")
        if not path:
            return
        try:
            summary, tables, deals, count = self.store.export_csv(path, *filters)
        except OSError:
            QMessageBox.warning(self, "CSV로 저장", "파일을 저장하지 못했습니다. 같은 이름의 파일이 열려 있다면 닫고 다시 시도해 주세요.")
            return
        QMessageBox.information(self, "CSV로 저장", f"{count:,}건을 저장했습니다.\n{summary.name}\n{deals.name}\n{tables.name}")

    def shutdown(self):
        self.timer.stop()
        self.runner.stop(wait=5)


class GlossaryPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 18)
        outer.setSpacing(10)
        outer.addWidget(label("용어 사전", "h1"))
        outer.addWidget(label("공시와 재무제표에 자주 나오는 말을 쉽게 풀었습니다.", "muted"))
        self.search = QLineEdit(placeholderText="용어 검색 (예: 부채비율)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._update)
        outer.addWidget(self.search)
        self.view = QTextBrowser()
        outer.addWidget(self.view, 1)
        self._update("")

    def _update(self, text: str):
        self.view.setHtml(glossary_html(text))


class SettingsPage(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 18)
        outer.setSpacing(12)
        outer.addWidget(label("인증키와 설정", "h1"))
        panel = QFrame(objectName="panel")
        box = QVBoxLayout(panel)
        box.setContentsMargins(18, 16, 18, 16)
        box.setSpacing(9)
        box.addWidget(label("OpenDART 인증키", "h2"))
        self.state = label("", "", True)
        box.addWidget(self.state)
        row = QHBoxLayout()
        change = QPushButton("키 입력 / 변경")
        change.setObjectName("primary")
        change.clicked.connect(lambda: main.ask_key())
        self.test = QPushButton("연결 테스트")
        self.test.clicked.connect(self._test)
        self.remove = QPushButton("저장된 키 삭제")
        self.remove.clicked.connect(self._delete)
        issue = QPushButton("키 발급 사이트")
        issue.clicked.connect(lambda: safe_open(KEY_APPLY_URL))
        for button in (change, self.test, self.remove, issue):
            row.addWidget(button)
        row.addStretch(1)
        box.addLayout(row)
        box.addWidget(label("키는 Windows DPAPI로 암호화해 이 PC의 내 계정 폴더에 저장합니다. 다른 계정이나 다른 PC로 파일을 복사해도 풀 수 없습니다. PDF와 화면의 호출 내역에는 키가 표시되지 않습니다.", "muted", True))
        outer.addWidget(panel)

        panel2 = QFrame(objectName="panel")
        box2 = QVBoxLayout(panel2)
        box2.setContentsMargins(18, 16, 18, 16)
        box2.setSpacing(9)
        box2.addWidget(label("회사 목록", "h2"))
        self.index_state = label("", "muted", True)
        box2.addWidget(self.index_state)
        refresh = QPushButton("회사 목록 새로 받기")
        refresh.clicked.connect(self._refresh)
        box2.addWidget(refresh, 0, Qt.AlignLeft)
        outer.addWidget(panel2)

        panel3 = QFrame(objectName="panel")
        box3 = QVBoxLayout(panel3)
        box3.setContentsMargins(18, 16, 18, 16)
        box3.addWidget(label("인증키 받는 법", "h2"))
        for index, step in enumerate(KEY_STEPS, 1):
            box3.addWidget(label(f"{index}. {step}", "muted", True))
        outer.addWidget(panel3)
        outer.addStretch(1)
        outer.addWidget(label(f"{APP_NAME} {__version__}", "muted"))

    def refresh_state(self):
        has_key = self.main.has_key()
        stored = self.main.keys.exists()
        if has_key:
            where = "암호화 저장됨" if stored else "이번 실행 동안만 사용"
            self.state.setText(f"<b>{mask_key(self.main.peek_key())}</b> &nbsp; ({where})")
        else:
            self.state.setText("등록된 인증키가 없습니다.")
        self.test.setEnabled(has_key)
        self.remove.setEnabled(stored or has_key)
        total = self.main.index.count()
        self.index_state.setText(f"{total:,}개 회사를 검색할 수 있습니다." if total else "첫 검색 때 회사 목록을 자동으로 내려받습니다.")

    def _test(self):
        key = self.main.require_key()
        if not key:
            return
        self.test.setEnabled(False)

        def done(_):
            self.refresh_state()
            QMessageBox.information(self, "연결 테스트", "OpenDART에 정상적으로 연결되었습니다.")

        def fail(error: DartError):
            self.refresh_state()
            QMessageBox.warning(self, "연결 테스트", error.message)

        self.main.run_task(lambda: self.main.make_client(key).get_json("company", allow_empty=False, corp_code="00126380"), done, fail, "연결 확인 중", handle_key_error=False)

    def _delete(self):
        answer = QMessageBox.question(self, "인증키 삭제", "저장된 인증키를 삭제할까요?\n삭제하면 다음 조회 때 다시 입력해야 합니다.")
        if answer == QMessageBox.Yes:
            self.main.forget_key()

    def _refresh(self):
        key = self.main.require_key()
        if key:
            self.main.refresh_index(key, self.refresh_state)


class MainWindow(QMainWindow):
    task_finished = Signal(int, object, object)

    def __init__(self, client_factory: Callable[[str], DartClient] = DartClient, keys: KeyStore | None = None, index: CorpIndex | None = None,
                 scan_store: ScanStore | None = None):
        super().__init__()
        self.make_client = client_factory
        self.keys = keys or KeyStore()
        self.index = index or CorpIndex()
        self.scan_store = scan_store or ScanStore(self.index.path.with_name("derivative_scan.sqlite"))
        self.session_key: str | None = None
        self.last_company: Company | None = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self._tasks: dict[int, tuple] = {}
        self._task_id = 0
        self._index_loading = False
        self._index_waiters: list[Callable] = []
        self.task_finished.connect(self._on_task_finished, Qt.QueuedConnection)

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1320, 860)
        self.setMinimumSize(1120, 700)
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        nav = QFrame(objectName="nav")
        nav.setFixedWidth(204)
        nav_box = QVBoxLayout(nav)
        nav_box.setContentsMargins(12, 20, 12, 14)
        nav_box.setSpacing(4)
        nav_box.addWidget(label(APP_NAME, "brand"))
        nav_box.addWidget(label("금융감독원 OpenDART 활용", "brandSub"))
        nav_box.addSpacing(16)
        self.stack = QStackedWidget()
        self.home = HomePage(self)
        self.analysis = AnalysisPage(self)
        self.agent_page = AgentPage(self)
        self.scan_page = ScanPage(self)
        self.examples = ExamplesPage(self)
        self.runner = RunnerPage(self)
        self.explorer = ExplorerPage(self)
        self.glossary = GlossaryPage()
        self.settings = SettingsPage(self)
        self.pages = {"home": self.home, "analysis": self.analysis, "scan": self.scan_page, "agent": self.agent_page, "examples": self.examples, "runner": self.runner,
                      "explorer": self.explorer, "glossary": self.glossary, "settings": self.settings}
        for page in self.pages.values():
            self.stack.addWidget(page)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text in (("home", "시작하기"), ("analysis", "업체 분석"), ("scan", "파생 전수조사"), ("agent", "AI 에이전트"), ("examples", "예제로 시작"), ("explorer", "전체 API"),
                          ("glossary", "용어 사전"), ("settings", "인증키·설정")):
            button = QPushButton(text, objectName="navButton", checkable=True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: self.go(k))
            self.nav_group.addButton(button)
            self.nav_buttons[key] = button
            nav_box.addWidget(button)
        nav_box.addStretch(1)
        guide = QPushButton("이용 가이드 PDF", objectName="navButton")
        guide.clicked.connect(self.save_guide)
        nav_box.addWidget(guide)
        self.key_badge = label("", "keyBadge", True)
        nav_box.addWidget(self.key_badge)
        layout.addWidget(nav)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.refresh_key_state()
        self.go("home")

    # ------------------------------------------------- 화면 전환과 상태
    def go(self, key: str):
        self.stack.setCurrentWidget(self.pages[key])
        target = "examples" if key == "runner" else key
        self.nav_buttons[target].setChecked(True)
        if key == "settings":
            self.settings.refresh_state()
        elif key == "agent":
            self.agent_page.refresh_login()

    def open_example(self, example: Example):
        self.runner.load(example)
        self.go("runner")

    def ask_agent(self, prompt: str):
        self.go("agent")
        self.agent_page.submit(prompt)

    def status(self, text: str, timeout: int = 6000):
        self.statusBar().showMessage(text, timeout)

    # ------------------------------------------------- 인증키
    def has_key(self) -> bool:
        return bool(self.session_key) or self.keys.exists()

    def peek_key(self) -> str:
        return self.require_key(prompt=False) or ""

    def require_key(self, prompt: bool = True) -> str | None:
        if self.session_key:
            return self.session_key
        if self.keys.exists():
            try:
                self.session_key = self.keys.load()
                return self.session_key
            except KeyStoreError as exc:
                if not prompt:
                    return None
                return self.ask_key(str(exc))
        return self.ask_key() if prompt else None

    def ask_key(self, message: str = "") -> str | None:
        dialog = KeyDialog(self, message)
        if dialog.exec() != QDialog.Accepted:
            return None
        value = dialog.value
        self.session_key = value
        if dialog.remember.isChecked():
            try:
                self.keys.save(value)
                self.status("인증키를 암호화해서 저장했습니다.")
            except (KeyStoreError, OSError) as exc:
                QMessageBox.warning(self, "저장 실패", f"키를 저장하지 못했습니다. 이번 실행 동안만 사용합니다.\n{exc}")
        else:
            self.keys.delete()
        self.refresh_key_state()
        return value

    def forget_key(self):
        self.session_key = None
        self.keys.delete()
        self.refresh_key_state()
        self.status("저장된 인증키를 삭제했습니다.")

    def refresh_key_state(self):
        self.key_badge.setText("● 인증키 준비됨" if self.has_key() else "○ 인증키 필요")
        self.home.refresh()
        self.settings.refresh_state()

    # ------------------------------------------------- 백그라운드 작업
    def run_task(self, function: Callable, on_done: Callable, on_error: Callable | None = None, busy: str = "처리 중", handle_key_error: bool = True):
        self._task_id += 1
        self._tasks[self._task_id] = (on_done, on_error, handle_key_error)
        self.progress.show()
        self.statusBar().showMessage(busy)
        self.pool.start(_Task(self, self._task_id, function))

    @Slot(int, object, object)
    def _on_task_finished(self, task_id: int, result, error):
        on_done, on_error, handle_key_error = self._tasks.pop(task_id)
        if not self._tasks:
            self.progress.hide()
            self.statusBar().clearMessage()
        if error is None:
            on_done(result)
            return
        if on_error:
            on_error(error)
        else:
            QMessageBox.warning(self, "알림", error.message)
        if handle_key_error and error.code in KEY_ERROR_CODES:
            self.session_key = None
            self.refresh_key_state()
            self.go("settings")
            QMessageBox.warning(self, "인증키 확인", error.message)

    # ------------------------------------------------- 회사 목록
    def with_index(self, callback: Callable):
        if self.index.is_ready():
            callback()
            if self.index.is_stale() and not self._index_loading and (self.session_key or self.keys.exists()):
                key = self.require_key(prompt=False)
                if key:
                    self.refresh_index(key, quiet=True)
            return
        key = self.require_key()
        if key:
            self.refresh_index(key, callback=callback)

    def refresh_index(self, key: str, callback: Callable | None = None, quiet: bool = False):
        if callback:
            self._index_waiters.append(callback)
        if self._index_loading:
            return
        self._index_loading = True

        def done(total: int):
            self._index_loading = False
            waiters, self._index_waiters = self._index_waiters, []
            self.status(f"회사 목록 {total:,}개를 준비했습니다.")
            self.settings.refresh_state()
            for waiter in waiters:
                waiter()

        def fail(error: DartError):
            self._index_loading = False
            self._index_waiters.clear()
            if not quiet:
                QMessageBox.warning(self, "회사 목록", error.message)

        self.run_task(lambda: self.index.refresh(self.make_client(key)), done, fail, "회사 목록을 내려받는 중 (처음 한 번, 약 10~30초)")

    # ------------------------------------------------- PDF
    def save_report(self, report: Report | None):
        if not report:
            return
        stem = re.sub(r'[\\/:*?"<>|]+', "_", report.file_stem)
        default = downloads_dir() / f"{stem}_{date.today():%Y%m%d}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "PDF로 저장", str(default), "PDF 파일 (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self.statusBar().showMessage("PDF를 만드는 중입니다. 건수가 많으면 몇 초 걸릴 수 있습니다.")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            pages = export_pdf(report, path)
        except OSError as exc:
            QMessageBox.warning(self, "PDF 저장 실패", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()
        box = QMessageBox(self)
        box.setWindowTitle("PDF 저장 완료")
        box.setText(f"{Path(path).name}\n{pages}쪽으로 저장했습니다.")
        open_button = box.addButton("열기", QMessageBox.AcceptRole)
        box.addButton("닫기", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def save_guide(self):
        self.save_report(build_guide_report())

    def closeEvent(self, event):
        self.agent_page.shutdown()
        self.scan_page.shutdown()
        self.pool.clear()
        self.pool.waitForDone(3000)
        super().closeEvent(event)


def main() -> int:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DartGuide.Desktop.App")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setFont(QFont("Malgun Gothic", 10))
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    window = MainWindow()
    window.show()
    return app.exec()
