"""Wealth reads, recommendation, comparison and the risk questionnaire."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException

from bank_core.access import (
    _get_actor,
    _record_failure_and_raise,
    _request_id,
)
from bank_core.idempotency import (
    _idempotency_abort,
    _idempotency_begin,
    _idempotency_complete,
)
from bank_core.models import (
    RiskAssessmentRequest,
)
from bank_core.payments import (
    _ensure_account,
)
from bank_core.persistence import (
    persist_ledger,
)
from bank_core.wealth_data import (
    PROFILE_LABELS,
    RISK_GRADE_META,
    RISK_QUIZ_SCORES,
    WEALTH_HOLDINGS,
    WEALTH_PRODUCTS,
    WEALTH_RISK_PROFILES,
    _wealth_product_view,
    profile_rank,
    record_risk_assessment,
    risk_assessment_attempts,
    risk_grade_for_score,
)
from security import (
    STATE_LOCK,
    SecurityError,
    audit_event,
    sanitize_text,
)

router = APIRouter()


@router.get("/api/v1/wealth/products")
def get_wealth_products(user_id: Optional[str] = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """GREEN: list product facts; no recommendation is fabricated by the API."""

    requested = user_id or x_user_id
    if not requested:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(requested, x_user_id, action="WEALTH_QUERY", request_id=request_id)
    _ensure_account(requested)
    audit_event("WEALTH_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id)
    return {"code": 200, "data": [_wealth_product_view(product) for product in WEALTH_PRODUCTS]}


@router.get("/api/v1/wealth/holdings/{user_id}")
def get_wealth_holdings(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="WEALTH_HOLDINGS_QUERY", request_id=request_id)
    _ensure_account(user_id)
    rows = [dict(row) for row in WEALTH_HOLDINGS.get(user_id, [])]
    audit_event("WEALTH_HOLDINGS_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@router.get("/api/v1/wealth/recommend")
def recommend_wealth(user_id: Optional[str] = None, risk_profile: Optional[str] = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    requested = user_id or x_user_id
    if not requested:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(requested, x_user_id, action="WEALTH_RECOMMEND", request_id=request_id)
    _ensure_account(requested)
    stored_profile = (WEALTH_RISK_PROFILES.get(requested) or {}).get("risk_profile")
    profile = str(risk_profile or stored_profile or "CONSERVATIVE").upper()
    if profile not in {"CONSERVATIVE", "BALANCED", "AGGRESSIVE", "C1", "C2", "C3", "C4", "C5"}:
        raise HTTPException(status_code=400, detail={"code": "INVALID_RISK_PROFILE", "message": "风险偏好无效"})
    max_risk = profile_rank(profile) or 1
    products = [p for p in WEALTH_PRODUCTS if int(str(p.get("risk_level", "R3")).lstrip("R")) <= max_risk]
    audit_event("WEALTH_RECOMMEND", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"risk_profile": profile, "count": len(products)})
    return {"code": 200, "risk_profile": profile, "risk_profile_label": PROFILE_LABELS.get(profile, profile), "disclaimer": "推荐仅基于演示资料，不构成投资建议。", "data": [_wealth_product_view(p) for p in products]}


# The stored suitability profile is written with the questionnaire's raw score
# and the answer sheet that produced it.  Both stay server-side: together they
# are the scoring key, so a client that can read them can derive the minimum
# answer set for any grade and walk straight through the suitability gate that
# decides what it is allowed to buy.  The assessment endpoint already withholds
# the score ("The score stays on file; the investor sees the type and its
# meaning") - this projection keeps the read path consistent with it.
RISK_PROFILE_CLIENT_FIELDS = (
    "risk_profile",
    "assessed_at",
    "source",
    "horizon_years",
    "loss_tolerance",
    "experience",
)


def risk_profile_view(profile: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Client-safe projection of one stored risk profile."""

    if not profile:
        return None
    return {key: deepcopy(profile[key]) for key in RISK_PROFILE_CLIENT_FIELDS if key in profile}


