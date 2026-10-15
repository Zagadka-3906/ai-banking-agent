"""Mock core-banking API with an explicit security boundary.

The ledger is intentionally in memory for a competition demo.  Mutating
operations still require a risk precheck, a signed short-lived one-time
nonce, and (for red operations) a server-validated MFA proof.  Every decision
and outcome is appended to a tamper-evident audit trail.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import logging
import math
import os
import re
import secrets
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from http_controls import cache_bounded_body

from security import (
    AUTH_SECRET_CONFIGURED,
    AUTH_TOKENS,
    AUDIT_LOGS,
    DEVICES,
    MFA_PROOFS,
    SecurityError,
    amount_to_cents,
    audit_event,
    authorize_execution,
    behavior_large_amount_cents,
    behavior_status,
    circuit_status,
    cancel_auth_token,
    confirm_auth_token,
    ensure_circuit_closed,
    enroll_device,
    get_audit_logs,
    get_audit_trace,
    hash_password,
    inspect_auth_token,
    issue_auth_token,
    issue_face_challenge,
    issue_demo_otp,
    issue_session_token,
    list_devices,
    record_security_failure,
    record_security_success,
    record_behavior_signal,
    renew_session,
    resolve_session,
    resolve_store_digest_algorithm,
    revoke_device,
    revoke_session,
    revoke_sessions_for,
    security_health,
    sanitize_text,
    store_integrity_digest,
    validate_actor,
    verify_login_otp,
    verify_mfa,
    verify_password,
    verify_audit_chain,
)
from security import STATE_LOCK
from scheduler import TASK_STORE, TaskState
from planner import analyze_transactions, filter_transactions
from sandbox import sandbox_health
from notification_outbox import OUTBOX, enqueue as enqueue_notification, visible as visible_notifications
from mock_merchant import CATALOG as MOCK_MERCHANT_CATALOG, place_order, quote_cents


logger = logging.getLogger("mock_banking.core")

app = FastAPI(
    title="Mock Bank Core API",
    description="银行 AI 智能体受控沙箱环境（含分级权限与审计）",
    version="2.0.0",
)
# Keep the HTTP edge defensive even when the mock is run directly under
# Uvicorn (without a reverse proxy/WAF).  The body limit is deliberately
# generous for normal JSON requests but prevents an untrusted channel from
# turning prompt/metadata fields into an unbounded memory allocation.
try:
    _MAX_REQUEST_BYTES = max(16_384, min(int(os.getenv("BANK_MAX_REQUEST_BYTES", "1048576")), 10_000_000))
except (TypeError, ValueError):
    _MAX_REQUEST_BYTES = 1_048_576

# Paths that must stay reachable without a session and without rate limiting
# (container probes and the interactive API docs).
_HEALTH_PATHS = {"/healthz", "/api/v1/security/health"}
_DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")
# Endpoints that must stay reachable *before* a session exists.  Without this,
# strict session mode would refuse the login request itself and no client could
# ever obtain the credential it is being asked for.
_SESSION_BOOTSTRAP_PATHS = {"/api/v1/auth/login"}
RATE_LIMIT_WINDOW_SECONDS = 60
# Per-principal request windows.  The demo has no reverse proxy, so a simple
# in-process fixed window is the cheapest way to blunt accidental or hostile
# floods; production should also apply an edge/WAF limit.
RATE_LIMIT_STATE: dict[str, dict[str, Any]] = {}


def _rate_limit_exempt(path: str) -> bool:
    return path in _HEALTH_PATHS or path.startswith(_DOCS_PREFIXES)


def _rate_limit_key(request: "Request") -> str:
    """Pick a throttling bucket the caller cannot trivially re-key.

    ``X-User-Id`` is a *object selector*, not a credential: outside strict
    session mode anyone can send any value, so using it as the rate-limit key
    meant a caller could rotate the header and reset the window at will.  The
    verified session principal wins; otherwise the peer address; the header is
    only the fallback for callers with no address at all (the in-process test
    client), where it still keeps one principal's traffic from throttling
    another's.
    """

    session_token = request.headers.get("X-Session-Token")
    if session_token:
        try:
            return f"session:{resolve_session(session_token)}"
        except SecurityError:
            # An invalid session is answered by the middleware above; bucket it
            # by peer so a bad token cannot be used to re-key either.
            pass
    if request.client and request.client.host:
        return f"peer:{request.client.host}"
    return f"user:{request.headers.get('X-User-Id') or 'anonymous'}"


def _strict_session_required() -> bool:
    return os.getenv("BANK_REQUIRE_SESSION", "1").lower() in {"1", "true", "yes", "on"}


def _rate_limit_check(key: str) -> Optional[int]:
    """Return ``Retry-After`` seconds when over the per-minute limit, else None."""

    try:
        limit = int(os.getenv("BANK_RATE_LIMIT_PER_MINUTE", "240"))
    except (TypeError, ValueError):
        limit = 240
    if limit <= 0:
        return None
    now = time.monotonic()
    record = RATE_LIMIT_STATE.get(key)
    if not record or now - float(record.get("start", 0.0)) >= RATE_LIMIT_WINDOW_SECONDS:
        RATE_LIMIT_STATE[key] = {"start": now, "count": 1, "notified": False}
        return None
    record["count"] = int(record.get("count", 0)) + 1
    if record["count"] > limit:
        if not record.get("notified"):
            record["notified"] = True
            audit_event("RATE_LIMITED", user_id=key, decision="BLOCK", outcome="RATE_LIMITED", reason="rate limit exceeded", metadata={"limit": limit, "window_seconds": RATE_LIMIT_WINDOW_SECONDS})
        return max(1, int(RATE_LIMIT_WINDOW_SECONDS - (now - float(record.get("start", now)))))
    return None


def _apply_security_headers(response: JSONResponse, path: str) -> JSONResponse:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def request_safety_middleware(request: Request, call_next):
    path = request.url.path
    # 1) A session token is authoritative over a self-asserted X-User-Id: it is
    #    verified first and its subject replaces any client-supplied header, so
    #    a caller can never claim another principal alongside a valid session.
    session_token = request.headers.get("X-Session-Token")
    if session_token:
        try:
            principal = resolve_session(session_token)
        except SecurityError as exc:
            return _apply_security_headers(
                JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.detail}}),
                path,
            )
        forwarded = [(name, value) for name, value in request.scope["headers"] if name.lower() != b"x-user-id"]
        forwarded.append((b"x-user-id", principal.encode("utf-8")))
        request.scope["headers"] = forwarded
        # Downstream handlers read this to bind approvals to the session that
        # requested them, so revoking the session invalidates them.
        request.state.session_token = session_token
    elif _strict_session_required() and path.startswith("/api/v1/") and not _rate_limit_exempt(path) and path not in _SESSION_BOOTSTRAP_PATHS:
        return _apply_security_headers(
            JSONResponse(status_code=401, content={"detail": {"code": "SESSION_REQUIRED", "message": "银行数据访问需要会话凭证"}}),
            path,
        )
    # 2) Per-principal request window (probes and docs are exempt).
    if not _rate_limit_exempt(path):
        # The bucket key must not be something the caller can pick freely: an
        # ``X-User-Id`` header is self-asserted outside strict session mode, so
        # keying on it let anyone rotate the header and get a fresh window per
        # request.  Prefer the verified session principal, then the peer
        # address, and keep the header only as a last resort (an in-process
        # test client has no peer address).
        rate_key = _rate_limit_key(request)
        retry_after = _rate_limit_check(rate_key)
        if retry_after is not None:
            response = _apply_security_headers(
                JSONResponse(status_code=429, content={"detail": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后重试"}}),
                path,
            )
            response.headers["Retry-After"] = str(retry_after)
            return response
    try:
        await cache_bounded_body(request, _MAX_REQUEST_BYTES)
    except SecurityError as exc:
        return _apply_security_headers(
            JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.detail}}),
            path,
        )
    response = await call_next(request)
    return _apply_security_headers(response, request.url.path)


# ---------------------------------------------------------------------------
# In-memory demo ledger (public names retained for backwards compatibility)
# ---------------------------------------------------------------------------
# Every demo account shares one password.  The argon2id digest is baked in
# rather than computed at import: hashing costs ~33 ms per call, and a fixed
# digest keeps start-up (and therefore every test run) deterministic.  A real
# deployment provisions credentials through an enrolment flow, never a source
# constant.  The plaintext (`Bank@2026`) is documented in .env.example.
DEMO_PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$01eEUN2Ii59Smsmuoc9mhg$O+kWnd/qcEgxrcodomCtjb5PgtkprfAfvHOfDqFEyi8"

ACCOUNTS: dict[str, dict[str, Any]] = {
    "user_zhang": {"card_no": "622202001", "name": "张三", "phone": "13800138000", "balance": 15000.0, "locked_amount": 0.0, "status": "ACTIVE", "credential_version": 1, "password_hash": DEMO_PASSWORD_HASH},
    "user_li": {"card_no": "622202002", "name": "李四", "phone": "13900139000", "balance": 2000.0, "locked_amount": 0.0, "status": "ACTIVE", "credential_version": 1, "password_hash": DEMO_PASSWORD_HASH},
    "user_wife": {"card_no": "622202003", "name": "王美（爱人）", "phone": "13700137000", "balance": 5000.0, "locked_amount": 0.0, "status": "ACTIVE", "credential_version": 1, "password_hash": DEMO_PASSWORD_HASH},
}

CONTACTS: dict[str, str] = {
    "张三": "user_zhang",
    "李四": "user_li",
    "爱人": "user_wife",
    "老婆": "user_wife",
    "王美": "user_wife",
    "王美（爱人）": "user_wife",
    "13900139000": "user_li",
    "13700137000": "user_wife",
}

# Per-user contact book: contacts the user added through the app (name, phone,
# relationship, birthday).  A contact whose phone matches an existing account is
# *linked* to it; anything else gets a zero-balance shadow payee account so a
# transfer to the new contact settles end to end in the mock.  Shadow accounts
# carry an unusable password digest, so enrollment can never become a login.
USER_CONTACTS: dict[str, list[dict[str, Any]]] = {}

# 关系词表：家人细分为爱人/兄弟/儿子/女儿/父母。录入接受中文标签或规范码，
# 存储统一为规范码；同一张表也用于“按称呼转账”时的称呼归一。
CONTACT_RELATIONSHIPS = {
    "爱人": "SPOUSE", "老婆": "SPOUSE", "媳妇": "SPOUSE", "妻子": "SPOUSE", "夫人": "SPOUSE", "丈夫": "SPOUSE", "老公": "SPOUSE",
    "兄弟": "SIBLING", "兄妹": "SIBLING", "姐妹": "SIBLING", "哥哥": "SIBLING", "弟弟": "SIBLING", "姐姐": "SIBLING", "妹妹": "SIBLING",
    "儿子": "SON",
    "女儿": "DAUGHTER",
    "父亲": "PARENT", "爸爸": "PARENT", "母亲": "PARENT", "妈妈": "PARENT", "父母": "PARENT",
    "家人": "FAMILY", "亲人": "FAMILY",
    "朋友": "FRIEND", "好友": "FRIEND",
    "同事": "COLLEAGUE",
    "其他": "OTHER",
}
CONTACT_RELATIONSHIP_LABELS = {"SPOUSE": "爱人", "SIBLING": "兄弟", "SON": "儿子", "DAUGHTER": "女儿", "PARENT": "父母", "FAMILY": "家人", "FRIEND": "朋友", "COLLEAGUE": "同事", "OTHER": "其他"}

# 转账称呼比录入更宽：用户嘴里说出来的亲属叫法都要能归到关系类别。
CONTACT_RELATION_SYNONYMS: dict[str, str] = dict(CONTACT_RELATIONSHIPS)
CONTACT_RELATION_SYNONYMS.update({
    "太太": "SPOUSE", "先生": "SPOUSE", "内人": "SPOUSE",
    "兄": "SIBLING", "弟": "SIBLING", "姐": "SIBLING", "妹": "SIBLING",
    "闺女": "DAUGHTER",
    "爸": "PARENT", "妈": "PARENT",
    "家里": "FAMILY",
})
CONTACT_PHONE_RE = re.compile(r"^\d{5,20}$")
CONTACT_BIRTHDAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Same reasoning as CARD_MAX_PER_USER: the book is part of the persisted ledger
# and the snapshot is rewritten in full on every mutation, so an uncapped add is
# unbounded growth with quadratic disk work - and each new non-matching phone
# also provisions an account record.
CONTACT_MAX_PER_USER = max(1, int(os.getenv("BANK_MAX_CONTACTS_PER_USER", "200")))
# Whether a contact-book entry that resolves to a *real* account carries that
# account owner's consent.  A typed phone number proves nothing on its own, so
# a provisional link is good enough to pay *to* (the caller's own money) but
# never proof of account ownership; see ``_aa_request_eligible``.
LINK_VERIFIED = "OWNER_CONSENTED"
LINK_UNVERIFIED = "PROVISIONAL"

DAILY_TRANSFERS: dict[str, float] = {"user_zhang": 0.0}
DAILY_TRANSFER_DATE: dict[str, str] = {}
# Individual lock records make rollback/unlock auditable and prevent a caller
# from releasing another user's reserved funds.
LOCKS: dict[str, dict[str, Any]] = {}
# Additional mock products make the other competition scenarios executable
# without pretending that a real bank is connected.  These records are
# deterministic fixtures; all writes still pass through the same authorization
# gateway used by transfers.
# 演示产品货架：四款虚构产品覆盖 R1-R4 完整风险谱系。除旧字段外新增
# category/benchmark/term/underlying/target_clients，供理财页展示完整要素；
# 全部为虚构演示数据，业绩比较基准不是收益承诺。
WEALTH_PRODUCTS: list[dict[str, Any]] = [
    {
        "product_id": "CJ01002",
        "name": "稳健添利·日日盈",
        "category": "现金管理类",
        "risk_level": "R1",
        "benchmark": "2.05% ~ 2.35%（7日年化）",
        "annualized_yield": 0.022,
        "min_amount": 1.0,
        "liquidity": "T+0 快速赎回/T+1 确认",
        "term": "灵活申赎（工作日 T+1 确认，单日快速赎回最高 1 万元）",
        "underlying": "高流动性资产：银行存款、同业存单、高信用评级短期债券",
        "target_clients": "追求资金高流动性、主要存放日常零花与备用金的保守型投资者",
    },
    {
        "product_id": "AY90204",
        "name": "安逸安享·季季丰90天",
        "category": "固定收益类",
        "risk_level": "R2",
        "benchmark": "2.75% ~ 3.10%（年化）",
        "annualized_yield": 0.029,
        "min_amount": 1000.0,
        "liquidity": "90 天封闭",
        "term": "90 天封闭运作，到期自动滚存或转出",
        "underlying": "80% 以上配置国债、政策性金融债、AAA 级企业信用债，其余投资货币市场工具",
        "target_clients": "有短期闲置资金，希望收益高于活期存款且能接受固定锁定期限的用户",
    },
    {
        "product_id": "ZY18031",
        "name": "智远创赢·成长平衡180天",
        "category": "固收+混合类",
        "risk_level": "R3",
        "benchmark": "3.60% ~ 4.80%（年化）",
        "annualized_yield": 0.042,
        "min_amount": 10000.0,
        "liquidity": "180 天定开",
        "term": "180天运作期",
        "underlying": "主仓位（70%~85%）投资中高等级固定收益证券筑底；副仓位（15%~30%）参与沪深300成分股打新、高股息红利资产及量化中性策略",
        "target_clients": "具有一定风险承受能力，希望在债底防守基础上争取权益市场超额收益的稳健进取型客户",
    },
    {
        "product_id": "QK36509",
        "name": "乾坤进取·产业智选封闭1年",
        "category": "权益类",
        "risk_level": "R4",
        "benchmark": "沪深300指数收益率×70% + 中债综合财富指数收益率×30%",
        "annualized_yield": None,
        "min_amount": 50000.0,
        "liquidity": "365 天封闭",
        "term": "365 天全封闭运行",
        "underlying": "不低于 80% 资产直接或间接投资于 A 股及港股通标的，重点布局半导体、先进制造与高端装备等成长行业",
        "target_clients": "能够承受本金波动、看好产业转型长期红利的高净值积极型投资者",
    },
]

# 详情页使用的静态行情快照。数值均为虚构演示数据，明确标注快照日期，
# 不代表实时行情、历史实际表现或未来收益，也不参与任何交易决策与记账。
_WEALTH_DETAIL_FIXTURES: dict[str, dict[str, Any]] = {
    "CJ01002": {
        "latest_nav": 1.0261, "daily_change_pct": 0.02,
        "annual_return_pct": 2.61, "recent_month_return_pct": 0.18, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1周", "product_return_pct": 0.05, "benchmark_return_pct": 0.04, "peer_average_pct": 0.04, "rank": "18/86"},
            {"period": "近1月", "product_return_pct": 0.18, "benchmark_return_pct": 0.17, "peer_average_pct": 0.16, "rank": "32/86"},
            {"period": "近3月", "product_return_pct": 0.61, "benchmark_return_pct": 0.56, "peer_average_pct": 0.52, "rank": "28/86"},
            {"period": "近6月", "product_return_pct": 1.28, "benchmark_return_pct": 1.15, "peer_average_pct": 1.10, "rank": "20/86"},
            {"period": "近1年", "product_return_pct": 2.61, "benchmark_return_pct": 2.35, "peer_average_pct": 2.25, "rank": "24/86"},
            {"period": "成立以来", "product_return_pct": 8.92, "benchmark_return_pct": 7.80, "peer_average_pct": 7.60, "rank": "15/80"},
        ],
        "analysis_metrics": [
            {"label": "超低波动风格", "score": 5, "value": "波动率 0.05%", "description": "净值平稳几乎无回撤"},
            {"label": "现金管理定位", "score": 5, "value": "活期替代", "description": "日常零花与备用金优选"},
            {"label": "高流动性支持", "score": 5, "value": "T+0 极速", "description": "支持单日快速赎回 1 万元"},
            {"label": "夏普比率 3.28", "score": 5, "value": "回撤 0.00%", "description": "风险调整后性价比极高"},
        ],
        "manager": {
            "name": "林晨", "title": "现金管理投资部 董事总经理", "tenure_since": "2021-03-04",
            "experience_years": 9.0, "managed_scale_billion": 85.2, "annualized_yield_pct": 2.75,
            "tenure_return_pct": 8.92, "avatar_url": "https://example.com/manager_linchen.png",
            "bio": "复旦大学金融工程硕士，专注现金管理与短久期流动性资产配置，风控严谨稳健",
        },
        "asset_allocation": [
            {"name": "银行存款", "percentage": 45.0},
            {"name": "同业存单", "percentage": 30.0},
            {"name": "短期债券", "percentage": 20.0},
            {"name": "现金及其他", "percentage": 5.0},
        ],
        "top_holdings": [
            {"name": "24中国农业银行存单088", "category": "同业存单", "percentage": 14.5, "code": "01240088", "daily_change_pct": 0.01},
            {"name": "24中国工商银行CD052", "category": "同业存单", "percentage": 12.8, "code": "01240052", "daily_change_pct": 0.01},
            {"name": "24建设银行同业存单", "category": "同业存单", "percentage": 11.2, "code": "01240091", "daily_change_pct": 0.00},
            {"name": "23国开行金融债01", "category": "政策金融债", "percentage": 8.5, "code": "230201", "daily_change_pct": 0.02},
            {"name": "24中行同业存单012", "category": "同业存单", "percentage": 7.6, "code": "01240012", "daily_change_pct": 0.01},
        ],
        "established_at": "2021-03-04", "scale_billion": 63.8, "manager_company": "华夏量化理财管理有限公司",
        "custodian": "中国工商银行股份有限公司", "fund_type": "现金管理类 · 货币型", "dividend_mode": "每日结转 / 红利再投资",
        "original_fee_rate": 0.50, "discount_fee_rate": 0.00, "fee_rate": 0.00,
        "rating_agency": "晨星评级 ★★★★★", "award_title": "金选现金管理奖 · 卓越流动性",
        "promo_title": "零钱理财 每日计息", "promo_subtitle": "申购费率0折，快速赎回实时到账",
        "institution_founded_years": 26, "institution_aum_billion": 21380.93, "institution_rating": "综合评级 AAA",
        "purchase_rule": "1元起购，工作日 00:00-15:00 申请", "redemption_rule": "工作日支持快速赎回，单日上限1万元",
        "confirm_time": "T+1", "summary": "适合日常备用金管理的超低波动现金管理产品，每日计算收益并结转份额。",
        "announcement": "本页面为 Mock 行情快照，仅供产品演示，不构成投资建议。",
    },
    "AY90204": {
        "latest_nav": 1.0738, "daily_change_pct": 0.08,
        "annual_return_pct": 3.05, "recent_month_return_pct": 0.24, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1周", "product_return_pct": 0.06, "benchmark_return_pct": 0.05, "peer_average_pct": 0.05, "rank": "15/74"},
            {"period": "近1月", "product_return_pct": 0.24, "benchmark_return_pct": 0.23, "peer_average_pct": 0.21, "rank": "18/74"},
            {"period": "近3月", "product_return_pct": 0.76, "benchmark_return_pct": 0.72, "peer_average_pct": 0.68, "rank": "21/74"},
            {"period": "近6月", "product_return_pct": 1.52, "benchmark_return_pct": 1.45, "peer_average_pct": 1.38, "rank": "17/74"},
            {"period": "近1年", "product_return_pct": 3.05, "benchmark_return_pct": 2.92, "peer_average_pct": 2.78, "rank": "16/74"},
            {"period": "成立以来", "product_return_pct": 18.25, "benchmark_return_pct": 16.50, "peer_average_pct": 15.80, "rank": "12/65"},
        ],
        "analysis_metrics": [
            {"label": "纯债筑底风格", "score": 4, "value": "中短久期", "description": "精选AAA信用债与利率债"},
            {"label": "最大回撤控制", "score": 5, "value": "0.18%", "description": "回撤控制能力卓越，平稳向上"},
            {"label": "机构重仓比例", "score": 4, "value": "82.5%", "description": "险资与机构客户重仓青睐"},
            {"label": "夏普比率 2.85", "score": 4, "value": "波动率 0.42%", "description": "稳健收益性价比高，持有体验佳"},
        ],
        "manager": {
            "name": "周宁", "title": "固定收益投资部 执行总经理", "tenure_since": "2020-07-15",
            "experience_years": 12.0, "managed_scale_billion": 45.8, "annualized_yield_pct": 3.42,
            "tenure_return_pct": 18.25, "avatar_url": "https://example.com/manager_zhouneng.png",
            "bio": "上海财经大学硕士，12年债券投研经验，擅长利率走势预判与信用利差挖掘",
        },
        "asset_allocation": [
            {"name": "利率债", "percentage": 40.0},
            {"name": "信用债", "percentage": 40.0},
            {"name": "货币工具", "percentage": 15.0},
            {"name": "现金及其他", "percentage": 5.0},
        ],
        "top_holdings": [
            {"name": "23附息国债12", "category": "记账式国债", "percentage": 14.5, "code": "019712", "daily_change_pct": 0.03},
            {"name": "22进出口行05", "category": "政策金融债", "percentage": 12.0, "code": "220305", "daily_change_pct": 0.04},
            {"name": "23中石油MTN002", "category": "AAA级中票", "percentage": 9.8, "code": "10238012", "daily_change_pct": 0.05},
            {"name": "23国家电网债01", "category": "央企信用债", "percentage": 8.2, "code": "10238055", "daily_change_pct": 0.02},
            {"name": "24国开05", "category": "政策金融债", "percentage": 6.5, "code": "240205", "daily_change_pct": 0.04},
        ],
        "established_at": "2020-07-15", "scale_billion": 42.6, "manager_company": "华夏量化理财管理有限公司",
        "custodian": "中国农业银行股份有限公司", "fund_type": "固定收益类 · 纯债型", "dividend_mode": "季季分红 / 现金分红",
        "original_fee_rate": 0.80, "discount_fee_rate": 0.08, "fee_rate": 0.15,
        "rating_agency": "晨星评级 ★★★★★", "award_title": "金牛固收产品奖 · 稳健回报标杆",
        "promo_title": "季季分红 稳健优选", "promo_subtitle": "申购费率1折，持有到期免赎回费",
        "institution_founded_years": 26, "institution_aum_billion": 21380.93, "institution_rating": "综合评级 AAA",
        "purchase_rule": "1000元起购，工作日 00:00-15:00 申请", "redemption_rule": "封闭期满后可申请赎回或自动滚存",
        "confirm_time": "T+1", "summary": "以优质高等级固收资产打底、期限明确的稳健型纯债理财产品。",
        "announcement": "本页面为 Mock 行情快照，仅供产品演示，不构成投资建议。",
    },
    "ZY18031": {
        "latest_nav": 1.2761, "daily_change_pct": 0.38,
        "annual_return_pct": 19.46, "recent_month_return_pct": 3.89, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1月", "product_return_pct": 3.89, "peer_average_pct": 1.18, "benchmark_return_pct": 1.18, "rank": "58/420"},
            {"period": "近3月", "product_return_pct": 8.67, "peer_average_pct": 3.42, "benchmark_return_pct": 3.42, "rank": "40/415"},
            {"period": "近6月", "product_return_pct": -2.12, "peer_average_pct": -4.25, "benchmark_return_pct": -4.25, "rank": "51/390"},
            {"period": "近1年", "product_return_pct": 19.46, "peer_average_pct": 10.28, "benchmark_return_pct": 10.28, "rank": "49/360"},
            {"period": "成立以来", "product_return_pct": 36.95, "peer_average_pct": 19.50, "benchmark_return_pct": 19.50, "rank": "36/280"},
        ],
        "analysis_metrics": [
            {"label": "大盘成长风格", "score": 4, "value": "成长均衡", "description": "聚焦核心资产与行业龙头"},
            {"label": "行业配置均衡", "score": 4, "value": "制造/消费", "description": "兼顾防守底仓与进攻弹性"},
            {"label": "机构重仓比例", "score": 4, "value": "78.2%", "description": "专业机构投资者高度认可"},
            {"label": "夏普比率 1.45", "score": 4, "value": "回撤 3.12%", "description": "控制力优秀 · 稳健增厚"},
        ],
        "manager": {
            "name": "陈敏", "title": "固定收益与多资产部 投资总监", "tenure_since": "2015-06-01",
            "experience_years": 11.0, "managed_scale_billion": 32.6, "annualized_yield_pct": 12.88,
            "tenure_return_pct": 57.51, "avatar_url": "https://example.com/manager_avatar.png",
            "bio": "清华大学金融硕士，从业11年又94天，擅长固收增强与多资产配置，长期业绩优异",
        },
        "asset_allocation": [
            {"name": "债券资产", "percentage": 74.5},
            {"name": "股票权益", "percentage": 18.2},
            {"name": "现金及其他", "percentage": 7.3},
        ],
        "promo_title": "财富日限时加息", "promo_subtitle": "申购费率1折，额度有限",
        "institution_founded_years": 26, "institution_aum_billion": 21380.93, "institution_rating": "综合评级 AA+",
        "custodian": "中国建设银行股份有限公司", "fund_type": "固收+混合类 · 中风险", "dividend_mode": "现金分红 / 红利再投资",
        "original_fee_rate": 1.00, "discount_fee_rate": 0.10,
        "rating_agency": "晨星评级 ★★★★★", "award_title": "金牛基金奖 · 卓越回报标杆",
        "top_holdings": [
            {"name": "中债优选180天资产支持专项计划", "category": "债券资产", "percentage": 18.6, "code": "190288", "daily_change_pct": 0.05},
            {"name": "国开行金融债2026年第七期", "category": "利率债", "percentage": 15.2, "code": "260207", "daily_change_pct": 0.08},
            {"name": "华域汽车", "category": "制造业权益", "percentage": 4.8, "code": "600741", "daily_change_pct": 1.35},
            {"name": "宁德时代", "category": "先进制造权益", "percentage": 3.9, "code": "300750", "daily_change_pct": 2.15},
            {"name": "中证红利量化增强策略", "category": "量化策略", "percentage": 3.4, "code": "501029", "daily_change_pct": -0.42},
        ],
        "established_at": "2000-09-01", "scale_billion": 63.8, "manager_company": "华夏量化理财管理有限公司",
        "purchase_rule": "10000元起购，开放期内申请", "redemption_rule": "每180天开放一次赎回",
        "confirm_time": "T+1", "fee_rate": 0.60,
        "summary": "在固收底仓基础上争取权益增强收益的平衡型产品，兼顾回撤防守与超额增长。",
        "announcement": "本页面为 Mock 行情快照，仅供产品演示，不构成投资建议。",
    },
    "QK36509": {
        "latest_nav": 1.2643, "daily_change_pct": 0.41,
        "annual_return_pct": 12.64, "recent_month_return_pct": 1.32, "net_value_date": "2026-09-15",
        "performance_history": [
            {"period": "近1周", "product_return_pct": 0.85, "benchmark_return_pct": 0.62, "peer_average_pct": 0.68, "rank": "11/43"},
            {"period": "近1月", "product_return_pct": 1.32, "benchmark_return_pct": 1.08, "peer_average_pct": 1.15, "rank": "12/43"},
            {"period": "近3月", "product_return_pct": 4.86, "benchmark_return_pct": 4.12, "peer_average_pct": 4.30, "rank": "10/43"},
            {"period": "近6月", "product_return_pct": 6.85, "benchmark_return_pct": 5.20, "peer_average_pct": 5.60, "rank": "9/43"},
            {"period": "近1年", "product_return_pct": 12.64, "benchmark_return_pct": 10.21, "peer_average_pct": 10.85, "rank": "8/43"},
            {"period": "成立以来", "product_return_pct": 48.60, "benchmark_return_pct": 35.20, "peer_average_pct": 38.40, "rank": "6/38"},
        ],
        "analysis_metrics": [
            {"label": "硬科技成长风格", "score": 4, "value": "先进制造", "description": "专注半导体、新材料与高端装备"},
            {"label": "高弹性进攻定位", "score": 4, "value": "超额显著", "description": "结构性行情具备强超额能力"},
            {"label": "机构重仓比例", "score": 4, "value": "76.4%", "description": "头部公募与机构重点配置"},
            {"label": "夏普比率 1.28", "score": 3, "value": "回撤 8.45%", "description": "中高风险高收益回报匹配"},
        ],
        "manager": {
            "name": "沈岳", "title": "权益投资部 董事总经理", "tenure_since": "2018-06-12",
            "experience_years": 17.0, "managed_scale_billion": 18.5, "annualized_yield_pct": 15.60,
            "tenure_return_pct": 48.60, "avatar_url": "https://example.com/manager_shenyue.png",
            "bio": "清华大学物理学学士、清华五道口金融硕士，17年投研经验，专注硬科技产业升级",
        },
        "asset_allocation": [
            {"name": "权益资产", "percentage": 70.0},
            {"name": "债券资产", "percentage": 20.0},
            {"name": "现金及其他", "percentage": 10.0},
        ],
        "top_holdings": [
            {"name": "宁德时代", "category": "新能源制造", "percentage": 8.9, "code": "300750", "daily_change_pct": 2.15},
            {"name": "中芯国际", "category": "集成电路", "percentage": 8.2, "code": "688981", "daily_change_pct": 3.42},
            {"name": "比亚迪", "category": "整车与电池", "percentage": 7.8, "code": "002594", "daily_change_pct": 1.18},
            {"name": "汇川技术", "category": "工控自动化", "percentage": 6.9, "code": "300124", "daily_change_pct": 0.85},
            {"name": "阳光电源", "category": "光伏逆变器", "percentage": 6.2, "code": "300274", "daily_change_pct": 1.95},
        ],
        "established_at": "2018-06-12", "scale_billion": 9.7, "manager_company": "华夏量化理财管理有限公司",
        "custodian": "招商银行股份有限公司", "fund_type": "权益类 · 行业主题型", "dividend_mode": "现金分红 / 红利再投资",
        "original_fee_rate": 1.50, "discount_fee_rate": 0.15, "fee_rate": 1.20,
        "rating_agency": "晨星评级 ★★★★★", "award_title": "硬科技主题卓越基金 · 金选标杆",
        "promo_title": "产业智选 领跑成长", "promo_subtitle": "申购费率1折，聚焦先进制造与硬科技",
        "institution_founded_years": 26, "institution_aum_billion": 21380.93, "institution_rating": "综合评级 AAA",
        "purchase_rule": "50000元起购，募集期内申请", "redemption_rule": "封闭期满后开放赎回",
        "confirm_time": "T+1", "summary": "面向长期资金、聚焦产业成长机会的高波动权益类产品。",
        "announcement": "本页面为 Mock 行情快照，仅供产品演示，不构成投资建议。",
    },
}

# 以月度快照生成覆盖近1月/近3月/近1年的归一化展示序列；仅用于读接口。
_WEALTH_NAV_BASE = ("2025-09-15", "2025-10-15", "2025-11-17", "2025-12-15", "2026-01-15", "2026-02-13", "2026-03-16", "2026-04-15", "2026-05-15", "2026-06-15", "2026-07-15", "2026-08-15", "2026-09-15")


def _normalized_wealth_series(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a stable chart whose labeled period returns are mathematically true.

    This is a normalized performance chart, deliberately separate from the
    product's latest NAV.  Available documented periods are exact anchors;
    intervening monthly points are linear interpolation for presentation.  A
    fixture with an inception return also receives an explicit inception point,
    so selecting ``成立以来`` never relabels a one-year series as lifetime data.
    """

    history = {row["period"]: row for row in detail["performance_history"]}
    has_extended_history = {"近6月", "成立以来"} <= history.keys()
    total_period = "成立以来" if has_extended_history else "近1年"
    product_last = 1.0 + float(history[total_period]["product_return_pct"]) / 100.0
    benchmark_last = 1.0 + float(history[total_period]["benchmark_return_pct"]) / 100.0
    product_anchors = {
        0: product_last / (1.0 + float(history["近1年"]["product_return_pct"]) / 100.0),
        9: product_last / (1.0 + float(history["近3月"]["product_return_pct"]) / 100.0),
        11: product_last / (1.0 + float(history["近1月"]["product_return_pct"]) / 100.0),
        12: product_last,
    }
    benchmark_anchors = {
        0: benchmark_last / (1.0 + float(history["近1年"]["benchmark_return_pct"]) / 100.0),
        9: benchmark_last / (1.0 + float(history["近3月"]["benchmark_return_pct"]) / 100.0),
        11: benchmark_last / (1.0 + float(history["近1月"]["benchmark_return_pct"]) / 100.0),
        12: benchmark_last,
    }
    if has_extended_history:
        product_anchors[6] = product_last / (1.0 + float(history["近6月"]["product_return_pct"]) / 100.0)
        benchmark_anchors[6] = benchmark_last / (1.0 + float(history["近6月"]["benchmark_return_pct"]) / 100.0)
    series = []
    for index, date in enumerate(_WEALTH_NAV_BASE):
        left = max(anchor for anchor in product_anchors if anchor <= index)
        right = min(anchor for anchor in product_anchors if anchor >= index)
        ratio = 0.0 if left == right else (index - left) / (right - left)
        product_nav = product_anchors[left] + (product_anchors[right] - product_anchors[left]) * ratio
        benchmark_nav = benchmark_anchors[left] + (benchmark_anchors[right] - benchmark_anchors[left]) * ratio
        series.append({"date": date, "nav": round(product_nav, 6), "benchmark_nav": round(benchmark_nav, 6)})
    if has_extended_history and detail.get("established_at", "") < _WEALTH_NAV_BASE[0]:
        series.insert(0, {"date": detail["established_at"], "nav": 1.0, "benchmark_nav": 1.0})
    return series




