from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe_bot.trades.bitbank_bitflyer.config import BotConfig


@dataclass(frozen=True)
class MomentumBlock:
    """Why a maker action is currently blocked by the momentum guard."""

    adverse_move_jpy: Decimal
    reason: str  # "momentum" while the move exceeds threshold, "cooldown" after


class MomentumGuard:
    """Blocks maker placement while the bitFlyer mid bursts toward a maker.

    ``record`` keeps a short history of bitFlyer mid prices. ``blocked``
    reports the adverse mid move for a maker action when it reaches the
    configured threshold within the lookback window; once tripped the side
    stays blocked for the cooldown period after the last adverse observation.

    Directionality: a falling mid endangers a BUY maker (its hedge sells into
    the drop) and a rising mid endangers a SELL maker; moves away from the
    maker never block.
    """

    def __init__(
        self,
        enabled: bool,
        window_seconds: float,
        threshold_jpy: Decimal,
        cooldown_seconds: float,
    ) -> None:
        self.enabled = enabled
        self.window_seconds = window_seconds
        self.threshold_jpy = threshold_jpy
        self.cooldown_seconds = cooldown_seconds
        self._samples: deque[tuple[float, Decimal]] = deque()
        self._cooldown_until: dict[str, float] = {"BUY": 0.0, "SELL": 0.0}

    @classmethod
    def from_config(cls, config: BotConfig) -> MomentumGuard:
        return cls(
            enabled=config.momentum_guard_enabled,
            window_seconds=config.momentum_guard_window_seconds,
            threshold_jpy=config.momentum_guard_threshold_jpy,
            cooldown_seconds=config.momentum_guard_cooldown_seconds,
        )

    def record(self, mid: Decimal, now: float | None = None) -> None:
        if not self.enabled:
            return
        now = time.time() if now is None else now
        self._samples.append((now, mid))
        self._prune(now)

    def blocked(self, action: str, now: float | None = None) -> MomentumBlock | None:
        if not self.enabled:
            return None
        now = time.time() if now is None else now
        self._prune(now)
        move = self._adverse_move(action)
        if move >= self.threshold_jpy:
            self._cooldown_until[action] = now + self.cooldown_seconds
            return MomentumBlock(adverse_move_jpy=move, reason="momentum")
        if now < self._cooldown_until[action]:
            return MomentumBlock(adverse_move_jpy=move, reason="cooldown")
        return None

    def peek(self, action: str, now: float | None = None) -> tuple[Decimal, str | None]:
        """Adverse move and block reason without refreshing the cooldown.

        For monitoring only: unlike ``blocked`` it never extends the cooldown,
        so the web UI can poll it without affecting strategy behaviour.
        """
        if not self.enabled:
            return Decimal("0"), None
        now = time.time() if now is None else now
        self._prune(now)
        move = self._adverse_move(action)
        if move >= self.threshold_jpy:
            return move, "momentum"
        if now < self._cooldown_until[action]:
            return move, "cooldown"
        return move, None

    def _adverse_move(self, action: str) -> Decimal:
        if len(self._samples) < 2:
            return Decimal("0")
        earliest = self._samples[0][1]
        latest = self._samples[-1][1]
        return earliest - latest if action == "BUY" else latest - earliest

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
