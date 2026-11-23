"""보고서용 차트. 한 패널에 한 지표만 그려 단위가 섞이지 않게 한다."""
from __future__ import annotations

import io
from typing import Callable, Sequence

from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

SERIES = "#2a78d6"
INK = "#1b1f24"
MUTED = "#5b6470"
GRID = "#e4e7ec"
SURFACE = "#ffffff"
_FONT_PATH = r"C:\Windows\Fonts\malgun.ttf"
_FONT_BOLD_PATH = r"C:\Windows\Fonts\malgunbd.ttf"


def _font(size: float, bold: bool = False) -> font_manager.FontProperties:
    return font_manager.FontProperties(fname=_FONT_BOLD_PATH if bold else _FONT_PATH, size=size)


def _to_png(figure: Figure) -> bytes:
    FigureCanvasAgg(figure)
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=200, facecolor=SURFACE)
    return buffer.getvalue()


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0, colors=MUTED)


def column_panels(panels: Sequence[tuple[str, Sequence[str], Sequence[float | None]]],
                  formatter: Callable[[float | None], str]) -> bytes:
    """지표별 세로 막대 패널. 지표마다 자체 축을 쓰고 값은 막대에 직접 표시한다."""
    count = len(panels)
    figure = Figure(figsize=(6.8, 2.75), layout="constrained")
    axes = figure.subplots(1, count, squeeze=False)[0]
    for ax, (title, labels, values) in zip(axes, panels):
        _style(ax)
        ax.spines["bottom"].set_visible(False)
        numeric = [v if v is not None else 0 for v in values]
        positions = range(len(labels))
        ax.bar(positions, numeric, width=0.55, color=SERIES, zorder=2)
        ax.axhline(0, color="#aeb5bf", linewidth=0.8, zorder=3)
        ax.set_title(title, fontproperties=_font(10.5, True), color=INK, loc="left", pad=12)
        ax.set_xticks(list(positions), labels)
        for tick in ax.get_xticklabels():
            tick.set_fontproperties(_font(8.5))
            tick.set_color(MUTED)
        ax.set_yticks([])
        low, high = min(numeric + [0]), max(numeric + [0])
        span = (high - low) or 1
        ax.set_ylim(low - span * (0.18 if low < 0 else 0.02), high + span * 0.2)
        for x, value, raw in zip(positions, numeric, values):
            offset, align = (3, "bottom") if value >= 0 else (-3, "top")
            ax.annotate(formatter(raw), (x, value), xytext=(0, offset), textcoords="offset points",
                        ha="center", va=align, fontproperties=_font(8.5), color=INK)
    return _to_png(figure)


def bar_rows(title: str, labels: Sequence[str], values: Sequence[float],
             formatter: Callable[[float], str]) -> bytes:
    """이름이 긴 항목의 순위를 보여 주는 가로 막대."""
    shown = [label if len(label) <= 18 else f"{label[:17]}…" for label in labels]
    height = max(1.9, 0.34 * len(shown) + 0.75)
    figure = Figure(figsize=(6.8, height), layout="constrained")
    ax = figure.subplots()
    _style(ax)
    ax.spines["bottom"].set_visible(False)
    positions = list(range(len(shown)))
    ax.barh(positions, values, height=0.58, color=SERIES, zorder=2)
    ax.axvline(0, color="#aeb5bf", linewidth=0.8, zorder=3)
    ax.set_yticks(positions, shown)
    for tick in ax.get_yticklabels():
        tick.set_fontproperties(_font(8.5))
        tick.set_color(INK)
    ax.invert_yaxis()
    ax.set_xticks([])
    ax.set_title(title, fontproperties=_font(10.5, True), color=INK, loc="left", pad=10)
    largest = max([abs(v) for v in values] + [1])
    ax.set_xlim(min(0, min(values, default=0)) * 1.2, largest * 1.18)
    for y, value in zip(positions, values):
        ax.annotate(formatter(value), (value, y), xytext=(4, 0), textcoords="offset points",
                    ha="left", va="center", fontproperties=_font(8.5), color=INK)
    return _to_png(figure)


def month_columns(title: str, labels: Sequence[str], values: Sequence[int]) -> bytes:
    """월별 건수. 라벨이 많으면 일부만 표시하고 최댓값에 수치를 단다."""
    figure = Figure(figsize=(6.8, 2.35), layout="constrained")
    ax = figure.subplots()
    _style(ax)
    positions = list(range(len(labels)))
    ax.bar(positions, values, width=0.62, color=SERIES, zorder=2)
    ax.set_title(title, fontproperties=_font(10.5, True), color=INK, loc="left", pad=10)
    step = max(1, len(labels) // 8)
    ticks = positions[::step]
    ax.set_xticks(ticks, [labels[i] for i in ticks])
    for tick in ax.get_xticklabels():
        tick.set_fontproperties(_font(8.5))
        tick.set_color(MUTED)
    ax.set_yticks([])
    top = max(values, default=0)
    ax.set_ylim(0, (top or 1) * 1.25)
    if top:
        peak = values.index(top)
        ax.annotate(f"최다 {top}건", (peak, top), xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontproperties=_font(8.5), color=INK)
    return _to_png(figure)