def _wealth_product_view(product: Mapping[str, Any]) -> dict[str, Any]:
    """Project presentation fixtures without persisting them with the ledger."""

    view = deepcopy(dict(product))
    detail = _WEALTH_DETAIL_FIXTURES.get(str(product.get("product_id")))
    if detail is None:
        return view
    view.update({"data_source": "MOCK_FIXTURE", "as_of_date": "2026-09-15", "nav_series": _normalized_wealth_series(detail), **deepcopy(detail)})
    return view
WEALTH_HOLDINGS: dict[str, list[dict[str, Any]]] = {"user_zhang": []}
# 风险档案与账户分开存储，初始为空：用户首次咨询理财业务时由 Agent 触发
# 8 题风险测评问卷（C1-C5），结果与原始答案一起记录在此（测评得分仅供
# 服务端定档，不对用户展示）。空档案让演示能完整走一遍"首次触发测评"。
WEALTH_RISK_PROFILES: dict[str, dict[str, Any]] = {}

# 风险测评重做限次（用户 -> 完成时间戳列表）：当日 ≤2 次、近 1 月 ≤4 次、
# 近 1 年 ≤8 次。滚动窗口按完成时刻计，随账本快照持久化。
RISK_ASSESSMENT_LIMITS: tuple[tuple[float, int, str], ...] = (
    (86400.0, 2, "今日"),
    (30 * 86400.0, 4, "近 1 个月"),
    (365 * 86400.0, 8, "近 1 年"),
)
RISK_ATTEMPTS: dict[str, list[float]] = {}


def risk_assessment_attempts(user_id: str) -> dict[str, Any]:
    """某用户当前的重做余量（供触发侧在出卷前拒绝，而非交卷时才失败）。"""

    now = time.time()
    history = [ts for ts in RISK_ATTEMPTS.get(user_id, []) if now - ts < 365 * 86400.0]
    counts = {}
    for window, _limit, label in RISK_ASSESSMENT_LIMITS:
        counts[label] = sum(1 for ts in history if now - ts < window)
    allowed, message = True, ""
    for window, limit, label in RISK_ASSESSMENT_LIMITS:
        used = sum(1 for ts in history if now - ts < window)
        if used >= limit:
            allowed, message = False, f"风险测评重做次数已达上限：{label}最多 {limit} 次，请稍后再试"
            break
    return {"allowed": allowed, "message": message, "history": history, "counts": counts}


def record_risk_assessment(user_id: str) -> None:
    now = time.time()
    history = [ts for ts in RISK_ATTEMPTS.get(user_id, []) if now - ts < 365 * 86400.0]
    history.append(now)
    RISK_ATTEMPTS[user_id] = history

# 8 题问卷评分表（题目编号 -> 选项得分），服务端定档唯一权威。
RISK_QUIZ_SCORES: dict[int, dict[str, int]] = {
    1: {"A": 5, "B": 4, "C": 3, "D": 1},
    2: {"A": 1, "B": 3, "C": 4, "D": 5},
    3: {"A": 1, "B": 2, "C": 4, "D": 5},
    4: {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5},
    5: {"A": 1, "B": 2, "C": 3, "D": 5},
    6: {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5},
    7: {"A": 0, "B": 2, "C": 3, "D": 4, "E": 5},
    8: {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5},
}
# 总分 -> 投资者类型（类型名/特点/匹配产品级别）。得分不对用户展示。
RISK_GRADE_TABLE: tuple[tuple[int, str], ...] = ((36, "C5"), (30, "C4"), (23, "C3"), (15, "C2"), (8, "C1"))
RISK_GRADE_META: dict[str, tuple[str, str, str]] = {
    "C1": ("保守型", "风险厌恶度极高，首要目标为资产保全", "R1（低风险：现金管理类、存款）"),
    "C2": ("稳健型", "偏好本金相对安全，可承受极小净值波动", "R1、R2（中低风险：纯债类、稳健固收）"),
    "C3": ("平衡型", "追求收益与风险平衡，兼顾债底与权益增厚", "R1 ~ R3（中等风险：固收+、平衡混合）"),
    "C4": ("进取型", "具备较强抗风险能力，主张长期资本增值", "R1 ~ R4（中高风险：权益类、股票型基金）"),
    "C5": ("激进型", "追求高收益，能承受本金重大损失", "R1 ~ R5（高风险：高杠杆、衍生品、行业主题）"),
}


PROFILE_LABELS: dict[str, str] = {
    "CONSERVATIVE": "保守型", "C1": "保守型",
    "C2": "稳健型", "BALANCED": "平衡型", "C3": "平衡型",
    "C4": "进取型", "AGGRESSIVE": "进取型",
    "C5": "激进型",
}


def risk_grade_for_score(score: int) -> str:
    for threshold, grade in RISK_GRADE_TABLE:
        if score >= threshold:
            return grade
    return "C1"


def profile_rank(profile: Optional[str]) -> Optional[int]:
    # 档案代码 -> 可购买的最高产品风险等级（R 几）。
    return {
        "CONSERVATIVE": 1, "C1": 1,
        "BALANCED": 2, "C2": 2,
        "C3": 3,
        "AGGRESSIVE": 4, "C4": 4,
        "C5": 5,
    }.get(str(profile or "").upper())
# 每用户卡片列表（支持一户多卡）。卡号是虚构演示号段，接口只回传脱敏形式；
# 卡余额与所属账户共享（借记卡语义），转账日限额按卡生效。
CARDS: dict[str, list[dict[str, Any]]] = {
    "user_zhang": [
        {"card_id": "CARD-001", "pan_ref": "MOCK-PAN-001", "number_masked": "**** **** **** 3082", "last4": "3082", "type": "借记卡", "status": "ACTIVE", "daily_limit": 5000.0, "label": "工资卡"}
    ],
    "user_li": [{"card_id": "CARD-002", "number_masked": "**** **** **** 0022", "last4": "0022", "type": "借记卡", "status": "ACTIVE", "daily_limit": 5000.0, "label": "演示借记卡"}],
    "user_wife": [{"card_id": "CARD-003", "number_masked": "**** **** **** 0033", "last4": "0033", "type": "借记卡", "status": "ACTIVE", "daily_limit": 5000.0, "label": "演示借记卡"}],
}
# Mock PANs live in a separate, read-only vault.  The ledger only stores the
# opaque reference and a masked projection; never put a full PAN or PIN here.
MOCK_CARD_VAULT: dict[str, dict[str, str]] = {
    "MOCK-PAN-001": {"card_number": "6222020000023082", "owner_user_id": "user_zhang", "owner_name": "张三", "issuer_name": "招商银行", "pin_hash": "$argon2id$v=19$m=65536,t=3,p=4$tUBB40MvKGEJJke2Q8yCIA$P2uj5W+fw/Y1HHA2wuOHFeo6i3FyX+NNLfkAOASpwkU"},
    "MOCK-PAN-002": {"card_number": "4111111111111111", "owner_user_id": "user_zhang", "owner_name": "张三", "issuer_name": "招商银行", "pin_hash": "$argon2id$v=19$m=65536,t=3,p=4$tUBB40MvKGEJJke2Q8yCIA$P2uj5W+fw/Y1HHA2wuOHFeo6i3FyX+NNLfkAOASpwkU"},
    "MOCK-PAN-003": {"card_number": "5555555555554444", "owner_user_id": "user_zhang", "owner_name": "张三", "issuer_name": "招商银行", "pin_hash": "$argon2id$v=19$m=65536,t=3,p=4$tUBB40MvKGEJJke2Q8yCIA$P2uj5W+fw/Y1HHA2wuOHFeo6i3FyX+NNLfkAOASpwkU"},
}
CARD_DISCLOSURE_FAILURES: dict[tuple[str, str], dict[str, Any]] = {}
# Self-service issuance needs a ceiling like every other collection here.  Cards
# are part of the persisted ledger and the snapshot is rewritten in full on each
# mutation, so an un-capped add is both unbounded growth and quadratic disk work
# - and each card carries its own daily spending limit.
CARD_MAX_PER_USER = max(1, int(os.getenv("BANK_MAX_CARDS_PER_USER", "10")))
# A newly issued card starts at this daily payment ceiling.
CARD_DEFAULT_DAILY_LIMIT = 5000.0
# ...and CARD_LIMIT may move it, but only within a sane band.  The transfer
# per-transaction ceiling is not a card-limit policy: it let one MFA approval
# raise a 5,000-yuan daily limit to 1,000,000.
CARD_MAX_DAILY_LIMIT = max(
    CARD_DEFAULT_DAILY_LIMIT,
    float(os.getenv("BANK_MAX_CARD_DAILY_LIMIT", "200000")),
)


def _user_cards(user_id: str) -> list[dict[str, Any]]:
    return CARDS.get(user_id, [])


def _first_active_card(user_id: str) -> Optional[dict[str, Any]]:
    cards = _user_cards(user_id)
    return next((card for card in cards if card.get("status") == "ACTIVE"), None)


def _find_card(user_id: str, card_id: Optional[str]) -> Optional[dict[str, Any]]:
    """按卡号找卡；未给卡号时回退第一张 ACTIVE 卡（演示单卡语义兼容）。"""

    cards = _user_cards(user_id)
    if card_id:
        card_id = str(card_id)
        return next((card for card in cards if str(card.get("card_id")) == card_id), None)
    return _first_active_card(user_id) or (cards[0] if cards else None)


def _select_card(user_id: str, card_id: Optional[str], *, allowed_states: Optional[set[str]] = None) -> dict[str, Any]:
    """Resolve the card one operation applies to, refusing to guess.

    Two ways this used to pick the wrong card, both reachable from the app:

    * an **omitted** id fell back to "the first ACTIVE card", so a multi-card user
      who pointed at their second card could have the first one frozen;
    * a **wrong** id fell back the same way (``_find_card(...) or
      _first_active_card(...)``), so a typo - or another account's id - silently
      retargeted the operation at whatever card came first.

    Ambiguity is therefore judged against the states this operation can actually
    act on: un-reporting a lost card considers lost cards, freezing considers
    active ones.  A named id that does not resolve is always an error, never a
    fallback.
    """

    cards = _user_cards(user_id)
    if not cards:
        raise SecurityError("当前用户没有可操作的卡片", 404, "CARD_NOT_FOUND")
    if card_id:
        found = _find_card(user_id, card_id)
        if found is None:
            raise SecurityError("卡片不存在或不属于当前用户", 404, "CARD_NOT_FOUND")
        return found
    candidates = [item for item in cards if allowed_states is None or str(item.get("status")) in allowed_states]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
    names = "、".join(str(item.get("card_id")) for item in candidates[:5])
    raise SecurityError(f"您有多张可选的卡片（{names}），请指定要操作的那一张", 409, "CARD_ID_REQUIRED")


# The single source of truth for what each card operation may act on, as
# source-state -> next-state.  It used to exist as three near-copies (a precheck
# map, an executor map and the selector's implicit "ACTIVE only"), which is how a
# card could be validated against one table and executed against another.
CARD_TRANSITIONS: dict[str, dict[str, str]] = {
    "CARD_FREEZE": {"ACTIVE": "FROZEN"},
    "CARD_UNFREEZE": {"FROZEN": "ACTIVE"},
    "CARD_REPORT_LOST": {"ACTIVE": "LOST", "FROZEN": "LOST"},
    "CARD_UNREPORT_LOST": {"LOST": "ACTIVE"},
}


def _card_source_states(operation: str) -> Optional[set[str]]:
    """States ``operation`` can act on, or None when it has no state restriction."""

    transitions = CARD_TRANSITIONS.get(operation)
    return set(transitions) if transitions else None


def _mock_card_masked() -> str:
    """虚构借记卡的脱敏卡号（62 开头 16 位，仅回传掩码形式）。"""

    digits = "622202" + "".join(str(secrets.randbelow(10)) for _ in range(10))
    return "**** **** **** " + digits[-4:]


def _issue_card_locked(user_id: str, *, card_type: str, label: Optional[str] = None, pan_ref: Optional[str] = None, number_masked: Optional[str] = None, last4: Optional[str] = None) -> dict[str, Any]:
    """Append one card to ``user_id``'s wallet, enforcing the per-user ceiling.

    Every issuance path funnels through here.  ``CARD_APPLY`` (the agent's
    "申请一张虚拟卡" flow) used to append directly, so the ceiling that
    ``/cards/{user_id}/add`` enforced could be bypassed simply by asking the
    assistant instead - and each card is ledger state whose snapshot is
    rewritten in full, which is what the ceiling exists to bound.

    Caller must hold ``STATE_LOCK``.
    """

    held = CARDS.setdefault(user_id, [])
    if len(held) >= CARD_MAX_PER_USER:
        raise SecurityError(
            f"每人最多持有 {CARD_MAX_PER_USER} 张卡片，请先注销不再使用的卡",
            409,
            "CARD_LIMIT_REACHED",
        )
    safe_masked = number_masked or _mock_card_masked()
    card = {
        "card_id": f"CARD-{uuid.uuid4().hex[:8].upper()}",
        **({"pan_ref": pan_ref} if pan_ref else {}),
        "number_masked": safe_masked,
        "last4": last4 or re.sub(r"\D", "", safe_masked)[-4:],
        "type": card_type,
        "status": "ACTIVE",
        "daily_limit": CARD_DEFAULT_DAILY_LIMIT,
        "label": label,
    }
    held.append(card)
    return card


def _card_summary(card: Mapping[str, Any], *, available_balance: Optional[float] = None) -> dict[str, Any]:
    """Explicitly safe card projection; private vault references never escape."""

    allowed = {"card_id", "last4", "number_masked", "type", "status", "daily_limit", "label"}
    result = {key: card[key] for key in allowed if key in card}
    if "last4" not in result:
        masked = str(card.get("number_masked") or "")
        result["last4"] = re.sub(r"\D", "", masked)[-4:] if len(re.sub(r"\D", "", masked)) >= 4 else ""
    if available_balance is not None:
        result["available_balance"] = available_balance
    return result


def _normalize_card_number(value: Any) -> str:
    number = re.sub(r"[\s-]", "", str(value or ""))
    if not re.fullmatch(r"\d{12,19}", number):
        raise SecurityError("银行卡信息无法核验", 400, "CARD_BINDING_REJECTED")
    checksum = 0
    for index, digit in enumerate(reversed(number)):
        value_int = int(digit)
        if index % 2 == 1:
            value_int *= 2
            if value_int > 9:
                value_int -= 9
        checksum += value_int
    if checksum % 10:
        raise SecurityError("银行卡信息无法核验", 400, "CARD_BINDING_REJECTED")
    return number


def _card_mask(number: str) -> str:
    return f"**** **** **** {number[-4:]}"
# Per-card, per-day settled spend.  The card's ``daily_limit`` is enforced on
# settlement, so lowering a limit (CARD_LIMIT) immediately constrains further
# payments instead of only being a display field.
CARD_DAILY_SPENT: dict[str, float] = {}
CARD_DAILY_SPENT_DATE: dict[str, str] = {}
SUBSCRIPTIONS: dict[str, list[dict[str, Any]]] = {
    "user_zhang": [
        {"subscription_id": "SUB-APPLE", "merchant": "Apple 订阅", "amount": 15.0, "next_charge": "2026-10-03", "status": "ACTIVE"},
        {"subscription_id": "SUB-VIDEO", "merchant": "视频会员", "amount": 25.0, "next_charge": (datetime.now().date() + timedelta(days=7)).isoformat(), "status": "ACTIVE"},
    ]
}
SCHEDULED_TRANSFERS: list[dict[str, Any]] = []
SPLIT_REQUESTS: list[dict[str, Any]] = []
MOCK_ORDERS: list[dict[str, Any]] = []
TRANSACTIONS: list[dict[str, Any]] = [
    {"tx_id": "TX1000", "account_id": "user_zhang", "merchant": "咖啡店", "category": "餐饮", "amount": 28.0, "time": f"{datetime.now().strftime('%Y-%m-%d')} 09:15:00"},
    {"tx_id": "TX1001", "account_id": "user_zhang", "merchant": "美团外卖", "category": "餐饮", "amount": 45.0, "time": "2026-08-01 12:30:00"},
    {"tx_id": "TX1002", "account_id": "user_zhang", "merchant": "Apple 订阅", "category": "订阅代扣", "amount": 15.0, "time": "2026-08-03 08:00:00"},
    {"tx_id": "TX1003", "account_id": "user_zhang", "merchant": "海底捞火锅", "category": "餐饮", "amount": 320.0, "time": "2026-08-05 19:20:00"},
    {"tx_id": "TX1004", "account_id": "user_zhang", "merchant": "境外未知商户", "category": "可疑交易", "amount": 999.0, "time": "2026-08-07 03:15:00", "card_id": "CARD-001", "country": "US"},
    *[{"tx_id": f"TX-CLOUD-{offset}", "account_id": "user_zhang", "merchant": "云端存储", "category": "订阅代扣", "amount": 19.9, "time": f"{(datetime.now().date() - timedelta(days=offset)).isoformat()} 09:00:00"} for offset in (60, 30, 0)],
]
DISPUTE_CASES: list[dict[str, Any]] = []


@contextmanager
def _ledger_transaction():
    """Best-effort atomicity for the in-memory demo ledger.

    Production deployments must replace this shim with a database transaction
    and an outbox.  Keeping the snapshot boundary here nevertheless prevents
    a task-store or validation failure from leaving half-applied balance,
    lock, schedule, holding, or subscription mutations in the competition
    sandbox.
    """

    names = (
        "ACCOUNTS",
        "DAILY_TRANSFERS",
        "DAILY_TRANSFER_DATE",
        "CARD_DAILY_SPENT",
        "CARD_DAILY_SPENT_DATE",
        "LOCKS",
        "WEALTH_HOLDINGS",
        "CARDS",
        "SUBSCRIPTIONS",
        "SCHEDULED_TRANSFERS",
        "SPLIT_REQUESTS",
        "MOCK_ORDERS",
        "DISPUTE_CASES",
        "TRANSACTIONS",
        "OUTBOX",
        "WEALTH_RISK_PROFILES",
    )
    with STATE_LOCK:
        snapshots = {name: deepcopy(globals()[name]) for name in names}
        task_snapshot = TASK_STORE.snapshot()
        try:
            yield
        except Exception:
            for name, snapshot in snapshots.items():
                current = globals()[name]
                if isinstance(current, dict):
                    current.clear()
                    current.update(deepcopy(snapshot))
                else:
                    current[:] = deepcopy(snapshot)
            TASK_STORE.restore(task_snapshot)
            raise

# ---------------------------------------------------------------------------
# Durable ledger store
#
# The business ledger is in memory because this is a competition sandbox, but
# a restart that silently empties every balance is a failure mode the device
# registry and the audit file already refuse.  Point ``BANK_LEDGER_STORE_PATH``
# at a file and every committed mutation is persisted atomically and replayed
# at start-up, so a container recreate keeps the accounts, transactions and
# reservations it had.
#
# What is stored is security-sensitive - balances and password digests are a
# fraud target - so the file carries an integrity digest: keyed (HMAC) when
# ``BANK_AUTH_SECRET`` is configured, otherwise a plain checksum that only
# detects corruption, and ``ledger_store_status`` says which one is in force
# rather than implying a protection that is not there.  A file that fails its
# digest, or that holds a structurally invalid record, is neither loaded nor
# overwritten: writes are frozen so the next mutation cannot quietly destroy
# the evidence. Owned by the core process, which is the only writer; the
# Android/IM gateway never touches it.
# ---------------------------------------------------------------------------

LEDGER_STORE_VERSION = 1
OPERATION_RECEIPTS: dict[str, dict[str, Any]] = {}
_LEDGER_STORE_BLOCKED = False
_LEDGER_STORE_LOAD_SUMMARY: dict[str, Any] = {}

# Every domain collection whose committed state a restart must restore.  The
# daily counters are included so risk accumulation survives a restart instead
# of giving a fresh 1,000-yuan yellow allowance after every bounce.
LEDGER_STATE_NAMES = (
    "ACCOUNTS",
    "CONTACTS",
    "USER_CONTACTS",
    "DAILY_TRANSFERS",
    "DAILY_TRANSFER_DATE",
    "CARD_DAILY_SPENT",
    "CARD_DAILY_SPENT_DATE",
    "LOCKS",
    "WEALTH_PRODUCTS",
    "WEALTH_HOLDINGS",
    "WEALTH_RISK_PROFILES",
    "RISK_ATTEMPTS",
    "CARDS",
    "SUBSCRIPTIONS",
    "SCHEDULED_TRANSFERS",
    "SPLIT_REQUESTS",
    "MOCK_ORDERS",
    "DISPUTE_CASES",
    "TRANSACTIONS",
    "OUTBOX",
    "OPERATION_RECEIPTS",
)


def _ledger_store_target(path: Optional[str] = None) -> Optional[str]:
    """The configured store path, or None when durability is off."""

    target = (path if path is not None else os.getenv("BANK_LEDGER_STORE_PATH")) or ""
    return target.strip() or None


def _ledger_store_digest(canonical: str, *, keyed: bool) -> dict[str, str]:
    return store_integrity_digest(canonical, keyed=keyed)