@router.get("/api/v1/wealth/risk-profile")
def get_risk_profile(x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """GREEN: the caller's own stored suitability profile (or none yet).

    The wealth page reads this to decide whether to show "去测评" instead of
    letting a purchase die later at precheck with 428.  Only the client-safe
    projection is returned - never the score or the answers behind it.
    """

    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="WEALTH_QUERY", request_id=request_id)
    _ensure_account(actor)
    profile = risk_profile_view(WEALTH_RISK_PROFILES.get(actor))
    attempts = risk_assessment_attempts(actor)
    audit_event("WEALTH_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"source": "risk_profile", "present": profile is not None})
    return {"code": 200, "data": profile, "attempts": {"allowed": attempts["allowed"], "message": attempts["message"], "today": attempts["counts"].get("今日", 0), "month": attempts["counts"].get("近 1 个月", 0), "year": attempts["counts"].get("近 1 年", 0)}}


@router.post("/api/v1/wealth/risk-assessment")
def wealth_risk_assessment(
    req: RiskAssessmentRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Record an explicit suitability questionnaire for the authenticated user.

    Two request shapes share this endpoint.  ``answers`` carries the 8-question
    C1-C5 quiz: the server is the only scoring authority (the client submits
    option letters, never points), and the resulting grade is stored with the
    raw answers for audit.  Without ``answers`` the legacy three-field
    questionnaire (horizon/tolerance/experience) still works unchanged.
    Missing or invalid inputs are rejected rather than replaced with a
    conservative-looking default.  An optional idempotency key makes client
    retries safe.  The numeric score is stored server-side and never echoed to
    the investor - they see the type, its traits and the matching levels.
    """

    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="WEALTH_RISK_ASSESSMENT", request_id=request_id)
    quiz_mode = isinstance(req.answers, dict) and len(req.answers) > 0
    grade: str
    profile_payload: dict[str, Any]
    try:
        _ensure_account(req.user_id)
        if quiz_mode:
            answers_norm: dict[str, str] = {}
            for number in map(str, range(1, 9)):
                letter = str(req.answers.get(number) or "").strip().upper()
                if letter not in RISK_QUIZ_SCORES[int(number)]:
                    allowed = "/".join(sorted(RISK_QUIZ_SCORES[int(number)]))
                    raise SecurityError(f"第 {number} 题答案无效（仅支持 {allowed}）", 400, "INVALID_ASSESSMENT")
                answers_norm[number] = letter
            score = sum(RISK_QUIZ_SCORES[int(q)][letter] for q, letter in answers_norm.items())
            grade = risk_grade_for_score(score)
            type_name, _traits, _levels = RISK_GRADE_META[grade]
            idem_payload = {"user_id": req.user_id, "answers": answers_norm}
            profile_payload = {
                "risk_profile": grade,
                "score": score,
                "answers": dict(answers_norm),
                "assessed_at": datetime.now(timezone.utc).isoformat(),
                "source": "QUESTIONNAIRE",
            }
        else:
            if req.horizon_years is None or isinstance(req.horizon_years, bool) or req.horizon_years < 0 or req.horizon_years > 100:
                raise SecurityError("请填写 0 到 100 年之间的投资期限", 400, "INVALID_HORIZON")
            if req.loss_tolerance is None or req.experience is None:
                raise SecurityError("风险承受程度和投资经验不能为空", 400, "ASSESSMENT_INCOMPLETE")
            tolerance_raw = sanitize_text(req.loss_tolerance, field="风险承受程度", max_length=40).upper().strip()
            experience_raw = sanitize_text(req.experience, field="投资经验", max_length=40).upper().strip()
            # Accept a small, explicit set of Chinese labels at the API boundary;
            # arbitrary model-generated labels are still rejected.
            tolerance = {
                "低": "LOW", "低风险": "LOW", "保守": "LOW",
                "中": "MEDIUM", "中风险": "MEDIUM", "中等风险": "MEDIUM", "平衡": "MEDIUM",
                "高": "HIGH", "高风险": "HIGH", "激进": "HIGH",
            }.get(tolerance_raw, tolerance_raw)
            experience = {
                "无": "NONE", "无经验": "NONE", "没有经验": "NONE", "新手": "NONE",
                "基础": "BASIC", "基础经验": "BASIC", "一般": "BASIC",
                "专业": "PROFESSIONAL", "专业经验": "PROFESSIONAL",
            }.get(experience_raw, experience_raw)
            if tolerance not in {"LOW", "MEDIUM", "HIGH"} or experience not in {"NONE", "BASIC", "PROFESSIONAL"}:
                raise SecurityError("风险评估选项无效", 400, "INVALID_ASSESSMENT")
            # Suitability is bounded by both loss tolerance and demonstrated
            # experience.  A high tolerance from a novice cannot silently unlock a
            # high-risk product.
            tolerance_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}[tolerance]
            experience_cap = {"NONE": 1, "BASIC": 2, "PROFESSIONAL": 3}[experience]
            effective_rank = min(tolerance_rank, experience_cap)
            grade = {1: "CONSERVATIVE", 2: "BALANCED", 3: "AGGRESSIVE"}[effective_rank]
            idem_payload = {
                "user_id": req.user_id,
                "horizon_years": int(req.horizon_years),
                "loss_tolerance": tolerance,
                "experience": experience,
            }
            profile_payload = {
                "risk_profile": grade,
                "assessed_at": datetime.now(timezone.utc).isoformat(),
                "horizon_years": int(req.horizon_years),
                "loss_tolerance": tolerance,
                "experience": experience,
                "source": "AGENT_OR_API",
            }
        attempts = risk_assessment_attempts(req.user_id)
        if not attempts["allowed"]:
            raise SecurityError(attempts["message"], 429, "ASSESSMENT_LIMIT_REACHED")
        cached = _idempotency_begin(actor, "wealth_risk_assessment", idempotency_key, idem_payload, request_id=request_id)
        if cached is not None:
            return cached
        with STATE_LOCK:
            WEALTH_RISK_PROFILES[req.user_id] = profile_payload
        record_risk_assessment(req.user_id)
    except SecurityError as exc:
        _idempotency_abort(actor, "wealth_risk_assessment", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="WEALTH_RISK_ASSESSMENT", request_id=request_id)
    if quiz_mode:
        type_name, traits, levels = RISK_GRADE_META[grade]
        # The score stays on file; the investor sees the type and its meaning.
        response = {
            "code": 200,
            "risk_profile": grade,
            "risk_type": type_name,
            "traits": traits,
            "matching_levels": levels,
            "disclaimer": "风险评估仅供演示，不构成投资建议。",
        }
    else:
        response = {
            "code": 200,
            "risk_profile": grade,
            "horizon_years": int(req.horizon_years),
            "disclaimer": "风险评估仅供演示，不构成投资建议。",
        }
    _idempotency_complete(actor, "wealth_risk_assessment", idempotency_key, idem_payload, response)
    persist_ledger()
    audit_event("WEALTH_RISK_ASSESSMENT", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"profile": grade, "mode": "questionnaire" if quiz_mode else "legacy"})
    return response


@router.get("/api/v1/wealth/compare")
def compare_wealth(
    user_id: Optional[str] = None,
    product_ids: str = "",
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """GREEN: compare two or more allow-listed product facts."""

    requested = user_id or x_user_id
    if not requested:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(requested, x_user_id, action="WEALTH_COMPARE", request_id=request_id)
    _ensure_account(requested)
    values = [sanitize_text(item, field="理财产品编号", max_length=64).upper() for item in str(product_ids or "").split(",") if str(item).strip()]
    if len(values) < 2 or len(values) > 10 or len(set(values)) != len(values):
        raise HTTPException(status_code=400, detail={"code": "INVALID_PRODUCTS", "message": "产品对比至少需要两个不同的产品编号"})
    rows = []
    for product_id in values:
        product = next((item for item in WEALTH_PRODUCTS if item.get("product_id") == product_id), None)
        if not product:
            raise HTTPException(status_code=404, detail={"code": "PRODUCT_NOT_FOUND", "message": "理财产品不存在"})
        rows.append(_wealth_product_view(product))
    audit_event("WEALTH_COMPARE", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"product_count": len(rows)})
    return {"code": 200, "disclaimer": "对比仅基于演示资料，不构成投资建议。", "data": rows}
