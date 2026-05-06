"""Live arming token flow — explicit operator authorization for live execution.

Safety rules:
  - Tokens are in-memory only; they die with the process.
  - Only one valid token per process at any time.
  - Arming requires config live_confirmed=true, MT5 connected, balance > 0.
  - Tokens expire after TTL (default 30 minutes).
  - Disarming invalidates the token immediately.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import uuid4


@dataclass(frozen=True)
class LiveArmingToken:
    """Immutable token authorizing one live trading session."""

    token_id: str
    run_id: str
    armed_at: datetime
    expires_at: datetime
    armed_by: str
    reason: str
    symbols: list[str]
    max_orders: int

    @property
    def is_expired(self) -> bool:
        return datetime.now(tz=UTC) > self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.is_expired


class ArmingService:
    """Manages live arming tokens in memory.

    Usage:
        service = ArmingService()
        token = service.arm(...)
        if token and token.is_valid:
            # proceed with live execution
        service.disarm("operator request")
    """

    _DEFAULT_TTL_MINUTES: ClassVar[int] = 30
    _token: LiveArmingToken | None = None

    def arm(
        self,
        *,
        run_id: str,
        armed_by: str,
        reason: str,
        symbols: list[str],
        max_orders: int,
        ttl_minutes: int = _DEFAULT_TTL_MINUTES,
    ) -> LiveArmingToken | None:
        """Create and store a new live arming token.

        Returns the token on success, None if already armed.
        """
        if self._token is not None and self._token.is_valid:
            return None  # already armed

        now = datetime.now(tz=UTC)
        token = LiveArmingToken(
            token_id=str(uuid4()),
            run_id=run_id,
            armed_at=now,
            expires_at=now + timedelta(minutes=ttl_minutes),
            armed_by=armed_by,
            reason=reason,
            symbols=symbols,
            max_orders=max_orders,
        )
        self._token = token
        return token

    def disarm(self, reason: str) -> bool:
        """Invalidate the current token.

        Returns True if a token was active and is now cleared.
        """
        had_token = self._token is not None
        self._token = None
        return had_token

    def consume_token(self, token_id: str, reason: str = "") -> bool:
        """Consume current token after an execution attempt.

        Returns True when the active token matches token_id and is cleared.
        """
        token = self.get_valid_token()
        if token is None:
            return False
        if token.token_id != token_id:
            return False
        self._token = None
        return True

    def get_valid_token(self) -> LiveArmingToken | None:
        """Return the current token if it exists and is not expired."""
        if self._token is None:
            return None
        if self._token.is_expired:
            self._token = None
            return None
        return self._token

    @property
    def is_armed(self) -> bool:
        return self.get_valid_token() is not None

    @property
    def current_token(self) -> LiveArmingToken | None:
        """Return the raw token (even if expired). Useful for audit."""
        return self._token