def _finite_nonnegative(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and float(value) >= 0


def _account_record_valid(user_id: str, record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    if not user_id or not isinstance(record.get("name"), str) or not isinstance(record.get("status"), str):
        return False
    if not isinstance(record.get("password_hash"), str) or not record["password_hash"]:
        return False
    return _finite_nonnegative(record.get("balance")) and _finite_nonnegative(record.get("locked_amount", 0.0))


def _ledger_state_valid(state: dict[str, Any]) -> bool:
    """Re-validate a persisted snapshot before it is trusted again.

    A digest only proves the file is the one that was written; these checks
    prove the *shape* is one this version understands, so a schema drift can
    never silently resurrect half a ledger.
    """

    accounts = state.get("ACCOUNTS")
    if not isinstance(accounts, dict) or not accounts:
        return False
    for user_id, record in accounts.items():
        if not _account_record_valid(str(user_id), record):
            return False
    for name in ("CONTACTS", "DAILY_TRANSFER_DATE", "CARD_DAILY_SPENT_DATE"):
        container = state.get(name)
        if not isinstance(container, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in container.items()):
            return False
    for name in ("DAILY_TRANSFERS", "CARD_DAILY_SPENT"):
        container = state.get(name)
        if not isinstance(container, dict) or any(not _finite_nonnegative(v) for v in container.values()):
            return False
    locks = state.get("LOCKS")
    if not isinstance(locks, dict):
        return False
    for lock_id, record in locks.items():
        if not isinstance(record, dict) or record.get("lock_id") != lock_id or not isinstance(record.get("user_id"), str):
            return False
        if not _finite_nonnegative(record.get("amount")):
            return False
    products = state.get("WEALTH_PRODUCTS")
    if not isinstance(products, list) or any(not isinstance(item, dict) or not isinstance(item.get("product_id"), str) for item in products):
        return False
    holdings = state.get("WEALTH_HOLDINGS")
    if not isinstance(holdings, dict) or any(not isinstance(rows, list) for rows in holdings.values()):
        return False
    profiles = state.get("WEALTH_RISK_PROFILES")
    if not isinstance(profiles, dict):
        return False
    valid_profiles = {"CONSERVATIVE", "BALANCED", "AGGRESSIVE", "C1", "C2", "C3", "C4", "C5"}
    for record in profiles.values():
        if not isinstance(record, dict) or record.get("risk_profile") not in valid_profiles:
            return False
    cards = state.get("CARDS")
    if not isinstance(cards, dict) or any(not isinstance(rows, list) for rows in cards.values()):
        return False
    for rows in cards.values():
        for record in rows:
            if not isinstance(record, dict) or not isinstance(record.get("card_id"), str) or not record["card_id"]:
                return False
    for name in ("SUBSCRIPTIONS", "WEALTH_HOLDINGS"):
        container = state.get(name)
        if not isinstance(container, dict):
            return False
    for name in ("SCHEDULED_TRANSFERS", "SPLIT_REQUESTS", "TRANSACTIONS", "MOCK_ORDERS"):
        rows = state.get(name, [] if name == "MOCK_ORDERS" else None)
        if not isinstance(rows, list):
            return False
    for row in state.get("TRANSACTIONS") or []:
        if not isinstance(row, dict) or not isinstance(row.get("account_id"), str) or not _finite_nonnegative(row.get("amount", 0.0)):
            return False
    outbox = state.get("OUTBOX", [])
    receipts = state.get("OPERATION_RECEIPTS", {})
    if not isinstance(receipts, dict):
        return False
    for key, receipt in receipts.items():
        try:
            scoped = json.loads(key)
        except (TypeError, ValueError):
            return False
        if (not isinstance(scoped, list) or len(scoped) != 3 or not all(isinstance(item, str) for item in scoped)
                or scoped[0] not in accounts or scoped[1] not in {"transfer_execute", "lock_fund", "operation_execute", "task_execute"}
                or not isinstance(receipt, dict) or not _finite_nonnegative(receipt.get("completed_at"))
                or not isinstance(receipt.get("response"), dict)):
            return False
        if receipt.get("fingerprint") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(receipt["fingerprint"])):
            return False
        if receipt.get("request_fingerprint") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(receipt["request_fingerprint"])):
            return False
    if not isinstance(outbox, list):
        return False
    for row in outbox:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row.get(k) for k in ("event_id", "owner_id", "recipient_id", "split_id")):
            return False
        if not _finite_nonnegative(row.get("amount", 0.0)):
            return False
    # Contact book rows feed the payee allow-list, so a half-written row must
    # not come back from a restart: name/phone/contact_id are the resolution
    # keys and every row needs all three.
    book = state.get("USER_CONTACTS")
    if not isinstance(book, dict):
        return False
    for rows in book.values():
        if not isinstance(rows, list):
            return False
        for row in rows:
            if not isinstance(row, dict):
                return False
            if not all(isinstance(row.get(key), str) and row.get(key) for key in ("contact_id", "name", "phone")):
                return False
            if not CONTACT_PHONE_RE.fullmatch(str(row.get("phone"))):
                return False
            # ``owner_id``/``link_verification`` are consent bookkeeping added
            # later; snapshots written before them must still load (consent is
            # recomputed on read anyway), so only validate the shape when present.
            for key in ("owner_id", "link_verification"):
                if key in row and not isinstance(row.get(key), str):
                    return False
    try:
        tasks, _ = TASK_STORE.decode_records(state.get("TASK_STORE", []))
    except (TypeError, ValueError):
        return False
    if any(task.owner_id not in accounts for task in tasks.values()):
        return False
    return True


def _persist_ledger_locked() -> None:
    """Write the ledger snapshot atomically.  Assumes ``STATE_LOCK`` is held."""

    global _LEDGER_STORE_BLOCKED
    target = _ledger_store_target()
    if not target or _LEDGER_STORE_BLOCKED:
        return
    state = {name: deepcopy(globals()[name]) for name in LEDGER_STATE_NAMES}
    state["TASK_STORE"] = TASK_STORE.export_records()
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot = {
        "version": LEDGER_STORE_VERSION,
        "updated_at": int(time.time()),
        "state": state,
        "integrity": _ledger_store_digest(canonical, keyed=AUTH_SECRET_CONFIGURED),
    }
    temporary = f"{target}.tmp"
    try:
        directory = os.path.dirname(os.path.abspath(target))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass  # best effort; Windows ACLs are not POSIX modes
        # Replace, never truncate: a crash mid-write must not leave a half file
        # that the next start would refuse as tampered.
        os.replace(temporary, target)
    except OSError:
        # The committed mutation stands; the next one retries the write.  The
        # health endpoint reports the store, so a silently failing disk still
        # surfaces to an operator.
        _LEDGER_STORE_BLOCKED = True
        logger.exception("Unable to persist the ledger store")


def persist_ledger() -> dict[str, Any]:
    """Persist on demand; a no-op when durability is off or writes are frozen."""

    with STATE_LOCK:
        _persist_ledger_locked()
    return ledger_store_status()


def load_ledger_store(path: Optional[str] = None) -> dict[str, Any]:
    """Load a persisted ledger, refusing anything that fails validation.

    Opt-in and re-callable (tests re-point the path): with no
    ``BANK_LEDGER_STORE_PATH`` the in-memory demo stays deterministic and
    side-effect free.  Only a validated snapshot is written into the globals;
    a rejected file freezes writes instead of letting the next mutation
    overwrite the evidence.
    """

    global _LEDGER_STORE_BLOCKED, _LEDGER_STORE_LOAD_SUMMARY
    target = _ledger_store_target(path)
    if not target:
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": None, "message": "BANK_LEDGER_STORE_PATH not configured"}
        return _LEDGER_STORE_LOAD_SUMMARY
    try:
        with open(target, "r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except FileNotFoundError:
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "message": "账本文件不存在，从种子账本开始"}
        return _LEDGER_STORE_LOAD_SUMMARY
    except OSError as exc:
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": f"账本文件无法读取: {type(exc).__name__}"}
        return _LEDGER_STORE_LOAD_SUMMARY
    except json.JSONDecodeError:
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": "账本文件不是合法 JSON"}
        return _LEDGER_STORE_LOAD_SUMMARY

    if not isinstance(snapshot, dict) or snapshot.get("version") != LEDGER_STORE_VERSION:
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": "账本文件版本不受支持"}
        return _LEDGER_STORE_LOAD_SUMMARY
    state = snapshot.get("state")
    integrity = snapshot.get("integrity")
    if not isinstance(state, dict) or not isinstance(integrity, dict):
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": "账本文件结构无效"}
        return _LEDGER_STORE_LOAD_SUMMARY

    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # The algorithm comes from our configuration, never from the file being
    # verified: see ``resolve_store_digest_algorithm``.
    keyed, algorithm_error = resolve_store_digest_algorithm(integrity)
    if algorithm_error:
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": algorithm_error}
        return _LEDGER_STORE_LOAD_SUMMARY
    expected = _ledger_store_digest(canonical, keyed=bool(keyed))["value"]
    if not hmac.compare_digest(expected, str(integrity.get("value") or "")):
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": "账本文件完整性校验失败"}
        return _LEDGER_STORE_LOAD_SUMMARY
    if not _ledger_state_valid(state):
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": "账本文件记录无效"}
        return _LEDGER_STORE_LOAD_SUMMARY

    with STATE_LOCK:
        for name in LEDGER_STATE_NAMES:
            container = globals()[name]
            restored = deepcopy(state.get(name, [] if name in {"OUTBOX", "MOCK_ORDERS"} else None))
            if isinstance(container, dict):
                container.clear()
                if isinstance(restored, dict):
                    container.update(restored)
            elif isinstance(container, list):
                container[:] = restored if isinstance(restored, list) else []
        # Older snapshots did not contain a queue. Keep them readable, but do
        # not invent approvals or replay merchant/reminder actions whose prior
        # task state was never saved.
        TASK_STORE.restore(TASK_STORE.decode_records(state.get("TASK_STORE", [])))
    _LEDGER_STORE_LOAD_SUMMARY = {
        "loaded_state": True,
        "path": target,
        "rejected": False,
        "keyed": bool(keyed),
        "accounts": len(state.get("ACCOUNTS") or {}),
        "transactions": len(state.get("TRANSACTIONS") or []),
    }
    return _LEDGER_STORE_LOAD_SUMMARY


def ledger_store_status() -> dict[str, Any]:
    """Describe ledger durability without leaking the configured path."""

    configured = bool(_ledger_store_target())
    return {
        "mode": "memory+file" if configured else "memory_only",
        "file_configured": configured,
        "file_loaded": bool(_LEDGER_STORE_LOAD_SUMMARY.get("loaded_state")),
        "file_rejected": bool(_LEDGER_STORE_LOAD_SUMMARY.get("rejected")),
        "writes_frozen": _LEDGER_STORE_BLOCKED,
        # False means the file carries a plain checksum: whoever can write it can
        # also forge it.  Configure BANK_AUTH_SECRET to make the digest keyed.
        "integrity_keyed": AUTH_SECRET_CONFIGURED,
    }


# Idempotency records make a committed operation safely retryable when a
# client loses the HTTP response.  Keys are scoped to the authenticated
# principal and endpoint; values never contain credentials.
IDEMPOTENCY_RECORDS: dict[tuple[str, str, str], dict[str, Any]] = {}
IDEMPOTENCY_TTL_SECONDS = max(300, int(os.getenv("BANK_IDEMPOTENCY_TTL_SECONDS", "86400")))
IDEMPOTENCY_MAX_RECORDS = max(100, int(os.getenv("BANK_IDEMPOTENCY_MAX_RECORDS", "10000")))


def _idempotency_fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idempotency_begin(actor: str, endpoint: str, key: Optional[str], payload: Any, *, request_id: str) -> Optional[dict[str, Any]]:
    """Reserve a key, or return a previously completed response.

    ``None`` means the caller owns a new reservation.  A pending reservation
    is rejected to prevent concurrent duplicate ledger mutations.
    """
    if not key:
        return None
    try:
        safe_key = sanitize_text(key, field="幂等键", max_length=128)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="IDEMPOTENCY", request_id=request_id)
    if not safe_key or len(safe_key) < 8:
        exc = SecurityError("幂等键长度不足", 400, "INVALID_IDEMPOTENCY_KEY")
        _record_failure_and_raise(exc, user_id=actor, action="IDEMPOTENCY", request_id=request_id)
    fingerprint = _idempotency_fingerprint(payload)
    now = time.time()
    with STATE_LOCK:
        # Opportunistically expire old entries and cap memory.
        expired = [k for k, v in IDEMPOTENCY_RECORDS.items() if now - float(v.get("created_at", now)) > IDEMPOTENCY_TTL_SECONDS]
        for old_key in expired:
            IDEMPOTENCY_RECORDS.pop(old_key, None)
        scoped = (actor, endpoint, safe_key)
        committed = OPERATION_RECEIPTS.get(json.dumps([actor, endpoint, safe_key]))
        if committed and committed.get("fingerprint") and now - committed["completed_at"] <= IDEMPOTENCY_TTL_SECONDS:
            if committed["fingerprint"] != fingerprint:
                raise SecurityError("幂等键已用于不同请求", 409, "IDEMPOTENCY_KEY_REUSE")
            audit_event("IDEMPOTENCY_REPLAY", user_id=actor, decision="ALLOW", outcome="CACHED_RESPONSE", request_id=request_id, metadata={"endpoint": endpoint})
            return deepcopy(committed["response"])
        existing = IDEMPOTENCY_RECORDS.get(scoped)
        if existing:
            if existing.get("fingerprint") != fingerprint:
                raise SecurityError("幂等键已用于不同请求", 409, "IDEMPOTENCY_KEY_REUSE")
            if existing.get("status") == "COMPLETED":
                audit_event("IDEMPOTENCY_REPLAY", user_id=actor, decision="ALLOW", outcome="CACHED_RESPONSE", request_id=request_id, metadata={"endpoint": endpoint})
                return deepcopy(existing.get("response") or {})
            raise SecurityError("相同请求正在处理中，请勿重复提交", 409, "IDEMPOTENCY_IN_PROGRESS")
        if len(IDEMPOTENCY_RECORDS) >= IDEMPOTENCY_MAX_RECORDS:
            # Remove the oldest item; this is bounded housekeeping, not a
            # correctness path because nonce replay protection remains active.
            oldest = min(IDEMPOTENCY_RECORDS, key=lambda item: IDEMPOTENCY_RECORDS[item].get("created_at", now))
            IDEMPOTENCY_RECORDS.pop(oldest, None)
        IDEMPOTENCY_RECORDS[scoped] = {"fingerprint": fingerprint, "status": "PENDING", "created_at": now}
    return None


def _idempotency_completed_request(actor: str, endpoint: str, key: Optional[str], request_fingerprint: str, *, request_id: str) -> Optional[dict[str, Any]]:
    """Replay a settled request before mutable business prerequisites change.

    The ordinary fingerprint still binds the validated execution context. A
    second fingerprint records the submitted intent so a completed cancellation
    or card transition can be retried after its own success changed that state.
    Legacy receipts without this field retain their original validation path.
    """

    if not key:
        return None
    safe_key = sanitize_text(key, field="幂等键", max_length=128)
    with STATE_LOCK:
        entry = OPERATION_RECEIPTS.get(json.dumps([actor, endpoint, safe_key]))
        if entry is None:
            candidate = IDEMPOTENCY_RECORDS.get((actor, endpoint, safe_key))
            entry = candidate if candidate and candidate.get("status") == "COMPLETED" else None
        if not entry or time.time() - float(entry.get("completed_at", 0)) > IDEMPOTENCY_TTL_SECONDS:
            return None
        previous = entry.get("request_fingerprint")
        if previous is None:
            return None
        if previous != request_fingerprint:
            raise SecurityError("幂等键已用于不同请求", 409, "IDEMPOTENCY_KEY_REUSE")
        audit_event("IDEMPOTENCY_REPLAY", user_id=actor, decision="ALLOW", outcome="CACHED_RESPONSE", request_id=request_id, metadata={"endpoint": endpoint})
        return deepcopy(entry["response"])


def _idempotency_complete(actor: str, endpoint: str, key: Optional[str], payload: Any, response: dict[str, Any], *, request_fingerprint: Optional[str] = None) -> None:
    if not key:
        return
    safe_key = sanitize_text(key, field="幂等键", max_length=128)
    scoped = (actor, endpoint, safe_key)
    with STATE_LOCK:
        entry = IDEMPOTENCY_RECORDS.get(scoped)
        if entry and entry.get("fingerprint") == _idempotency_fingerprint(payload):
            entry.update({"status": "COMPLETED", "response": deepcopy(response), "completed_at": time.time()})
            if request_fingerprint is not None:
                entry["request_fingerprint"] = request_fingerprint
            if endpoint in {"transfer_execute", "lock_fund", "operation_execute", "task_execute"}:
                receipt_key = json.dumps([actor, endpoint, safe_key])
                OPERATION_RECEIPTS[receipt_key] = {"completed_at": time.time(), "fingerprint": entry["fingerprint"], "response": deepcopy(response)}
                if request_fingerprint is not None:
                    OPERATION_RECEIPTS[receipt_key]["request_fingerprint"] = request_fingerprint
                if len(OPERATION_RECEIPTS) > IDEMPOTENCY_MAX_RECORDS:
                    oldest = min(OPERATION_RECEIPTS, key=lambda item: OPERATION_RECEIPTS[item]["completed_at"])
                    OPERATION_RECEIPTS.pop(oldest, None)


def _idempotency_abort(actor: str, endpoint: str, key: Optional[str]) -> None:
    if not key:
        return
    try:
        safe_key = sanitize_text(key, field="幂等键", max_length=128)
    except SecurityError:
        return
    with STATE_LOCK:
        IDEMPOTENCY_RECORDS.pop((actor, endpoint, safe_key), None)


class StrictModel(BaseModel):
    # Unknown model fields are ignored; only explicit allowlisted fields below
    # can reach a business operation.
    model_config = ConfigDict(extra="ignore")


class PrecheckRequest(StrictModel):
    from_user: str
    target_name_or_id: str
    amount: float = Field(...)
    remark: Optional[str] = "智能转账"
    # The card this payment will settle against.  Optional so single-card
    # clients keep working; with several ACTIVE cards the core refuses to guess.
    card_id: Optional[str] = None


class TransferExecuteRequest(StrictModel):
    from_user: str
    target_name_or_id: str
    amount: float = Field(...)
    remark: Optional[str] = "智能转账"
    mfa_proof: Optional[str] = None
    user_confirmed: bool = False
    card_id: Optional[str] = None


class ConfirmationRequest(StrictModel):
    auth_token: Optional[str] = None
    user_id: Optional[str] = None


class MFAVerifyRequest(StrictModel):
    auth_token: Optional[str] = None
    otp: Optional[str] = None
    sms_code: Optional[str] = None
    face_attestation: Optional[str] = None
    user_id: Optional[str] = None


class FaceChallengeRequest(StrictModel):
    auth_token: str
    user_id: Optional[str] = None


class LoginRequest(StrictModel):
    user_id: str
    password: str
    # Optional second factor at login, required only when the deployment asks
    # for it (BANK_LOGIN_REQUIRE_OTP=1).
    otp: Optional[str] = None


class DeviceRegisterRequest(StrictModel):
    """Device enrollment: the client proves possession of a P-256 private key."""

    public_key: str = Field(..., description="Base64(DER SubjectPublicKeyInfo) 的公钥")
    label: Optional[str] = None
    platform: Optional[str] = None
    app_version: Optional[str] = None


class LockPrecheckRequest(StrictModel):
    user_id: str
    amount: float = Field(...)
    reason: str = "跨场景联动储备"
    event_at: Optional[str] = None
    reminder_at: Optional[str] = None


class LockFundRequest(StrictModel):
    user_id: str
    amount: float = Field(...)
    reason: str
    auth_token: Optional[str] = None
    mfa_proof: Optional[str] = None
    user_confirmed: bool = False
    event_at: Optional[str] = None
    reminder_at: Optional[str] = None


class UnlockPrecheckRequest(StrictModel):
    user_id: str
    amount: float
    lock_id: Optional[str] = None
    reason: str = "解除资金隔离"


class UnlockFundRequest(StrictModel):
    user_id: str
    amount: float
    lock_id: Optional[str] = None
    reason: str = "解除资金隔离"
    auth_token: Optional[str] = None
    mfa_proof: Optional[str] = None
    user_confirmed: bool = False


class OperationPrecheckRequest(StrictModel):
    """Common policy request for card, wealth and subscription actions."""

    user_id: str
    operation: str
    amount: Optional[float] = 0.0
    target: Optional[str] = None
    product_id: Optional[str] = None
    card_id: Optional[str] = None
    transaction_id: Optional[str] = None
    transaction_snapshot_hash: Optional[str] = None
    new_limit: Optional[float] = None
    merchant: Optional[str] = None
    participants: Optional[list[str]] = None
    product_ids: Optional[list[str]] = None
    scheduled_at: Optional[str] = None
    reason: Optional[str] = "智能银行操作"


class OperationExecuteRequest(StrictModel):
    user_id: str
    operation: str = ""
    amount: Optional[float] = 0.0
    target: Optional[str] = None
    product_id: Optional[str] = None
    card_id: Optional[str] = None
    transaction_id: Optional[str] = None
    transaction_snapshot_hash: Optional[str] = None
    new_limit: Optional[float] = None
    merchant: Optional[str] = None
    participants: Optional[list[str]] = None
    product_ids: Optional[list[str]] = None
    scheduled_at: Optional[str] = None
    reason: Optional[str] = "智能银行操作"
    # Only meaningful for PASSWORD_CHANGE, and deliberately not part of the
    # signed approval context: credential material must never travel inside a
    # bearer token the client holds.
    new_password: Optional[str] = None
    auth_token: Optional[str] = None
    mfa_proof: Optional[str] = None
    user_confirmed: bool = False


class TaskActionRequest(StrictModel):
    reason: str = "用户请求"


class TaskPrecheckRequest(StrictModel):
    user_id: str


class TaskExecuteRequest(StrictModel):
    user_id: str
    auth_token: Optional[str] = None
    mfa_proof: Optional[str] = None


class RiskAssessmentRequest(StrictModel):
    user_id: str
    # Questionnaire answers are intentionally optional at the transport
    # boundary so the API can return a precise 400/428-style clarification
    # instead of Pydantic silently applying a suitability-affecting default.
    horizon_years: Optional[int] = None
    loss_tolerance: Optional[str] = None
    experience: Optional[str] = None
    # 8 题问卷模式：{"1": "B", ..., "8": "C"}。提供 answers 时服务端是唯一
    # 评分权威（客户端只提交选项字母，不提交分数），并按总分定 C1-C5 档。
    answers: Optional[dict[str, str]] = None


def _presented_session(request: Optional[Request]) -> Optional[str]:
    """The raw session credential this request presented, if any.

    Set by ``request_safety_middleware`` after it verified the token, so an
    approval can be bound to the session that asked for it.
    """

    if request is None:
        return None
    value = getattr(request.state, "session_token", None)
    return str(value) if value else None


def _request_id(value: Optional[str]) -> str:
    if value:
        try:
            return sanitize_text(value, field="请求编号", max_length=80)
        except SecurityError:
            pass
    return f"REQ{uuid.uuid4().hex}"


def _raise_security(exc: SecurityError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.detail},
        headers={"Retry-After": "30"} if exc.status_code == 423 else None,
    )


def _record_failure_and_raise(exc: SecurityError, *, user_id: Optional[str], action: str, request_id: str) -> None:
    security_codes = {
        "TOKEN_REQUIRED", "INVALID_TOKEN", "INVALID_TOKEN_SIGNATURE", "TOKEN_NOT_FOUND", "TOKEN_REPLAY", "TOKEN_EXPIRED",
        "TOKEN_BINDING_MISMATCH", "TOKEN_CONTEXT_MISMATCH", "TOKEN_INVALID", "TOKEN_OPERATION_MISMATCH", "MFA_FAILED", "MFA_PROOF_INVALID",
        "MFA_PROOF_REPLAY", "MFA_PROOF_MISMATCH", "MFA_PROOF_EXPIRED", "MFA_REQUIRED", "MFA_PROOF_REQUIRED",
        "IDENTITY_MISMATCH", "PROMPT_INJECTION_BLOCKED", "CIRCUIT_OPEN",
        # Online password guessing counts towards the same per-account breaker as
        # a faulty nonce: the point of the breaker is to stop repetition.
        "INVALID_CREDENTIALS", "INVALID_LOGIN_OTP",
    }
    if exc.code in security_codes:
        record_security_failure(user_id, exc.code, request_id=request_id)
    audit_event(action, user_id=user_id, decision="BLOCK", outcome="FAILURE", reason=exc.code, request_id=request_id, metadata={"message": exc.detail})
    _raise_security(exc)


_WRITE_ACTIONS_REQUIRING_HEADER = {
    "RISK_PRECHECK",
    "LOCK_PRECHECK",
    "UNLOCK_PRECHECK",
    "OPERATION_PRECHECK",
    "TRANSFER_EXECUTE",
    "LOCK_FUND",
    "UNLOCK_FUND",
    "OPERATION_EXECUTE",
    # Risk assessment persists a suitability profile.  Although the resulting
    # profile is a green/read-style fact, the questionnaire write must still
    # be attributed to an authenticated principal so another account cannot be
    # overwritten by a forged body user id.
    "WEALTH_RISK_ASSESSMENT",
    # The contact book is per-user data that feeds the payee allow-list: adding
    # or removing entries without a presented principal would let an anonymous
    # caller seed (or clear) somebody else's payee list.
    "CONTACT_ADD",
    "CONTACT_DELETE",
    "CARD_ADD",
    "CARD_DETAIL_REVEAL",
}


def _get_actor(requested_user: str, presented_user: Optional[str], *, action: str, request_id: str) -> str:
    # A body/path user id is an object selector, not authentication.  Require
    # a presented principal for every operation that can mint a nonce or
    # mutate state; otherwise a caller could deliberately attribute repeated
    # bad-token failures to another account while demo compatibility remains
    # enabled for read-only queries.
    if action in _WRITE_ACTIONS_REQUIRING_HEADER and not (presented_user or "").strip():
        exc = SecurityError("写操作需要登录身份", 401, "AUTHENTICATION_REQUIRED")
        _record_failure_and_raise(exc, user_id=None, action=action, request_id=request_id)
        raise AssertionError("unreachable")
    try:
        return validate_actor(requested_user, presented_user)
    except SecurityError as exc:
        # Attribute an authentication/identity failure to the presented
        # principal (or anonymous), never to the account named in an
        # untrusted request body/path.  Otherwise an attacker could repeatedly
        # submit ``requested_user=victim`` with a mismatching header and open
        # the victim's circuit breaker as a denial-of-service primitive.
        failure_user = presented_user or (None if exc.code == "AUTHENTICATION_REQUIRED" else requested_user)
        _record_failure_and_raise(exc, user_id=failure_user, action=action, request_id=request_id)
        raise AssertionError("unreachable")


def _resolve_target(value: str, *, from_user: Optional[str] = None) -> tuple[str, dict[str, Any]]:
    safe_value = sanitize_text(value, field="收款人", max_length=128)
    # The caller's own contact book is the first payee allow-list.  A name or
    # phone resolves directly; a kinship word ("爱人"/"儿子"/"兄弟"…) resolves
    # to the contact carrying that relationship - exactly one, otherwise the
    # ambiguity is named instead of guessing.
    if from_user:
        contact: Optional[dict[str, Any]] = None
        with STATE_LOCK:
            rows = USER_CONTACTS.get(from_user, [])
            contact = next(
                (row for row in rows if safe_value in {str(row.get("name") or ""), str(row.get("phone") or "")}),
                None,
            )
            if contact is None:
                category = CONTACT_RELATION_SYNONYMS.get(safe_value) or (safe_value if safe_value in CONTACT_RELATIONSHIP_LABELS else None)
                if category:
                    matches = [row for row in rows if row.get("relationship") == category]
                    if len(matches) > 1:
                        names = "、".join(str(row.get("name")) for row in matches[:5])
                        label = CONTACT_RELATIONSHIP_LABELS.get(category, category)
                        raise SecurityError(
                            f"有多个关系为“{label}”的联系人（{names}），请说明具体姓名",
                            409,
                            "CONTACT_TARGET_AMBIGUOUS",
                        )
                    contact = matches[0] if matches else None
        if contact and contact.get("account_id"):
            receiver = ACCOUNTS.get(str(contact["account_id"]))
            if receiver and receiver.get("status") == "ACTIVE":
                if str(contact["account_id"]) == from_user:
                    raise SecurityError("不允许向本人账户转账", 400, "SELF_TRANSFER")
                return str(contact["account_id"]), receiver
    target_id = CONTACTS.get(safe_value, safe_value)
    receiver = ACCOUNTS.get(target_id)
    if not receiver:
        if safe_value in CONTACT_RELATION_SYNONYMS or safe_value in CONTACT_RELATIONSHIP_LABELS:
            raise SecurityError(
                f"联系人簿中没有关系为“{safe_value}”的联系人，请先添加或直接说明姓名",
                404,
                "TARGET_NOT_FOUND",
            )
        raise SecurityError("收款方账户不存在或未登记为联系人", 404, "TARGET_NOT_FOUND")
    if from_user and target_id == from_user:
        raise SecurityError("不允许向本人账户转账", 400, "SELF_TRANSFER")
    if receiver.get("status") != "ACTIVE":
        raise SecurityError("收款方账户当前不可用", 409, "TARGET_INACTIVE")
    return target_id, receiver


def _ensure_account(user_id: str) -> dict[str, Any]:
    account = ACCOUNTS.get(user_id)
    if not account:
        raise SecurityError("账户不存在", 404, "ACCOUNT_NOT_FOUND")
    if account.get("status") != "ACTIVE":
        raise SecurityError("账户已被安全锁定或不可用", 423, "ACCOUNT_LOCKED")
    return account


