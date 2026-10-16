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
from concurrent.futures.process import BrokenProcessPool
import json
import math
import multiprocessing
import os
import operator
import threading
import time
from typing import Any, Optional


class SandboxError(ValueError):
    """Raised when an expression is outside the read-only sandbox policy."""


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
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


MAX_EXPRESSION_LENGTH = env_int("BANK_SANDBOX_MAX_EXPRESSION", 1000, 64, 10000)
MAX_INPUT_BYTES = env_int("BANK_SANDBOX_MAX_INPUT_BYTES", 200000, 1024, 5_000_000)
MAX_RESULT_BYTES = env_int("BANK_SANDBOX_MAX_RESULT_BYTES", 200000, 1024, 5_000_000)
MAX_AST_NODES = env_int("BANK_SANDBOX_MAX_AST_NODES", 256, 32, 4096)
MAX_COLLECTION_ITEMS = env_int("BANK_SANDBOX_MAX_COLLECTION_ITEMS", 1000, 16, 100_000)
MAX_INTEGER_BITS = env_int("BANK_SANDBOX_MAX_INTEGER_BITS", 4096, 64, 1_000_000)
MAX_POWER_EXPONENT = env_int("BANK_SANDBOX_MAX_POWER_EXPONENT", 10000, 1, 1_000_000)
MAX_VALUE_DEPTH = 32
MAX_VALUE_NODES = 20_000
DEFAULT_TIMEOUT_SECONDS = _env_float("BANK_SANDBOX_TIMEOUT_SECONDS", 1.5, 0.1, 10.0)


_TELEMETRY_LOCK = threading.Lock()
_TELEMETRY: dict[str, Any] = {
    "last_executor": "none",
    "last_fallback_reason": None,
    "os_isolation": "none",
    "process_successes": 0,
    "thread_successes": 0,
    "fallbacks": 0,
    "timeouts": 0,
    "failures": 0,
    "rejections": 0,
    "last_transition_timestamp": None,
}


def _record_telemetry(
    *,
    executor: str | None = None,
    isolation: str | None = None,
    fallback_reason: str | None = None,
    process_success: bool = False,
    thread_success: bool = False,
    fallback: bool = False,
    timeout: bool = False,
    failure: bool = False,
) -> None:
    """Update process-local health counters without exposing exception data."""

    with _TELEMETRY_LOCK:
        if executor is not None:
            _TELEMETRY["last_executor"] = executor
        if isolation is not None:
            _TELEMETRY["os_isolation"] = isolation
        if fallback_reason is not None:
            _TELEMETRY["last_fallback_reason"] = fallback_reason
        for key, enabled in (
            ("process_successes", process_success),
            ("thread_successes", thread_success),
            ("fallbacks", fallback),
            ("timeouts", timeout),
            ("failures", failure),
        ):
            if enabled:
                _TELEMETRY[key] += 1
        if any((executor, isolation, fallback_reason, process_success, thread_success, fallback, timeout, failure)):
            _TELEMETRY["last_transition_timestamp"] = time.time()


def _sandbox_telemetry() -> dict[str, Any]:
    with _TELEMETRY_LOCK:
        return dict(_TELEMETRY)


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

