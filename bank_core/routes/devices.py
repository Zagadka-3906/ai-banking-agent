"""Device enrollment for the red tier's signature factor."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from bank_core.access import (
    get_actor,
    record_failure_and_raise,
    server_request_id,
)
from bank_core.models import (
    DeviceRegisterRequest,
)
from bank_core.payments import (
    ensure_account,
)
from security import (
    SecurityError,
    audit_event,
    enroll_device,
    list_devices,
    revoke_device,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Device trust root: enrolling the key behind the red tier's biometric factor
# ---------------------------------------------------------------------------
@router.post("/api/v1/devices/register")
def register_device(
    req: DeviceRegisterRequest,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Register this device's public key against the authenticated principal.

    Enrollment is a credential-establishing operation, so it demands a session
    credential rather than a self-asserted identity header: once a key is
    enrolled, its signatures satisfy the biometric factor for that account.
    The middleware has already replaced ``X-User-Id`` with the session subject
    when a session is presented, so the two cannot disagree here.
    """

    request_id = server_request_id(x_request_id)
    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "设备注册必须出示会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    try:
        ensure_account(x_user_id)
        summary = enroll_device(
            req.public_key,
            user_id=x_user_id,
            label=req.label,
            platform=req.platform,
            app_version=req.app_version,
            request_id=request_id,
        )
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=x_user_id, action="DEVICE_REGISTER", request_id=request_id)
    return {"code": 200, **summary}


@router.get("/api/v1/devices")
def get_devices(x_session_token: Optional[str] = Header(None, alias="X-Session-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """List the devices enrolled to the caller.  No key material is returned.

    Demands a session credential like enrollment: the
    device registry is the red tier's trust root, so it must not be readable -
    or revocable, below - from a self-asserted identity header alone.
    """

    request_id = server_request_id(x_request_id)
    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "设备查询必须出示会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    actor = get_actor(x_user_id, x_user_id, action="DEVICE_QUERY", request_id=request_id)
    rows = list_devices(actor)
    audit_event("DEVICE_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@router.post("/api/v1/devices/{device_id}/revoke")
def revoke_registered_device(device_id: str, x_session_token: Optional[str] = Header(None, alias="X-Session-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Stop accepting attestations from one of the caller's own devices.

    A session credential is required for the same reason as enrollment: this
    endpoint controls the device trust root, and revoking a device is a
    security response, not a profile edit.
    """

    request_id = server_request_id(x_request_id)
    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "设备吊销必须出示会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    actor = get_actor(x_user_id, x_user_id, action="DEVICE_REVOKE", request_id=request_id)
    try:
        summary = revoke_device(device_id, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=actor, action="DEVICE_REVOKE", request_id=request_id)
    return {"code": 200, **summary}
