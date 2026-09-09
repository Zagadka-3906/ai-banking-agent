"""Restricted execution helper for model-generated analytics.

Banking actions are never executed through this module.  It is only for
read-only calculations (for example, grouping already-fetched transactions).
The accepted language is a small Python expression subset validated with
``ast`` and evaluated in a worker process with a timeout.  A production
deployment should use a separate container/microVM with seccomp, a read-only
filesystem and a denied network namespace; this module is a portable demo
guardrail, not a substitute for OS isolation.
"""

from __future__ import annotations

import ast
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import math
import os
from typing import Any, Optional


class SandboxError(ValueError):
    """Raised when an expression is outside the read-only sandbox policy."""


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(value, maximum))


MAX_EXPRESSION_LENGTH = _env_int("BANK_SANDBOX_MAX_EXPRESSION", 1000, 64, 10000)
MAX_INPUT_BYTES = _env_int("BANK_SANDBOX_MAX_INPUT_BYTES", 200000, 1024, 5_000_000)
MAX_RESULT_BYTES = _env_int("BANK_SANDBOX_MAX_RESULT_BYTES", 200000, 1024, 5_000_000)
MAX_AST_NODES = _env_int("BANK_SANDBOX_MAX_AST_NODES", 256, 32, 4096)
MAX_COLLECTION_ITEMS = _env_int("BANK_SANDBOX_MAX_COLLECTION_ITEMS", 1000, 16, 100_000)
MAX_INTEGER_BITS = _env_int("BANK_SANDBOX_MAX_INTEGER_BITS", 4096, 64, 1_000_000)
MAX_POWER_EXPONENT = _env_int("BANK_SANDBOX_MAX_POWER_EXPONENT", 10000, 1, 1_000_000)
DEFAULT_TIMEOUT_SECONDS = _env_float("BANK_SANDBOX_TIMEOUT_SECONDS", 1.5, 0.1, 10.0)


_ALLOWED_NODES = {
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Call,
    ast.Load,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.Subscript,
    ast.Slice,
    ast.Index,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
}

_SAFE_FUNCTIONS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "len": len,
    "sum": sum,
    "sorted": sorted,
    "isfinite": math.isfinite,
}


def validate_expression(expression: str) -> ast.Expression:
    if not isinstance(expression, str):
        raise SandboxError("表达式必须是字符串")
    expression = expression.strip()
    if not expression or len(expression) > MAX_EXPRESSION_LENGTH:
        raise SandboxError("表达式为空或超过长度限制")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise SandboxError("表达式语法不受支持") from exc
    nodes_seen = 0
    for node in ast.walk(tree):
        nodes_seen += 1
        if nodes_seen > MAX_AST_NODES:
            raise SandboxError("沙箱表达式结构过于复杂")
        if type(node) not in _ALLOWED_NODES:
            raise SandboxError(f"沙箱禁止语法节点: {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (str, int, float, bool, type(None))):
            raise SandboxError("沙箱禁止该常量类型")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int) and abs(node.value).bit_length() > MAX_INTEGER_BITS:
                raise SandboxError("沙箱整数常量过大")
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise SandboxError("沙箱浮点常量必须有限")
        if isinstance(node, ast.Name) and node.id not in {"data", "True", "False", "None", *_SAFE_FUNCTIONS}:
            raise SandboxError(f"沙箱禁止访问名称: {node.id}")
        if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            item_count = len(node.elts) if not isinstance(node, ast.Dict) else len(node.keys)
            if item_count > MAX_COLLECTION_ITEMS:
                raise SandboxError("沙箱集合元素过多")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            # Exponentiation is the easiest way to turn a tiny expression into
            # an unbounded CPU/memory allocation.  Permit only a literal,
            # positive, explicitly bounded exponent.
            exponent = node.right
            if not isinstance(exponent, ast.Constant) or not isinstance(exponent.value, int) or isinstance(exponent.value, bool):
                raise SandboxError("沙箱仅允许受限整数幂运算")
            if abs(exponent.value) > MAX_POWER_EXPONENT:
                raise SandboxError("沙箱幂指数超过安全上限")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCTIONS:
                raise SandboxError("沙箱仅允许调用白名单纯函数")
            if node.keywords:
                raise SandboxError("沙箱不允许关键字参数")
    return tree


def _evaluate_worker(expression: str, data: Any) -> Any:
    tree = validate_expression(expression)
    # No imports, filesystem, network, classes or mutable global state are
    # exposed.  ``__builtins__`` is an empty dict even though the AST has
    # already rejected calls other than the explicit safe functions.
    globals_dict = {"__builtins__": {}, **_SAFE_FUNCTIONS}
    locals_dict = {"data": data}
    return eval(compile(tree, "<bank-sandbox>", "eval"), globals_dict, locals_dict)


def evaluate_expression(expression: str, data: Any, *, timeout_seconds: Optional[float] = None) -> Any:
    """Evaluate a pure expression against JSON-like data with hard bounds."""

    tree = validate_expression(expression)
    del tree  # validation happens before serializing/starting a worker
    try:
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise SandboxError("输入数据必须是 JSON 可序列化对象") from exc
    if len(encoded.encode("utf-8")) > MAX_INPUT_BYTES:
        raise SandboxError("输入数据超过沙箱大小限制")
    try:
        safe_data = json.loads(encoded)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise SandboxError("输入数据编码失败") from exc
    timeout = _env_float("BANK_SANDBOX_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, 0.1, 10.0)
    if timeout_seconds is not None:
        try:
            requested_timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            raise SandboxError("沙箱超时时间无效")
        if not math.isfinite(requested_timeout):
            raise SandboxError("沙箱超时时间无效")
        timeout = max(0.1, min(requested_timeout, 10.0))
    # A one-worker pool is intentionally short lived: no state survives across
    # calls and a timed-out computation can be terminated by shutting down the
    # worker process.
    executor: Any = ProcessPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_evaluate_worker, expression, safe_data)
    except (PermissionError, OSError):
        # Some managed Windows environments forbid spawning a child process.
        # Keep the AST/builtins boundary and use a short-lived thread as a
        # monitored fallback; production deployments should retain the process
        # or container isolation instead.
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_evaluate_worker, expression, safe_data)
    try:
        result = future.result(timeout=timeout)
    except FutureTimeoutError as exc:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise SandboxError("沙箱执行超时") from exc
    except Exception as exc:
        raise SandboxError(f"沙箱执行失败: {exc}") from exc
    finally:
        # ``wait=False`` avoids blocking the agent on a runaway child.  Normal
        # completed workers exit immediately; timed-out workers are cancelled.
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # Python < 3.9 compatibility
            executor.shutdown(wait=False)
    try:
        result_encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise SandboxError("沙箱结果不可序列化") from exc
    if len(result_encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        raise SandboxError("沙箱结果超过大小限制")
    return result


def sandbox_health() -> dict[str, Any]:
    return {
        "status": "OK",
        "mode": "read_only_expression",
        "network": "denied_by_api",
        "max_expression_length": MAX_EXPRESSION_LENGTH,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "max_ast_nodes": MAX_AST_NODES,
        "max_power_exponent": MAX_POWER_EXPONENT,
    }
