"""イベント定義に書かれる条件式の型(判別共用体)。

評価はエンジン側(`kankei.engine.evaluate`)が行う。ここでは構造のみを定義する。
条件はすべて有効値窓口経由で参照される前提であり、保存値を直接読む条件は置かない(§4)。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Acquainted:
    """参加者2名に面識がある。"""


@dataclass(frozen=True, slots=True)
class NotAcquainted:
    """参加者2名に面識がない。"""


@dataclass(frozen=True, slots=True)
class TrackIs:
    """ペアの指定トラックが指定状態にある。"""

    track: str
    state: str


@dataclass(frozen=True, slots=True)
class AxisAtLeast:
    """from_role→to_role の軸の有効値が value 以上。"""

    axis: str
    from_role: str
    to_role: str
    value: int


@dataclass(frozen=True, slots=True)
class AxisBelow:
    """from_role→to_role の軸の有効値が value 未満。"""

    axis: str
    from_role: str
    to_role: str
    value: int


@dataclass(frozen=True, slots=True)
class HasTrait:
    """役割のキャラが性格タグを持つ。"""

    role: str
    trait: str


@dataclass(frozen=True, slots=True)
class CompatAtLeast:
    """compat(from_role→to_role) が value 以上(§21)。"""

    from_role: str
    to_role: str
    value: int


@dataclass(frozen=True, slots=True)
class CooldownElapsed:
    """このイベント自身のクールダウンが経過している(未設定なら真)。"""


@dataclass(frozen=True, slots=True)
class AllOf:
    """すべて成立。"""

    of: tuple[Condition, ...]


@dataclass(frozen=True, slots=True)
class AnyOf:
    """いずれか成立。"""

    of: tuple[Condition, ...]


@dataclass(frozen=True, slots=True)
class Not:
    """否定。"""

    cond: Condition


type Condition = (
    Acquainted
    | NotAcquainted
    | TrackIs
    | AxisAtLeast
    | AxisBelow
    | HasTrait
    | CompatAtLeast
    | CooldownElapsed
    | AllOf
    | AnyOf
    | Not
)