def _daily_spent(user_id: str) -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    # Initialize the date bucket without discarding a deliberately seeded
    # demo counter; only a known prior date is reset on rollover.
    if user_id not in DAILY_TRANSFER_DATE:
        DAILY_TRANSFER_DATE[user_id] = today
        DAILY_TRANSFERS.setdefault(user_id, 0.0)
    elif DAILY_TRANSFER_DATE.get(user_id) != today:
        DAILY_TRANSFER_DATE[user_id] = today
        DAILY_TRANSFERS[user_id] = 0.0
    return float(DAILY_TRANSFERS.get(user_id, 0.0))


def _split_shares(total_cents: int, participant_count: int) -> tuple[int, int]:
    """Split cents so the parts always add up exactly (no rounding leakage).

    Participants pay an equal floor share; the initiator bears the remainder,
    so ``participant_share * n + initiator_share == total`` for every amount
    (for example 100.00 / 3 -> 33.33 + 33.33 + 33.34).
    """

    divisor = participant_count + 1
    participant_share = total_cents // divisor
    initiator_share = total_cents - participant_share * participant_count
    return participant_share, initiator_share


def _prepare_split_shares(user_id: str, total: float, participants: list[str], target: Optional[str]) -> dict[str, Any]:
    """Create payment requests; only each participant can authorize a debit."""

    total_cents = int(round(total * 100))
    participant_share_cents, initiator_share_cents = _split_shares(total_cents, len(participants))
    _ensure_account(user_id)
    rows: list[dict[str, Any]] = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for name in participants:
        target_id, account = _resolve_target(name, from_user=user_id)
        share = participant_share_cents / 100.0
        rows.append({"name": name, "account_id": target_id, "display_name": account.get("name"), "share": share, "status": "PENDING", "reason": None})
    outstanding = participant_share_cents * len(participants) / 100.0
    return {
        "collection": rows,
        "collected_amount": 0.0,
        "outstanding_amount": outstanding,
        "participant_share": participant_share_cents / 100.0,
        "initiator_share": initiator_share_cents / 100.0,
        "status": "PENDING",
        "created_at": timestamp,
        "target": target,
    }


def _record_split_payment(*, split_id: str, payer_id: str, recipient_id: str, amount: float, transaction_id: str) -> dict[str, Any]:
    """Reconcile one independently authorized transfer with an AA request."""

    split = next((row for row in SPLIT_REQUESTS if row.get("split_id") == split_id), None)
    if not split or split.get("user_id") != recipient_id:
        raise SecurityError("AA 收款请求不存在或收款人不匹配", 404, "SPLIT_NOT_FOUND")
    participant = next((row for row in split["collection"] if row.get("account_id") == payer_id), None)
    if not participant or participant.get("status") != "PENDING":
        raise SecurityError("该 AA 份额不存在或已处理", 409, "SPLIT_SHARE_NOT_PENDING")
    if amount_to_cents(participant["share"]) != amount_to_cents(amount):
        raise SecurityError("AA 付款金额与待付份额不符", 409, "SPLIT_AMOUNT_MISMATCH")
    participant.update(status="COLLECTED", transaction_id=transaction_id, collected_at=datetime.now().isoformat())
    split["collected_amount"] = round(float(split["collected_amount"]) + amount, 2)
    split["outstanding_amount"] = round(float(split["outstanding_amount"]) - amount, 2)
    _refresh_split_status(split)
    split["collected_at"] = datetime.now().isoformat()
    return split


def _refresh_split_status(split: dict[str, Any]) -> None:
    shares = split.get("collection", [])
    if all(item.get("status") == "COLLECTED" for item in shares):
        split["status"] = "SETTLED"
    elif float(split.get("collected_amount", 0)) > 0:
        split["status"] = "PARTIAL"
    elif any(item.get("status") == "PENDING" for item in shares):
        split["status"] = "PENDING"
    else:
        split["status"] = "DECLINED"


