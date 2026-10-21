"""JSON-safe serialization helpers for pandas/numpy objects."""

from __future__ import annotations

import math
from datetime import date, datetime, time
from typing import Any

import numpy as np
import pandas as pd


def to_jsonable(value: Any, max_depth: int = 6, _depth: int = 0) -> Any:
    """Recursively convert pandas/numpy/date objects into JSON-serializable types."""
    if _depth > max_depth:
        return str(value)

    if value is None or isinstance(value, (str, bool, int)):
        return value

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if isinstance(value, (np.integer,)):
        return int(value)

    if isinstance(value, (np.floating,)):
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f

    if isinstance(value, (np.bool_,)):
        return bool(value)

    if isinstance(value, (np.ndarray,)):
        return [to_jsonable(v, max_depth, _depth + 1) for v in value.tolist()]

    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()

    if isinstance(value, time):
        return value.isoformat()

    if isinstance(value, pd.Timedelta):
        return str(value)

    if isinstance(value, pd.Interval):
        return str(value)

    if isinstance(value, pd.MultiIndex):
        return [str(v) for v in value.tolist()]

    if isinstance(value, pd.Index):
        return [to_jsonable(v, max_depth, _depth + 1) for v in value.tolist()]

    if isinstance(value, pd.Series):
        return {str(k): to_jsonable(v, max_depth, _depth + 1) for k, v in value.items()}

    if isinstance(value, pd.DataFrame):
        return [
            {str(k): to_jsonable(v, max_depth, _depth + 1) for k, v in row.items()}
            for row in value.to_dict(orient="records")
        ]

    if isinstance(value, dict):
        return {str(k): to_jsonable(v, max_depth, _depth + 1) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v, max_depth, _depth + 1) for v in value]

    if isinstance(value, (np.generic,)):
        return to_jsonable(value.item(), max_depth, _depth + 1)

    if pd.isna(value) if not isinstance(value, (list, dict)) else False:
        return None

    return str(value)
