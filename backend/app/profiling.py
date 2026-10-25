"""Data loading and profiling."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pandas as pd

from .models import ColumnProfile, DataSummary
from .serialize import to_jsonable

MAX_SAMPLE_ROWS = 5
MAX_PREVIEW_ROWS = 8


def load_workbook(path: str, filename: str) -> Dict[str, pd.DataFrame]:
    """Load an Excel workbook or CSV into a dict of sheet_name -> DataFrame."""
    lowered = filename.lower()
    if lowered.endswith(".csv"):
        try:
            df = pd.read_csv(path, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(path, encoding="latin-1")
        return {"Sheet1": df}

    sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    cleaned: Dict[str, pd.DataFrame] = {}
    for name, df in sheets.items():
        if df.empty and len(sheets) > 1:
            continue
        # Drop fully empty rows/columns that Excel often produces
        df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
        df.columns = [str(c).strip() for c in df.columns]
        cleaned[str(name)] = df
    if not cleaned:
        cleaned["Sheet1"] = pd.read_excel(path, engine="openpyxl")
    return cleaned


def classify_series(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_categorical_dtype(series) or series.dtype == object:
        # Heuristic: object columns that parse as dates
        if _looks_like_dates(series):
            return "datetime"
        return "categorical"
    return "other"


def _looks_like_dates(series: pd.Series) -> bool:
    sample = series.dropna().astype(str).head(30)
    if sample.empty or len(sample) < 5:
        return False
    try:
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
        return parsed.notna().mean() > 0.9
    except Exception:
        return False


def profile_sheet(df: pd.DataFrame) -> List[ColumnProfile]:
    profiles: List[ColumnProfile] = []
    for col in df.columns:
        series = df[col]
        kind = classify_series(series)
        info = ColumnProfile(
            name=str(col),
            dtype=str(series.dtype),
            kind=kind,
            null_count=int(series.isna().sum()),
            unique_count=int(series.nunique(dropna=True)),
            sample_values=to_jsonable(series.dropna().head(MAX_SAMPLE_ROWS).tolist()),
        )

        if kind == "numeric":
            non_null = series.dropna()
            if not non_null.empty:
                info.min = float(non_null.min())
                info.max = float(non_null.max())
                info.mean = float(non_null.mean())
                std = non_null.std()
                info.std = float(std) if pd.notna(std) else None
        elif kind == "categorical":
            counts = series.value_counts().head(5)
            info.top_values = {str(k): int(v) for k, v in counts.items()}
        elif kind == "datetime":
            coerced = series if pd.api.types.is_datetime64_any_dtype(series) else pd.to_datetime(
                series, errors="coerce", format="mixed"
            )
            if coerced.notna().any():
                info.min_date = str(coerced.min())
                info.max_date = str(coerced.max())

        profiles.append(info)
    return profiles


def build_summary(
    filename: str, sheets: Dict[str, pd.DataFrame], active_sheet: str
) -> DataSummary:
    df = sheets[active_sheet]
    profiles = profile_sheet(df)
    return DataSummary(
        filename=filename,
        active_sheet=active_sheet,
        sheets=list(sheets.keys()),
        rows=int(len(df)),
        columns=int(len(df.columns)),
        numeric_count=sum(1 for p in profiles if p.kind == "numeric"),
        categorical_count=sum(1 for p in profiles if p.kind == "categorical"),
        date_columns=[p.name for p in profiles if p.kind == "datetime"],
        column_profiles=profiles,
        preview=to_jsonable(df.head(MAX_PREVIEW_ROWS).to_dict(orient="records")),
    )


def suggest_questions(summary: DataSummary) -> List[str]:
    questions = [
        "What are the summary statistics for this data?",
        "Which categories have the highest totals?",
        "What patterns or outliers do you notice?",
    ]
    if summary.numeric_count >= 2:
        questions.append("Show the correlation between numeric columns as a heatmap")
    if summary.date_columns:
        questions.append("Show me the trend over time as a line chart")
    if summary.categorical_count and summary.numeric_count:
        questions.append("Break down the main metric by the top categories with a bar chart")
    if summary.numeric_count:
        questions.append("Plot a histogram of the main numeric column")
    return questions[:6]


def generate_data_description(
    summary: DataSummary, sheets: Dict[str, pd.DataFrame] | None = None
) -> str:
    """Compact natural-language description of the active sheet for LLM prompts."""
    lines = [
        f"File: {summary.filename}",
        f"Sheets available: {summary.sheets}",
        f"Active sheet: '{summary.active_sheet}' "
        f"({summary.rows} rows x {summary.columns} columns)",
        "",
        "Columns:",
    ]
    for p in summary.column_profiles:
        desc = f"  - {p.name} ({p.kind}, dtype={p.dtype}): {p.unique_count} unique, {p.null_count} null"
        if p.kind == "numeric" and p.mean is not None:
            desc += f"; range [{p.min:.4g}, {p.max:.4g}], mean={p.mean:.4g}"
        elif p.kind == "categorical" and p.top_values:
            top = ", ".join(f"{k}={v}" for k, v in list(p.top_values.items())[:3])
            desc += f"; top: {top}"
        elif p.kind == "datetime" and p.min_date:
            desc += f"; dates {p.min_date} .. {p.max_date}"
        lines.append(desc)

    frame = sheets.get(summary.active_sheet) if sheets else None
    if frame is not None and not frame.empty:
        preview = to_jsonable(frame.head(5).to_dict(orient="records"))
        lines.extend(["", "Sample rows:", json.dumps(preview, indent=2, default=str)])
    return "\n".join(lines)
