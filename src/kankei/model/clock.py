"""ゲーム内時刻(§8)。実時間を持たず、tick のみから導出する。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True, slots=True)
class GameTime:
    """ゲーム内時刻。tick 由来の単一整数で表し、比較可能。"""

    tick: int

    def __post_init__(self) -> None:
        if self.tick < 0:
            raise ValueError("tick は0以上")

    def plus_ticks(self, ticks: int) -> GameTime:
        """ticks 後の時刻。"""
        return GameTime(self.tick + ticks)


@dataclass(frozen=True, slots=True)
class Calendar:
    """tick と日の換算。ticks_per_day は定義データ(settings)由来。"""

    ticks_per_day: int

    def __post_init__(self) -> None:
        if self.ticks_per_day <= 0:
            raise ValueError("ticks_per_day は正の整数")

    def day(self, time: GameTime) -> int:
        """0始まりの日番号。"""
        return time.tick // self.ticks_per_day

    def tick_of_day(self, time: GameTime) -> int:
        """日内の tick。"""
        return time.tick % self.ticks_per_day

    def plus_days(self, time: GameTime, days: int) -> GameTime:
        """days 日後の同時刻。"""
        return time.plus_ticks(days * self.ticks_per_day)

    def start_of_day(self, day: int) -> GameTime:
        """指定日の開始時刻。"""
        return GameTime(day * self.ticks_per_day)
