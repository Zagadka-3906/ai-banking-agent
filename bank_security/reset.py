"""Test-only reset of every piece of process-local security state."""

from __future__ import annotations

from bank_security.audit import reset_audit_chain_locked
from bank_security.authorization import AUTH_TOKENS, FACE_CHALLENGES, MFA_PROOFS
from bank_security.circuit import BEHAVIOR_STATE, SECURITY_STATE
from bank_security.config import STATE_LOCK
from bank_security.devices import DEVICES, unfreeze_device_store
from bank_security.sessions import SESSION_TOKENS


def reset_security_state() -> None:
    """Test helper; never expose this through the public API."""

    with STATE_LOCK:
        AUTH_TOKENS.clear()
        MFA_PROOFS.clear()
        FACE_CHALLENGES.clear()
        SESSION_TOKENS.clear()
        BEHAVIOR_STATE.clear()
        SECURITY_STATE.clear()
        DEVICES.clear()
        reset_audit_chain_locked()
    # A rejected file freezes writes for the life of the process, so a test that
    # deliberately feeds a forged store must not leave every later test frozen.
    unfreeze_device_store()
