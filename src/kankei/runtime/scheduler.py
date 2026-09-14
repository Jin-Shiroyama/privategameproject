"""実時間→tick 変換(Pacer)。正本§8・計画書§7.1。

- `elapsed = min(max(raw, 0), 上限)`: 負の経過(巻き戻り)は 0、上限超過分は捨てる。
- 端数は `Fraction` 秒で累積し、切り捨てが蓄積してドリフトしない。
- 速度設定(`real_seconds_per_game_day`)は `seconds_per_tick` だけを変える。
- `start(now)` で計測基準を置き直す。停止していた時間は進まない。
"""

from __future__ import annotations

from fractions import Fraction


class Pacer:
    """実時間の経過を tick 数へ変換する。乱数・世界状態を持たない。"""

    def __init__(
        self, ticks_per_day: int, real_seconds_per_game_day: float, max_real_elapsed_seconds: float
    ) -> None:
        if ticks_per_day <= 0:
            raise ValueError("ticks_per_day は正の整数")
        if real_seconds_per_game_day <= 0 or max_real_elapsed_seconds <= 0:
            raise ValueError("実時間の設定は正の数")
        self._ticks_per_day = ticks_per_day
        self._max_elapsed = Fraction(max_real_elapsed_seconds)
        self._seconds_per_tick = Fraction(real_seconds_per_game_day) / ticks_per_day
        self._last_real: float | None = None
        self._residual = Fraction(0)

    @property
    def seconds_per_tick(self) -> Fraction:
        """1 tick あたりの実秒。"""
        return self._seconds_per_tick

    @property
    def residual_seconds(self) -> Fraction:
        """未消化の実秒(0 以上 seconds_per_tick 未満)。"""
        return self._residual

    def start(self, now: float) -> None:
        """計測基準を置き直す(起動・再開)。停止分は進まない。"""
        self._last_real = now
        self._residual = Fraction(0)

    def set_speed(self, real_seconds_per_game_day: float) -> None:
        """進行速度を変える。ゲーム内時間の進む速さだけに影響する。"""
        if real_seconds_per_game_day <= 0:
            raise ValueError("実時間の設定は正の数")
        self._seconds_per_tick = Fraction(real_seconds_per_game_day) / self._ticks_per_day

    def advance(self, now: float) -> int:
        """前回からの実測経過をクランプして累積し、進めるべき tick 数を返す。"""
        if self._last_real is None:
            raise RuntimeError("start() を先に呼んでください")
        raw = Fraction(now) - Fraction(self._last_real)
        self._last_real = now
        elapsed = min(max(raw, Fraction(0)), self._max_elapsed)
        self._residual += elapsed
        ticks = int(self._residual // self._seconds_per_tick)
        self._residual -= ticks * self._seconds_per_tick
        return ticks
