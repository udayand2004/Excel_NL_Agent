"""OpenAI LLM integration: clarification, code generation, self-repair, explanation."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openai import APIStatusError, AsyncOpenAI, OpenAIError

from .config import Settings
from .models import ChartSpec, ChartType, DataSummary
from .profiling import generate_data_description
from .prompt_budget import (
    TokenRateLimiter,
    estimate_tokens,
    format_history,
    trim_middle,
    truncate_to_tokens,
)

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


class LLMRateLimitError(LLMError):
    pass


@dataclass
class _Target:
    client: AsyncOpenAI
    provider: str
    model: str
    rate_limiter: Optional[TokenRateLimiter] = None


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


def _is_request_too_large(exc: Exception) -> bool:
    if isinstance(exc, APIStatusError) and exc.status_code == 413:
        return True
    message = str(exc).lower()
    return "request too large" in message


def _is_json_mode_rejected(exc: Exception) -> bool:
    if not isinstance(exc, APIStatusError) or exc.status_code != 400:
        return False
    message = str(exc).lower()
    return "response_format" in message or "json_object" in message or "json mode" in message


def _prompt_token_count(system: str, user: str) -> int:
    return estimate_tokens(system) + estimate_tokens(user) + 24


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
            self.primary_client = self._client(
                settings.groq_api_key,
                settings.groq_base_url,
                timeout=settings.groq_timeout_seconds,
            )
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
            self.fallback_client = self._client(
                "ollama",
                settings.ollama_base_url,
                timeout=settings.ollama_timeout_seconds,
                max_retries=0,
            )
            self.fallback_provider = "ollama"
            self.fallback_model = settings.ollama_model

        self.primary_target: Optional[_Target] = None
        if self.primary_client is not None:
            self.primary_target = _Target(
                client=self.primary_client,
                provider=self.primary_provider or "none",
                model=self.primary_model or settings.llm_model,
                rate_limiter=(
                    TokenRateLimiter(settings.groq_input_tpm)
                    if self.primary_provider == "groq"
                    else None
                ),
            )

        self.aux_target: Optional[_Target] = None
        if (
            self.primary_target is not None
            and self.primary_provider == "groq"
            and settings.groq_aux_model
            and settings.groq_aux_model != self.primary_target.model
        ):
            self.aux_target = _Target(
                client=self.primary_target.client,
                provider="groq",
                model=settings.groq_aux_model,
                rate_limiter=TokenRateLimiter(settings.groq_aux_input_tpm),
            )

        self.fallback_target: Optional[_Target] = None
        if self.fallback_client is not None:
            self.fallback_target = _Target(
                client=self.fallback_client,
                provider=self.fallback_provider or "none",
                model=self.fallback_model or settings.ollama_model,
            )

        self.rate_limiter = (
            self.primary_target.rate_limiter if self.primary_target is not None else None
        )

    @staticmethod
    def _client(
        api_key: str,
        base_url: str,
        timeout: float = 120.0,
        max_retries: int = 1,
    ) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=api_key or "not-needed",
            base_url=base_url or None,
            timeout=timeout,
            max_retries=max_retries,
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

    @property
    def aux_provider(self) -> Optional[str]:
        return self.aux_target.provider if self.aux_target is not None else None

    @property
    def aux_model(self) -> Optional[str]:
        return self.aux_target.model if self.aux_target is not None else None

    def _targets_for(self, tier: str) -> List[_Target]:
        order = (
            [self.aux_target, self.primary_target, self.fallback_target]
            if tier == "aux"
            else [self.primary_target, self.aux_target, self.fallback_target]
        )
        targets: List[_Target] = []
        seen = set()
        for target in order:
            if target is None:
                continue
            key = (target.provider, target.model)
            if key in seen:
                continue
            seen.add(key)
            targets.append(target)
        return targets

    def _prompt_budget(self, name: str) -> int:
        if self.primary_provider == "groq":
            requested = int(getattr(self.settings, f"groq_{name}_prompt_tokens", 4000))
            return max(200, min(requested, self.settings.groq_max_input_tokens))
        return {
            "code": 14000,
            "explain": 6000,
            "chart": 1500,
            "clarify": 5000,
            "error": 2500,
        }.get(name, 4000)

    def _aux_budget(self, name: str, non_groq: int) -> int:
        if self.primary_provider == "groq":
            return int(getattr(self.settings, f"groq_{name}_tokens", non_groq))
        return non_groq

    @staticmethod
    def _fit_user_prompt(system: str, user: str, max_input_tokens: int) -> str:
        available = max(200, max_input_tokens - estimate_tokens(system) - 24)
        if estimate_tokens(user) > available:
            user = trim_middle(user, available)
        return user

    async def _chat_once(
        self,
        client: AsyncOpenAI,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        kwargs: Dict[str, Any] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if reasoning_effort and model.startswith("openai/gpt-oss"):
            kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}
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
            if json_mode and _is_json_mode_rejected(exc):
                logger.warning("response_format rejected; retrying without it (%s)", exc)
                return await self._chat_once(
                    client, model, system, user, temperature, max_tokens,
                    json_mode=False, reasoning_effort=reasoning_effort,
                )
            raise
        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.info(
                "LLM %s tokens: prompt=%s completion=%s",
                model,
                getattr(usage, "prompt_tokens", "?"),
                getattr(usage, "completion_tokens", "?"),
            )
        content = response.choices[0].message.content or ""
        return content.strip()

    async def _request_with_shrink(
        self,
        client: AsyncOpenAI,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        try:
            return await self._chat_once(
                client, model, system, user, temperature, max_tokens,
                json_mode, reasoning_effort,
            )
        except OpenAIError as exc:
            if not _is_request_too_large(exc):
                raise
            current_tokens = estimate_tokens(user)
            if current_tokens <= 400:
                raise
            smaller_budget = max(350, int(current_tokens * 0.55))
            smaller = trim_middle(user, smaller_budget)
            if estimate_tokens(smaller) >= current_tokens:
                raise
            logger.warning(
                "Prompt too large (%s); retrying with ~%s estimated tokens",
                exc,
                smaller_budget,
            )
            return await self._chat_once(
                client, model, system, smaller, temperature, max_tokens,
                json_mode, reasoning_effort,
            )

    async def _chat(
        self,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
        max_input_tokens: Optional[int] = None,
        tier: str = "code",
        allow_fallback: bool = True,
    ) -> str:
        targets = self._targets_for(tier)
        if not allow_fallback:
            targets = [target for target in targets if target is not self.fallback_target]
        if not targets:
            raise LLMError("No LLM backend is configured")

        budget = max_input_tokens or self._prompt_budget("code")
        if any(target.provider == "groq" for target in targets):
            budget = min(budget, self.settings.groq_max_input_tokens)
        user = self._fit_user_prompt(system, user, budget)
        prompt_tokens = _prompt_token_count(system, user)
        errors: List[str] = []

        for target in targets:
            if target.rate_limiter is not None:
                max_wait = 12.0 if target.provider == "groq" and allow_fallback else 4.0
                allowed = await target.rate_limiter.try_acquire(
                    prompt_tokens, max_wait=max_wait
                )
                if not allowed:
                    error = (
                        f"{target.provider}/{target.model}: per-minute input budget of "
                        f"{target.rate_limiter.limit} tokens exceeded"
                    )
                    errors.append(error)
                    logger.warning("Skipping LLM %s", error)
                    if target.provider == "groq":
                        raise LLMRateLimitError(
                            f"Groq's per-minute token limit "
                            f"({target.rate_limiter.limit:,}) is reached. "
                            "Please wait about a minute before asking again."
                        )
                    continue

            try:
                return await self._request_with_shrink(
                    target.client,
                    target.model,
                    system,
                    user,
                    temperature,
                    max_tokens,
                    json_mode,
                    "low" if tier == "aux" else "medium",
                )
            except Exception as exc:
                errors.append(f"{target.provider}/{target.model}: {exc}")
                logger.warning(
                    "LLM %s/%s failed (%s); trying next target",
                    target.provider,
                    target.model,
                    exc,
                )
                if target.provider == "groq" and (
                    isinstance(exc, APIStatusError) and exc.status_code == 429
                ):
                    raise LLMRateLimitError(
                        "Groq's per-minute token limit is reached. "
                        "Please wait about a minute before asking again."
                    ) from exc

        raise LLMError("No LLM target succeeded: " + "; ".join(errors))

    async def clarification_questions(self, summary: DataSummary, sheets: Dict[str, Any]) -> List[str]:
        budget = self._prompt_budget("clarify")
        description = generate_data_description(
            summary,
            sheets,
            max_columns=36,
            max_tokens=max(550, int(budget * 0.7)),
            include_samples=False,
        )
        prompt = f"""A user uploaded a spreadsheet. Analyze this profile and propose 2-3 short,
