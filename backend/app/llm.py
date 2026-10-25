"""OpenAI LLM integration: clarification, code generation, self-repair, explanation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI, OpenAIError

from .config import Settings
from .models import ChartSpec, ChartType, DataSummary
from .profiling import generate_data_description

logger = logging.getLogger(__name__)

SYSTEM_CODE = (
    "You are an expert Python data analyst. You write ONLY valid Python code using "
    "pandas and numpy. Never use markdown fences. Never import modules other than "
    "pandas/numpy/math/statistics/datetime. Never import matplotlib; the application "
    "renders charts separately from the returned data. Never access files, network, or "
    "dunder attributes. Always assign the final answer to a variable called `result`."
)

SYSTEM_JSON = (
    "You are a precise data analysis assistant. Respond with a single valid JSON "
    "object only — no markdown, no commentary."
)


class LLMError(Exception):
    pass


def strip_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        # take the largest fenced block
        blocks = [parts[i] for i in range(1, len(parts), 2)]
        if blocks:
            block = max(blocks, key=len).strip()
            if block.startswith("python"):
                block = block[6:].strip()
            elif block.startswith("json"):
                block = block[4:].strip()
            return block
    return text


def parse_json(text: str) -> Any:
    cleaned = strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


class LLMService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.primary_client: Optional[AsyncOpenAI] = None
        self.primary_provider: Optional[str] = None
        self.primary_model: Optional[str] = None
        self.fallback_client: Optional[AsyncOpenAI] = None
        self.fallback_provider: Optional[str] = None
        self.fallback_model: Optional[str] = None

        if settings.groq_api_key:
            self.primary_client = self._client(settings.groq_api_key, settings.groq_base_url)
            self.primary_provider = "groq"
            self.primary_model = settings.groq_model
        elif settings.openai_api_key and settings.openai_api_key.lower() != "ollama":
            self.primary_client = self._client(settings.openai_api_key, settings.openai_base_url)
            self.primary_provider = "openai" if not settings.openai_base_url else "openai-compatible"
            self.primary_model = settings.llm_model
        elif settings.openai_base_url:
            self.primary_client = self._client("ollama", settings.openai_base_url)
            self.primary_provider = (
                "ollama" if "11434" in settings.openai_base_url else "openai-compatible"
            )
            self.primary_model = settings.llm_model

        if (
            self.primary_provider is not None
            and self.primary_provider != "ollama"
            and settings.ollama_base_url
        ):
            self.fallback_client = self._client("ollama", settings.ollama_base_url)
            self.fallback_provider = "ollama"
            self.fallback_model = settings.ollama_model

    @staticmethod
    def _client(api_key: str, base_url: str) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=api_key or "not-needed",
            base_url=base_url or None,
            timeout=120.0,
            max_retries=1,
        )

    @property
    def configured(self) -> bool:
        return self.primary_client is not None or self.fallback_client is not None

    @property
    def provider(self) -> str:
        return self.primary_provider or "none"

    @property
    def model(self) -> str:
        return self.primary_model or self.settings.llm_model

    async def _chat_once(
        self,
        client: AsyncOpenAI,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        kwargs: Dict[str, Any] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
        except OpenAIError as exc:
            # Some OpenAI-compatible servers reject response_format — retry once without it
            if json_mode and "response_format" in kwargs:
                logger.warning("response_format rejected; retrying without it (%s)", exc)
                return await self._chat_once(
                    client, model, system, user, temperature, max_tokens, json_mode=False
                )
            raise
        content = response.choices[0].message.content or ""
        return content.strip()

    async def _chat(
        self,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        if self.primary_client is None:
            raise LLMError("No LLM backend is configured")

        try:
            return await self._chat_once(
                self.primary_client,
                self.primary_model or self.settings.llm_model,
                system,
                user,
                temperature,
                max_tokens,
                json_mode,
            )
        except Exception as exc:
            if self.fallback_client is None:
                logger.exception("Primary LLM request failed")
                raise LLMError(str(exc)) from exc

            logger.warning(
                "Primary LLM %s/%s failed (%s); falling back to %s/%s",
                self.primary_provider,
                self.primary_model,
                exc,
                self.fallback_provider,
                self.fallback_model,
            )
            try:
                return await self._chat_once(
                    self.fallback_client,
                    self.fallback_model or self.settings.ollama_model,
                    system,
                    user,
                    temperature,
                    max_tokens,
                    json_mode,
                )
            except Exception as fallback_exc:
                logger.exception("Fallback LLM request failed")
                raise LLMError(
                    f"primary ({self.primary_provider}): {exc}; "
                    f"fallback ({self.fallback_provider}): {fallback_exc}"
                ) from fallback_exc

    async def clarification_questions(self, summary: DataSummary, sheets: Dict[str, Any]) -> List[str]:
        description = generate_data_description(summary, sheets)
        prompt = f"""A user uploaded a spreadsheet. Analyze this profile and propose 2-3 short, 
high-value clarifying questions whose answers would most improve analysis accuracy. 
Focus on ambiguous column meanings, units/currency, time granularity, and business goals.

{description}

Return JSON: {{"questions": ["...", "...", "..."]}}"""
        try:
            raw = await self._chat(SYSTEM_JSON, prompt, temperature=0.3, max_tokens=300, json_mode=True)
            data = parse_json(raw)
            questions = data.get("questions", []) if isinstance(data, dict) else data
            return [str(q) for q in questions][:3]
        except Exception:  # noqa: BLE001 - clarification is best-effort
            logger.warning("Falling back to heuristic clarification questions")
            return [
                "What does each column represent in business terms?",
                "Are the numeric values in a specific currency or unit?",
                "What time period does this data cover?",
            ]

    async def generate_code(
        self,
        question: str,
        summary: DataSummary,
        sheets: Dict[str, Any],
        context: str,
        history: List[Dict[str, str]],
        error_feedback: Optional[str] = None,
        previous_code: Optional[str] = None,
    ) -> str:
        description = generate_data_description(summary, sheets)
        history_txt = json.dumps(history[-6:], indent=2, default=str)

        repair_section = ""
        if error_feedback and previous_code:
            repair_section = f"""
