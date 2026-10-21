"""Dark-themed chart rendering from analysis results."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any, Optional

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from .models import ChartSpec, ChartType

logger = logging.getLogger(__name__)

PALETTE = ["#60a5fa", "#a78bfa", "#34d399", "#fbbf24", "#f472b6", "#22d3ee", "#fb7185"]
BG = "#0f172a"
PANEL = "#1e293b"
GRID = "#334155"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"
TITLE = "#f8fafc"

plt.style.use("dark_background")
sns.set_palette(PALETTE)


def _style_axes(ax) -> None:
    ax.set_facecolor(PANEL)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.xaxis.label.set_color(TEXT)
    ax.yaxis.label.set_color(TEXT)
    ax.title.set_color(TITLE)
    ax.title.set_fontsize(12)
    ax.title.set_fontweight("bold")
    for spine in ax.spines.values():
        spine.set_color(GRID)


def _fig() -> Any:
    fig, ax = plt.subplots(figsize=(10, 6), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(PANEL)
    return fig, ax


def _legend(ax) -> None:
    handles, labels = ax.get_legend_handles_labels()
    if labels:
        ax.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=9)


def _to_frame(result: Any) -> Optional[pd.DataFrame]:
    if isinstance(result, pd.DataFrame):
        return result
    if isinstance(result, pd.Series):
        return result.rename("value").reset_index()
    return None


def _numeric_cols(df: pd.DataFrame):
    return list(df.select_dtypes(include=[np.number]).columns)


def _pick_xy(df: pd.DataFrame, spec: ChartSpec):
    cols = list(df.columns)
    numeric = _numeric_cols(df)
    x = spec.x if spec.x in cols else (cols[0] if cols else None)
    if spec.y in cols:
        y = spec.y
    else:
        candidates = [c for c in numeric if c != x]
        y = candidates[0] if candidates else (numeric[0] if numeric else (cols[1] if len(cols) > 1 else None))
    return x, y


def _apply_top_n(df: pd.DataFrame, spec: ChartSpec, y: Optional[str]) -> pd.DataFrame:
    n = spec.top_n or 15
    if len(df) <= n or y is None:
        return df
    try:
        return df.nlargest(n, y)
    except Exception:
        return df.head(n)


def render_chart(result: Any, spec: ChartSpec, question: str) -> Optional[str]:
    """Render a chart for the analysis result; returns base64 PNG or None."""
    try:
        df = _to_frame(result)
        if df is None or df.empty:
            return None

        chart_type = spec.type
        if chart_type == ChartType.NONE:
            chart_type = _fallback_type(df, question)
        if chart_type == ChartType.NONE:
            return None

        fig, ax = _fig()
        ok = False

        if chart_type == ChartType.BAR:
            ok = _bar(df, ax, spec)
        elif chart_type == ChartType.LINE:
            ok = _line(df, ax, spec)
        elif chart_type == ChartType.SCATTER:
            ok = _scatter(df, ax, spec)
        elif chart_type == ChartType.PIE:
            ok = _pie(df, ax, spec)
        elif chart_type == ChartType.HEATMAP:
            ok = _heatmap(df, ax, spec)
        elif chart_type == ChartType.BOX:
            ok = _box(df, ax, spec)
        elif chart_type == ChartType.AREA:
            ok = _area(df, ax, spec)
        elif chart_type == ChartType.HISTOGRAM:
            ok = _histogram(df, ax, spec)

        if not ok:
            plt.close(fig)
            return None

        if spec.title:
            ax.set_title(spec.title)
        elif not ax.get_title():
            ax.set_title(question[:90])

        if len(ax.get_xticklabels()) > 8:
            plt.setp(ax.get_xticklabels(), rotation=35, ha="right", fontsize=8)

        _legend(ax)
        ax.grid(alpha=0.15)
        fig.tight_layout()
        return _fig_to_base64(fig)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chart rendering failed")
        return None
    finally:
        plt.close("all")


def _fallback_type(df: pd.DataFrame, question: str) -> ChartType:
    q = question.lower()
    numeric = _numeric_cols(df)
    categorical = [c for c in df.columns if c not in numeric]
    if len(df) == len(df.columns) and numeric and df.index.tolist() == list(df.columns):
        return ChartType.HEATMAP
    if any(w in q for w in ("pie", "share", "proportion")) and categorical and numeric:
        return ChartType.PIE
    if any(w in q for w in ("trend", "over time", "monthly")):
        return ChartType.LINE
    if categorical and numeric:
        return ChartType.BAR
    if len(numeric) >= 2:
        return ChartType.SCATTER
    if len(numeric) == 1:
        return ChartType.HISTOGRAM
    return ChartType.NONE


def _bar(df: pd.DataFrame, ax, spec: ChartSpec) -> bool:
    x, y = _pick_xy(df, spec)
    if y is None:
        return False
    df = _apply_top_n(df, spec, y)
    hue = spec.hue if spec.hue in df.columns and spec.hue not in (x, y) else None
    if hue:
        pivot = df.pivot_table(index=x, columns=hue, values=y, aggfunc="sum")
        pivot.plot(kind="bar", ax=ax, color=PALETTE, edgecolor=BG, width=0.8)
    else:
        ax.bar(df[x].astype(str), df[y], color=PALETTE[0], edgecolor=BG, width=0.8)
    ax.set_xlabel(str(x))
    ax.set_ylabel(str(y))
    return True


def _line(df: pd.DataFrame, ax, spec: ChartSpec) -> bool:
    x, y = _pick_xy(df, spec)
    if y is None:
        return False
    xs = df[x]
    if not pd.api.types.is_numeric_dtype(xs):
        try:
            xs = pd.to_datetime(xs, errors="raise", format="mixed")
        except Exception:
            pass
    y_cols = [c for c in _numeric_cols(df) if c == y or c in (spec.y or "",)]
    if not y_cols:
        y_cols = [y]
    for col in y_cols:
        ax.plot(xs, df[col], marker="o", markersize=4, linewidth=2, label=str(col), color=PALETTE[0])
    ax.set_xlabel(str(x))
    ax.set_ylabel(", ".join(str(c) for c in y_cols))
    return True


def _scatter(df: pd.DataFrame, ax, spec: ChartSpec) -> bool:
    numeric = _numeric_cols(df)
    if len(numeric) < 2:
        return False
    x = spec.x if spec.x in numeric else numeric[0]
    y = spec.y if spec.y in numeric else numeric[1]
    hue = spec.hue if spec.hue in df.columns else None
    if hue and not pd.api.types.is_numeric_dtype(df[hue]):
        for i, (name, group) in enumerate(df.groupby(hue, observed=True)):
            ax.scatter(group[x], group[y], s=45, alpha=0.75, label=str(name), color=PALETTE[i % len(PALETTE)])
    else:
        ax.scatter(df[x], df[y], s=45, alpha=0.75, color=PALETTE[0])
    ax.set_xlabel(str(x))
    ax.set_ylabel(str(y))
    return True


def _pie(df: pd.DataFrame, ax, spec: ChartSpec) -> bool:
    x, y = _pick_xy(df, spec)
    if y is None:
        return False
    df = df.head(8)
    labels = df[x].astype(str)
    values = pd.to_numeric(df[y], errors="coerce").fillna(0)
    if (values <= 0).all():
        return False
    colors = PALETTE[: len(values)]
    wedges, texts, autotexts = ax.pie(
        values, labels=labels, autopct="%1.1f%%", colors=colors, startangle=90,
        wedgeprops={"linewidth": 1, "edgecolor": BG},
    )
    for t in texts:
        t.set_color(TEXT)
        t.set_fontsize(9)
    for at in autotexts:
        at.set_color(BG)
        at.set_fontweight("bold")
        at.set_fontsize(9)
    ax.set_ylabel("")
    return True


def _heatmap(df: pd.DataFrame, ax, spec: ChartSpec) -> bool:
    numeric = df.select_dtypes(include=[np.number])
    if numeric.empty:
        return False
    is_matrix = (
        len(numeric) > 1
        and len(numeric) == len(numeric.columns)
        and [str(c) for c in numeric.columns] == [str(i) for i in numeric.index]
    )
    if not is_matrix:
        numeric = numeric.corr(numeric_only=True) if numeric.shape[1] > 1 else numeric
        if numeric.empty or numeric.shape[1] < 2:
            return False
    sns.heatmap(
        numeric.astype(float),
        annot=numeric.shape[0] <= 12,
        fmt=".2f",
        cmap="RdYlBu_r" if is_matrix else "viridis",
        center=0 if is_matrix else None,
        ax=ax,
        linewidths=0.4,
        cbar_kws={"shrink": 0.8},
    )
    ax.set_title(spec.title or "Correlation Heatmap")
    return True


def _box(df: pd.DataFrame, ax, spec: ChartSpec) -> bool:
    numeric = _numeric_cols(df)
    if not numeric:
        return False
    data = df[numeric[:8]]
    data.boxplot(ax=ax, patch_artist=True, grid=False)
    for patch in ax.artists:
        patch.set_facecolor(PALETTE[0])
        patch.set_alpha(0.6)
    ax.set_ylabel("Value")
    return True


def _area(df: pd.DataFrame, ax, spec: ChartSpec) -> bool:
    x, y = _pick_xy(df, spec)
    numeric = [c for c in _numeric_cols(df)]
    if not numeric:
        return False
    if len(numeric) == 1:
        ax.fill_between(range(len(df)), df[numeric[0]], alpha=0.5, color=PALETTE[0], label=numeric[0])
    else:
        ax.stackplot(
            range(len(df)),
            *[df[c] for c in numeric[:6]],
            labels=[str(c) for c in numeric[:6]],
            colors=PALETTE[:6],
            alpha=0.8,
        )
    ax.set_xticks(range(len(df)))
    ax.set_xticklabels(df[x].astype(str) if x in df.columns else range(len(df)), fontsize=7)
    ax.set_xlabel(str(x) if x else "index")
    ax.set_ylabel("Value")
    return True


def _histogram(df: pd.DataFrame, ax, spec: ChartSpec) -> bool:
    numeric = _numeric_cols(df)
    if not numeric:
        return False
    col = spec.y if spec.y in numeric else (spec.x if spec.x in numeric else numeric[0])
    values = pd.to_numeric(df[col], errors="coerce").dropna()
    if values.empty:
        return False
    ax.hist(values, bins=min(30, max(10, len(values) // 5)), color=PALETTE[0], edgecolor=BG, alpha=0.85)
    ax.set_xlabel(str(col))
    ax.set_ylabel("Frequency")
    return True


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")