high-value clarifying questions whose answers would most improve analysis accuracy.
Focus on ambiguous column meanings, units/currency, time granularity, and business goals.

{description}

Return JSON: {{"questions": ["...", "...", "..."]}}"""
        try:
            raw = await self._chat(
                SYSTEM_JSON,
                prompt,
                temperature=0.3,
                max_tokens=700,
                json_mode=True,
                max_input_tokens=budget,
                tier="aux",
                allow_fallback=False,
            )
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
        budget = self._prompt_budget("code")
        description = generate_data_description(
            summary,
            sheets,
            question=question,
            max_columns=48,
            max_tokens=max(650, int(budget * 0.44)),
        )
        context_txt = (
            truncate_to_tokens(context, self._aux_budget("context", 600))
            if context
            else "(none)"
        )
        history_txt = format_history(
            history,
            current_question=question,
            max_tokens=self._aux_budget("history", 900),
        ) or "(none)"
        question_txt = truncate_to_tokens(question, 300)

        repair_section = ""
        if error_feedback and previous_code:
            repair_section = f"""
PREVIOUS ATTEMPT FAILED.
Previous code:
{truncate_to_tokens(previous_code, 450)}

Error:
{truncate_to_tokens(error_feedback, 300)}

Fix the issue and return corrected code only.
"""

        prompt = f"""{description}

