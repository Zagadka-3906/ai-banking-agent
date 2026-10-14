"""Shared HTTP ingress limits for Core and Gateway."""

import re

from starlette.requests import ClientDisconnect, Request

from security import SecurityError


async def cache_bounded_body(request: Request, max_bytes: int) -> None:
    """Bound actual bytes before JSON parsing, including chunked requests.

    Content-Length is only an early rejection hint. Cache accepted bytes using
    Starlette's Request.body() convention so BaseHTTPMiddleware can replay the
    consumed stream to the endpoint without another unbounded read.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        if not re.fullmatch(r"[0-9]+", declared):
            raise SecurityError("请求长度字段无效", 400, "INVALID_CONTENT_LENGTH")
        # Compare as decimal strings to avoid Python's integer digit limit.
        normalized = declared.lstrip("0") or "0"
        limit = str(max_bytes)
        if len(normalized) > len(limit) or (len(normalized) == len(limit) and normalized > limit):
            raise SecurityError("请求体超过安全限制", 413, "REQUEST_TOO_LARGE")

    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > max_bytes:
                raise SecurityError("请求体超过安全限制", 413, "REQUEST_TOO_LARGE")
            body.extend(chunk)
    except ClientDisconnect as exc:
        raise SecurityError("请求体传输未完成", 400, "REQUEST_BODY_INCOMPLETE") from exc
    request._body = bytes(body)
