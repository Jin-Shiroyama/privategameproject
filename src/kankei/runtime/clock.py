"""実時間の取得と待機の注入点。シミュレーション層はこれらに触れない。"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    """単調増加の実時間(秒)。"""

    def now(self) -> float:
        """現在の単調時刻。"""
        ...


class Sleeper(Protocol):
    """待機。"""

    def sleep(self, seconds: float) -> None:
        """seconds 秒待つ。"""
        ...


class MonotonicClock:
    """`time.monotonic()`。壁時計は使わない。"""

    def now(self) -> float:
        """現在の単調時刻。"""
        return time.monotonic()


class TimeSleeper:
    """`time.sleep()`。"""

    def sleep(self, seconds: float) -> None:
        """seconds 秒待つ。"""
        time.sleep(seconds)


class FakeClock:
    """テスト用。値を手で進める(巻き戻しも可)。"""

    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def now(self) -> float:
        """現在値。"""
        return self.value

    def advance(self, seconds: float) -> None:
        """seconds 進める(負も可)。"""
        self.value += seconds


class NoSleep:
    """テスト用。待たずに呼び出しを記録する。"""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def sleep(self, seconds: float) -> None:
        """記録のみ。"""
        self.calls.append(seconds)