EARLIER USER CONTEXT:
{context_txt}

RECENT CONVERSATION:
{history_txt}

RULES:
1. `df` is the active sheet as a DataFrame; `sheets` is a dict of sheet name -> DataFrame.
2. Use `df` unless the question mentions a specific other sheet: `sheets["Name"]`.
3. Assign the final answer to `result`. Cap DataFrames to 10 rows for rankings
   and "top" questions unless the user explicitly asks for a different count;
   otherwise cap at 50.
4. For chart-friendly output prefer tidy DataFrames with named columns (e.g. groupby().reset_index()).
5. Handle missing values sensibly. Use only pandas/numpy/math/statistics/datetime.
   Do not import or call matplotlib; return chart-friendly data instead.
6. No print statements for the answer; no file/network access; no dunder attributes.
7. If the sheet is a wide numeric table and the question asks for a breakdown by
   categories without naming a category column, treat the numeric measure columns
   as categories: aggregate column totals (e.g. `df.select_dtypes("number").sum()`),
   sort descending, return a tidy `category,total` DataFrame, and cap it to the top 10.

{repair_section}
USER QUESTION:
{question_txt}

Return ONLY Python code — no markdown, no explanations."""

        return await self._chat(
            SYSTEM_CODE,
            prompt,
            temperature=self.settings.temperature_code,
            max_tokens=1200,
            max_input_tokens=budget,
        )

    async def plan_chart(self, question: str, result: Any, summary: DataSummary) -> ChartSpec:
        """Ask the LLM to pick the best chart type and encoding for the result."""
        import pandas as pd

        if not isinstance(result, (pd.DataFrame, pd.Series)):
            return ChartSpec(type=ChartType.NONE)

        if isinstance(result, pd.DataFrame):
            columns = [str(column) for column in result.columns]
            shown = ", ".join(columns[:20])
            if len(columns) > 20:
                shown += f", ... ({len(columns) - 20} more)"
            sample_frame = result.head(8)
            if len(columns) > 10:
                sample_frame = sample_frame.iloc[:, :10]
            sample = sample_frame.to_dict(orient="records")
            result_desc = f"DataFrame; shape {result.shape}; columns: {shown}"
        elif isinstance(result, pd.Series):
            result_desc = f"Series named {result.name!r}; index samples {list(result.index[:5])}"
            sample = result.head(8).to_dict()
        else:
            result_desc = f"scalar/list value: {type(result).__name__}"
            sample = result

        sample_txt = truncate_to_tokens(
            json.dumps(sample, separators=(",", ":"), default=str), 160
        )
        question_txt = truncate_to_tokens(question, 120)
        prompt = f"""Question: {question_txt}