def _split_id_from_remark(remark: str) -> Optional[str]:
    match = re.fullmatch(r"AA:(SPL[A-Z0-9]+)", remark, re.IGNORECASE)
    if not match:
        match = re.search(r"(?:备注|附言)\s*[:：为是]?\s*AA:(SPL[A-Z0-9]+)\s*$", remark, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _card_daily_spent(card_id: str) -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    if card_id not in CARD_DAILY_SPENT_DATE:
        CARD_DAILY_SPENT_DATE[card_id] = today
        CARD_DAILY_SPENT.setdefault(card_id, 0.0)
    elif CARD_DAILY_SPENT_DATE.get(card_id) != today:
        CARD_DAILY_SPENT_DATE[card_id] = today
        CARD_DAILY_SPENT[card_id] = 0.0
    return float(CARD_DAILY_SPENT.get(card_id, 0.0))


def _payment_card(user_id: str, card_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The single card a payment settles against, validated for status.

    One resolution for both halves of the check, because enforcement used to
    read ``_first_active_card()`` (the limit) while recording read ``cards[0]``
    (the counter) - so with one frozen and one active card the transfer was
    authorised against the active card's limit and *recorded* on the frozen
    one, leaving both cards' displayed figures wrong.

    ``card_id`` names the card explicitly (an operator choosing on the app);
    otherwise the only ACTIVE card is used and ambiguity is an error rather
    than a guess.
    """

    cards = _user_cards(user_id)
    if not cards:
        return None
    if card_id:
        card = _find_card(user_id, card_id)
        if card is None:
            raise SecurityError("卡片不存在或不属于当前用户", 404, "CARD_NOT_FOUND")
        if str(card.get("status")) != "ACTIVE":
            raise SecurityError("该卡片当前不可用于交易", 423, "CARD_BLOCKED")
        return card
    active = [card for card in cards if str(card.get("status")) == "ACTIVE"]
    if not active:
        raise SecurityError("当前没有可用于交易的卡片，请先解冻或解除挂失", 423, "CARD_BLOCKED")
    if len(active) > 1:
        names = "、".join(str(card.get("card_id")) for card in active[:5])
        raise SecurityError(f"您有多张可用的卡片（{names}），请指定用于支付的卡片", 409, "CARD_ID_REQUIRED")
    return active[0]


def _enforce_card_limit(user_id: str, amount: float, *, card: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Refuse a settlement that would exceed the paying card's daily limit.

    Returns the card that was checked, which the caller must hand to
    ``_record_card_spend`` so both halves use one card.
    """

    resolved = card if card is not None else _payment_card(user_id)
    if not resolved:
        return None
    limit = resolved.get("daily_limit")
    if limit is None:
        return resolved
    try:
        limit_value = float(limit)
    except (TypeError, ValueError):
        return resolved
    spent = _card_daily_spent(str(resolved.get("card_id") or ""))
    if round(spent + amount, 2) > round(limit_value, 2):
        raise SecurityError("超出卡片当日支付限额，请调整额度或明日再试", 400, "CARD_LIMIT_EXCEEDED")
    return resolved


def _record_card_spend(user_id: str, amount: float, *, card: Optional[dict[str, Any]] = None) -> None:
    resolved = card if card is not None else _payment_card(user_id)
    if not resolved:
        return
    card_id = str(resolved.get("card_id") or "")
    if not card_id:
        return
    CARD_DAILY_SPENT[card_id] = round(_card_daily_spent(card_id) + amount, 2)


def _charge_card_and_balance(user_id: str, amount: float, *, card_id: Optional[str] = None) -> dict[str, Any]:
    """Enforce the paying card's limit and record the spend, consistently.

    Used by every path that actually moves money out of the account (transfer,
    wealth purchase), so ``daily_limit`` cannot be enforced on one outflow and
    silently ignored on another.
    """

    card = _payment_card(user_id, card_id)
    if card is None:
        raise SecurityError("付款卡片当前不可用于交易", 423, "CARD_BLOCKED")
    checked = _enforce_card_limit(user_id, amount, card=card)
    return checked or card


# Sensitive operations that feed the behaviour-combination breaker.  The
# signal names line up with ``security.BEHAVIOR_SIGNAL_WEIGHTS``.
_BEHAVIOR_OPERATION_SIGNALS: dict[str, str] = {
    "CARD_REPORT_LOST": "CARD_REPORT_LOST",
    "CARD_FREEZE": "CARD_STATE_CHANGE",
    "CARD_UNFREEZE": "CARD_STATE_CHANGE",
    "CARD_LIMIT": "CARD_LIMIT_CHANGE",
    "PASSWORD_CHANGE": "PASSWORD_CHANGE",
    "WEALTH_PURCHASE": "WEALTH_PURCHASE",
    "WEALTH_REDEEM": "WEALTH_REDEEM",
}

GENERIC_OPERATION_RISK: dict[str, str] = {
    # Yellow: a reversible/low-value action still needs an explicit click.
    "CARD_APPLY": "YELLOW",
    # Subscription/debit lists are yellow by contest policy: the user confirms
    # before Core releases them, and the confirmation is a single-use nonce.
    "SUBSCRIPTION_QUERY": "YELLOW",
    "SUBSCRIPTION_CANCEL": "YELLOW",
    "SCHEDULED_TRANSFER": "YELLOW",
    "SPLIT_BILL": "YELLOW",
    # Red: money investment, card security and limit changes require MFA.
    "WEALTH_PURCHASE": "RED",
    "WEALTH_REDEEM": "RED",
    "CARD_LIMIT": "RED",
    "CARD_FREEZE": "RED",
    "CARD_UNFREEZE": "RED",
    "CARD_REPORT_LOST": "RED",
    "CARD_UNREPORT_LOST": "RED",
    "PASSWORD_CHANGE": "RED",
    "FRAUD_PROTECT": "RED",
}


def _pending_transfer_amount(user_id: str, *, exclude_schedule_id: Optional[str] = None) -> float:
    """Return unconsumed transfer reservations for cumulative risk checks.

    A precheck is a reservation, not a settlement.  Counting active signed
    nonces prevents two concurrent 600-yuan prechecks from both being shown as
    yellow and bypassing the 1,000-yuan daily threshold.
    """

    now = time.time()
    total = 0.0
    # Callers normally hold STATE_LOCK already; RLock keeps this helper safe
    # when used independently (for example by a concurrent risk probe).
    with STATE_LOCK:
        for record in AUTH_TOKENS.values():
            if record.get("used") or float(record.get("expires_at", 0)) <= now:
                continue
            payload = record.get("payload") or {}
            if payload.get("from_user") == user_id and payload.get("operation", "TRANSFER") in {"TRANSFER", "SCHEDULED_TRANSFER"}:
                try:
                    total += float(payload.get("amount_cents", 0)) / 100.0
                except (TypeError, ValueError):
                    continue
        # Once a scheduled approval nonce is consumed, the resulting task
        # remains an outstanding money-movement reservation. Keep it in the
        # cumulative risk calculation until cancelled or settled, preventing a
        # user from consuming a yellow nonce and immediately obtaining another
        # yellow nonce that exceeds the daily threshold.
        total += sum(
            float(row.get("amount", 0) or 0)
            for row in SCHEDULED_TRANSFERS
            if row.get("user_id") == user_id and row.get("schedule_id") != exclude_schedule_id and row.get("status") in {"SCHEDULED", "WAITING_USER"}
        )
    return round(total, 2)


def _operation_target(req: OperationPrecheckRequest | OperationExecuteRequest) -> str:
    """Stable token binding for a generic operation."""

    candidate = req.product_id or req.card_id or req.target or getattr(req, "merchant", None)
    if candidate:
        return sanitize_text(candidate, field="操作目标", max_length=128)
    return f"__{req.operation.upper()}__"


def _operation_amount(value: Optional[float], *, operation: str = "") -> float:
    """Normalize an operation amount, or return the non-financial sentinel.

    The signed token format uses cents and requires a positive value, so
    operations that carry no money (freeze, cancel, password change) use 0.01
    as an internal sentinel that is never debited.

    A supplied amount of exactly zero is treated as "no amount" rather than as
    a one-cent operation: the executor defaults ``amount`` to 0, so absent and
    explicit zero are not distinguishable at this boundary, and every operation
    that actually moves money is separately required to carry a positive amount
    (``AMOUNT_REQUIRED``).  What must never happen is a zero being quietly
    rounded up into an instruction the caller did not give, which is why
    ``CARD_LIMIT`` - where zero would mean "stop this card paying" - gets a
    precise rejection instead of the sentinel.
    """

    if value is None or (not isinstance(value, bool) and isinstance(value, (int, float)) and float(value) == 0):
        if operation == "CARD_LIMIT" and value is not None:
            raise SecurityError(
                "卡片日限额必须大于 0；如需停用该卡请使用冻结或挂失",
                400,
                "INVALID_CARD_LIMIT",
            )
        return 0.01
    # Return the canonical cent-rounded value as well as validating it.  Using
    # the raw binary float here would let a value such as 0.005 be signed as
    # one cent but persisted as a sub-cent amount in scheduled/split/wealth
    # records, making risk totals and ledger facts disagree.
    return amount_to_cents(value) / 100.0


def _context_hash(context: dict[str, Any]) -> str:
    """Return a deterministic digest for approval-bound operation details."""

    canonical = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_datetime(value: Optional[str], *, field: str, require_future: bool = False) -> Optional[str]:
    """Normalize an ISO/date value for token binding and scheduler use.

    A date without a time is interpreted at 09:00 in UTC for deterministic
    demos.  The normalized UTC string is what gets signed, so equivalent input
    representations cannot produce an authorization mismatch at execution.
    """

    if value is None or not str(value).strip():
        return None
    text = sanitize_text(value, field=field, max_length=80).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise SecurityError(f"{field}格式无效", 400, "INVALID_DATETIME") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc).replace(microsecond=0)
    if require_future and parsed <= datetime.now(timezone.utc):
        raise SecurityError(f"{field}必须是未来时间", 400, "DATETIME_NOT_FUTURE")
    return parsed.isoformat()


def _birthday_times(event_at: Optional[str], reminder_at: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Normalize a cross-scene event and derive the auditable reminder time."""

    normalized_event = _normalize_datetime(event_at, field="事件日期", require_future=True) if event_at else None
    if not normalized_event:
        if reminder_at:
            raise SecurityError("缺少生日日期，无法计算提醒时间", 400, "EVENT_DATE_REQUIRED")
        return None, None
    event_dt = datetime.fromisoformat(normalized_event)
    expected_reminder = (event_dt - timedelta(days=2)).isoformat()
    if reminder_at:
        normalized_reminder = _normalize_datetime(reminder_at, field="提醒日期")
        if normalized_reminder != expected_reminder:
            raise SecurityError("提醒时间必须是事件日期前 2 天", 400, "INVALID_REMINDER_TIME")
    return normalized_event, expected_reminder


def _operation_context(
    *,
    operation: str,
    target: str,
    amount: float,
    reason: str,
    new_limit: Optional[float] = None,
    merchant: Optional[str] = None,
    participants: Optional[list[str]] = None,
    product_ids: Optional[list[str]] = None,
    risk_profile: Optional[str] = None,
    subscription_id: Optional[str] = None,
    scheduled_at: Optional[str] = None,
    transaction_id: Optional[str] = None,
    transaction_snapshot_hash: Optional[str] = None,
) -> dict[str, Any]:
    """Normalize every field that can alter a generic operation's outcome."""

    safe_participants: list[str] = []
    for value in participants or []:
        safe_participants.append(sanitize_text(value, field="分摊参与人", max_length=120))
    if len(safe_participants) > 20 or len(set(safe_participants)) != len(safe_participants):
        raise SecurityError("分摊参与人数量或内容无效", 400, "INVALID_PARTICIPANTS")
    safe_product_ids: list[str] = []
    for value in product_ids or []:
        item = sanitize_text(value, field="理财产品编号", max_length=64)
        if item and item not in safe_product_ids:
            safe_product_ids.append(item)
    if len(safe_product_ids) > 10:
        raise SecurityError("理财产品数量超过限制", 400, "INVALID_PRODUCTS")
    # Cancellation clients may identify the same subscription as either
    # ``target`` during precheck or ``merchant`` during execution.  Canonicalize
    # that alias so adding the explicit merchant field at execution cannot
    # invalidate an otherwise unchanged approval; a genuinely different
    # merchant still changes the digest and is rejected.
    merchant_value = merchant or (target if operation == "SUBSCRIPTION_CANCEL" else None)
    safe_merchant = sanitize_text(merchant_value, field="商户", max_length=120) if merchant_value else None
    normalized_schedule = _normalize_datetime(
        scheduled_at,
        field="计划时间",
        require_future=operation == "SCHEDULED_TRANSFER",
    ) if scheduled_at else None
    # ``amount`` is the backwards-compatible CARD_LIMIT field.  Bind the
    # effective limit rather than only the optional ``new_limit`` so a client
    # cannot approve one value and inject another representation at execution;
    # explicit and implicit forms carrying the same value remain equivalent.
    effective_new_limit = amount if operation == "CARD_LIMIT" and new_limit is None else new_limit
    normalized_new_limit = amount_to_cents(effective_new_limit) if effective_new_limit not in (None, 0) else None
    return {
        "operation": operation,
        "target": target,
        "amount_cents": amount_to_cents(amount),
        "reason": reason,
        "new_limit_cents": normalized_new_limit,
        "merchant": safe_merchant,
        "participants": safe_participants,
        "product_ids": safe_product_ids,
        "risk_profile": sanitize_text(risk_profile, field="风险档案", max_length=32).upper() if risk_profile else None,
        "subscription_id": sanitize_text(subscription_id, field="订阅编号", max_length=128) if subscription_id else None,
        "scheduled_at": normalized_schedule,
        "transaction_id": sanitize_text(transaction_id, field="交易编号", max_length=128) if transaction_id else None,
        "transaction_snapshot_hash": sanitize_text(transaction_snapshot_hash, field="交易快照摘要", max_length=128) if transaction_snapshot_hash else None,
    }


def _fraud_transaction(user_id: str, transaction_id: Optional[str] = None) -> dict[str, Any]:
    """Resolve exactly one owned suspicious overseas transaction."""
    rows = [row for row in TRANSACTIONS if row.get("account_id") == user_id and (row.get("category") == "可疑交易" or row.get("country"))]
    if transaction_id:
        row = next((item for item in rows if item.get("tx_id") == transaction_id), None)
        if row is None:
            raise SecurityError("交易不存在或不属于当前用户", 404, "TRANSACTION_NOT_FOUND")
    elif not rows:
        raise SecurityError("未找到可疑交易，请指定交易编号以便核对", 404, "TRANSACTION_NOT_FOUND")
    elif len(rows) != 1:
        raise SecurityError("匹配到多笔可疑交易，请指定交易编号", 409, "TRANSACTION_ID_REQUIRED")
    else:
        row = rows[0]
    if not row.get("card_id"):
        raise SecurityError("该交易未关联卡片，无法安全冻结", 409, "CARD_LINK_MISSING")
    return row


def _validate_split_participants(user_id: str, participants: Optional[list[str]]) -> list[str]:
    """Resolve AA participants against the Core contact allow-list.

    Participant names are used for the local split-event record,
    but they are still security-sensitive routing data.  Accepting arbitrary
    model-generated strings would allow an event to target an unregistered
    account (or make the audit trail impossible to reconcile).
    Return the original display values so the UI remains human-readable while
    proving that each value resolves to an active, non-self account.
    """

    values = list(participants or [])
    if not values:
        raise SecurityError("AA 分摊至少需要一位参与人", 400, "PARTICIPANTS_REQUIRED")
    if len(values) > 20 or len(set(values)) != len(values):
        raise SecurityError("分摊参与人数量或内容无效", 400, "INVALID_PARTICIPANTS")
    resolved: list[str] = []
    resolved_ids: set[str] = set()
    for value in values:
        display = sanitize_text(value, field="分摊参与人", max_length=120)
        target_id, _ = _resolve_target(display, from_user=user_id)
        # An AA request only names a verified contact. Money moves later only
        # when the participant authenticates and approves an ordinary transfer.
        if not _aa_request_eligible(user_id, display):
            raise SecurityError(
                f"“{display}”尚未确认关联账户，AA 请求只能发给已确认的联系人",
                403,
                "PARTICIPANT_NOT_AUTHORIZED",
            )
        if target_id in resolved_ids:
            raise SecurityError("分摊参与人不能重复指向同一账户", 400, "INVALID_PARTICIPANTS")
        resolved_ids.add(target_id)
        resolved.append(display)
    return resolved


def _operation_risk_level(user_id: str, operation: str, amount: float) -> str:
    """Return the core-owned risk class for a generic operation.

    An AA request does not debit anyone. Each participant's eventual payment
    uses the ordinary transfer risk check on that participant's daily total.
    """

    if operation not in {"SCHEDULED_TRANSFER"}:
        return GENERIC_OPERATION_RISK[operation]
    with STATE_LOCK:
        accumulated_after = round(_daily_spent(user_id) + _pending_transfer_amount(user_id) + float(amount), 2)
    return "YELLOW" if accumulated_after <= 1000.0 else "RED"


def _fund_lock_risk_level(amount: Any) -> str:
    """Escalate a large reservation/release to the red verification tier."""

    normalized = amount_to_cents(amount) / 100.0
    return "YELLOW" if normalized <= 1000.0 else "RED"


def _validate_operation_name(operation: str) -> str:
    try:
        normalized = sanitize_text(operation, field="操作类型", max_length=64).upper()
    except SecurityError:
        raise
    if normalized not in GENERIC_OPERATION_RISK:
        raise SecurityError("不支持或未登记的操作类型", 400, "OPERATION_NOT_ALLOWED")
    return normalized


def _ensure_product_suitable(user_id: str, product_id: str) -> dict[str, Any]:
    """Return a product only when it is compatible with the user's risk file."""

    product_id = sanitize_text(product_id, field="理财产品编号", max_length=64).upper()
    product = next((item for item in WEALTH_PRODUCTS if item.get("product_id") == product_id), None)
    if not product:
        raise SecurityError("理财产品不存在", 404, "PRODUCT_NOT_FOUND")
    profile = (WEALTH_RISK_PROFILES.get(user_id) or {}).get("risk_profile")
    # Gate on the rank helper rather than a hand-written set of codes.  The
    # 8-question questionnaire grades C1-C5 while the older form produced
    # CONSERVATIVE/BALANCED/AGGRESSIVE, and a literal set here silently answered
    # "please complete the assessment first" to every user who had *just*
    # completed it - the purchase path was unreachable for them.  ``profile_rank``
    # already knows both vocabularies; unknown/absent means "not assessed".
    max_rank = profile_rank(profile)
    if max_rank is None:
        raise SecurityError("申购前请先完成风险评估", 428, "RISK_ASSESSMENT_REQUIRED")
    try:
        product_rank = int(str(product.get("risk_level", "R9")).lstrip("R"))
    except (TypeError, ValueError):
        raise SecurityError("理财产品风险等级无效", 500, "PRODUCT_RISK_INVALID")
    if product_rank > max_rank:
        raise SecurityError("当前风险档案不适合该产品，请先重新评估", 403, "RISK_PROFILE_MISMATCH")
    return product


def _resolve_subscription(user_id: str, target: Optional[str], merchant: Optional[str]) -> dict[str, Any]:
    """Resolve one subscription deterministically before signing approval."""

    candidate = sanitize_text(target or merchant, field="订阅商户或编号", max_length=128)
    if not candidate:
        raise SecurityError("取消订阅缺少商户或订阅编号", 400, "TARGET_REQUIRED")
    rows = list(SUBSCRIPTIONS.get(user_id, []))
    exact = [
        row for row in rows
        if str(row.get("subscription_id", "")).casefold() == candidate.casefold()
        or str(row.get("merchant", "")).casefold() == candidate.casefold()
    ]
    matches = exact or [
        row for row in rows
        if candidate.casefold() in str(row.get("merchant", "")).casefold()
    ]
    if not matches:
        raise SecurityError("订阅不存在", 404, "SUBSCRIPTION_NOT_FOUND")
    if len(matches) != 1:
        raise SecurityError("匹配到多个订阅，请提供订阅编号", 409, "SUBSCRIPTION_AMBIGUOUS")
    if str(matches[0].get("status")) != "ACTIVE":
        raise SecurityError("该订阅已取消或不可用", 409, "SUBSCRIPTION_NOT_ACTIVE")
    return matches[0]


def _require_token_subject(
    auth_token: str,
    *,
    body_user: Optional[str],
    header_user: Optional[str],
    action: str,
    request_id: str,
) -> str:
    """Bind confirmation/MFA to both the signed subject and login header."""

    try:
        payload, _ = inspect_auth_token(auth_token)
        subject = str(payload.get("from_user") or "").strip()
        if not subject:
            raise SecurityError("安全凭证缺少用户主体", 403, "TOKEN_INVALID")
        # Approval and MFA are authorization endpoints.  Require the login
        # principal even in local demo mode so a bearer nonce alone cannot be
        # replayed by a different channel; strict deployments additionally
        # enforce the same rule on every read/write endpoint.
        if not header_user:
            raise SecurityError("请先完成登录认证", 401, "AUTHENTICATION_REQUIRED")
        # Re-use the same per-principal circuit gate as normal API calls;
        # otherwise an already locked account could continue issuing
        # confirmations/MFA challenges with an outstanding nonce.
        ensure_circuit_closed(header_user)
        if subject != header_user:
            raise SecurityError("安全凭证与登录身份不一致", 403, "IDENTITY_MISMATCH")
        if body_user and subject != body_user:
            raise SecurityError("安全凭证与请求身份不一致", 403, "IDENTITY_MISMATCH")
        return header_user or body_user or subject
    except SecurityError as exc:
        # ``body_user`` is untrusted until both the signed token and the
        # authenticated header have been checked.  Recording failures against
        # it here would let an unauthenticated caller repeatedly submit
        # ``{"user_id": "victim"}`` and open the victim's circuit breaker.
        # Only a presented header principal is eligible for attribution; when
        # no header exists, use the anonymous bucket in the security module.
        _record_failure_and_raise(exc, user_id=header_user, action=action, request_id=request_id)
        raise AssertionError("unreachable")


@app.exception_handler(SecurityError)
async def security_error_handler(_: Request, exc: SecurityError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": exc.detail}},
        headers={"Retry-After": "30"} if exc.status_code == 423 else None,
    )


# ---------------------------------------------------------------------------
# Green zone: read-only operations
# ---------------------------------------------------------------------------
@app.get("/api/v1/accounts/{user_id}/balance")
def get_balance(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    # Return a stable not-found result for an unknown resource before doing
    # object-level identity comparison; this avoids leaking whether a valid
    # caller is associated with a different account.
    if user_id not in ACCOUNTS:
        audit_event("BALANCE_QUERY", user_id=x_user_id, decision="BLOCK", outcome="NOT_FOUND", reason="ACCOUNT_NOT_FOUND", request_id=request_id)
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "账户不存在"})
    actor = _get_actor(user_id, x_user_id, action="BALANCE_QUERY", request_id=request_id)
    try:
        account = _ensure_account(user_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="BALANCE_QUERY", request_id=request_id)
    result = {
        "code": 200,
        "user_name": account["name"],
        "total_balance": account["balance"],
        "locked_amount": account["locked_amount"],
        "available_balance": account["balance"] - account["locked_amount"],
        "status": account["status"],
    }
    audit_event("BALANCE_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id)
    return result


@app.get("/api/v1/accounts/{user_id}/transactions")
def get_transactions(user_id: str, date_scope: str = "ALL", x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), category: Optional[str] = None):
    request_id = _request_id(x_request_id)
    if user_id not in ACCOUNTS:
        audit_event("TRANSACTION_QUERY", user_id=x_user_id, decision="BLOCK", outcome="NOT_FOUND", reason="ACCOUNT_NOT_FOUND", request_id=request_id)
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "账户不存在"})
    actor = _get_actor(user_id, x_user_id, action="TRANSACTION_QUERY", request_id=request_id)
    try:
        _ensure_account(user_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="TRANSACTION_QUERY", request_id=request_id)
    scope = str(date_scope or "ALL").upper()
    scope = {"今年": "YEAR", "年度": "YEAR", "全年": "YEAR"}.get(scope, scope)
    if scope not in {"TODAY", "MONTH", "LAST_MONTH", "YEAR", "ALL"}:
        raise HTTPException(status_code=400, detail={"code": "INVALID_DATE_SCOPE", "message": "不支持的时间范围"})
    records, category = _bill_records(user_id, scope, category)
    audit_event("TRANSACTION_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"record_count": len(records), "date_scope": scope, "category": category})
    return {"code": 200, "date_scope": scope, "category": category, "total_records": len(records), "data": records}


def _bill_records(user_id: str, scope: str, category: Optional[str]) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Apply the same date/category predicates before listing or aggregating."""
    if category is not None:
        category = sanitize_text(category, field="交易分类", max_length=120)
        if not category or re.search(r"[\x00-\x1f\x7f]", category):
            raise HTTPException(status_code=400, detail={"code": "INVALID_CATEGORY", "message": "交易分类不能为空或包含控制字符"})
    rows = filter_transactions([tx.copy() for tx in TRANSACTIONS if tx.get("account_id") == user_id], scope)
    if category is not None:
        rows = [row for row in rows if str(row.get("category") or "其他").strip() == category]
    return rows, category


@app.get("/api/v1/contacts/{user_id}")
def get_contacts(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CONTACT_QUERY", request_id=request_id)
    _ensure_account(user_id)
    # Only expose the caller's allow-listed contacts; phone numbers are
    # masked so this endpoint cannot become an account-enumeration oracle.
    rows = []
    seen = set()
    for alias, target_id in CONTACTS.items():
        if target_id in seen:
            continue
        receiver = ACCOUNTS.get(target_id)
        if not receiver:
            continue
        phone = str(receiver.get("phone", ""))
        rows.append({"contact_id": target_id, "display_name": receiver.get("name"), "phone_masked": phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "****"})
        seen.add(target_id)
    audit_event("CONTACT_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


class ContactAddRequest(StrictModel):
    name: str
    phone: str
    relationship: str
    # A birthday is optional display/reminder data, not an authorization input;
    # it is validated as a plain calendar date and never accepted in the future.
    birthday: Optional[str] = None


def _contact_public_view(row: dict[str, Any], *, caller_id: Optional[str] = None) -> dict[str, Any]:
    """Client-safe projection of one contact-book row (no account internals).

    ``account_name`` is the *payee account holder's* real name, so it is only
    disclosed once that account has actually consented to being linked (see
    ``_link_verification``).  Otherwise the caller sees the name they typed:
    anyone can type someone else's phone number, and echoing the holder's real
    name back would turn the contact book into a name-lookup oracle over the
    account database.

    Consent is recomputed on every read rather than replayed from the stored
    row, because it can be granted *after* the contact was created: the owner
    registering this caller's number later must take effect immediately.
    """

    account_id = str(row.get("account_id") or "")
    verification = _contact_link_status(row, caller_id=caller_id)
    link_verified = verification == LINK_VERIFIED
    linked_name = ACCOUNTS.get(account_id, {}).get("name") if account_id and link_verified else None
    return {
        "contact_id": row.get("contact_id"),
        "name": row.get("name"),
        "phone": row.get("phone"),
        "relationship": row.get("relationship"),
        "relationship_label": CONTACT_RELATIONSHIP_LABELS.get(str(row.get("relationship")), str(row.get("relationship"))),
        "birthday": row.get("birthday"),
        "account_id": account_id or None,
        "linked_existing": bool(row.get("linked_existing")),
        "account_name": linked_name,
        "link_verification": verification,
        "link_verified": link_verified,
        "created_at": row.get("created_at"),
    }


def _link_verification(account_id: str, phone: str, name: str, *, caller_id: Optional[str] = None) -> str:
    """Record the link consent state for a freshly added contact.

    Kept for the persisted row; readers should call ``_contact_link_status`` so
    a later grant is not missed.
    """

    del name
    return _contact_consent(account_id, phone, caller_id=caller_id)


def _contact_consent(account_id: str, phone: str, *, caller_id: Optional[str]) -> str:
    """Whether a phone-to-account link carries the account owner's consent.

    A phone number alone is *not* proof of anything: it is a string the caller
    typed.  Linking it is fine for a payee the caller is paying (their own
    money, their own choice), but it must not let them target that account
    with an AA request or infer the holder's real name.

    Consent exists when the two parties have each registered the other: the
    account holder's own book contains this phone, or the caller is paying with
    the number registered on their own account.
    """

    caller = ACCOUNTS.get(str(caller_id or ""))
    if caller and str(caller.get("phone") or "") == phone:
        return LINK_VERIFIED
    if caller_id and str(caller_id) == account_id:
        return LINK_VERIFIED
    for row in USER_CONTACTS.get(account_id, []):
        if str(row.get("phone") or "") == phone:
            return LINK_VERIFIED
    # The caller's own number for this account may have been registered by the
    # holder only after the contact was first added, so treat "the book the
    # contact points at now lists the caller's number" as consent too.
    if caller:
        caller_phone = str(caller.get("phone") or "")
        for row in USER_CONTACTS.get(account_id, []):
            if caller_phone and str(row.get("phone") or "") == caller_phone:
                return LINK_VERIFIED
    return LINK_UNVERIFIED


def _contact_link_status(row: Mapping[str, Any], *, caller_id: Optional[str] = None) -> str:
    """Live consent state for a stored contact-book row."""

    account_id = str(row.get("account_id") or "")
    if not account_id:
        return LINK_UNVERIFIED
    owner_id = str(row.get("owner_id") or caller_id or "")
    return _contact_consent(account_id, str(row.get("phone") or ""), caller_id=owner_id or None)


def _aa_request_eligible(user_id: str, name: str) -> bool:
    """Whether ``name`` resolves to a verified recipient of an AA request."""

    with STATE_LOCK:
        for row in USER_CONTACTS.get(user_id, []):
            if name in {str(row.get("name") or ""), str(row.get("phone") or "")}:
                if str(row.get("account_id") or "") not in ACCOUNTS:
                    return False
                return _contact_consent(str(row.get("account_id") or ""), str(row.get("phone") or ""), caller_id=user_id) == LINK_VERIFIED
    return name in CONTACTS


def _resolve_contact_account(name: str, phone: str, *, caller_id: Optional[str] = None) -> tuple[str, bool, str]:
    """Return the payee account a contact points at, provisioning one if needed.

    A phone that already belongs to a demo account links to it and records
    whether the owner consented (see ``_link_verification``).  Anything else
    gets a zero-balance shadow payee account so the transfer settles end to
    end; its password digest is deliberately unusable, so a contact can never
    be turned into a login.
    """

    with STATE_LOCK:
        linked = next(
            (uid for uid, account in ACCOUNTS.items() if str(account.get("phone") or "") == phone and str(account.get("status")) == "ACTIVE"),
            None,
        )
        if linked:
            return linked, True, _link_verification(linked, phone, name, caller_id=caller_id)
        account_id = f"ext{uuid.uuid4().hex[:12]}"
        ACCOUNTS[account_id] = {
            "card_no": "----",
            "name": name,
            "phone": phone,
            "balance": 0.0,
            "locked_amount": 0.0,
            "status": "ACTIVE",
            "credential_version": 1,
            # Unusable digest: argon2 verification fails on it, so the shadow
            # payee cannot be logged into; a bare ``""`` would trip the ledger
            # store's structural validation instead.
            "password_hash": "!contact-book-no-login",
            "source": "CONTACT_BOOK",
        }
        return account_id, False, LINK_UNVERIFIED


@app.get("/api/v1/contacts/{user_id}/book")
def list_contact_book(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """The caller's own contact book (distinct from the built-in payee allow-list)."""

    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CONTACT_QUERY", request_id=request_id)
    _ensure_account(user_id)
    with STATE_LOCK:
        rows = [_contact_public_view(dict(row), caller_id=user_id) for row in USER_CONTACTS.get(user_id, [])]
    audit_event("CONTACT_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows), "source": "contact_book"})
    return {"code": 200, "data": rows}


@app.post("/api/v1/contacts/{user_id}/book")
def add_contact(user_id: str, req: ContactAddRequest, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    """Add one contact (name, phone, relationship, birthday) for the caller.

    The contact becomes a first-class payee: by name or phone it resolves in
    ``_resolve_target`` like any built-in contact.  Everything is validated at
    the boundary - the relationship against a small allow-list, the phone as
    digits, the birthday as a real (not future) calendar date.
    """

    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CONTACT_ADD", request_id=request_id)
    idem_payload = {"user_id": user_id, "name": req.name, "phone": req.phone, "relationship": req.relationship, "birthday": req.birthday}
    cached = _idempotency_begin(actor, "contact_add", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        _ensure_account(user_id)
        name = sanitize_text(req.name, field="联系人姓名", max_length=120)
        if not name:
            raise SecurityError("联系人姓名不能为空", 400, "CONTACT_NAME_REQUIRED")
        phone = sanitize_text(req.phone, field="联系电话", max_length=20)
        if not CONTACT_PHONE_RE.fullmatch(phone or ""):
            raise SecurityError("联系电话格式无效", 400, "INVALID_CONTACT_PHONE")
        relationship_raw = sanitize_text(req.relationship, field="与联系人关系", max_length=20)
        # Accept the Chinese label ("家人") or the canonical code ("FAMILY");
        # anything else is refused rather than stored as free text.
        relationship = CONTACT_RELATIONSHIPS.get(relationship_raw)
        if relationship is None and relationship_raw.upper() in CONTACT_RELATIONSHIP_LABELS:
            relationship = relationship_raw.upper()
        if relationship is None:
            raise SecurityError("关系仅支持：家人/朋友/同事/其他", 400, "INVALID_RELATIONSHIP")
        birthday: Optional[str] = None
        if req.birthday:
            birthday_value = sanitize_text(req.birthday, field="生日", max_length=10)
            if not CONTACT_BIRTHDAY_RE.fullmatch(birthday_value or ""):
                raise SecurityError("生日格式应为 YYYY-MM-DD", 400, "INVALID_BIRTHDAY")
            try:
                parsed = datetime.strptime(birthday_value, "%Y-%m-%d").date()
            except ValueError as exc:
                raise SecurityError("生日不是有效日期", 400, "INVALID_BIRTHDAY") from exc
            if parsed > datetime.now().date():
                raise SecurityError("生日不能是未来日期", 400, "INVALID_BIRTHDAY")
            birthday = birthday_value
    except SecurityError as exc:
        _idempotency_abort(actor, "contact_add", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="CONTACT_ADD", request_id=request_id)

    with STATE_LOCK:
        with _ledger_transaction():
            existing = USER_CONTACTS.get(user_id, [])
            if any(row.get("name") == name and row.get("phone") == phone for row in existing):
                raise SecurityError("该联系人已存在", 409, "CONTACT_EXISTS")
            if len(existing) >= CONTACT_MAX_PER_USER:
                raise SecurityError(
                    f"联系人最多 {CONTACT_MAX_PER_USER} 位，请先删除不再使用的联系人",
                    409,
                    "CONTACT_LIMIT_REACHED",
                )
            account_id, linked_existing, link_verification = _resolve_contact_account(name, phone, caller_id=user_id)
            row = {
                "contact_id": f"CTT{uuid.uuid4().hex[:10].upper()}",
                "owner_id": user_id,
                "name": name,
                "phone": phone,
                "relationship": relationship,
                "birthday": birthday,
                "account_id": account_id,
                "linked_existing": linked_existing,
                "link_verification": link_verification,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            USER_CONTACTS.setdefault(user_id, []).append(row)
    persist_ledger()
    response = {"code": 200, "status": "CREATED", "data": _contact_public_view(dict(row), caller_id=user_id)}
    _idempotency_complete(actor, "contact_add", idempotency_key, idem_payload, response)
    audit_event(
        "CONTACT_ADD",
        user_id=actor,
        risk_level="GREEN",
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={"contact_id": row["contact_id"], "name": name, "phone_masked": phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "****", "relationship": relationship, "linked_existing": linked_existing},
    )
    return response


@app.post("/api/v1/contacts/{user_id}/book/{contact_id}/remove")
def remove_contact(user_id: str, contact_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Remove one contact from the caller's book.

    Only the book entry goes; a provisioned shadow account stays so past
    transfers and its received funds remain reconcilable.
    """

    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CONTACT_DELETE", request_id=request_id)
    _ensure_account(user_id)
    safe_contact_id = sanitize_text(contact_id, field="联系人编号", max_length=64)
    with STATE_LOCK:
        with _ledger_transaction():
            rows = USER_CONTACTS.get(user_id, [])
            index = next((i for i, row in enumerate(rows) if row.get("contact_id") == safe_contact_id), None)
            if index is None:
                raise SecurityError("联系人不存在", 404, "CONTACT_NOT_FOUND")
            removed = rows.pop(index)
    persist_ledger()
    audit_event(
        "CONTACT_DELETE",
        user_id=actor,
        risk_level="GREEN",
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={"contact_id": safe_contact_id, "name": str(removed.get("name") or "")},
    )
    return {"code": 200, "status": "REMOVED", "data": {"contact_id": safe_contact_id}}


@app.get("/api/v1/accounts/{user_id}/bill-analysis")
def get_bill_analysis(user_id: str, date_scope: str = "ALL", x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), category: Optional[str] = None):
    """GREEN: return source-bound bill facts for a report or chart."""

    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="BILL_ANALYSIS", request_id=request_id)
    _ensure_account(user_id)
    scope = str(date_scope or "ALL").upper()
    scope = {"今年": "YEAR", "年度": "YEAR", "全年": "YEAR"}.get(scope, scope)
    if scope not in {"TODAY", "MONTH", "LAST_MONTH", "YEAR", "ALL"}:
        raise HTTPException(status_code=400, detail={"code": "INVALID_DATE_SCOPE", "message": "不支持的时间范围"})
    rows, category = _bill_records(user_id, scope, category)
    facts = analyze_transactions(rows, user_id=actor, request_id=request_id)
    audit_event("BILL_ANALYSIS", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"date_scope": scope, "category": category, "record_count": facts["record_count"]})
    return {"code": 200, "date_scope": scope, "category": category, "facts": facts, "records": rows}


@app.get("/api/v1/wealth/products")
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


@app.get("/api/v1/wealth/holdings/{user_id}")
def get_wealth_holdings(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="WEALTH_HOLDINGS_QUERY", request_id=request_id)
    _ensure_account(user_id)
    rows = [dict(row) for row in WEALTH_HOLDINGS.get(user_id, [])]
    audit_event("WEALTH_HOLDINGS_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@app.get("/api/v1/wealth/recommend")
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


@app.get("/api/v1/wealth/risk-profile")
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


@app.post("/api/v1/wealth/risk-assessment")
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


@app.get("/api/v1/wealth/compare")
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


def _subscription_query_result(user_id: str) -> dict[str, Any]:
    """YELLOW: registered debits plus recurring candidates from verified records.

    Only ``execute_operation`` calls this, after the user confirmed a
    ``SUBSCRIPTION_QUERY`` precheck; there is no unconfirmed read route.
    """

    from subscription_detector import detect_recurring_debits

    subscriptions = [dict(row) for row in SUBSCRIPTIONS.get(user_id, [])]
    registered = [dict(row, source="REGISTERED") for row in subscriptions if row.get("status") == "ACTIVE"]
    registered_merchants = {str(row["merchant"]).casefold() for row in registered}
    detected = [row for row in detect_recurring_debits(TRANSACTIONS, user_id=user_id) if row["merchant"].casefold() not in registered_merchants]
    return {
        "status": "QUERIED",
        "subscriptions": subscriptions,
        "candidates": registered + detected,
        "renewal_reminders": [{"subscription_id": row["subscription_id"], "next_charge": row["next_charge"], "message": f"{row['merchant']} 将于 {row['next_charge']} 扣款 ¥{row['amount']:.2f}"} for row in registered],
    }


@app.post("/api/v1/subscriptions/{user_id}/reminders")
def create_renewal_reminders(user_id: str, merchant: Optional[str] = None, days_before: int = 2, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Create idempotent renewal reminders, optionally for one named merchant.

    No money moves here, so this is a green write: it only materialises the
    detected renewals as auditable task-center items.  Two days is the default;
    the caller may explicitly choose an offset from zero to 365 days. Repeating
    the same subscription/date/offset returns the existing task without a duplicate.
    """

    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="SUBSCRIPTION_REMINDER", request_id=request_id)
    _ensure_account(user_id)
    if isinstance(days_before, bool) or not 0 <= days_before <= 365:
        raise HTTPException(status_code=400, detail={"code": "INVALID_REMINDER_DAYS", "message": "提醒提前天数须在 0 到 365 天之间"})
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with STATE_LOCK:
        with _ledger_transaction():
            try:
                selected_rows = [_resolve_subscription(user_id, merchant, None)] if merchant else list(SUBSCRIPTIONS.get(user_id, []))
            except SecurityError as exc:
                _record_failure_and_raise(exc, user_id=actor, action="SUBSCRIPTION_REMINDER_CREATE", request_id=request_id)
            for row in selected_rows:
                if row.get("status") != "ACTIVE":
                    continue
                try:
                    charge_date = datetime.strptime(str(row.get("next_charge")), "%Y-%m-%d")
                except (TypeError, ValueError):
                    skipped.append({"subscription_id": row.get("subscription_id"), "reason": "INVALID_NEXT_CHARGE"})
                    continue
                run_at = (charge_date - timedelta(days=days_before)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
                task = TASK_STORE.create(
                    actor,
                    "RENEWAL_REMINDER",
                    {
                        "subscription_id": row["subscription_id"],
                        "merchant": row.get("merchant"),
                        "amount": row.get("amount"),
                        "next_charge": row.get("next_charge"),
                        "days_before": days_before,
                        "message": f"{row.get('merchant')} 将于 {row.get('next_charge')} 扣款 ¥{float(row.get('amount') or 0):,.2f}，需要取消请提前操作",
                    },
                    run_at=run_at,
                    idempotency_key=f"renewal:{row['subscription_id']}:{row.get('next_charge')}" + (f":days-{days_before}" if days_before != 2 else ""),
                )
                created.append(task.to_dict())
        persist_ledger()
    audit_event(
        "SUBSCRIPTION_REMINDER_CREATE",
        user_id=actor,
        risk_level="GREEN",
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={"created": len(created), "skipped": len(skipped)},
    )
    return {"code": 200, "status": "CREATED", "data": created, "skipped": skipped}


@app.get("/api/v1/cards/{user_id}")
def get_cards(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CARD_QUERY", request_id=request_id)
    account = _ensure_account(user_id)
    available = round(float(account["balance"]) - float(account.get("locked_amount", 0.0)), 2)
    data: list[dict[str, Any]] = []
    for card in _user_cards(user_id):
        enriched = _card_summary(card)
        card_id = str(card.get("card_id") or "")
        if card_id:
            spent = _card_daily_spent(card_id)
            enriched["daily_spent_today"] = spent
            try:
                enriched["daily_remaining"] = round(max(0.0, float(card.get("daily_limit") or 0.0) - spent), 2)
            except (TypeError, ValueError):
                enriched["daily_remaining"] = None
        # 借记卡语义：卡余额 = 所属账户可用余额。
        enriched["available_balance"] = available
        data.append(enriched)
    audit_event("CARD_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(data)})
    return {"code": 200, "data": data}


class CardAddRequest(StrictModel):
    card_type: str = "借记卡"
    label: Optional[str] = None
    card_number: str
    source: str
    user_confirmed: StrictBool


@app.post("/api/v1/cards/{user_id}/add")
def add_card(
    user_id: str,
    req: CardAddRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """自行添加一张银行卡（演示：开立即生效，虚构卡号仅回传脱敏形式）。"""

    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CARD_ADD", request_id=request_id)
    try:
        normalized_number = _normalize_card_number(req.card_number)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="CARD_ADD", request_id=request_id)
    idem_payload = {"user_id": user_id, "card_type": req.card_type, "label": req.label, "card_fingerprint": hashlib.sha256(normalized_number.encode("utf-8")).hexdigest(), "source": req.source, "user_confirmed": req.user_confirmed}
    cached = _idempotency_begin(actor, "card_add", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        _ensure_account(user_id)
        card_type_raw = sanitize_text(req.card_type or "借记卡", field="卡片类型", max_length=20)
        card_type = {"借记卡": "借记卡", "虚拟卡": "虚拟卡"}.get(card_type_raw, card_type_raw.upper() if card_type_raw.upper() in {"DEBIT", "VIRTUAL"} else None)
        if card_type is None:
            raise SecurityError("卡片类型仅支持：借记卡/虚拟卡", 400, "INVALID_CARD_TYPE")
        label = sanitize_text(req.label, field="卡片别名", max_length=40) if req.label else None
        source = str(req.source or "").strip().upper()
        if source not in {"MANUAL", "OCR"}:
            raise SecurityError("银行卡信息无法核验", 400, "CARD_BINDING_REJECTED")
        if not req.user_confirmed:
            raise SecurityError("请确认银行卡信息后继续", 400, "CARD_CONFIRMATION_REQUIRED")
        if len(_user_cards(user_id)) >= CARD_MAX_PER_USER:
            raise SecurityError(
                f"每人最多持有 {CARD_MAX_PER_USER} 张卡片，请先注销不再使用的卡",
                409,
                "CARD_LIMIT_REACHED",
            )
        pan_ref, vault = next(((ref, item) for ref, item in MOCK_CARD_VAULT.items() if item["card_number"] == normalized_number), (None, None))
        if vault is None or vault.get("owner_user_id") != user_id:
            raise SecurityError("银行卡信息无法核验", 400, "CARD_BINDING_REJECTED")
    except SecurityError as exc:
        _idempotency_abort(actor, "card_add", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="CARD_ADD", request_id=request_id)

    try:
        with STATE_LOCK:
            with _ledger_transaction():
                # Re-check the opaque reference while holding the same lock as
                # issuance. This closes the concurrent identical-bind race.
                if any(item.get("pan_ref") == pan_ref for item in _user_cards(user_id)):
                    raise SecurityError("银行卡已绑定", 409, "CARD_BINDING_REJECTED")
                card = _issue_card_locked(user_id, card_type=card_type, label=label, pan_ref=pan_ref, number_masked=_card_mask(normalized_number), last4=normalized_number[-4:])
    except SecurityError as exc:
        _idempotency_abort(actor, "card_add", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="CARD_ADD", request_id=request_id)
    persist_ledger()
    response = {"code": 200, "status": "ISSUED", "data": _card_summary(card)}
    _idempotency_complete(actor, "card_add", idempotency_key, idem_payload, response)
    audit_event("CARD_ADD", user_id=actor, risk_level="YELLOW", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"card_id": card["card_id"], "last4": card["last4"], "source": source})
    return response


class CardRevealRequest(StrictModel):
    pin: str


@app.post("/api/v1/cards/{user_id}/{card_id}/reveal")
def reveal_card(user_id: str, card_id: str, req: CardRevealRequest, request: Request = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CARD_DETAIL_REVEAL", request_id=request_id)
    session = _presented_session(request)
    if not session:
        exc = SecurityError("需要登录会话后查看卡片信息", 401, "CARD_DISCLOSURE_DENIED")
        _record_failure_and_raise(exc, user_id=actor, action="CARD_DETAIL_REVEAL", request_id=request_id)
    card = _find_card(user_id, card_id)
    vault = MOCK_CARD_VAULT.get(str(card.get("pan_ref"))) if card else None
    key = (user_id, card_id)
    failure = CARD_DISCLOSURE_FAILURES.get(key, {})
    now = time.time()
    if float(failure.get("locked_until", 0)) > now:
        exc = SecurityError("卡片查看已锁定，请稍后重试", 423, "CARD_DISCLOSURE_LOCKED")
        _record_failure_and_raise(exc, user_id=actor, action="CARD_DETAIL_REVEAL", request_id=request_id)
    valid_target = bool(card and vault and card.get("status") != "LOST" and vault.get("owner_user_id") == actor)
    if not valid_target or not verify_password(req.pin, vault.get("pin_hash") if vault else None):
        failed = int(failure.get("failed", 0)) + 1
        if failed >= 5:
            CARD_DISCLOSURE_FAILURES[key] = {"failed": failed, "locked_until": now + 300}
            exc = SecurityError("卡片查看已锁定，请稍后重试", 423, "CARD_DISCLOSURE_LOCKED")
        else:
            CARD_DISCLOSURE_FAILURES[key] = {"failed": failed, "locked_until": 0}
            exc = SecurityError("卡片查看验证失败", 401, "CARD_DISCLOSURE_DENIED")
        _record_failure_and_raise(exc, user_id=actor, action="CARD_DETAIL_REVEAL", request_id=request_id)
    CARD_DISCLOSURE_FAILURES.pop(key, None)
    disclosure_id = f"DISC-{uuid.uuid4().hex[:12].upper()}"
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    audit_event("CARD_DETAIL_REVEAL", user_id=actor, risk_level="RED", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"card_id": card_id, "last4": card.get("last4"), "disclosure_id": disclosure_id, "expires_at": expires_at})
    return {"code": 200, "data": {"owner_name": vault["owner_name"], "card_number": vault["card_number"], "issuer_name": vault["issuer_name"], "expires_at": expires_at, "disclosure_id": disclosure_id}}


# ---------------------------------------------------------------------------
# Risk prechecks, explicit confirmation and MFA
# ---------------------------------------------------------------------------
@app.post("/api/v1/risk/precheck")
def risk_precheck(req: PrecheckRequest, request: Request = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.from_user, x_user_id, action="RISK_PRECHECK", request_id=request_id)
    idem_payload = {"from_user": req.from_user, "target": req.target_name_or_id, "amount": req.amount, "remark": req.remark or "智能转账"}
    cached = _idempotency_begin(actor, "risk_precheck", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        _ensure_account(req.from_user)
        target_id, receiver = _resolve_target(req.target_name_or_id, from_user=req.from_user)
        remark = sanitize_text(req.remark or "智能转账", field="转账备注", max_length=200)
        cents = amount_to_cents(req.amount)
        if not math.isfinite(float(req.amount)):
            raise SecurityError("金额格式无效", 400, "INVALID_AMOUNT")
        # Resolve the paying card before a credential is issued, so an
        # ambiguous or unusable wallet is reported now rather than after the
        # user has approved and passed MFA.  Execution re-checks it.
        payer_card = _payment_card(req.from_user, req.card_id)
        if req.card_id or payer_card is not None:
            _enforce_card_limit(req.from_user, cents / 100.0, card=payer_card)
    except SecurityError as exc:
        _idempotency_abort(actor, "risk_precheck", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="RISK_PRECHECK", request_id=request_id)
    from security import STATE_LOCK
    with STATE_LOCK:
        current_spent = _daily_spent(req.from_user)
        pending = _pending_transfer_amount(req.from_user)
        accumulated_after = round(current_spent + pending + cents / 100.0, 2)
        risk_level = "YELLOW" if accumulated_after <= 1000.0 else "RED"
        required_actions = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
        token, payload = issue_auth_token(from_user=req.from_user, target_id=target_id, target_display=receiver["name"], amount=req.amount, remark=remark, risk_level=risk_level, required_actions=required_actions, operation="TRANSFER", session_token=_presented_session(request))
    audit_event("RISK_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"jti": payload["jti"], "target_id": target_id, "amount_cents": cents, "daily_accumulated_after_cents": int(round(accumulated_after * 100)), "required_actions": required_actions})
    response = {"risk_level": risk_level, "daily_accumulated_now": current_spent, "daily_pending_reserved": pending, "daily_accumulated_after": accumulated_after, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required_actions, "target_id": target_id, "target_name": receiver["name"], "mfa_required": risk_level == "RED"}
    _idempotency_complete(actor, "risk_precheck", idempotency_key, idem_payload, response)
    return response


@app.post("/api/v1/risk/precheck/lock")
def lock_precheck(req: LockPrecheckRequest, request: Request = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="LOCK_PRECHECK", request_id=request_id)
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "reason": req.reason, "event_at": req.event_at, "reminder_at": req.reminder_at}
    cached = _idempotency_begin(actor, "lock_precheck", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        account = _ensure_account(req.user_id)
        reason = sanitize_text(req.reason, field="锁定原因", max_length=300)
        cents = amount_to_cents(req.amount)
        event_at, reminder_at = _birthday_times(req.event_at, req.reminder_at)
        if account["balance"] - account["locked_amount"] < cents / 100.0:
            raise SecurityError("可用余额不足以锁定此金额", 400, "INSUFFICIENT_FUNDS")
    except SecurityError as exc:
        _idempotency_abort(actor, "lock_precheck", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="LOCK_PRECHECK", request_id=request_id)
    lock_context_hash = _context_hash({"event_at": event_at, "reminder_at": reminder_at})
    risk_level = _fund_lock_risk_level(req.amount)
    required_actions = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
    token, payload = issue_auth_token(from_user=req.user_id, target_id="__LOCK_FUND__", target_display="资金隔离池", amount=req.amount, remark=reason, risk_level=risk_level, required_actions=required_actions, operation="LOCK_FUND", context_hash=lock_context_hash, session_token=_presented_session(request))
    audit_event("LOCK_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"jti": payload["jti"], "amount_cents": cents})
    response = {"risk_level": risk_level, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required_actions, "mfa_required": risk_level == "RED", "event_at": event_at, "reminder_at": reminder_at}
    _idempotency_complete(actor, "lock_precheck", idempotency_key, idem_payload, response)
    return response


@app.post("/api/v1/risk/precheck/unlock")
def unlock_precheck(req: UnlockPrecheckRequest, request: Request = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    """Issue a yellow-zone nonce for releasing a previously locked amount."""
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="UNLOCK_PRECHECK", request_id=request_id)
    try:
        _ensure_account(req.user_id)
        reason = sanitize_text(req.reason or "解除资金隔离", field="解除原因", max_length=300)
        cents = amount_to_cents(req.amount)
        lock_id = sanitize_text(req.lock_id, field="锁定编号", max_length=100) if req.lock_id else None
        with STATE_LOCK:
            candidates = [v for v in LOCKS.values() if v.get("user_id") == req.user_id and v.get("status") == "LOCKED" and (not lock_id or v.get("lock_id") == lock_id)]
            available_locked = sum(float(v.get("amount", 0)) for v in candidates)
        if available_locked < cents / 100.0:
            raise SecurityError("可解除的锁定金额不足", 400, "LOCK_NOT_FOUND")
        target = lock_id or "__UNLOCK_FUND__"
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="UNLOCK_PRECHECK", request_id=request_id)
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "lock_id": req.lock_id, "reason": reason}
    cached = _idempotency_begin(actor, "unlock_precheck", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    risk_level = _fund_lock_risk_level(req.amount)
    required_actions = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
    token, payload = issue_auth_token(from_user=req.user_id, target_id=target, target_display="资金隔离池", amount=req.amount, remark=reason, risk_level=risk_level, required_actions=required_actions, operation="UNLOCK_FUND", session_token=_presented_session(request))
    audit_event("UNLOCK_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"jti": payload["jti"], "amount_cents": cents, "lock_id": lock_id})
    response = {"risk_level": risk_level, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required_actions, "mfa_required": risk_level == "RED", "lock_id": lock_id}
    _idempotency_complete(actor, "unlock_precheck", idempotency_key, idem_payload, response)
    return response


@app.post("/api/v1/risk/precheck/operation")
def operation_precheck(
    req: OperationPrecheckRequest,
    request: Request = None,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Create a short-lived approval nonce for non-transfer scenarios.

    This endpoint is intentionally generic so adding a new card/wealth
    operation cannot accidentally create a second, unprotected authorization
    path.  The operation name is checked against ``GENERIC_OPERATION_RISK``.
    """

    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="OPERATION_PRECHECK", request_id=request_id)
    resolved_subscription_id: Optional[str] = None
    fraud_tx: Optional[dict[str, Any]] = None
    try:
        _ensure_account(req.user_id)
        operation = _validate_operation_name(req.operation)
        target = _operation_target(req)
        if operation == "FRAUD_PROTECT":
            fraud_tx = _fraud_transaction(req.user_id, req.transaction_id)
            target = str(fraud_tx["card_id"])
            reason = sanitize_text(req.reason or "客户否认境外消费，申请反诈保护", field="操作原因", max_length=300)
            if req.transaction_snapshot_hash and req.transaction_snapshot_hash != _context_hash(fraud_tx):
                raise SecurityError("交易信息已变化，请重新核验", 409, "TRANSACTION_CONTEXT_CHANGED")
        if operation == "PASSWORD_CHANGE" and any(value for value in (req.target, req.product_id, req.card_id, req.merchant)):
            raise SecurityError("密码修改不接受外部目标编号", 400, "TARGET_NOT_ALLOWED")
        reason = sanitize_text(req.reason or ("客户否认境外消费，申请反诈保护" if operation == "FRAUD_PROTECT" else "智能银行操作"), field="操作原因", max_length=300)
        amount_input = req.new_limit if operation == "CARD_LIMIT" and req.amount in (None, 0) and req.new_limit is not None else req.amount
        amount = _operation_amount(amount_input, operation=operation)
        if operation in {"CARD_APPLY", "CARD_FREEZE", "CARD_UNFREEZE", "CARD_REPORT_LOST", "CARD_UNREPORT_LOST", "SUBSCRIPTION_QUERY", "SUBSCRIPTION_CANCEL", "PASSWORD_CHANGE", "FRAUD_PROTECT"} and amount_input not in (None, 0):
            raise SecurityError("该操作不接受金额字段", 400, "AMOUNT_NOT_ALLOWED")
        if operation in {"SCHEDULED_TRANSFER", "SPLIT_BILL", "WEALTH_PURCHASE", "WEALTH_REDEEM", "CARD_LIMIT"} and (amount_input is None or float(amount_input) <= 0):
            raise SecurityError("该操作需要明确的正数金额", 400, "AMOUNT_REQUIRED")
        if operation == "CARD_LIMIT":
            effective_limit = req.new_limit if req.new_limit is not None else req.amount
            if effective_limit is None or not math.isfinite(float(effective_limit)) or float(effective_limit) <= 0:
                raise SecurityError("卡片额度必须为正数", 400, "INVALID_CARD_LIMIT")
        if operation == "SCHEDULED_TRANSFER":
            if not req.target:
                raise SecurityError("预约转账缺少收款人", 400, "TARGET_REQUIRED")
            _resolve_target(req.target, from_user=req.user_id)
            if not req.scheduled_at:
                raise SecurityError("预约转账缺少执行时间", 400, "SCHEDULE_REQUIRED")
            # A scheduled transfer must remain a future task.  Normalize it
            # before signing so execution and scheduler storage use the same
            # canonical instant and cannot silently turn into an immediate
            # debit after approval.
            normalized_scheduled_at = _normalize_datetime(req.scheduled_at, field="计划时间", require_future=True)
        else:
            normalized_scheduled_at = req.scheduled_at
        validated_participants = _validate_split_participants(req.user_id, req.participants) if operation == "SPLIT_BILL" else list(req.participants or [])
        if operation == "SUBSCRIPTION_CANCEL" and not (req.target or req.merchant):
            raise SecurityError("取消订阅缺少商户或订阅编号", 400, "TARGET_REQUIRED")
        if operation == "SUBSCRIPTION_CANCEL":
            resolved_subscription_id = _resolve_subscription(req.user_id, req.target, req.merchant).get("subscription_id")
        if operation in {"WEALTH_PURCHASE", "WEALTH_REDEEM"} and not (req.product_id or req.target):
            raise SecurityError("理财操作缺少产品编号", 400, "PRODUCT_REQUIRED")
        if operation == "WEALTH_PURCHASE":
            if req.user_id not in WEALTH_RISK_PROFILES:
                raise SecurityError("申购前请先完成风险评估", 428, "RISK_ASSESSMENT_REQUIRED")
            product = _ensure_product_suitable(req.user_id, req.product_id or req.target or "")
            if amount < float(product["min_amount"]):
                raise SecurityError("申购金额低于产品起购金额", 400, "MIN_AMOUNT_NOT_MET")
            with STATE_LOCK:
                account = _ensure_account(req.user_id)
                if account["balance"] - account["locked_amount"] < amount:
                    raise SecurityError("可用余额不足以申购此金额", 400, "INSUFFICIENT_FUNDS")
        if operation == "WEALTH_COMPARE":
            product_ids = list(req.product_ids or [])
            if len(product_ids) < 2:
                raise SecurityError("产品对比至少需要两个产品编号", 400, "PRODUCTS_REQUIRED")
            if len(product_ids) > 10 or len(set(product_ids)) != len(product_ids):
                raise SecurityError("产品编号列表无效", 400, "INVALID_PRODUCTS")
            if any(not any(p["product_id"] == item for p in WEALTH_PRODUCTS) for item in product_ids):
                raise SecurityError("理财产品不存在", 404, "PRODUCT_NOT_FOUND")
        # Validate a product/card target before issuing a credential where
        # possible.  Unknown targets are rejected, never silently remapped.
        if operation == "WEALTH_REDEEM":
            product_id = req.product_id or req.target
            if not any(p["product_id"] == product_id for p in WEALTH_PRODUCTS):
                raise SecurityError("理财产品不存在", 404, "PRODUCT_NOT_FOUND")
            with STATE_LOCK:
                available_cents = sum(
                    max(0, int(round(float(holding.get("principal", 0)) * 100)))
                    for holding in WEALTH_HOLDINGS.get(req.user_id, [])
                    if holding.get("product_id") == product_id
                )
            if available_cents < amount_to_cents(amount):
                raise SecurityError("没有足够的持仓可赎回", 400, "HOLDING_NOT_FOUND")
        if operation.startswith("CARD_"):
            # CARD_APPLY opens a *new* card, so it is the one operation with no
            # card to resolve; everything else must name an unambiguous one.
            card = None if operation == "CARD_APPLY" else _select_card(req.user_id, req.card_id, allowed_states=_card_source_states(operation))
            if card:
                current_status = str(card.get("status") or "")
                # Same table the executor and the selector use, so a card can no
                # longer be validated against one copy of the rules and executed
                # against another.
                required_states = _card_source_states(operation)
                if required_states and current_status not in required_states:
                    raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
            if operation != "CARD_APPLY" and card:
                # Bind the signed approval to the concrete card even when the
                # conversational request omitted a card id.  This prevents a
                # card replacement between precheck and execution from
                # redirecting the action.
                target = req.card_id or card.get("card_id")
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="OPERATION_PRECHECK", request_id=request_id)
    # Risk calculation, nonce reservation and issuance share one lock. This
    # prevents concurrent scheduled/AA prechecks from both observing the same
    # daily total and receiving yellow approvals that collectively exceed the
    # threshold.
    with STATE_LOCK:
        risk_level = _operation_risk_level(req.user_id, operation, amount)
        required = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
        operation_context = _operation_context(
            operation=operation,
            target=target,
            amount=amount,
            reason=reason,
            new_limit=req.new_limit,
            merchant=req.merchant,
            participants=validated_participants,
            product_ids=req.product_ids,
            risk_profile=(WEALTH_RISK_PROFILES.get(req.user_id) or {}).get("risk_profile") if operation == "WEALTH_PURCHASE" else None,
            subscription_id=resolved_subscription_id,
            scheduled_at=normalized_scheduled_at,
            transaction_id=fraud_tx.get("tx_id") if fraud_tx else req.transaction_id,
            transaction_snapshot_hash=_context_hash(fraud_tx) if fraud_tx else req.transaction_snapshot_hash,
        )
        context_hash = _context_hash(operation_context)
        idem_payload = {"user_id": req.user_id, "operation": operation, "context": operation_context}
        cached = _idempotency_begin(actor, "operation_precheck", idempotency_key, idem_payload, request_id=request_id)
        if cached is not None:
            return cached
        token, payload = issue_auth_token(
            from_user=req.user_id,
            target_id=target,
            target_display=target,
            amount=amount,
            remark=reason,
            risk_level=risk_level,
            required_actions=required,
            operation=operation,
            context_hash=context_hash,
            session_token=_presented_session(request),
        )
    response = {"code": 200, "risk_level": risk_level, "operation": operation, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required, "mfa_required": risk_level == "RED", "target": target, "context_hash": context_hash}
    if fraud_tx:
        response["transaction"] = dict(fraud_tx)
        response["risk_reasons"] = ["境外交易", "交易类别标记为可疑交易", "用户明确否认该消费"]
    _idempotency_complete(actor, "operation_precheck", idempotency_key, idem_payload, response)
    audit_event("OPERATION_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"operation": operation, "jti": payload["jti"], "target": target})
    return response


def _login_otp_required() -> bool:
    """Whether a login must also present an SMS code.

    Optional by design: a password-only login is the demo default, and a
    deployment that wants the second factor turns it on here.
    """

    return os.getenv("BANK_LOGIN_REQUIRE_OTP", "0").lower() in {"1", "true", "yes", "on"}


@app.post("/api/v1/auth/login")
def login(req: LoginRequest, x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Authenticate a subject with a password, optionally plus an SMS code.

    Failures answer uniformly: an unknown account, a disabled account and a
    wrong password all return 401 ``INVALID_CREDENTIALS``, so the endpoint is not
    an account-enumeration oracle.  Every failure is attributed to the presented
    subject and counts towards that account's breaker, which blunts online
    guessing regardless of whether the account exists.
    """

    request_id = _request_id(x_request_id)
    principal = sanitize_text(req.user_id, field="用户标识", max_length=128)
    if not principal:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SUBJECT", "message": "缺少用户标识"})
    try:
        ensure_circuit_closed(principal)
    except SecurityError as exc:
        _raise_security(exc)

    account = ACCOUNTS.get(principal)
    if account is None or account.get("status") != "ACTIVE":
        _record_failure_and_raise(
            SecurityError("账号或口令不正确", 401, "INVALID_CREDENTIALS"),
            user_id=principal,
            action="LOGIN",
            request_id=request_id,
        )
    if not verify_password(req.password, account.get("password_hash")):
        _record_failure_and_raise(
            SecurityError("账号或口令不正确", 401, "INVALID_CREDENTIALS"),
            user_id=principal,
            action="LOGIN",
            request_id=request_id,
        )
    if _login_otp_required() and not verify_login_otp(req.otp):
        _record_failure_and_raise(
            SecurityError("短信验证码不正确", 401, "INVALID_LOGIN_OTP"),
            user_id=principal,
            action="LOGIN",
            request_id=request_id,
        )

    record_security_success(principal)
    result = issue_session_token(principal, request_id=request_id)
    audit_event(
        "LOGIN",
        user_id=principal,
        risk_level="GREEN",
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={"otp_required": _login_otp_required()},
    )
    return {"code": 200, **result}


@app.get("/api/v1/auth/session")
def resolve_session_endpoint(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """Resolve a presented session credential to its subject.

    A channel gateway must not keep its own copy of the session registry: a
    credential is only meaningful to the authority that issued it, and a second
    copy would drift - or worse, keep honouring a token the core has already
    dropped.  The gateway therefore asks this endpoint instead.

    The middleware has already validated the token and replaced ``X-User-Id``
    with its subject before the request reaches here, so an invalid or expired
    session never gets this far.
    """

    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "缺少会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    return {"code": 200, "user_id": x_user_id}


@app.post("/api/v1/auth/session/renew")
def renew_session_endpoint(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Hand a still-signed-in client a successor credential.

    The session window is short on purpose, but an absolute expiry signs out a
    user who is mid-form.  The middleware has already proved the presented
    token is live, so this can only ever slide an active session forward; an
    expired one is refused before it reaches here and must log in again.
    """

    request_id = _request_id(x_request_id)
    try:
        result = renew_session(x_session_token, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=x_user_id, action="SESSION_RENEW", request_id=request_id)
    return {"code": 200, **result}


@app.post("/api/v1/auth/logout")
def logout(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Revoke this login's credentials, including its renewed tokens.

    A client signing out must be able to kill the credential it holds on the
    server as well; wiping local storage only would leave a copied token valid
    until it expires. The scope is this login and its renewals; independent
    logins remain active. Credential rotation revokes every login instead.
    """

    request_id = _request_id(x_request_id)
    try:
        result = revoke_session(x_session_token, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=x_user_id, action="LOGOUT", request_id=request_id)
    return {"code": 200, "status": "LOGGED_OUT", "user_id": result["user_id"]}


# ---------------------------------------------------------------------------
# Device trust root: enrolling the key behind the red tier's biometric factor
# ---------------------------------------------------------------------------
@app.post("/api/v1/devices/register")
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

    request_id = _request_id(x_request_id)
    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "设备注册必须出示会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    try:
        _ensure_account(x_user_id)
        summary = enroll_device(
            req.public_key,
            user_id=x_user_id,
            label=req.label,
            platform=req.platform,
            app_version=req.app_version,
            request_id=request_id,
        )
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=x_user_id, action="DEVICE_REGISTER", request_id=request_id)
    return {"code": 200, **summary}


@app.get("/api/v1/devices")
def get_devices(x_session_token: Optional[str] = Header(None, alias="X-Session-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """List the devices enrolled to the caller.  No key material is returned.

    Demands a session credential like enrollment: the
    device registry is the red tier's trust root, so it must not be readable -
    or revocable, below - from a self-asserted identity header alone.
    """

    request_id = _request_id(x_request_id)
    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "设备查询必须出示会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    actor = _get_actor(x_user_id, x_user_id, action="DEVICE_QUERY", request_id=request_id)
    rows = list_devices(actor)
    audit_event("DEVICE_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@app.post("/api/v1/devices/{device_id}/revoke")
def revoke_registered_device(device_id: str, x_session_token: Optional[str] = Header(None, alias="X-Session-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Stop accepting attestations from one of the caller's own devices.

    A session credential is required for the same reason as enrollment: this
    endpoint controls the device trust root, and revoking a device is a
    security response, not a profile edit.
    """

    request_id = _request_id(x_request_id)
    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "设备吊销必须出示会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    actor = _get_actor(x_user_id, x_user_id, action="DEVICE_REVOKE", request_id=request_id)
    try:
        summary = revoke_device(device_id, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="DEVICE_REVOKE", request_id=request_id)
    return {"code": 200, **summary}


@app.post("/api/v1/auth/confirm")
@app.post("/api/v1/auth/confirmation")
@app.post("/api/v1/risk/confirm")
def confirm_operation(req: ConfirmationRequest, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    auth_token = req.auth_token or x_auth_token
    # Do not treat a body user id as an authenticated principal.  Until the
    # token subject and X-User-Id are verified, failures must be attributed only
    # to the presented header (or anonymous), otherwise this endpoint becomes
    # a cross-account circuit-breaker DoS primitive.
    actor = x_user_id
    try:
        if not auth_token:
            raise SecurityError("缺少安全授权凭证", 403, "TOKEN_REQUIRED")
        actor = _require_token_subject(auth_token, body_user=req.user_id, header_user=x_user_id, action="USER_CONFIRMATION", request_id=request_id)
        result = confirm_auth_token(auth_token, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="USER_CONFIRMATION", request_id=request_id)
    return {"code": 200, **result}


@app.post("/api/v1/risk/cancel")
def cancel_operation(
    req: ConfirmationRequest,
    request: Request,
    x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Cancel a nonce owned by the presented principal and original session."""

    request_id = _request_id(x_request_id)
    try:
        if not x_user_id:
            raise SecurityError("需要用户身份", 401, "AUTHENTICATION_REQUIRED")
        if req.user_id and req.user_id != x_user_id:
            raise SecurityError("请求用户与登录身份不一致", 403, "IDENTITY_MISMATCH")
        auth_token = req.auth_token or x_auth_token
        if not auth_token:
            raise SecurityError("缺少安全授权凭证", 403, "TOKEN_REQUIRED")
        result = cancel_auth_token(
            auth_token, user_id=x_user_id, presented_session=_presented_session(request),
            request_id=request_id,
        )
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=x_user_id, action="AUTH_TOKEN_CANCEL", request_id=request_id)
    return {"code": 200, **result}


@app.post("/api/v1/auth/mfa/verify")
@app.post("/api/v1/auth/mfa")
@app.post("/api/v1/risk/mfa/verify")
def mfa_verify(req: MFAVerifyRequest, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    otp = req.otp if req.otp is not None else req.sms_code
    auth_token = req.auth_token or x_auth_token
    # The request body is untrusted until the signed token/header binding has
    # succeeded; see ``confirm_operation`` above.
    actor = x_user_id
    try:
        if not auth_token:
            raise SecurityError("缺少安全授权凭证", 403, "TOKEN_REQUIRED")
        actor = _require_token_subject(auth_token, body_user=req.user_id, header_user=x_user_id, action="MFA_VERIFY", request_id=request_id)
        result = verify_mfa(auth_token, otp=otp, face_attestation=req.face_attestation, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="MFA_VERIFY", request_id=request_id)
    return {"code": 200, **result}


@app.post("/api/v1/auth/mfa/otp")
def mfa_demo_otp(req: FaceChallengeRequest, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    try:
        actor = _require_token_subject(req.auth_token, body_user=req.user_id, header_user=x_user_id, action="DEMO_OTP", request_id=request_id)
        return {"code": 200, **issue_demo_otp(req.auth_token, user_id=actor, request_id=request_id)}
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=x_user_id, action="DEMO_OTP", request_id=request_id)


@app.post("/api/v1/auth/mfa/face/challenge")
@app.post("/api/v1/risk/mfa/face/challenge")
def mfa_face_challenge(req: FaceChallengeRequest, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Issue a liveness challenge the demo biometric SDK must sign."""

    request_id = _request_id(x_request_id)
    actor = x_user_id
    try:
        actor = _require_token_subject(req.auth_token, body_user=req.user_id, header_user=x_user_id, action="FACE_CHALLENGE", request_id=request_id)
        result = issue_face_challenge(req.auth_token, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="FACE_CHALLENGE", request_id=request_id)
    return {"code": 200, **result}


# ---------------------------------------------------------------------------
# Yellow/red execution gates
# ---------------------------------------------------------------------------
@app.post("/api/v1/transfers/execute")
def execute_transfer(req: TransferExecuteRequest, request: Request = None, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"), x_user_confirmation: Optional[str] = Header(None, alias="X-User-Confirmation"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.from_user, x_user_id, action="TRANSFER_EXECUTE", request_id=request_id)
    token = x_auth_token
    # Do not trust a boolean supplied by an agent/browser.  Only
    # ``/risk/confirm`` (which validates the signed nonce) can set the
    # server-side ``record.confirmed`` bit.  The header is retained solely for
    # correlation/legacy clients and is deliberately ignored for authorization.
    confirmation = False
    proof = req.mfa_proof or x_mfa_proof
    idem_payload = {"from_user": req.from_user, "target": req.target_name_or_id, "amount": req.amount, "remark": req.remark or "智能转账", "card_id": req.card_id}
    cached = _idempotency_begin(actor, "transfer_execute", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        remark = sanitize_text(req.remark or "智能转账", field="转账备注", max_length=200)
        if not token:
            raise SecurityError("缺少安全授权凭证，严禁直接执行", 403, "TOKEN_REQUIRED")
        with _ledger_transaction():
            # Rollback restores ledger records with fresh objects. Resolve
            # both accounts under the transaction lock so no failed parallel
            # request can leave this settlement holding a detached payee.
            _ensure_account(req.from_user)
            target_id, receiver = _resolve_target(req.target_name_or_id, from_user=req.from_user)
            with authorize_execution(token, from_user=req.from_user, target_id=target_id, amount=req.amount, remark=remark, confirmation=confirmation, mfa_proof=proof, operation="TRANSFER", presented_session=_presented_session(request), request_id=request_id) as (payload, _record):
                sender = ACCOUNTS[req.from_user]
                amount = amount_to_cents(req.amount) / 100.0
                # The paying card's status and daily limit are enforced on
                # settlement, and the same card records the spend.
                paying_card = _charge_card_and_balance(req.from_user, amount, card_id=req.card_id)
                available = sender["balance"] - sender["locked_amount"]
                if available < amount:
                    raise SecurityError("可用余额不足（部分资金已被锁定）", 400, "INSUFFICIENT_FUNDS")
                sender["balance"] = round(sender["balance"] - amount, 2)
                receiver["balance"] = round(receiver["balance"] + amount, 2)
                DAILY_TRANSFERS[req.from_user] = round(_daily_spent(req.from_user) + amount, 2)
                _record_card_spend(req.from_user, amount, card=paying_card)
                if int(round(amount * 100)) >= behavior_large_amount_cents():
                    record_behavior_signal(req.from_user, "LARGE_TRANSFER", metadata={"amount_cents": int(round(amount * 100)), "target_id": target_id}, request_id=request_id)
                txn_id = f"TXN{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"
                TRANSACTIONS.append({"tx_id": txn_id, "account_id": req.from_user, "merchant": f"转账给 {receiver['name']}", "category": "转账支出", "amount": amount, "remark": remark, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                split_id = _split_id_from_remark(remark)
                if split_id:
                    _record_split_payment(split_id=split_id, payer_id=req.from_user, recipient_id=target_id, amount=amount, transaction_id=txn_id)
                    TRANSACTIONS.append({"tx_id": f"TXN{uuid.uuid4().hex[:12].upper()}", "account_id": target_id, "merchant": f"AA 收款 - {sender['name']}", "category": "AA 收款", "amount": amount, "remark": remark, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    enqueue_notification(f"AA_PAID:{split_id}:{req.from_user}", owner_id=target_id, recipient_id=req.from_user, split_id=split_id, amount=amount, outcome="COLLECTED", message=f"AA 份额已支付 {amount:.2f} 元")
                risk_level = payload.get("risk_level")
        record_security_success(actor)
    except SecurityError as exc:
        _idempotency_abort(actor, "transfer_execute", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="TRANSFER_EXECUTE", request_id=request_id)
    except Exception as exc:
        _idempotency_abort(actor, "transfer_execute", idempotency_key)
        audit_event("TRANSFER_EXECUTE", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "核心记账失败，请稍后重试"}) from exc
    audit_event("TRANSFER_EXECUTE", user_id=actor, risk_level=risk_level, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"transaction_id": txn_id, "target_id": target_id, "amount_cents": int(round(amount * 100))})
    response = {"code": 200, "status": "SUCCESS", "transaction_id": txn_id, "transferred_amount": amount, "sender_balance": ACCOUNTS[req.from_user]["balance"], "receiver_name": receiver["name"], "risk_level": risk_level}
    _idempotency_complete(actor, "transfer_execute", idempotency_key, idem_payload, response)
    persist_ledger()
    return response


@app.post("/api/v1/accounts/lock_fund")
def lock_fund(req: LockFundRequest, request: Request = None, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"), x_user_confirmation: Optional[str] = Header(None, alias="X-User-Confirmation"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="LOCK_FUND", request_id=request_id)
    # ``user_confirmed`` and ``X-User-Confirmation`` are advisory input only;
    # authorization_execution checks the confirmation bit set by the signed
    # nonce confirmation endpoint.
    confirmation = False
    token = x_auth_token or req.auth_token
    proof = x_mfa_proof or req.mfa_proof
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "reason": req.reason, "event_at": req.event_at, "reminder_at": req.reminder_at}
    cached = _idempotency_begin(actor, "lock_fund", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        reason = sanitize_text(req.reason, field="锁定原因", max_length=300)
        event_at, reminder_at = _birthday_times(req.event_at, req.reminder_at)
        if not token:
            raise SecurityError("资金锁定需要先完成风险预检并确认", 403, "TOKEN_REQUIRED")
        with _ledger_transaction():
            account = _ensure_account(req.user_id)
            context_hash = _context_hash({"event_at": event_at, "reminder_at": reminder_at})
            with authorize_execution(token, from_user=req.user_id, target_id="__LOCK_FUND__", amount=req.amount, remark=reason, confirmation=confirmation, mfa_proof=proof, operation="LOCK_FUND", context_hash=context_hash, presented_session=_presented_session(request), request_id=request_id) as (_payload, _record):
                amount = amount_to_cents(req.amount) / 100.0
                if account["balance"] - account["locked_amount"] < amount:
                    raise SecurityError("可用余额不足以锁定此金额", 400, "INSUFFICIENT_FUNDS")
                account["locked_amount"] = round(account["locked_amount"] + amount, 2)
                remaining = round(account["balance"] - account["locked_amount"], 2)
                lock_id = f"LCK{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
                LOCKS[lock_id] = {"lock_id": lock_id, "user_id": req.user_id, "amount": amount, "reason": reason, "event_at": event_at, "reminder_at": reminder_at, "status": "LOCKED", "created_at": datetime.now().isoformat()}
                task = TASK_STORE.create(actor, "FUND_RESERVATION", {"lock_id": lock_id, "amount": amount, "reason": reason, "event_at": event_at, "reminder_at": reminder_at}, compensation="UNLOCK_FUND", idempotency_key=f"lock:{lock_id}")
                TASK_STORE.transition(task.task_id, TaskState.SUCCEEDED, owner_id=actor)
                reminder_task = None
                order_task_ids: list[str] = []
                if event_at and reminder_at:
                    reminder_task = TASK_STORE.create(actor, "REMINDER", {"lock_id": lock_id, "message": "生日前 2 天提醒订购鲜花与蛋糕", "event_at": event_at, "reminder_at": reminder_at}, run_at=reminder_at, depends_on=[task.task_id], idempotency_key=f"reminder:{lock_id}")
                    # Orders wait for a fresh approval at the due time. Only
                    # an allow-listed local merchant can receive mock funds.
                    for item in ("鲜花", "蛋糕"):
                        order_task = TASK_STORE.create(
                            actor,
                            "ORDER_DRAFT",
                            {"lock_id": lock_id, "item": item, "amount": quote_cents(item) / 100, "event_at": event_at, "reminder_at": reminder_at},
                            run_at=reminder_at,
                            depends_on=[task.task_id],
                            compensation="CANCEL_ORDER_DRAFT",
                            idempotency_key=f"order-draft:{lock_id}:{item}",
                        )
                        order_task_ids.append(order_task.task_id)
        if int(round(amount * 100)) >= behavior_large_amount_cents():
            record_behavior_signal(req.user_id, "LARGE_LOCK", metadata={"amount_cents": int(round(amount * 100)), "lock_id": lock_id}, request_id=request_id)
        record_security_success(actor)
    except SecurityError as exc:
        _idempotency_abort(actor, "lock_fund", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="LOCK_FUND", request_id=request_id)
    except Exception as exc:
        _idempotency_abort(actor, "lock_fund", idempotency_key)
        audit_event("LOCK_FUND", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "核心记账失败，请稍后重试"}) from exc
    audit_event("LOCK_FUND", user_id=actor, risk_level=_payload.get("risk_level", "YELLOW"), decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"amount_cents": int(round(amount * 100)), "reason": reason, "lock_id": lock_id})
    response = {"code": 200, "status": "LOCKED", "lock_id": lock_id, "task_id": task.task_id, "reminder_task_id": reminder_task.task_id if reminder_task else None, "order_task_ids": order_task_ids, "event_at": event_at, "reminder_at": reminder_at, "locked_amount": account["locked_amount"], "remaining_available": remaining, "reason": reason}
    _idempotency_complete(actor, "lock_fund", idempotency_key, idem_payload, response)
    persist_ledger()
    return response


@app.post("/api/v1/accounts/unlock_fund")
@app.post("/api/v1/accounts/rollback_lock")
def unlock_fund(req: UnlockFundRequest, request: Request = None, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    """Release reserved funds through the same confirmation gate.

    A lock id, when supplied, must belong to the authenticated user.  Without
    one, the oldest matching locks are released FIFO; the signed precheck
    token binds the amount and reason.
    """
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="UNLOCK_FUND", request_id=request_id)
    token = x_auth_token or req.auth_token
    proof = x_mfa_proof or req.mfa_proof
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "lock_id": req.lock_id, "reason": req.reason}
    cached = _idempotency_begin(actor, "unlock_fund", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        reason = sanitize_text(req.reason or "解除资金隔离", field="解除原因", max_length=300)
        if not token:
            raise SecurityError("解除资金隔离需要先完成风险预检并确认", 403, "TOKEN_REQUIRED")
        lock_id = sanitize_text(req.lock_id, field="锁定编号", max_length=100) if req.lock_id else None
        target = lock_id or "__UNLOCK_FUND__"
        with _ledger_transaction():
            account = _ensure_account(req.user_id)
            candidates = sorted(
                [v for v in LOCKS.values() if v.get("user_id") == req.user_id and v.get("status") == "LOCKED" and (not lock_id or v.get("lock_id") == lock_id)],
                key=lambda row: row.get("created_at", ""),
            )
            requested_amount = amount_to_cents(req.amount) / 100.0
            if lock_id and (not candidates or float(candidates[0].get("amount", 0)) < requested_amount):
                raise SecurityError("锁定编号或可解除金额不匹配", 400, "LOCK_NOT_FOUND")
            if not lock_id and sum(float(v.get("amount", 0)) for v in candidates) < requested_amount:
                raise SecurityError("可解除的锁定金额不足", 400, "LOCK_NOT_FOUND")
            with authorize_execution(token, from_user=req.user_id, target_id=target, amount=req.amount, remark=reason, confirmation=False, mfa_proof=proof, operation="UNLOCK_FUND", presented_session=_presented_session(request), request_id=request_id) as (_payload, _record):
                left = requested_amount
                released_ids: list[str] = []
                for row in candidates:
                    if left <= 0:
                        break
                    release = min(left, float(row["amount"]))
                    row["amount"] = round(float(row["amount"]) - release, 2)
                    left = round(left - release, 2)
                    released_ids.append(row["lock_id"])
                    if row["amount"] <= 0:
                        row["status"] = "RELEASED"
                        row["released_at"] = datetime.now().isoformat()
                account["locked_amount"] = round(max(0.0, float(account["locked_amount"]) - requested_amount), 2)
                remaining = round(float(account["balance"]) - float(account["locked_amount"]), 2)
                released_set = set(released_ids)
                for queued in TASK_STORE.list(owner_id=actor):
                    if queued.payload.get("lock_id") in released_set and queued.status in {TaskState.PENDING, TaskState.WAITING_USER}:
                        try:
                            TASK_STORE.transition(queued.task_id, TaskState.CANCELLED, owner_id=actor)
                        except (KeyError, ValueError):
                            pass
        record_security_success(actor)
    except SecurityError as exc:
        _idempotency_abort(actor, "unlock_fund", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="UNLOCK_FUND", request_id=request_id)
    except Exception as exc:
        _idempotency_abort(actor, "unlock_fund", idempotency_key)
        audit_event("UNLOCK_FUND", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "解除资金失败，请稍后重试"}) from exc
    audit_event("UNLOCK_FUND", user_id=actor, risk_level=_payload.get("risk_level", "YELLOW"), decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"amount_cents": int(round(requested_amount * 100)), "lock_ids": released_ids})
    response = {"code": 200, "status": "RELEASED", "released_amount": requested_amount, "lock_ids": released_ids, "locked_amount": account["locked_amount"], "remaining_available": remaining}
    _idempotency_complete(actor, "unlock_fund", idempotency_key, idem_payload, response)
    persist_ledger()
    return response


@app.post("/api/v1/operations/execute")
@app.post("/api/v1/transfers/schedule")
@app.post("/api/v1/transfers/split")
@app.post("/api/v1/subscriptions/cancel")
@app.post("/api/v1/cards/apply")
@app.post("/api/v1/cards/limit")
@app.post("/api/v1/cards/freeze")
@app.post("/api/v1/cards/unfreeze")
@app.post("/api/v1/cards/report-lost")
@app.post("/api/v1/cards/unreport-lost")
@app.post("/api/v1/wealth/purchase")
@app.post("/api/v1/wealth/redeem")
@app.post("/api/v1/auth/password/change")
def execute_operation(
    req: OperationExecuteRequest,
    x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"),
    x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user_id: Optional[str] = None,
    request: Request = None,
):
    """Execute an allow-listed card/wealth/subscription/task operation.

    A single endpoint keeps the policy boundary uniform.  Convenience routes
    in clients may map to this endpoint, but no operation can bypass the
    signed nonce and (for red actions) the MFA proof.
    """

    request_id = _request_id(x_request_id)
    if user_id and user_id != req.user_id:
        raise HTTPException(status_code=403, detail={"code": "IDENTITY_MISMATCH", "message": "路径用户与请求用户不一致"})
    if request is not None:
        path_operation = {
            "/cards/apply": "CARD_APPLY",
            "/cards/limit": "CARD_LIMIT",
            "/cards/freeze": "CARD_FREEZE",
            "/cards/unfreeze": "CARD_UNFREEZE",
            "/cards/report-lost": "CARD_REPORT_LOST",
            "/cards/unreport-lost": "CARD_UNREPORT_LOST",
            "/wealth/purchase": "WEALTH_PURCHASE",
            "/wealth/redeem": "WEALTH_REDEEM",
            "/auth/password/change": "PASSWORD_CHANGE",
            "/transfers/schedule": "SCHEDULED_TRANSFER",
            "/transfers/split": "SPLIT_BILL",
            "/subscriptions/cancel": "SUBSCRIPTION_CANCEL",
        }
        matched_operation = None
        for suffix, operation_name in path_operation.items():
            if request.url.path.endswith(suffix):
                matched_operation = operation_name
                break
        if matched_operation:
            supplied_operation = str(req.operation or "").strip().upper()
            if supplied_operation and supplied_operation != matched_operation:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "OPERATION_ROUTE_MISMATCH", "message": "请求路径与操作类型不一致"},
                )
            req.operation = matched_operation
    actor = _get_actor(req.user_id, x_user_id, action="OPERATION_EXECUTE", request_id=request_id)
    # Credential fields authorize the first execution, not a read of its
    # completed receipt. Never store credential material in replay metadata.
    submitted_intent = req.model_dump(exclude={"auth_token", "mfa_proof", "user_confirmed", "new_password"})
    submitted_intent["operation"] = str(req.operation or "").strip().upper()
    # Password rotation retains its existing authorization/context path; the
    # new submitted-intent replay shortcut must not equate different secrets.
    request_fingerprint = None if submitted_intent["operation"] == "PASSWORD_CHANGE" else _idempotency_fingerprint(submitted_intent)
    if request_fingerprint is not None:
        cached = _idempotency_completed_request(actor, "operation_execute", idempotency_key, request_fingerprint, request_id=request_id)
        if cached is not None:
            return cached
    token = x_auth_token or req.auth_token
    proof = x_mfa_proof or req.mfa_proof
    operation_risk = GENERIC_OPERATION_RISK.get(str(req.operation or "").upper(), "UNKNOWN")
    resolved_subscription_id: Optional[str] = None
    fraud_tx: Optional[dict[str, Any]] = None
    try:
        operation = _validate_operation_name(req.operation)
        _ensure_account(req.user_id)
        target = _operation_target(req)
        if operation == "FRAUD_PROTECT":
            fraud_tx = _fraud_transaction(req.user_id, req.transaction_id)
            target = str(fraud_tx["card_id"])
            if req.transaction_snapshot_hash and req.transaction_snapshot_hash != _context_hash(fraud_tx):
                raise SecurityError("交易信息已变化，请重新预检", 409, "TRANSACTION_CONTEXT_CHANGED")
        if operation == "PASSWORD_CHANGE" and any(value for value in (req.target, req.product_id, req.card_id, req.merchant)):
            raise SecurityError("密码修改不接受外部目标编号", 400, "TARGET_NOT_ALLOWED")
        amount_input = req.new_limit if operation == "CARD_LIMIT" and req.amount in (None, 0) and req.new_limit is not None else req.amount
        amount = _operation_amount(amount_input, operation=operation)
        reason = sanitize_text(req.reason or ("客户否认境外消费，申请反诈保护" if operation == "FRAUD_PROTECT" else "智能银行操作"), field="操作原因", max_length=300)
        if operation in {"CARD_APPLY", "CARD_FREEZE", "CARD_UNFREEZE", "CARD_REPORT_LOST", "CARD_UNREPORT_LOST", "SUBSCRIPTION_QUERY", "SUBSCRIPTION_CANCEL", "PASSWORD_CHANGE", "FRAUD_PROTECT"} and amount_input not in (None, 0):
            raise SecurityError("该操作不接受金额字段", 400, "AMOUNT_NOT_ALLOWED")
        if operation == "CARD_LIMIT":
            effective_limit = req.new_limit if req.new_limit is not None else req.amount
            if effective_limit is None or not math.isfinite(float(effective_limit)) or float(effective_limit) <= 0:
                raise SecurityError("卡片额度必须为正数", 400, "INVALID_CARD_LIMIT")
        if operation == "WEALTH_PURCHASE" and req.user_id not in WEALTH_RISK_PROFILES:
            raise SecurityError("申购前请先完成风险评估", 428, "RISK_ASSESSMENT_REQUIRED")
        if operation.startswith("CARD_"):
            # CARD_APPLY opens a *new* card, so it is the one operation with no
            # card to resolve; everything else must name an unambiguous one.
            card = None if operation == "CARD_APPLY" else _select_card(req.user_id, req.card_id, allowed_states=_card_source_states(operation))
            if card:
                current_status = str(card.get("status") or "")
                # Same table the executor and the selector use, so a card can no
                # longer be validated against one copy of the rules and executed
                # against another.
                required_states = _card_source_states(operation)
                if required_states and current_status not in required_states:
                    raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
            if operation != "CARD_APPLY" and card:
                target = req.card_id or card.get("card_id")
        if not token:
            raise SecurityError("该操作需要先完成风险预检", 403, "TOKEN_REQUIRED")
        validated_participants = _validate_split_participants(req.user_id, req.participants) if operation == "SPLIT_BILL" else list(req.participants or [])
        if operation == "SUBSCRIPTION_CANCEL":
            resolved_subscription_id = _resolve_subscription(req.user_id, req.target, req.merchant).get("subscription_id")
        operation_context = _operation_context(
            operation=operation,
            target=target,
            amount=amount,
            reason=reason,
            new_limit=req.new_limit,
            merchant=req.merchant,
            participants=validated_participants,
            product_ids=req.product_ids,
            risk_profile=(WEALTH_RISK_PROFILES.get(req.user_id) or {}).get("risk_profile") if operation == "WEALTH_PURCHASE" else None,
            subscription_id=resolved_subscription_id,
            scheduled_at=_normalize_datetime(req.scheduled_at, field="计划时间", require_future=True) if operation == "SCHEDULED_TRANSFER" else req.scheduled_at,
            transaction_id=fraud_tx.get("tx_id") if fraud_tx else req.transaction_id,
            transaction_snapshot_hash=_context_hash(fraud_tx) if fraud_tx else req.transaction_snapshot_hash,
        )
        context_hash = _context_hash(operation_context)
        idem_payload = {"user_id": req.user_id, "operation": operation, "context": operation_context}
        cached = _idempotency_begin(actor, "operation_execute", idempotency_key, idem_payload, request_id=request_id)
        if cached is not None:
            return cached
        with _ledger_transaction():
            with authorize_execution(token, from_user=req.user_id, target_id=target, amount=amount, remark=reason, confirmation=False, mfa_proof=proof, operation=operation, context_hash=context_hash, presented_session=_presented_session(request), request_id=request_id) as (authorization_payload, _record):
                operation_risk = authorization_payload.get("risk_level", operation_risk)
                account = ACCOUNTS[req.user_id]
                result: dict[str, Any] = {"operation": operation}
                if operation in {"WEALTH_PURCHASE", "WEALTH_REDEEM"}:
                    product_id = req.product_id or target
                    product = _ensure_product_suitable(req.user_id, product_id) if operation == "WEALTH_PURCHASE" else next((p for p in WEALTH_PRODUCTS if p["product_id"] == product_id), None)
                    if not product:
                        raise SecurityError("理财产品不存在", 404, "PRODUCT_NOT_FOUND")
                    if operation == "WEALTH_PURCHASE":
                        if amount < float(product["min_amount"]):
                            raise SecurityError("申购金额低于产品起购金额", 400, "MIN_AMOUNT_NOT_MET")
                        if account["balance"] - account["locked_amount"] < amount:
                            raise SecurityError("可用余额不足", 400, "INSUFFICIENT_FUNDS")
                        # A purchase is money leaving the account, so the card's
                        # daily payment limit applies here too.  Enforcing it
                        # only on transfers left an obvious second outflow that
                        # ignored the limit entirely.
                        paying_card = _charge_card_and_balance(req.user_id, amount, card_id=req.card_id)
                        account["balance"] = round(account["balance"] - amount, 2)
                        _record_card_spend(req.user_id, amount, card=paying_card)
                        holding = {"holding_id": f"HLD{uuid.uuid4().hex[:10].upper()}", "product_id": product_id, "product_name": product["name"], "principal": amount, "estimated_value": amount, "purchased_at": datetime.now().isoformat()}
                        WEALTH_HOLDINGS.setdefault(req.user_id, []).append(holding)
                        TRANSACTIONS.append({"tx_id": f"TXN{uuid.uuid4().hex[:12].upper()}", "account_id": req.user_id, "merchant": product["name"], "category": "理财申购", "amount": amount, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                        result.update({"status": "PURCHASED", "product": product, "holding": holding})
                    else:
                        holdings = WEALTH_HOLDINGS.setdefault(req.user_id, [])
                        # A product can have several purchase lots. Redeem the
                        # requested total across them in their existing order,
                        # atomically and in cents, without changing other products.
                        lots = [
                            (holding, int(round(float(holding.get("principal", 0)) * 100)))
                            for holding in holdings
                            if holding.get("product_id") == product_id and float(holding.get("principal", 0)) > 0
                        ]
                        remaining_cents = amount_to_cents(amount)
                        if sum(cents for _, cents in lots) < remaining_cents:
                            raise SecurityError("没有足够的持仓可赎回", 400, "HOLDING_NOT_FOUND")
                        for holding, principal_cents in lots:
                            if remaining_cents == 0:
                                break
                            redeemed_cents = min(principal_cents, remaining_cents)
                            residual_cents = principal_cents - redeemed_cents
                            if residual_cents == 0:
                                holdings.remove(holding)
                            else:
                                holding["principal"] = residual_cents / 100.0
                                if "estimated_value" in holding:
                                    holding["estimated_value"] = round(float(holding["estimated_value"]) * residual_cents / principal_cents, 2)
                            remaining_cents -= redeemed_cents
                        account["balance"] = round(account["balance"] + amount, 2)
                        result.update({"status": "REDEEMED", "product": product, "redeemed_amount": amount})
                elif operation == "CARD_APPLY":
                    # Same issuance path (and the same per-user ceiling) as the
                    # convenience endpoint - asking the assistant must not be a
                    # way around the wallet cap.
                    card = _issue_card_locked(req.user_id, card_type="VIRTUAL")
                    result.update({"status": "ISSUED", "card": card})
                elif operation == "CARD_LIMIT":
                    # ``amount`` is already normalized to cents before the
                    # authorization context is checked.  Persist that same
                    # canonical value so a sub-cent input cannot be approved
                    # as one value and stored with a different float rounding.
                    new_limit = amount
                    if not math.isfinite(float(new_limit)) or float(new_limit) <= 0:
                        raise SecurityError("卡片额度必须为正数", 400, "INVALID_CARD_LIMIT")
                    if float(new_limit) > CARD_MAX_DAILY_LIMIT:
                        raise SecurityError(
                            f"卡片日限额不得超过 {CARD_MAX_DAILY_LIMIT:.0f} 元",
                            400,
                            "CARD_LIMIT_OUT_OF_RANGE",
                        )
                    card = _select_card(req.user_id, req.card_id)
                    card["daily_limit"] = round(float(new_limit), 2)
                    result.update({"status": "LIMIT_UPDATED", "card": card})
                elif operation == "FRAUD_PROTECT":
                    tx = _fraud_transaction(req.user_id, operation_context.get("transaction_id"))
                    if operation_context.get("transaction_snapshot_hash") != _context_hash(tx) or target != tx.get("card_id"):
                        raise SecurityError("交易或卡片信息已变化，请重新预检", 409, "FRAUD_CONTEXT_CHANGED")
                    card = _select_card(req.user_id, str(tx["card_id"]), allowed_states={"ACTIVE", "FROZEN"})
                    if card.get("status") == "ACTIVE":
                        card["status"] = "FROZEN"
                    case = next((item for item in DISPUTE_CASES if item.get("owner_id") == req.user_id and item.get("transaction_id") == tx.get("tx_id") and item.get("status") in {"OPEN", "PENDING_REVIEW"}), None)
                    if case is None:
                        created_at = datetime.now(timezone.utc).isoformat()
                        case = {"case_id": f"CASE-{uuid.uuid4().hex[:10].upper()}", "owner_id": req.user_id, "transaction_id": tx["tx_id"], "transaction_snapshot_hash": operation_context["transaction_snapshot_hash"], "card_id": card["card_id"], "status": "PENDING_REVIEW", "protection_status": "CARD_PROTECTED", "statement": reason, "risk_reasons": ["境外交易", "交易类别标记为可疑交易", "用户明确否认该消费"], "created_at": created_at, "updated_at": created_at}
                        DISPUTE_CASES.append(case)
                    result.update({"status": "CARD_PROTECTED", "card": card, "dispute_case": case, "transaction": tx})
                elif operation in {"CARD_FREEZE", "CARD_UNFREEZE", "CARD_REPORT_LOST", "CARD_UNREPORT_LOST"}:
                    card = _select_card(req.user_id, req.card_id)
                    current_status = str(card.get("status") or "")
                    next_status = CARD_TRANSITIONS[operation].get(current_status)
                    if not next_status:
                        raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
                    card["status"] = next_status
                    result.update({"status": next_status, "card": card})
                elif operation == "PASSWORD_CHANGE":
                    # Now that logins are password-based, "change my password"
                    # has to actually change it - otherwise the old credential
                    # keeps working while the audit trail claims a rotation.
                    # The new password is NOT bound into the signed approval
                    # context on purpose: that context is serialized into the
                    # authorization nonce the client holds, and credential
                    # material has no business inside a bearer token.  What
                    # authorizes the change is the single-use, MFA-gated nonce.
                    if not req.new_password:
                        raise SecurityError("修改密码需要提供新口令", 400, "NEW_PASSWORD_REQUIRED")
                    account["password_hash"] = hash_password(req.new_password)
                    current_version = int(account.get("credential_version", 1))
                    account["credential_version"] = current_version + 1
                    account["credential_rotated_at"] = datetime.now(timezone.utc).isoformat()
                    # A rotated credential must invalidate the sessions it was
                    # used to obtain - including this caller's, which is why the
                    # client is expected to re-login afterwards.
                    revoked = revoke_sessions_for(req.user_id, reason="password_changed", request_id=request_id)
                    result.update({"status": "PASSWORD_CHANGED", "credential_version": account["credential_version"], "revoked_sessions": revoked})
                elif operation == "SUBSCRIPTION_CANCEL":
                    subscriptions = SUBSCRIPTIONS.setdefault(req.user_id, [])
                    selected = _resolve_subscription(req.user_id, req.target, req.merchant)
                    if operation_context.get("subscription_id") and selected.get("subscription_id") != operation_context.get("subscription_id"):
                        raise SecurityError("订阅状态在确认后发生变化，请重新预检", 409, "SUBSCRIPTION_CONTEXT_CHANGED")
                    selected["status"] = "CANCELLED"
                    # A cancelled subscription must not keep nagging: withdraw
                    # its pending renewal reminders as part of the same step.
                    for queued in TASK_STORE.list(owner_id=actor):
                        if queued.task_type != "RENEWAL_REMINDER" or queued.payload.get("subscription_id") != selected.get("subscription_id"):
                            continue
                        if queued.status in {TaskState.PENDING, TaskState.WAITING_USER}:
                            try:
                                TASK_STORE.transition(queued.task_id, TaskState.CANCELLED, owner_id=actor)
                            except (KeyError, ValueError):
                                pass
                    result.update({"status": "CANCELLED", "subscription": selected})
                elif operation == "SUBSCRIPTION_QUERY":
                    result.update(_subscription_query_result(req.user_id))
                elif operation == "SCHEDULED_TRANSFER":
                    scheduled_at = operation_context.get("scheduled_at")
                    schedule = {"schedule_id": f"SCH{uuid.uuid4().hex[:10].upper()}", "user_id": req.user_id, "target": req.target, "amount": amount, "card_id": req.card_id, "scheduled_at": scheduled_at, "status": "SCHEDULED"}
                    SCHEDULED_TRANSFERS.append(schedule)
                    # A signed nonce identifies one approved intent.  Use its
                    # jti for the internal task key so two deliberate,
                    # identical schedules are independent unless the caller
                    # supplies an HTTP Idempotency-Key; reusing only the
                    # context digest would alias distinct approvals and leave
                    # duplicate domain rows pointing at one task.
                    task = TASK_STORE.create(actor, "SCHEDULED_TRANSFER", schedule, run_at=scheduled_at, idempotency_key=f"schedule:{req.user_id}:{authorization_payload.get('jti')}")
                    result.update(schedule, task_id=task.task_id)
                elif operation == "SPLIT_BILL":
                    participants = list(operation_context.get("participants") or [])
                    # The initiator creates requests only. Each participant
                    # must authorize an ordinary transfer from their account.
                    collection = _prepare_split_shares(req.user_id, amount, participants, req.target)
                    split = {
                        "split_id": f"SPL{uuid.uuid4().hex[:10].upper()}",
                        "user_id": req.user_id,
                        "amount": amount,
                        "participants": participants,
                        "per_person": collection["participant_share"],
                        "initiator_share": collection["initiator_share"],
                        "participants_total": len(participants),
                        "target": req.target,
                        "status": collection["status"],
                        "collected_amount": collection["collected_amount"],
                        "outstanding_amount": collection["outstanding_amount"],
                        "collection": collection["collection"],
                        "created_at": collection["created_at"],
                    }
                    SPLIT_REQUESTS.append(split)
                    # Scope the task to this signed approval.  In particular,
                    # two AA requests for the same total but different
                    # participants must never share one task.
                    task = TASK_STORE.create(actor, "SPLIT_BILL", split, depends_on=[], idempotency_key=f"split:{req.user_id}:{authorization_payload.get('jti')}")
                    for item in collection["collection"]:
                        outcome = str(item.get("status") or "UNKNOWN")
                        enqueue_notification(f"AA:{split['split_id']}:{item['account_id']}", owner_id=actor, recipient_id=item["account_id"], split_id=split["split_id"], amount=item["share"], outcome=outcome, message=f"AA 待付款 {item['share']:.2f} 元；请用本人账户确认转账，备注 AA:{split['split_id']}")
                    result.update(split, task_id=task.task_id)
                else:  # guarded by _validate_operation_name; defensive fallback
                    raise SecurityError("操作未实现", 501, "OPERATION_NOT_IMPLEMENTED")
        behavior_signal = _BEHAVIOR_OPERATION_SIGNALS.get(operation)
        if behavior_signal:
            record_behavior_signal(actor, behavior_signal, metadata={"operation": operation}, request_id=request_id)
        record_security_success(actor)
    except SecurityError as exc:
        if 'idem_payload' in locals():
            _idempotency_abort(actor, "operation_execute", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="OPERATION_EXECUTE", request_id=request_id)
    except Exception as exc:
        if 'idem_payload' in locals():
            _idempotency_abort(actor, "operation_execute", idempotency_key)
        audit_event("OPERATION_EXECUTE", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "操作执行失败，请稍后重试"}) from exc
    response = {"code": 200, "status": "SUCCESS", "operation": operation, "data": result}
    audit_event("OPERATION_EXECUTE", user_id=actor, risk_level=operation_risk, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"operation": operation, "target": target})
    _idempotency_complete(actor, "operation_execute", idempotency_key, idem_payload, response, request_fingerprint=request_fingerprint)
    persist_ledger()
    return response


# ---------------------------------------------------------------------------
# Task centre: scheduling, cancellation and human takeover
# ---------------------------------------------------------------------------
@app.get("/api/v1/splits/inbox")
def split_inbox(x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    actor = _get_actor(x_user_id, x_user_id, action="SPLIT_INBOX", request_id=_request_id(x_request_id))
    with STATE_LOCK:
        rows = [
            {"split_id": split["split_id"], "from_user": split["user_id"],
             "from_name": ACCOUNTS[split["user_id"]]["name"], "share": item["share"],
             "status": item["status"], "target": split.get("target"),
             "payment_remark": f"AA:{split['split_id']}"}
            for split in SPLIT_REQUESTS for item in split.get("collection", [])
            if item.get("account_id") == actor
        ]
    audit_event("SPLIT_INBOX", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=_request_id(x_request_id), metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@app.post("/api/v1/splits/{split_id}/decline")
def decline_split(split_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    actor = _get_actor(x_user_id, x_user_id, action="SPLIT_DECLINE", request_id=_request_id(x_request_id))
    safe_id = sanitize_text(split_id, field="AA 编号", max_length=100)
    with _ledger_transaction():
        split = next((row for row in SPLIT_REQUESTS if row.get("split_id") == safe_id), None)
        item = next((row for row in split.get("collection", []) if row.get("account_id") == actor), None) if split else None
        if not item:
            raise HTTPException(status_code=404, detail={"code": "SPLIT_NOT_FOUND", "message": "AA 请求不存在"})
        if item.get("status") != "PENDING":
            raise HTTPException(status_code=409, detail={"code": "SPLIT_SHARE_NOT_PENDING", "message": "该份额已处理"})
        item["status"] = "DECLINED"
        _refresh_split_status(split)
        enqueue_notification(f"AA_DECLINED:{safe_id}:{actor}", owner_id=split["user_id"], recipient_id=actor, split_id=safe_id, amount=item["share"], outcome="DECLINED", message="AA 付款请求已拒绝")
    audit_event("SPLIT_DECLINE", user_id=actor, risk_level="YELLOW", decision="ALLOW", outcome="SUCCESS", request_id=_request_id(x_request_id), metadata={"split_id": safe_id})
    persist_ledger()
    return {"code": 200, "status": "DECLINED", "split_id": safe_id}


@app.get("/api/v1/notifications/outbox")
def notification_outbox_list(split_id: Optional[str] = None, x_session_token: Optional[str] = Header(None, alias="X-Session-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "查询 AA 事件需要会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    actor = _get_actor(x_user_id, x_user_id, action="NOTIFICATION_QUERY", request_id=_request_id(x_request_id))
    return {"code": 200, "data": visible_notifications(actor, split_id=split_id)}


@app.get("/api/v1/operations/{operation_id}")
def operation_status(operation_id: str, endpoint: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="OPERATION_QUERY", request_id=request_id)
    if endpoint not in {"transfer_execute", "lock_fund", "operation_execute", "task_execute"}:
        raise HTTPException(status_code=400, detail={"code": "INVALID_OPERATION_KIND", "message": "操作类型无效"})
    key = sanitize_text(operation_id, field="操作编号", max_length=128)
    with STATE_LOCK:
        stored = OPERATION_RECEIPTS.get(json.dumps([actor, endpoint, key]))
        entry = IDEMPOTENCY_RECORDS.get((actor, endpoint, key))
        if stored and time.time() - stored["completed_at"] <= IDEMPOTENCY_TTL_SECONDS:
            result = {"status": "COMPLETED", "receipt": deepcopy(stored["response"])}
        elif entry and time.time() - entry["created_at"] <= IDEMPOTENCY_TTL_SECONDS:
            result = {"status": entry["status"], "receipt": deepcopy(entry.get("response"))}
        else:
            result = {"status": "UNKNOWN", "receipt": None}
    audit_event("OPERATION_QUERY", user_id=actor, request_id=request_id, risk_level="GREEN", decision="ALLOW", outcome=result["status"])
    return {"code": 200, "operation_id": key, **result}


@app.get("/api/v1/tasks")
def list_tasks(x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="TASK_QUERY", request_id=request_id)
    rows = [task.to_dict() for task in TASK_STORE.list(owner_id=actor)]
    audit_event("TASK_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@app.post("/api/v1/tasks/run-due")
def run_due_tasks(x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Advance due reminder tasks in the demo scheduler.

    Scheduled money movement is deliberately not auto-settled here: it is
    moved to WAITING_USER so a fresh approval/MFA can be collected at run
    time.  Reminder tasks can be marked delivered automatically.
    """

    # Bound once: calling ``_request_id`` at each use site minted a *different*
    # correlation id per event when the caller supplied none, so one scheduler
    # run could not be traced as a single chain.
    request_id = _request_id(x_request_id)
    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    if expected:
        if not x_admin_token or not hmac.compare_digest(expected, x_admin_token):
            raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "需要调度管理员凭证"})
        owner = None
    else:
        if not x_user_id:
            raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
        owner = x_user_id
        _get_actor(owner, owner, action="TASK_RUN", request_id=request_id)
    changed = []
    with STATE_LOCK:
        for task in TASK_STORE.ready():
            if owner and task.owner_id != owner:
                continue
            try:
                running = TASK_STORE.transition_if_status(task.task_id, TaskState.PENDING, TaskState.RUNNING, owner_id=owner)
                if running is None:
                    continue
                # Due money movement pauses for a fresh confirmation/MFA.
                final_status = TaskState.WAITING_USER if task.task_type in {"SCHEDULED_TRANSFER", "ORDER_DRAFT"} else TaskState.SUCCEEDED
                updated = TASK_STORE.transition(task.task_id, final_status, owner_id=owner)
                changed.append(updated.to_dict())
            except (KeyError, ValueError):
                continue
        if changed:
            persist_ledger()
    audit_event("TASK_RUN", user_id=owner, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"changed": len(changed)})
    return {"code": 200, "data": changed}


@app.get("/api/v1/tasks/{task_id}")
def get_task(task_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="TASK_QUERY", request_id=request_id)
    task = TASK_STORE.get(sanitize_text(task_id, field="任务编号", max_length=100), owner_id=actor)
    if not task:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"})
    return {"code": 200, "data": task.to_dict()}


@app.get("/api/v1/mock-merchants/orders/{order_id}")
def get_mock_merchant_order(order_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    actor = _get_actor(x_user_id, x_user_id, action="MOCK_ORDER_QUERY", request_id=_request_id(x_request_id))
    safe_id = sanitize_text(order_id, field="模拟订单编号", max_length=100)
    with STATE_LOCK:
        order = next((row for row in MOCK_ORDERS if row.get("mock_order_id") == safe_id and row.get("user_id") == actor), None)
        if not order:
            raise HTTPException(status_code=404, detail={"code": "MOCK_ORDER_NOT_FOUND", "message": "订单不存在"})
        result = dict(order)
    audit_event("MOCK_ORDER_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=_request_id(x_request_id), metadata={"order_id": safe_id})
    return {"code": 200, "data": result}


def _task_settlement_context(task: Any, *, target_id: str, amount: float, card_id: Optional[str] = None) -> dict[str, Any]:
    payload = task.payload
    return {
        "task_id": task.task_id,
        "task_updated_at": task.updated_at,
        "schedule_id": payload.get("schedule_id"),
        "owner_id": task.owner_id,
        "target_id": target_id,
        "amount_cents": amount_to_cents(amount),
        "card_id": payload.get("card_id") if card_id is None else card_id,
        "scheduled_at": payload.get("scheduled_at"),
        "lock_id": payload.get("lock_id"),
        "item": payload.get("item"),
    }


@app.post("/api/v1/tasks/{task_id}/precheck")
def task_precheck(task_id: str, req: TaskPrecheckRequest, request: Request = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="TASK_PRECHECK", request_id=request_id)
    safe_id = sanitize_text(task_id, field="任务编号", max_length=100)
    task = TASK_STORE.get(safe_id, owner_id=actor)
    if not task:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"})
    if task.status != TaskState.WAITING_USER or task.task_type not in {"SCHEDULED_TRANSFER", "ORDER_DRAFT"}:
        raise HTTPException(status_code=409, detail={"code": "TASK_NOT_READY", "message": "任务当前不等待授权"})
    payload = task.payload
    amount = float(payload.get("amount", 0))
    if task.task_type == "SCHEDULED_TRANSFER":
        target_id, receiver = _resolve_target(payload.get("target"), from_user=actor)
        target_display = receiver.get("name", target_id)
        paying_card = _payment_card(actor, payload.get("card_id"))
        if paying_card is None:
            raise SecurityError("预约转账缺少可用付款卡", 409, "CARD_NOT_AVAILABLE")
        bound_card_id = str(paying_card.get("card_id") or "")
        # Persist the concrete choice on the task so the signed context cannot
        # silently fall back to a different default card at execution time.
        payload["card_id"] = bound_card_id
        schedule_row = next((row for row in SCHEDULED_TRANSFERS if row.get("schedule_id") == payload.get("schedule_id")), None)
        if schedule_row is not None and not schedule_row.get("card_id"):
            schedule_row["card_id"] = bound_card_id
        operation = "SCHEDULED_TRANSFER_SETTLE"
        risk = "YELLOW" if round(_daily_spent(actor) + _pending_transfer_amount(actor, exclude_schedule_id=payload.get("schedule_id")) + amount, 2) <= 1000 else "RED"
    else:
        target_id, target_display, operation, risk = f"__ORDER_DRAFT__:{task.task_id}", str(payload.get("item") or "provider-mock"), "ORDER_DRAFT_SETTLE", "YELLOW"
        if target_display not in MOCK_MERCHANT_CATALOG or amount_to_cents(amount) != quote_cents(target_display):
            raise SecurityError("模拟商户报价已变化", 409, "ORDER_QUOTE_CHANGED")
        lock = LOCKS.get(str(payload.get("lock_id") or ""))
        if not lock or lock.get("user_id") != actor or lock.get("status") != "LOCKED" or float(lock.get("amount", 0)) < amount:
            raise SecurityError("预留预算不足以订购商品", 409, "ORDER_BUDGET_UNAVAILABLE")
    context = _task_settlement_context(task, target_id=target_id, amount=amount, card_id=bound_card_id if task.task_type == "SCHEDULED_TRANSFER" else None)
    context_hash = _context_hash(context)
    required = ["USER_CLICK_CONFIRM"] if risk == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
    idem_payload = {"task_id": safe_id, "user_id": actor, "context_hash": context_hash}
    cached = _idempotency_begin(actor, "task_precheck", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    token, token_payload = issue_auth_token(from_user=actor, target_id=target_id, target_display=target_display, amount=amount, remark=f"任务结算:{safe_id}", risk_level=risk, required_actions=required, operation=operation, context_hash=context_hash, session_token=_presented_session(request))
    response = {"code": 200, "risk_level": risk, "operation": operation, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": token_payload["exp"], "required_actions": required, "mfa_required": risk == "RED", "task_id": safe_id, "context_hash": context_hash, "amount": amount, "item": payload.get("item")}
    _idempotency_complete(actor, "task_precheck", idempotency_key, idem_payload, response)
    audit_event("TASK_PRECHECK", user_id=actor, risk_level=risk, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"task_id": safe_id, "jti": token_payload["jti"]})
    return response


@app.post("/api/v1/tasks/{task_id}/execute")
def execute_task(task_id: str, req: TaskExecuteRequest, request: Request = None, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="TASK_EXECUTE", request_id=request_id)
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "任务执行需要 Idempotency-Key"})
    safe_id = sanitize_text(task_id, field="任务编号", max_length=100)
    idem_payload = {"task_id": safe_id, "user_id": actor}
    cached = _idempotency_begin(actor, "task_execute", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    token = req.auth_token or x_auth_token
    proof = req.mfa_proof or x_mfa_proof
    try:
        if not token:
            raise SecurityError("缺少安全授权凭证", 403, "TOKEN_REQUIRED")
        with _ledger_transaction():
            # Task payloads and account records must stay attached to the live
            # ledger from validation through debit/credit. Another rollback
            # can replace all of these objects while this lock is not held.
            task = TASK_STORE.get(safe_id, owner_id=actor)
            if not task:
                raise SecurityError("任务不存在", 404, "TASK_NOT_FOUND")
            if task.status != TaskState.WAITING_USER or task.task_type not in {"SCHEDULED_TRANSFER", "ORDER_DRAFT"}:
                raise SecurityError("任务当前不等待授权", 409, "TASK_NOT_READY")
            payload = task.payload
            if task.task_type == "SCHEDULED_TRANSFER":
                target_id, receiver = _resolve_target(payload.get("target"), from_user=actor)
                amount = float(payload.get("amount", 0))
                bound_card_id = payload.get("card_id")
                schedule = next((row for row in SCHEDULED_TRANSFERS if row.get("schedule_id") == payload.get("schedule_id")), None)
                if not schedule or any(schedule.get(key) != payload.get(key) for key in ("schedule_id", "target", "amount", "card_id", "scheduled_at")):
                    raise SecurityError("预约任务详情已变化，请重新预检", 409, "SCHEDULE_CONTEXT_CHANGED")
                operation = "SCHEDULED_TRANSFER_SETTLE"
                current_risk = "YELLOW" if round(_daily_spent(actor) + _pending_transfer_amount(actor, exclude_schedule_id=payload.get("schedule_id")) + amount, 2) <= 1000 else "RED"
            else:
                target_id, amount, operation, current_risk = f"__ORDER_DRAFT__:{task.task_id}", float(payload.get("amount", 0)), "ORDER_DRAFT_SETTLE", "YELLOW"
                if str(payload.get("item") or "") not in MOCK_MERCHANT_CATALOG or amount_to_cents(amount) != quote_cents(str(payload.get("item") or "")):
                    raise SecurityError("模拟商户报价已变化", 409, "ORDER_QUOTE_CHANGED")
            context_hash = _context_hash(_task_settlement_context(task, target_id=target_id, amount=amount, card_id=bound_card_id if task.task_type == "SCHEDULED_TRANSFER" else None))
            token_payload, _ = inspect_auth_token(token)
            if token_payload.get("risk_level") == "YELLOW" and current_risk == "RED":
                raise SecurityError("风险等级已升高，请重新授权", 409, "RISK_CHANGED_REAUTHORIZE")
            with authorize_execution(token, from_user=actor, target_id=target_id, amount=amount, remark=f"任务结算:{safe_id}", operation=operation, context_hash=context_hash, mfa_proof=proof, presented_session=_presented_session(request), request_id=request_id):
                running = TASK_STORE.transition_if_status(safe_id, TaskState.WAITING_USER, TaskState.RUNNING, owner_id=actor)
                if running is None:
                    raise SecurityError("任务已被取消或正在执行", 409, "TASK_STATE_CHANGED")
                if task.task_type == "ORDER_DRAFT":
                    lock = LOCKS.get(str(payload.get("lock_id") or ""))
                    account = ACCOUNTS[actor]
                    if not lock or lock.get("user_id") != actor or lock.get("status") != "LOCKED" or float(lock.get("amount", 0)) < amount or float(account.get("locked_amount", 0)) < amount:
                        raise SecurityError("预留预算不足以订购商品", 409, "ORDER_BUDGET_UNAVAILABLE")
                    if float(account["balance"]) < amount:
                        raise SecurityError("账户余额不足", 400, "INSUFFICIENT_FUNDS")
                    paying_card = _charge_card_and_balance(actor, amount)
                    order = place_order(user_id=actor, task_id=safe_id, item=str(payload["item"]), amount_cents=amount_to_cents(amount))
                    account["balance"] = round(float(account["balance"]) - amount, 2)
                    account["locked_amount"] = round(float(account["locked_amount"]) - amount, 2)
                    lock["amount"] = round(float(lock["amount"]) - amount, 2)
                    if lock["amount"] == 0:
                        lock["status"] = "SPENT"
                    _record_card_spend(actor, amount, card=paying_card)
                    MOCK_ORDERS.append(order)
                    TRANSACTIONS.append({"tx_id": f"TXN{uuid.uuid4().hex[:12].upper()}", "account_id": actor, "merchant": "本地模拟商户", "category": "跨场景订购", "amount": amount, "remark": f"{payload['item']} {order['mock_order_id']}", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    payload["mock_order_id"] = order["mock_order_id"]
                    result = {"execution_mode": "MOCK_MERCHANT", "funds_debited": True, "mock_order_id": order["mock_order_id"], "order_status": "PLACED", "item": payload["item"], "amount": amount}
                else:
                    schedule = next((row for row in SCHEDULED_TRANSFERS if row.get("schedule_id") == payload.get("schedule_id")), None)
                    if not schedule or schedule.get("status") not in {"WAITING_USER", "SCHEDULED"}:
                        raise SecurityError("预约转账状态已变化", 409, "SCHEDULE_CONTEXT_CHANGED")
                    paying_card = _charge_card_and_balance(actor, amount, card_id=payload.get("card_id"))
                    account = ACCOUNTS[actor]
                    if account["balance"] - account["locked_amount"] < amount:
                        raise SecurityError("可用余额不足（部分资金已被锁定）", 400, "INSUFFICIENT_FUNDS")
                    account["balance"] = round(account["balance"] - amount, 2)
                    receiver["balance"] = round(receiver["balance"] + amount, 2)
                    DAILY_TRANSFERS[actor] = round(_daily_spent(actor) + amount, 2)
                    _record_card_spend(actor, amount, card=paying_card)
                    txn_id = f"TXN{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"
                    TRANSACTIONS.append({"tx_id": txn_id, "account_id": actor, "merchant": f"转账给 {receiver['name']}", "category": "转账支出", "amount": amount, "remark": "预约转账", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    schedule["status"] = "SETTLED"
                    schedule["settled_at"] = datetime.now().isoformat()
                    result = {"execution_mode": "LEDGER", "transaction_id": txn_id, "transferred_amount": amount, "sender_balance": account["balance"]}
                TASK_STORE.transition(safe_id, TaskState.SUCCEEDED, owner_id=actor)
            response = {"code": 200, "status": "SUCCESS", "task_id": safe_id, **result}
            _idempotency_complete(actor, "task_execute", idempotency_key, idem_payload, response)
            persist_ledger()
        audit_event("TASK_EXECUTE", user_id=actor, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"task_id": safe_id})
        return response
    except SecurityError as exc:
        _idempotency_abort(actor, "task_execute", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="TASK_EXECUTE", request_id=request_id)
    except Exception as exc:
        _idempotency_abort(actor, "task_execute", idempotency_key)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "任务执行失败，请稍后重试"}) from exc


def _task_action(task_id: str, action: str, req: TaskActionRequest, x_user_id: Optional[str], x_request_id: Optional[str]):
    # Task state and its schedule/split reservation form one ledger mutation.
    # Hold the same lock used by settlement so cancellation cannot win only
    # half of that operation or be snapshotted between those changes.
    request_id = _request_id(x_request_id)
    with _ledger_transaction():
        response = _task_action_locked(task_id, action, req, x_user_id, request_id)
        try:
            persist_ledger()
        except Exception:
            audit_event(action, user_id=x_user_id, decision="BLOCK", outcome="PERSISTENCE_FAILED_ROLLBACK",
                        request_id=request_id, metadata={"task_id": task_id})
            raise
        return response


def _task_action_locked(task_id: str, action: str, req: TaskActionRequest, x_user_id: Optional[str], x_request_id: Optional[str]):
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action=action, request_id=request_id)
    try:
        safe_id = sanitize_text(task_id, field="任务编号", max_length=100)
        reason = sanitize_text(req.reason, field="操作原因", max_length=300)
        if action == "TASK_CANCEL":
            task = TASK_STORE.cancel(safe_id, owner_id=actor)
            # Keep money-movement reservations in sync with the task state so
            # a cancelled scheduled/AA request no longer contributes to the
            # daily risk accumulator.  The task store remains the source of
            # ownership authorization; this only updates the corresponding
            # domain fixture after cancellation succeeds.
            with STATE_LOCK:
                schedule_id = task.payload.get("schedule_id")
                split_id = task.payload.get("split_id")
                if schedule_id:
                    for row in SCHEDULED_TRANSFERS:
                        if row.get("schedule_id") == schedule_id:
                            row["status"] = "CANCELLED"
                            row["cancelled_at"] = datetime.now().isoformat()
                if split_id:
                    for row in SPLIT_REQUESTS:
                        if row.get("split_id") == split_id:
                            row["status"] = "CANCELLED"
                            row["cancelled_at"] = datetime.now().isoformat()
                # A cancelled prerequisite must not leave dependent reminder
                # or order-draft nodes runnable.  Cascade only to non-terminal
                # tasks owned by the same principal; terminal records remain
                # immutable for auditability.
                cancelled_lock_id = task.payload.get("lock_id") if isinstance(task.payload, dict) and task.task_type in {"REMINDER", "FUND_RESERVATION"} else None
                for dependent in TASK_STORE.list(owner_id=actor):
                    same_reservation = bool(cancelled_lock_id and dependent.payload.get("lock_id") == cancelled_lock_id)
                    if (safe_id in dependent.depends_on or same_reservation) and dependent.status in {TaskState.PENDING, TaskState.WAITING_USER, TaskState.FAILED}:
                        try:
                            TASK_STORE.transition(dependent.task_id, TaskState.CANCELLED, owner_id=actor)
                        except (KeyError, ValueError):
                            pass
        else:
            task = TASK_STORE.get(safe_id, owner_id=actor)
            if task is None:
                raise KeyError(safe_id)
            if task.status not in {TaskState.PENDING, TaskState.WAITING_USER, TaskState.FAILED, TaskState.HUMAN_TAKEOVER}:
                raise ValueError("task cannot be taken over in its current state")
            # Pause the entire dependent branch. A takeover must not leave
            # sibling orders sharing this reservation eligible for execution.
            paused = {safe_id}
            lock_id = task.payload.get("lock_id")
            while True:
                newly_paused = []
                for dependent in TASK_STORE.list(owner_id=actor):
                    if dependent.task_id in paused:
                        continue
                    related = bool(set(dependent.depends_on) & paused) or bool(lock_id and dependent.payload.get("lock_id") == lock_id)
                    if related and dependent.status == TaskState.RUNNING:
                        raise ValueError("related task is currently running; reconcile before takeover")
                    if related and dependent.status in {TaskState.PENDING, TaskState.WAITING_USER, TaskState.FAILED}:
                        newly_paused.append(dependent)
                if not newly_paused:
                    break
                for dependent in newly_paused:
                    paused.add(dependent.task_id)
            # Validate every node first so a rejection cannot partially pause
            # the branch. The enclosing STATE_LOCK also serializes execution.
            for paused_id in paused:
                TASK_STORE.takeover(paused_id, owner_id=actor, reason=reason)
    except (SecurityError, KeyError, ValueError) as exc:
        if isinstance(exc, SecurityError):
            _record_failure_and_raise(exc, user_id=actor, action=action, request_id=request_id)
        raise HTTPException(status_code=409 if isinstance(exc, ValueError) else 404, detail={"code": "TASK_STATE_INVALID", "message": str(exc)})
    audit_event(action, user_id=actor, decision="ALLOW", outcome=task.status, request_id=request_id, metadata={"task_id": task.task_id, "reason": reason})
    return {"code": 200, "data": task.to_dict()}


@app.post("/api/v1/tasks/{task_id}/cancel")
def cancel_task(task_id: str, req: Optional[TaskActionRequest] = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    return _task_action(task_id, "TASK_CANCEL", req or TaskActionRequest(), x_user_id, x_request_id)


@app.post("/api/v1/tasks/{task_id}/takeover")
def takeover_task(task_id: str, req: Optional[TaskActionRequest] = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    return _task_action(task_id, "TASK_TAKEOVER", req or TaskActionRequest(), x_user_id, x_request_id)


# ---------------------------------------------------------------------------
# Operational observability
# ---------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    """Liveness probe that does not disclose account or credential data."""

    return {"status": "ok", "service": "mock-banking-core", "version": app.version}


@app.get("/api/v1/security/health")
def security_health_endpoint():
    return {"code": 200, **security_health(), "sandbox": sandbox_health(), "ledger_persistence": ledger_store_status()}


@app.get("/api/v1/security/circuit/{user_id}")
def security_circuit_endpoint(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    if not x_user_id or x_user_id != user_id:
        raise HTTPException(status_code=403, detail={"code": "AUTHORIZATION_REQUIRED", "message": "只能查询当前用户的安全状态"})
    return {"code": 200, **circuit_status(user_id)}


@app.get("/api/v1/security/behavior/{user_id}")
def security_behavior_endpoint(user_id: str, x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    """Explain the behaviour-risk score behind a combination lockout."""

    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    admin_ok = bool(expected and x_admin_token and hmac.compare_digest(expected, x_admin_token))
    if not admin_ok and (not x_user_id or x_user_id != user_id):
        raise HTTPException(status_code=403, detail={"code": "AUTHORIZATION_REQUIRED", "message": "只能查询当前用户的行为风险状态"})
    return {"code": 200, **behavior_status(user_id)}


@app.get("/api/v1/security/audit")
@app.get("/api/v1/audit/logs")
def security_audit_endpoint(limit: int = 100, user_id: Optional[str] = None, x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    admin_ok = bool(expected and x_admin_token and hmac.compare_digest(expected, x_admin_token))
    # A normal user may inspect only their own redacted events; cross-user or
    # unscoped access remains admin-only.
    if not admin_ok and (not x_user_id or (user_id and user_id != x_user_id)):
        raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "需要审计管理员凭证"})
    if not admin_ok:
        # Bind the self-service view to a known account and the presented
        # principal.  Merely choosing ``?user_id=...`` is never sufficient.
        try:
            validate_actor(x_user_id or "", x_user_id)
            _ensure_account(x_user_id or "")
        except SecurityError as exc:
            _raise_security(exc)
        user_id = x_user_id
    return {"code": 200, "data": get_audit_logs(limit=limit, user_id=user_id)}


@app.get("/api/v1/security/audit/verify")
def security_audit_verify(x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    if not expected or not x_admin_token or not hmac.compare_digest(expected, x_admin_token):
        raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "需要审计管理员凭证"})
    return {"code": 200, **verify_audit_chain()}


@app.get("/api/v1/security/audit/trace/{request_id}")
def security_audit_trace(request_id: str, x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    """Return the full decision chain (precheck → confirm → MFA → settle) of one request.

    A user may trace only their own requests; an audit administrator holding
    ``BANK_ADMIN_AUDIT_TOKEN`` may trace any request.  For a normal user an
    unknown request id is indistinguishable from someone else's request (no
    enumeration oracle), while the administrator sees an empty chain.
    """

    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    admin_ok = bool(expected and x_admin_token and hmac.compare_digest(expected, x_admin_token))
    trace = get_audit_trace(request_id)
    principal = trace.get("principal")
    if not admin_ok:
        if not x_user_id or not principal or x_user_id != principal:
            raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "只能查看本人请求的审计链路"})
    return {"code": 200, **trace}


__all__ = ["app", "ACCOUNTS", "CONTACTS", "USER_CONTACTS", "DAILY_TRANSFERS", "TRANSACTIONS", "DISPUTE_CASES", "LOCKS", "WEALTH_PRODUCTS", "WEALTH_HOLDINGS", "WEALTH_RISK_PROFILES", "CARDS", "SUBSCRIPTIONS", "SCHEDULED_TRANSFERS", "SPLIT_REQUESTS", "AUTH_TOKENS", "MFA_PROOFS", "AUDIT_LOGS", "DEVICES", "reset_state"]


# Test/demo reset helper.  It is intentionally not exposed as an HTTP route;
# production callers must use a transactional rollback workflow instead.
_INITIAL_ACCOUNTS = deepcopy(ACCOUNTS)
_INITIAL_CONTACTS = deepcopy(CONTACTS)
_INITIAL_TRANSACTIONS = deepcopy(TRANSACTIONS)
_INITIAL_WEALTH_HOLDINGS = deepcopy(globals().get("WEALTH_HOLDINGS", {}))
_INITIAL_WEALTH_RISK_PROFILES = deepcopy(globals().get("WEALTH_RISK_PROFILES", {}))
_INITIAL_CARDS = deepcopy(globals().get("CARDS", {}))
_INITIAL_SUBSCRIPTIONS = deepcopy(globals().get("SUBSCRIPTIONS", {}))
_INITIAL_SCHEDULED_TRANSFERS = deepcopy(globals().get("SCHEDULED_TRANSFERS", []))
_INITIAL_SPLIT_REQUESTS = deepcopy(globals().get("SPLIT_REQUESTS", []))
_INITIAL_MOCK_ORDERS = deepcopy(globals().get("MOCK_ORDERS", []))
_INITIAL_DISPUTE_CASES = deepcopy(globals().get("DISPUTE_CASES", []))


def reset_state() -> None:
    """Restore the deterministic in-memory sandbox and clear security state.

    Deliberately does not touch the durable ledger file: the reset helper is a
    test/demo affordance that never goes through the HTTP authorization path,
    so rewriting the audited snapshot from it would let an out-of-band call
    rewrite what a deployment believes was committed.  Point a deployment's
    ``BANK_LEDGER_STORE_PATH`` at a scratch file when testing resets.
    """

    global _LEDGER_STORE_BLOCKED
    _LEDGER_STORE_BLOCKED = False
    with STATE_LOCK:
        ACCOUNTS.clear()
        ACCOUNTS.update(deepcopy(_INITIAL_ACCOUNTS))
        CONTACTS.clear()
        CONTACTS.update(deepcopy(_INITIAL_CONTACTS))
        USER_CONTACTS.clear()
        TRANSACTIONS[:] = deepcopy(_INITIAL_TRANSACTIONS)
        DAILY_TRANSFERS.clear()
        DAILY_TRANSFERS["user_zhang"] = 0.0
        DAILY_TRANSFER_DATE.clear()
        if "CARD_DAILY_SPENT" in globals():
            CARD_DAILY_SPENT.clear()
        if "CARD_DAILY_SPENT_DATE" in globals():
            CARD_DAILY_SPENT_DATE.clear()
        RATE_LIMIT_STATE.clear()
        LOCKS.clear()
        TASK_STORE.reset()
        OUTBOX.clear()
        IDEMPOTENCY_RECORDS.clear()
        OPERATION_RECEIPTS.clear()
        if "WEALTH_HOLDINGS" in globals():
            WEALTH_HOLDINGS.clear()
            WEALTH_HOLDINGS.update(deepcopy(_INITIAL_WEALTH_HOLDINGS))
        if "WEALTH_RISK_PROFILES" in globals():
            WEALTH_RISK_PROFILES.clear()
            WEALTH_RISK_PROFILES.update(deepcopy(_INITIAL_WEALTH_RISK_PROFILES))
        RISK_ATTEMPTS.clear()
        if "CARDS" in globals():
            CARDS.clear()
            CARDS.update(deepcopy(_INITIAL_CARDS))
        if "CARD_DISCLOSURE_FAILURES" in globals():
            CARD_DISCLOSURE_FAILURES.clear()
        if "SUBSCRIPTIONS" in globals():
            SUBSCRIPTIONS.clear()
            SUBSCRIPTIONS.update(deepcopy(_INITIAL_SUBSCRIPTIONS))
        if "SCHEDULED_TRANSFERS" in globals():
            SCHEDULED_TRANSFERS[:] = deepcopy(_INITIAL_SCHEDULED_TRANSFERS)
        if "SPLIT_REQUESTS" in globals():
            SPLIT_REQUESTS[:] = deepcopy(_INITIAL_SPLIT_REQUESTS)
        MOCK_ORDERS[:] = deepcopy(_INITIAL_MOCK_ORDERS)
        if "DISPUTE_CASES" in globals():
            DISPUTE_CASES[:] = deepcopy(_INITIAL_DISPUTE_CASES)
        try:
            from security import reset_security_state

            reset_security_state()
        except Exception:
            pass
        # Device enrollment is cleared by ``reset_security_state`` above; this is
        # belt-and-braces so a reset never leaves a test (or demo) trusting a
        # stale key even if that call raised.
        DEVICES.clear()


# Opt-in durability: when a path is configured, the committed ledger snapshot is
# replayed at import time so accounts, transactions and reservations survive a
# process restart.  With no path configured the in-memory demo stays
# deterministic and side-effect free.  This must run after every ledger global
# above is defined, which is why it sits at the end of the module.
_LEDGER_STORE_LOAD_SUMMARY = load_ledger_store()
if _LEDGER_STORE_LOAD_SUMMARY.get("rejected"):
    logger.error(
        "账本文件未通过校验，已拒绝加载并冻结写入（将使用种子账本）: %s",
        _LEDGER_STORE_LOAD_SUMMARY.get("error"),
    )
