"""directed層(A→B、§3・§4)。保存値と有効値の窓口。

- A→B と B→A は独立したキー。
- `stored`: イベントの増減を蓄積する保存値。軸の数値範囲内に収める。
- `effective`: 意思決定・条件参照・帯変換が使う有効値。cap 未実装の間(第①段階)は
  `PassThroughResolver` が保存値をそのまま返す。
  cap は `EffectiveValueResolver` の差し替えで接続する(§4)。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Protocol

from kankei.definitions.schema import AxisDef
from kankei.model.ids import CasterId

type DirectedKey = tuple[CasterId, CasterId, str]


class EffectiveValueResolver(Protocol):
    """保存値から有効値を求める窓口(§4)。第④段階で cap を差し込む接続点。"""

    def resolve(self, source: CasterId, target: CasterId, axis: AxisDef, stored: int) -> int:
        """有効値を返す。"""
        ...


class PassThroughResolver:
    """有効値 = 保存値(cap 未実装)。"""

    def resolve(self, source: CasterId, target: CasterId, axis: AxisDef, stored: int) -> int:
        """保存値をそのまま返す。"""
        return stored


class DirectedStore:
    """(source, target, axis) → 保存値。未設定なら軸の初期値。"""

    def __init__(
        self,
        axes: Mapping[str, AxisDef],
        resolver: EffectiveValueResolver | None = None,
        values: Mapping[DirectedKey, int] | None = None,
    ) -> None:
        self._axes = axes
        self._resolver: EffectiveValueResolver = resolver or PassThroughResolver()
        self._values: dict[DirectedKey, int] = {}
        if values:
            for (source, target, axis_id), value in values.items():
                self.set_stored(source, target, axis_id, value)

    @property
    def axes(self) -> Mapping[str, AxisDef]:
        """軸定義。"""
        return self._axes

    def _axis(self, axis_id: str) -> AxisDef:
        try:
            return self._axes[axis_id]
        except KeyError:
            raise KeyError(f"未知の軸 '{axis_id}'") from None

    def stored(self, source: CasterId, target: CasterId, axis_id: str) -> int:
        """保存値。"""
        axis = self._axis(axis_id)
        return self._values.get((source, target, axis_id), axis.initial)

    def effective(self, source: CasterId, target: CasterId, axis_id: str) -> int:
        """有効値。条件参照・帯変換はこの窓口だけを通す。"""
        axis = self._axis(axis_id)
        return self._resolver.resolve(source, target, axis, self.stored(source, target, axis_id))

    def set_stored(self, source: CasterId, target: CasterId, axis_id: str, value: int) -> int:
        """保存値を軸範囲内に収めて設定し、設定後の値を返す。"""
        if source == target:
            raise ValueError("自分自身への directed 値は持てません")
        axis = self._axis(axis_id)
        clamped = axis.clamp(value)
        self._values[(source, target, axis_id)] = clamped
        return clamped

    def items(self) -> Iterator[tuple[DirectedKey, int]]:
        """保存値をキーの安定順で列挙する(保存・比較用)。"""
        yield from sorted(self._values.items())

    def copy(self) -> DirectedStore:
        """同じ軸定義・resolver を共有する複製。"""
        return DirectedStore(self._axes, self._resolver, dict(self._values))