Analysis result: {result_desc}
Result sample: {sample_txt}

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
            raw = await self._chat(
                SYSTEM_JSON,
                prompt,
                temperature=0.1,
                max_tokens=500,
                json_mode=True,
                max_input_tokens=self._prompt_budget("chart"),
                tier="aux",
                allow_fallback=False,
            )
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
        description = generate_data_description(
            summary,
            sheets or {},
            question=question,
            max_columns=24,
            max_tokens=250,
            include_samples=False,
        )
        result_str = truncate_to_tokens(
            json.dumps(result_preview, separators=(",", ":"), default=str), 400
        )
        code_txt = truncate_to_tokens(code, 400)
        question_txt = truncate_to_tokens(question, 200)
        stdout_section = (
            f"Program output: {truncate_to_tokens(stdout, 80)}" if stdout else ""
        )
        currency_values: List[str] = []
        if sheets:
            for frame in sheets.values():
                if "currency" in getattr(frame, "columns", []):
                    currency_values = [
                        str(value) for value in frame["currency"].dropna().unique()[:3]
                    ]
                    break
        currency_note = (
            f"Dataset currency: {', '.join(currency_values)}. "
            "Use this currency code or its symbol; do not use '$' unless it is one of these.\n"
            if currency_values
            else "If the result has no currency, do not add a currency symbol.\n"
        )

        prompt = f"""Dataset: {description}

Question: {question_txt}

Code executed:
{code_txt}

Result:
{result_str}

{stdout_section}
{currency_note}
Write a concise, insightful answer in plain language (short paragraphs or bullets).
Lead with the direct answer, then notable patterns/caveats. Do not invent numbers
that are not in the result. For rankings, mention only the top 5-10 and summarize
the rest; never dump the full table. Keep the answer under 150 words.
Use simple markdown: **bold**, `code`, and lists."""

        try:
            return await self._chat(
                "You explain data analysis results clearly, accurately, and concisely.",
                prompt,
                temperature=self.settings.temperature_text,
                max_tokens=450,
                max_input_tokens=self._prompt_budget("explain"),
                tier="aux",
                allow_fallback=False,
            )
        except LLMError:
            logger.warning("Explanation service unavailable; using deterministic result summary")
            return self._fallback_explanation(result_preview)

    @staticmethod
    def _fallback_explanation(result_preview: Any) -> str:
        if isinstance(result_preview, list) and result_preview and isinstance(result_preview[0], dict):
            rows = result_preview[:5]
            columns = list(rows[0].keys())
            header = "| " + " | ".join(str(column) for column in columns) + " |"
            divider = "| " + " | ".join("---" for _ in columns) + " |"
            body = "\n".join(
                "| "
                + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns)
                + " |"
                for row in rows
            )
            return "Here are the top results:\n\n" + "\n".join([header, divider, body])
        if isinstance(result_preview, dict):
            pairs = "\n".join(f"- **{key}:** {value}" for key, value in list(result_preview.items())[:10])
            return "Here is the result:\n\n" + pairs
        return f"Result: {result_preview}"

    async def friendly_error(
        self,
        question: str,
        error: str,
        summary: DataSummary,
        sheets: Optional[Dict[str, Any]] = None,
    ) -> str:
        description = generate_data_description(
            summary,
            sheets or {},
            question=question,
            max_columns=18,
            max_tokens=300,
            include_samples=False,
        )
        question_txt = truncate_to_tokens(question, 200)
        error_txt = truncate_to_tokens(error, 300)
        prompt = f"""The user asked: {question_txt}
Data profile: {description}
Error: {error_txt}

Explain what went wrong in one or two sentences and suggest how to rephrase the question.
Be friendly and specific."""
        try:
            return await self._chat(
                "You are a helpful data assistant.",
                prompt,
                temperature=0.3,
                max_tokens=250,
                max_input_tokens=self._prompt_budget("error"),
                tier="aux",
                allow_fallback=False,
            )
        except LLMError:
            return (
                f"I couldn't complete that analysis ({error}). "
                "Try rephrasing with explicit column names, or ask something simpler like "
                "'what are the summary statistics?'."
            )
