"""Framework-independent security exceptions."""

from __future__ import annotations



class SecurityError(Exception):
    """A safe, user-facing security failure.

    ``status_code`` is consumed by the FastAPI adapter.  Keeping the class
    framework-independent makes the same checks usable from an agent worker
    or a CLI test.
    """

    def __init__(self, detail: str, status_code: int = 403, code: str = "SECURITY_ERROR"):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


class InjectionError(SecurityError):
    def __init__(self, detail: str = "检测到不安全输入，操作已阻断"):
        super().__init__(detail, status_code=400, code="PROMPT_INJECTION_BLOCKED")
