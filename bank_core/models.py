"""Request models for the transfer, lock, operation, MFA, login and task endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool


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
    # CARD_CHANNEL only: which payment channel, and whether to switch it on.
    channel: Optional[str] = None
    enable: Optional[StrictBool] = None
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
    # CARD_CHANNEL only: which payment channel, and whether to switch it on.
    channel: Optional[str] = None
    enable: Optional[StrictBool] = None
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
