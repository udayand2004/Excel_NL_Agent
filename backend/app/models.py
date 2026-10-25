"""Pydantic request/response models."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChartType(str, Enum):
    BAR = "bar"
    LINE = "line"
    SCATTER = "scatter"
    PIE = "pie"
    HISTOGRAM = "histogram"
    HEATMAP = "heatmap"
    BOX = "box"
    AREA = "area"
    NONE = "none"


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    kind: str  # numeric | categorical | datetime | boolean | other
    null_count: int = 0
    unique_count: int = 0
    sample_values: List[Any] = Field(default_factory=list)
    min: Optional[float] = None
    max: Optional[float] = None
    mean: Optional[float] = None
    std: Optional[float] = None
    top_values: Optional[Dict[str, int]] = None
    min_date: Optional[str] = None
    max_date: Optional[str] = None


class DataSummary(BaseModel):
    filename: str
    active_sheet: str
    sheets: List[str]
    rows: int
    columns: int
    numeric_count: int
    categorical_count: int
    date_columns: List[str]
    column_profiles: List[ColumnProfile]
    preview: List[Dict[str, Any]]


class UploadResponse(BaseModel):
    session_id: str
    data_summary: DataSummary
    suggested_questions: List[str]
    clarification_questions: List[str]
    needs_clarification: bool


class ClarifyRequest(BaseModel):
    clarification: str = Field(min_length=1, max_length=4000)


class ClarifyResponse(BaseModel):
    status: str
    message: str
    context: str


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=4000)


class ChartSpec(BaseModel):
    type: ChartType = ChartType.NONE
    x: Optional[str] = None
    y: Optional[str] = None
    title: Optional[str] = None
    hue: Optional[str] = None
    top_n: Optional[int] = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    generated_code: Optional[str] = None
    result_preview: Any = None
    result_kind: str = "none"  # table | series | scalar | none
    chart: Optional[str] = None  # base64 PNG
    chart_type: Optional[str] = None
    attempts: int = 1
    execution_ms: Optional[int] = None


class SheetRequest(BaseModel):
    session_id: str
    sheet: str


class SheetResponse(BaseModel):
    status: str
    active_sheet: str
    sheets: List[str]
    rows: int
    columns: int


class HealthResponse(BaseModel):
    status: str
    version: str
    model: str
    provider: str
    aux_provider: Optional[str] = None
    aux_model: Optional[str] = None
    fallback_provider: Optional[str] = None
    fallback_model: Optional[str] = None
    llm_configured: bool
    sessions_active: int
    python_version: str