YOUR PREVIOUS CODE FAILED.
Code:
{previous_code}

Error:
{error_feedback}

Fix the issue and return corrected code only.
"""

        prompt = f"""{description}

USER CONTEXT PROVIDED EARLIER:
{context or "(none)"}

CONVERSATION HISTORY:
{history_txt}

USER QUESTION:
{question}

Write Python code that answers the question.
Rules:
1. `df` is the active sheet as a DataFrame; `sheets` is a dict of sheet name -> DataFrame.
2. Use `df` unless the question mentions a specific other sheet: `sheets["Name"]`.
3. Assign the final answer to `result`. Cap DataFrames: `result = result.head(50)`.
4. For chart-friendly output prefer tidy DataFrames with named columns (e.g. groupby().reset_index()).
5. Handle missing values sensibly. Use only pandas/numpy/math/statistics/datetime.
   Do not import or call matplotlib; return chart-friendly data instead.
6. No print statements for the answer; no file/network access; no dunder attributes.

{repair_section}
Return ONLY Python code — no markdown, no explanations."""

        return await self._chat(
            SYSTEM_CODE,
            prompt,
            temperature=self.settings.temperature_code,
            max_tokens=1200,
        )

    async def plan_chart(self, question: str, result: Any, summary: DataSummary) -> ChartSpec:
        """Ask the LLM to pick the best chart type and encoding for the result."""
        if result is None:
            return ChartSpec(type=ChartType.NONE)

        import pandas as pd

        if isinstance(result, pd.DataFrame):
            result_desc = f"DataFrame with columns {list(result.columns)}; dtypes: {[str(t) for t in result.dtypes]}; shape {result.shape}"
            sample = result.head(8).to_dict(orient="records")
        elif isinstance(result, pd.Series):
            result_desc = f"Series named '{result.name}' with index samples {list(result.index[:5])}"
            sample = result.head(8).to_dict()
        else:
            result_desc = f"scalar/list value: {type(result).__name__}"
            sample = result

        prompt = f"""Question: {question}
Available columns in source data: {[p.name for p in summary.column_profiles]}
Analysis result type: {result_desc}
Result sample: {json.dumps(sample, default=str)[:1200]}

Pick the single best chart. Rules:
- pie only if <= 8 categories and values are positive parts of a whole
- heatmap only for correlation matrices or wide numeric grids
- line for time series / ordered trends
- scatter for relationships between two numeric columns
- histogram for a distribution of one numeric column
- box for outlier/quartile comparisons across groups
- area for cumulative/stacked trends
- bar for categorical comparisons (default)
- none if the result is a single scalar with no chartable structure

Return JSON:
{{"type": "...", "x": "<column name or null>", "y": "<column name or null>", "hue": "<optional grouping column>", "title": "<short title>", "top_n": <int or null>}}"""

        try:
            raw = await self._chat(SYSTEM_JSON, prompt, temperature=0.1, max_tokens=300, json_mode=True)
            data = parse_json(raw)
            chart_type = str(data.get("type", "none")).lower()
            try:
                ct = ChartType(chart_type)
            except ValueError:
                ct = ChartType.BAR
            return ChartSpec(
                type=ct,
                x=data.get("x"),
                y=data.get("y"),
                hue=data.get("hue"),
                title=data.get("title"),
                top_n=data.get("top_n"),
            )
        except Exception:  # noqa: BLE001 - chart planning is best-effort
            logger.warning("Chart planning failed; defaulting to bar")
            return ChartSpec(type=ChartType.BAR, title=question[:80])

    async def explain(
        self,
        question: str,
        code: str,
        result_preview: Any,
        summary: DataSummary,
        stdout: str = "",
        sheets: Optional[Dict[str, Any]] = None,
    ) -> str:
        description = generate_data_description(summary, sheets or {})
        result_str = json.dumps(result_preview, default=str)
        if len(result_str) > 2500:
            result_str = result_str[:2500] + "… [truncated]"

        prompt = f"""Dataset: {description}

Question: {question}

Code executed:
{code}

Result:
{result_str}

{f'Program output: {stdout[:500]}' if stdout else ''}

Write a concise, insightful answer in plain language (short paragraphs or bullets).
Lead with the direct answer, then notable patterns/caveats. Do not invent numbers 
that are not in the result. Use simple markdown: **bold**, `code`, and lists."""

        return await self._chat(
            "You explain data analysis results clearly, accurately, and concisely.",
            prompt,
            temperature=self.settings.temperature_text,
            max_tokens=700,
        )

    async def friendly_error(
        self,
        question: str,
        error: str,
        summary: DataSummary,
        sheets: Optional[Dict[str, Any]] = None,
    ) -> str:
        description = generate_data_description(summary, sheets or {})
        prompt = f"""The user asked: {question}
Data profile: {description[:1500]}
Error: {error}

Explain what went wrong in one or two sentences and suggest how to rephrase the question. 
Be friendly and specific."""
        try:
            return await self._chat(
                "You are a helpful data assistant.",
                prompt,
                temperature=0.3,
                max_tokens=250,
            )
        except LLMError:
            return (
                f"I couldn't complete that analysis ({error}). "
                "Try rephrasing with explicit column names, or ask something simpler like "
                "'what are the summary statistics?'."
            )