def _as_finite_number(value: Any) -> float:
    """Coerce a JSON scalar to a finite float, otherwise 0.0 (pure)."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _spend_total(items: Any, key: Any) -> float:
    """Sum positive amounts of a field across JSON objects (pure)."""

    total = 0.0
    for item in items or []:
        if isinstance(item, dict):
            number = _as_finite_number(item.get(key))
            if number > 0:
                total += number
    return round(total, 2)


def _refund_total(items: Any, key: Any) -> float:
    """Sum negative amounts (negated) of a field across JSON objects (pure)."""

    total = 0.0
    for item in items or []:
        if isinstance(item, dict):
            number = _as_finite_number(item.get(key))
            if number < 0:
                total -= number
    return round(total, 2)


def _group_spend(items: Any, group_key: Any, value_key: Any) -> dict[str, float]:
    """Group JSON objects by a key and sum positive values per group (pure)."""

    buckets: dict[str, float] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        number = _as_finite_number(item.get(value_key))
        if number <= 0:
            continue
        group = str(item.get(group_key) or "其他")
        buckets[group] = round(buckets.get(group, 0.0) + number, 2)
    return buckets


_SAFE_FUNCTIONS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "len": len,
    "sum": sum,
    "sorted": sorted,
    "isfinite": math.isfinite,
    "spend_total": _spend_total,
    "refund_total": _refund_total,
    "group_spend": _group_spend,
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


def _bounded_value(value: Any, *, depth: int = 0, budget: list[int] | None = None,
                   byte_limit: int = MAX_RESULT_BYTES) -> Any:
    """Bound every intermediate, not only the tiny result of e.g. len(x*N)."""
    if budget is None:
        budget = [MAX_VALUE_NODES, byte_limit]
    budget[0] -= 1
    budget[1] -= 8  # container/scalar syntax and separators
    if depth > MAX_VALUE_DEPTH or budget[0] < 0:
        raise SandboxError("沙箱数据结构超过限制")
    if isinstance(value, int) and value.bit_length() > MAX_INTEGER_BITS:
        raise SandboxError("沙箱整数结果超过位数限制")
    if isinstance(value, float) and not math.isfinite(value):
        raise SandboxError("沙箱数值结果必须有限")
    if isinstance(value, str) and len(value.encode("utf-8")) > MAX_RESULT_BYTES:
        raise SandboxError("沙箱字符串超过大小限制")
    if isinstance(value, str):
        budget[1] -= len(value.encode("utf-8"))
    elif isinstance(value, int):
        budget[1] -= value.bit_length() // 3 + 1
    elif isinstance(value, float):
        budget[1] -= 24
    if budget[1] < 0:
        raise SandboxError("沙箱数据累计大小超过限制")
    if isinstance(value, (list, tuple, set, dict)):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise SandboxError("沙箱集合元素过多")
        items = value.items() if isinstance(value, dict) else value
        for item in items:
            _bounded_value(item, depth=depth + 1, budget=budget)
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise SandboxError("沙箱结果类型不受支持")
    return value


_BINARY_OPERATORS = {
    "Add": operator.add, "Sub": operator.sub, "Mult": operator.mul,
    "Div": operator.truediv, "FloorDiv": operator.floordiv,
    "Mod": operator.mod, "Pow": operator.pow,
}


def _bounded_binary(operation: str, left: Any, right: Any) -> Any:
    if operation == "Mult":
        sequence, count = (left, right) if isinstance(right, int) else (right, left)
        if isinstance(sequence, (str, list, tuple)) and isinstance(count, int):
            limit = MAX_RESULT_BYTES if isinstance(sequence, str) else MAX_COLLECTION_ITEMS
            if len(sequence) * max(count, 0) > limit:
                raise SandboxError("沙箱重复分配超过限制")
        elif isinstance(left, int) and isinstance(right, int):
            if left and right and left.bit_length() + right.bit_length() - 1 > MAX_INTEGER_BITS:
                raise SandboxError("沙箱整数乘积超过限制")
    elif operation == "Add" and isinstance(left, (str, list, tuple)) and isinstance(right, type(left)):
        limit = MAX_RESULT_BYTES if isinstance(left, str) else MAX_COLLECTION_ITEMS
        if len(left) + len(right) > limit:
            raise SandboxError("沙箱合并结果超过限制")
    elif operation == "Mod" and isinstance(left, str):
        # A width such as %999999999s allocates before result validation.
        raise SandboxError("沙箱不允许字符串格式化运算")
    elif operation == "Pow" and isinstance(left, int) and isinstance(right, int):
        if abs(left) > 1 and right > 0 and left.bit_length() * right > MAX_INTEGER_BITS:
            raise SandboxError("沙箱幂运算结果超过位数限制")
    return _bounded_value(_BINARY_OPERATORS[operation](left, right))


class _BoundIntermediateOperations(ast.NodeTransformer):
    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        return ast.copy_location(ast.Call(
            func=ast.Name(id="_bounded_binary", ctx=ast.Load()),
            args=[ast.Constant(type(node.op).__name__), node.left, node.right], keywords=[],
        ), node)


def _evaluate_os_worker(expression: str, data: Any) -> Any:
    from sandbox_limits import apply_worker_limits
    apply_worker_limits()
    return _evaluate_worker(expression, data)


def _evaluate_worker(expression: str, data: Any) -> Any:
    tree = validate_expression(expression)
    _bounded_value(data, byte_limit=MAX_INPUT_BYTES)
    tree = ast.fix_missing_locations(_BoundIntermediateOperations().visit(tree))
    # No imports, filesystem, network, classes or mutable global state are
    # exposed.  ``__builtins__`` is an empty dict even though the AST has
    # already rejected calls other than the explicit safe functions.
    def bounded_function(function):
        def call(*args):
            return _bounded_value(function(*args))
        return call
    globals_dict = {"__builtins__": {}, "_bounded_binary": _bounded_binary,
                    **{name: bounded_function(fn) for name, fn in _SAFE_FUNCTIONS.items()}}
    locals_dict = {"data": data}
    result = _bounded_value(eval(compile(tree, "<bank-sandbox>", "eval"), globals_dict, locals_dict))
    # Enforce the output bound before IPC/pickling allocates it in the parent.
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_RESULT_BYTES:
        raise SandboxError("沙箱结果超过大小限制")
    return result


def _terminate_process_workers(executor: Any) -> None:
    """Future.cancel cannot stop running Python; terminate this disposable pool."""
    processes = list((getattr(executor, "_processes", None) or {}).values())
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(timeout=0.2)
        if process.is_alive():
            process.kill()
            process.join(timeout=0.2)


def _thread_fallback_executor(previous: Any) -> ThreadPoolExecutor:
    try:
        previous.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    return ThreadPoolExecutor(max_workers=1)


def _allow_thread_fallback() -> bool:
    # A thread is not an isolation boundary. It is opt-in only for local
    # diagnosis of environments that cannot spawn a process.
    return os.getenv("BANK_SANDBOX_ALLOW_THREAD_FALLBACK", "0").lower() in {"1", "true", "yes"}


def _run_in_worker(expression: str, safe_data: Any, timeout: float) -> Any:
    """Run the validated expression behind a short-lived isolation boundary.

    Uses a worker process. If process spawning fails or the worker dies,
    expression execution fails closed. A thread fallback is available only
    through the explicit BANK_SANDBOX_ALLOW_THREAD_FALLBACK diagnostic switch;
    a thread has no isolation boundary and must not be used for normal runs.
    """

    executor: Any = None
    using_process = True
    try:
        # Spawn avoids inheriting the gateway's address space and credentials;
        # it also makes the fixed resource budget predictable on POSIX hosts.
        executor = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        future = executor.submit(_evaluate_os_worker, expression, safe_data)
    except (PermissionError, OSError, RuntimeError) as exc:
        if not _allow_thread_fallback():
            _record_telemetry(executor="process", isolation="none", fallback_reason="submit_failure", failure=True)
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            raise SandboxError("沙箱隔离进程不可用") from exc
        # Managed environments may deny child-process spawning, and Windows
        # spawn re-imports ``__main__``: a plain script without the
        # ``if __name__ == "__main__"`` guard fails at submit time.  Retain the
        # AST/builtins/timeout boundary on a monitored thread instead.
        reason = "broken_process_pool" if isinstance(exc, BrokenProcessPool) else "submit_failure"
        _record_telemetry(
            executor="thread_fallback", isolation="none", fallback_reason=reason, fallback=True
        )
        executor = _thread_fallback_executor(executor)
        using_process = False
        try:
            future = executor.submit(_evaluate_worker, expression, safe_data)
        except Exception as thread_exc:
            _record_telemetry(executor="thread_fallback", failure=True)
            raise SandboxError(f"沙箱执行失败: {type(thread_exc).__name__}") from thread_exc
    try:
        result = future.result(timeout=timeout)
        if using_process:
            _record_telemetry(executor="process", isolation="process", process_success=True)
        else:
            _record_telemetry(executor="thread_fallback", thread_success=True)
        return result
    except FutureTimeoutError as exc:
        future.cancel()
        if using_process:
            _terminate_process_workers(executor)
        _record_telemetry(
            executor="process" if using_process else "thread_fallback",
            timeout=True,
        )
        raise SandboxError("沙箱执行超时") from exc
    except BrokenProcessPool:
        if not _allow_thread_fallback():
            _record_telemetry(executor="process", isolation="none", fallback_reason="broken_process_pool", failure=True)
            raise SandboxError("沙箱隔离进程不可用")
        _record_telemetry(
            executor="thread_fallback", isolation="none", fallback_reason="broken_process_pool", fallback=True
        )
        executor = _thread_fallback_executor(executor)
        using_process = False
        try:
            future = executor.submit(_evaluate_worker, expression, safe_data)
        except Exception as exc:
            _record_telemetry(executor="thread_fallback", failure=True)
            raise SandboxError(f"沙箱执行失败: {type(exc).__name__}") from exc
        try:
            result = future.result(timeout=timeout)
            _record_telemetry(executor="thread_fallback", thread_success=True)
            return result
        except FutureTimeoutError as exc:
            future.cancel()
            _record_telemetry(executor="thread_fallback", timeout=True)
            raise SandboxError("沙箱执行超时") from exc
        except Exception as exc:
            _record_telemetry(executor="thread_fallback", failure=True)
            raise SandboxError(f"沙箱执行失败: {type(exc).__name__}") from exc
    except Exception as exc:
        _record_telemetry(
            executor="process" if using_process else "thread_fallback",
            failure=True,
        )
        raise SandboxError(f"沙箱执行失败: {type(exc).__name__}") from exc
    finally:
        # A process timed out above has been terminated. Threads cannot be
        # forcibly stopped; their fallback retains only logical/API bounds.
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # Python < 3.9 compatibility
            executor.shutdown(wait=False)


def _evaluate_expression(expression: str, data: Any, *, timeout_seconds: Optional[float] = None) -> Any:
    """Evaluate a pure expression with logical bounds and a process deadline."""

    tree = validate_expression(expression)
    del tree  # validation happens before serializing/starting a worker
    _bounded_value(data, byte_limit=MAX_INPUT_BYTES)
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
    result = _run_in_worker(expression, safe_data, timeout)
    try:
        result_encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise SandboxError("沙箱结果不可序列化") from exc
    if len(result_encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        raise SandboxError("沙箱结果超过大小限制")
    return result


def evaluate_expression(expression: str, data: Any, *, timeout_seconds: Optional[float] = None,
                        user_id: Optional[str] = None, request_id: Optional[str] = None) -> Any:
    """Audit accepted/rejected calculations without logging expressions or data."""
    try:
        result = _evaluate_expression(expression, data, timeout_seconds=timeout_seconds)
    except Exception as exc:
        with _TELEMETRY_LOCK:
            _TELEMETRY["rejections"] += 1
            _TELEMETRY["last_transition_timestamp"] = time.time()
        _audit_execution(False, user_id=user_id, request_id=request_id)
        if isinstance(exc, SandboxError):
            raise
        raise SandboxError(f"沙箱执行失败: {type(exc).__name__}") from exc
    _audit_execution(True, user_id=user_id, request_id=request_id)
    return result


def _audit_execution(succeeded: bool, *, user_id: Optional[str] = None,
                     request_id: Optional[str] = None) -> None:
    try:
        from security import audit_event
        audit_event("SANDBOX_EXECUTION", decision="ALLOW" if succeeded else "BLOCK",
                    outcome="SUCCESS" if succeeded else "FAILURE",
                    user_id=user_id, request_id=request_id,
                    reason=None if succeeded else "POLICY_OR_EXECUTION_REJECTED")
    except Exception:
        # Health counters remain available even without the optional audit sink.
        pass


def sandbox_health() -> dict[str, Any]:
    return {
        "status": "OK",
        "mode": "read_only_expression",
        "network": "denied_by_api",
        "max_expression_length": MAX_EXPRESSION_LENGTH,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "max_ast_nodes": MAX_AST_NODES,
        "max_power_exponent": MAX_POWER_EXPONENT,
        "max_integer_bits": MAX_INTEGER_BITS,
        "max_input_bytes": MAX_INPUT_BYTES,
        "max_result_bytes": MAX_RESULT_BYTES,
        "max_collection_items": MAX_COLLECTION_ITEMS,
        "max_value_depth": MAX_VALUE_DEPTH,
        "max_value_nodes": MAX_VALUE_NODES,
        "memory_limit": "bounded_values_not_os_rss",
        "process_resource_policy": "windows_job_512MiB_3s" if os.name == "nt" else "posix_rlimit_512MiB_3s",
        "os_network_filesystem_isolation": False,
        "thread_timeout": "caller_deadline_not_thread_termination" if _allow_thread_fallback() else "disabled_fail_closed",
        "thread_fallback_enabled": _allow_thread_fallback(),
        "safe_functions": sorted(_SAFE_FUNCTIONS),
        "preferred_executor": "process",
        **_sandbox_telemetry(),
    }
