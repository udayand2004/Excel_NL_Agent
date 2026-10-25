"""Data loading and profiling."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import pandas as pd

from .models import ColumnProfile, DataSummary
from .prompt_budget import estimate_tokens, truncate_text, truncate_to_tokens
from .serialize import to_jsonable

MAX_SAMPLE_ROWS = 5
MAX_PREVIEW_ROWS = 8
MAX_DESCRIPTION_COLUMNS = 48
MAX_NAME_ONLY_CHARS = 1800

_STOP_TERMS = {
    "about",
    "across",
    "after",
    "also",
    "an",
    "analysis",
    "analyze",
    "and",
    "are",
    "as",
    "at",
    "average",
    "be",
    "before",
    "between",
    "bottom",
    "by",
    "calculate",
    "can",
    "chart",
    "column",
    "columns",
    "compare",
    "count",
    "data",
    "did",
    "do",
    "does",
    "each",
    "find",
    "for",
    "from",
    "get",
    "give",
    "group",
    "has",
    "have",
    "highest",
    "how",
    "in",
    "into",
    "is",
    "it",
    "its",
    "list",
    "lowest",
    "make",
    "many",
    "most",
    "number",
    "of",
    "on",
    "or",
    "over",
    "per",
    "please",
    "plot",
    "should",
    "show",
    "sheet",
    "sort",
    "sum",
    "than",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "this",
    "to",
    "top",
    "total",
    "trend",
    "up",
    "using",
    "value",
    "values",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "will",
    "with",
    "would",
    "you",
    "your",
}

_DATE_TERMS = {"date", "day", "month", "quarter", "time", "week", "year"}


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


def _terms(text: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(term) >= 2 and term not in _STOP_TERMS
    }


def _column_score(profile: ColumnProfile, question: str, terms: set[str]) -> int:
    name = profile.name.lower()
    name_terms = _terms(name)
    score = 0
    if len(name) >= 2 and name in question:
        score += 8
    score += 5 * len(terms & name_terms)
    score += sum(2 for term in terms if len(term) >= 3 and term in name)
    if profile.kind == "datetime" and terms & _DATE_TERMS:
        score += 3
    if profile.kind == "numeric" and terms & {
        "amount",
        "cost",
        "price",
        "revenue",
        "sales",
    }:
        score += 1
    if profile.kind != "numeric":
        for value in profile.sample_values[:5]:
            text = str(value).strip().lower()
            if len(text) >= 3 and text in question:
                score += 6
        for value in list((profile.top_values or {}).keys())[:5]:
            text = str(value).strip().lower()
            if len(text) >= 3 and text in question:
                score += 4
    return score


def select_description_columns(
    summary: DataSummary,
    question: str = "",
    max_columns: int = MAX_DESCRIPTION_COLUMNS,
) -> List[ColumnProfile]:
    """Keep question-relevant columns first, then fill with the leading columns."""
    profiles = summary.column_profiles
    if max_columns <= 0 or len(profiles) <= max_columns:
        return list(profiles)

    question_lower = (question or "").lower()
    terms = _terms(question_lower)
    ranked = sorted(
        enumerate(profiles),
        key=lambda item: (-_column_score(item[1], question_lower, terms), item[0]),
    )
    return [profile for _, profile in ranked[:max_columns]]


def _profile_line(profile: ColumnProfile) -> str:
    line = (
        f"  - {profile.name!r} ({profile.kind}): "
        f"{profile.unique_count} unique, {profile.null_count} null"
    )
    if profile.kind == "numeric" and profile.mean is not None:
        line += f"; range [{profile.min:.4g}, {profile.max:.4g}], mean={profile.mean:.4g}"
    elif profile.kind == "categorical" and profile.top_values:
        top = ", ".join(f"{k}={v}" for k, v in list(profile.top_values.items())[:2])
        line += f"; top: {top}"
    elif profile.kind == "datetime" and profile.min_date:
        line += f"; dates {profile.min_date} .. {profile.max_date}"
    return line


def generate_data_description(
    summary: DataSummary,
    sheets: Optional[Dict[str, pd.DataFrame]] = None,
    question: str = "",
    max_columns: int = MAX_DESCRIPTION_COLUMNS,
    max_tokens: Optional[int] = None,
    include_samples: bool = True,
) -> str:
    """Compact, question-aware description of the active sheet for LLM prompts."""
    profiles = summary.column_profiles
    selected = select_description_columns(summary, question, max_columns)
    available = max(200, max_tokens - 120) if max_tokens is not None else None
    detail_budget = int(available * 0.58) if available is not None else None
    name_budget = int(available * 0.18) if available is not None else None

    detailed: List[ColumnProfile] = []
    detail_lines: List[str] = []
    for profile in selected:
        line = _profile_line(profile)
        if (
            detail_budget is not None
            and estimate_tokens("\n".join(detail_lines + [line])) > detail_budget
        ):
            break
        detailed.append(profile)
        detail_lines.append(line)

    lines = [
        f"File: {summary.filename}",
        f"Sheets available: {summary.sheets}",
        f"Active sheet: '{summary.active_sheet}' "
        f"({summary.rows} rows x {summary.columns} columns)",
    ]
    if summary.columns and summary.numeric_count / summary.columns >= 0.8:
        lines.extend(
            [
                f"Layout: wide numeric table — {summary.numeric_count} of "
                f"{summary.columns} columns are numeric measures; "
                f"{summary.categorical_count} categorical dimension(s).",
                "If the question asks for a breakdown by categories without naming "
                "a category column, treat the numeric measure columns as categories "
                "and aggregate their column totals.",
            ]
        )
    lines.extend(
        [
            "",
            f"Columns with details ({len(detailed)} of {len(profiles)}):",
            *detail_lines,
        ]
    )

    detailed_names = {profile.name for profile in detailed}
    omitted_names = [
        profile.name for profile in profiles if profile.name not in detailed_names
    ]
    if omitted_names:
        names = ", ".join(omitted_names)
        if name_budget is not None:
            names = truncate_to_tokens(names, name_budget)
        else:
            names = truncate_text(names, MAX_NAME_ONLY_CHARS)
        lines.append(
            f"Other columns (name only; {len(omitted_names)} total): {names}"
        )

    frame = sheets.get(summary.active_sheet) if sheets else None
    if include_samples and frame is not None and not frame.empty:
        sample_columns = [
            profile.name for profile in detailed if profile.name in frame.columns
        ]
        if sample_columns:
            sample = to_jsonable(
                frame.loc[:, sample_columns].head(5).to_dict(orient="records")
            )
            sample_text = json.dumps(sample, separators=(",", ":"), default=str)
            if max_tokens is not None:
                sample_budget = max_tokens - estimate_tokens("\n".join(lines)) - 12
            else:
                sample_budget = 500
            if sample_budget >= 40:
                lines.extend(
                    ["", "Sample rows:", truncate_to_tokens(sample_text, sample_budget)]
                )

    return "\n".join(lines)
