"""AST-validated sandbox for executing LLM-generated pandas code."""

from __future__ import annotations

import ast
import math
import multiprocessing as mp
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

MAX_CODE_LENGTH = 8000
MAX_RESULT_ROWS = 200

ALLOWED_IMPORTS = {"pandas", "numpy", "math", "datetime", "statistics"}

# Attribute names that are never allowed (dunder / file / process access)
BLOCKED_ATTRS = {
    "__import__",
    "__builtins__",
    "__class__",
    "__closure__",
    "__code__",
    "__subclasses__",
    "__globals__",
    "__dict__",
    "__getattribute__",
    "__setattr__",
    "__delattr__",
    "__reduce__",
    "__reduce_ex__",
    "__bases__",
    "__mro__",
    "__qualname__",
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "breakpoint",
    "exit",
    "quit",
    "os",
    "sys",
    "subprocess",
    "socket",
    "shutil",
    "pathlib",
    "requests",
    "urllib",
    "http",
    "ctypes",
    "pickle",
    "shelve",
    "sqlite3",
    "importlib",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
    "memoryview",
    "bytearray",
}

BLOCKED_CALL_NAMES = {
    "eval",
    "exec",
    "compile",
    "open",
    "__import__",
    "input",
    "breakpoint",
    "exit",
    "quit",
}


class SandboxError(Exception):
    """Raised when generated code is rejected or fails validation."""


@dataclass
class ExecutionResult:
    value: Any
    stdout: str
    ok: bool
    error: Optional[str] = None


def validate_code(code: str) -> ast.Module:
    """Validate that the code only uses allowed syntax."""
    if len(code) > MAX_CODE_LENGTH:
        raise SandboxError(f"Code exceeds maximum length ({MAX_CODE_LENGTH} chars)")

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SandboxError(f"Syntax error: {exc.msg} (line {exc.lineno})") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    raise SandboxError(f"Import of '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORTS:
                raise SandboxError(f"Import from '{node.module}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in BLOCKED_ATTRS:
                raise SandboxError(f"Access to attribute '{node.attr}' is not allowed")
        elif isinstance(node, ast.Name):
            if node.id in BLOCKED_CALL_NAMES or node.id.startswith("__"):
                raise SandboxError(f"Use of '{node.id}' is not allowed")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BLOCKED_CALL_NAMES:
                raise SandboxError(f"Call to '{func.id}' is not allowed")
        elif isinstance(
            node,
            (
                ast.Global,
                ast.Nonlocal,
                ast.Lambda,
                ast.Yield,
                ast.YieldFrom,
                ast.Await,
                ast.AsyncFunctionDef,
                ast.AsyncFor,
                ast.AsyncWith,
            ),
        ):
            raise SandboxError(f"{type(node).__name__} is not allowed")

    return tree


def _safe_import(name: str, globals=None, locals=None, fromlist=(), level=0):
    """Import hook that only allows the pre-approved safe modules."""
    root = name.split(".")[0]
    if root not in ALLOWED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not allowed")
    return __import__(name, globals, locals, fromlist, level)


def _restricted_builtins() -> Dict[str, Any]:
    safe = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "frozenset": frozenset,
        "int": int,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "pow": pow,
        "range": range,
        "reversed": reversed,
        "round": round,
        "set": set,
        "slice": slice,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
        "True": True,
        "False": False,
        "None": None,
        "print": print,
        # pandas/numpy lazily call __import__ internally — gate it via _safe_import
        "__import__": _safe_import,
    }
    return safe


def _worker(code: str, df: pd.DataFrame, sheets: Dict[str, pd.DataFrame], queue: mp.Queue) -> None:
    """Child process: execute validated code and put the result on the queue."""
    import io
    import sys

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        tree = validate_code(code)
        namespace: Dict[str, Any] = {
            "__builtins__": _restricted_builtins(),
            "df": df.copy(),
            "sheets": {name: frame.copy() for name, frame in sheets.items()},
            "pd": pd,
            "np": np,
            "math": math,
            "result": None,
        }
        exec(compile(tree, "<generated>", "exec"), namespace)  # noqa: S102 - validated AST
        queue.put(("ok", namespace.get("result"), buf.getvalue()))
    except Exception as exc:  # noqa: BLE001 - forward any error to parent
        queue.put(("err", f"{type(exc).__name__}: {exc}", buf.getvalue()))
    finally:
        sys.stdout = old_stdout


def execute_safely(
    code: str,
    df: pd.DataFrame,
    sheets: Dict[str, pd.DataFrame],
    timeout: float = 10.0,
) -> ExecutionResult:
    """Execute generated code in a separate process with a timeout."""
    try:
        validate_code(code)  # fail fast before spawning
    except SandboxError as exc:
        return ExecutionResult(value=None, stdout="", ok=False, error=str(exc))

    ctx = mp.get_context("fork")
    queue: mp.Queue = ctx.Queue()
    proc = ctx.Process(target=_worker, args=(code, df, sheets, queue))
    proc.start()
    proc.join(timeout)

    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        return ExecutionResult(
            value=None,
            stdout="",
            ok=False,
            error=f"Code execution timed out after {timeout:.0f}s",
        )

    if queue.empty():
        return ExecutionResult(value=None, stdout="", ok=False, error="Execution produced no result")

    status, payload, stdout = queue.get()
    if status != "ok":
        return ExecutionResult(value=None, stdout=stdout, ok=False, error=str(payload))

    value = payload
    # Cap huge results so the LLM/UI never receive megabytes of data
    if isinstance(value, pd.DataFrame) and len(value) > MAX_RESULT_ROWS:
        value = value.head(MAX_RESULT_ROWS)
    elif isinstance(value, pd.Series) and len(value) > MAX_RESULT_ROWS:
        value = value.head(MAX_RESULT_ROWS)

    return ExecutionResult(value=value, stdout=stdout, ok=True)
