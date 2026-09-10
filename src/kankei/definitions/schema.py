"""定義データのスキーマ(frozen dataclass)。

軸・トラック・性格タグ・相性ルール・イベント・テンプレート・進行設定・検証用キャラを
1つの `ContentPack` に集約する。ゲーム固有の数値・条件はすべてここに載る定義データ側に置き、
コードにハードコードしない(§4)。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from kankei.definitions.conditions import Condition


class AxisLayer(StrEnum):
    """軸の層(§3)。第①段階で数値軸を持つのは directed のみ。"""

    DIRECTED = "directed"


class AxisRole(StrEnum):
    """軸の役割(§4)。latent はLLMプロンプト組み立てから除外される。"""

    EXPRESSED = "expressed"
    LATENT = "latent"


@dataclass(frozen=True, slots=True)
class Band:
    """expressed軸の帯。有効値が min 以上で該当する(上から順に評価)。"""

    min: int
    label: str
    hint: str


@dataclass(frozen=True, slots=True)
class AxisDef:
    """軸定義(§4)。"""

    id: str
    name: str
    layer: AxisLayer
    role: AxisRole
    min_value: int
    max_value: int
    initial: int
    bands: tuple[Band, ...]
    description: str

    def clamp(self, value: int) -> int:
        """保存値を軸の数値範囲内に収める。"""
        return max(self.min_value, min(self.max_value, value))


class TransitionMeaning(StrEnum):
    """遷移の意味(§17)。状態名の並び順から推定せず定義に明記する。"""

    PROMOTE = "promote"
    DEMOTE = "demote"
    DISSOLVE = "dissolve"


@dataclass(frozen=True, slots=True)
class TransitionDef:
    """トラック内の許容遷移。"""

    from_state: str
    to_state: str
    meaning: TransitionMeaning


@dataclass(frozen=True, slots=True)
class TrackDef:
    """pair層のトラック定義(§6)。"""

    id: str
    name: str
    states: tuple[str, ...]
    initial: str
    transitions: tuple[TransitionDef, ...]


@dataclass(frozen=True, slots=True)
class TraitDef:
    """solo属性としての性格タグ。"""

    id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class AffinityRule:
    """相性ルール(§21)。"""

    id: str
    a_has: str
    b_has: str
    value: int
    symmetric: bool


class EventShape(StrEnum):
    """候補の正規化方法。directed は役割順、pair は参加者IDソート、world は参加者なし。"""

    DIRECTED = "directed"
    PAIR = "pair"
    WORLD = "world"


class EventTrigger(StrEnum):
    """発火方式。lottery はスロット抽選、daily は日境界で必ず1件。"""

    LOTTERY = "lottery"
    DAILY = "daily"


class ObserverPolicy(StrEnum):
    """観測者候補(observed_by)の決め方(§19)。"""

    NONE = "none"
    OTHERS_PRESENT = "others_present"


class ContentRating(StrEnum):
    """内容区分。nsfw は型のみ予約(§10〜11)。第①段階のコンテンツは sfw のみ。"""

    SFW = "sfw"
    NSFW = "nsfw"


class CooldownScope(StrEnum):
    """クールダウンの適用範囲(§16)。"""

    ACTOR_TO_TARGET = "actor_to_target"
    PAIR = "pair"


@dataclass(frozen=True, slots=True)
class CooldownDef:
    """クールダウン定義。after_outcomes に列挙した結果分岐の成立時にのみ設定する。"""

    scope: CooldownScope
    days: int
    after_outcomes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OccurrenceDef:
    """発生条件と抽選重み。"""

    weight: int
    when: tuple[Condition, ...]


@dataclass(frozen=True, slots=True)
class DeltaSpec:
    """delta候補の定義。from_role→to_role の軸に value を加える。"""

    from_role: str
    to_role: str
    axis: str
    value: int


class Audience(StrEnum):
    """factの開示範囲(§19)。public は「その場の参加者・同席候補」であり全世界への配信ではない。"""

    PUBLIC = "public"
    PARTICIPANTS_ONLY = "participants_only"
    EXPLICIT = "explicit"


@dataclass(frozen=True, slots=True)
class FactSpec:
    """成立結果から生成するfactの定義。開示範囲は定義データ側に置く。"""

    kind: str
    audience: Audience
    recipients: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TransitionSpec:
    """結果に伴うトラック遷移の要求。"""

    track: str
    to_state: str


@dataclass(frozen=True, slots=True)
class ResultSpec:
    """成立結果(EventResult)の定義。

    1つの結果分岐から複数の結果を作れる(§16: 告白経験と関係成立は別の結果)。
    """

    kind: str
    success: bool
    deltas: tuple[DeltaSpec, ...]
    transition: TransitionSpec | None
    establish_acquaintance: bool
    facts: tuple[FactSpec, ...]


@dataclass(frozen=True, slots=True)
class OutcomeDef:
    """結果分岐。定義順に when を評価し、最初に成立した分岐を採用する(決定的判定、§16)。"""

    id: str
    when: tuple[Condition, ...]
    results: tuple[ResultSpec, ...]


@dataclass(frozen=True, slots=True)
class EventDef:
    """イベント定義(§16)。"""

    id: str
    name: str
    shape: EventShape
    roles: tuple[str, ...]
    trigger: EventTrigger
    observer_policy: ObserverPolicy
    content_rating: ContentRating
    occurrence: OccurrenceDef | None
    cooldown: CooldownDef | None
    outcomes: tuple[OutcomeDef, ...]


@dataclass(frozen=True, slots=True)
class TemplateDef:
    """テンプレートログ。(result_kind, success) に対する文面バリエーション。"""

    result_kind: str
    success: bool | None
    variants: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Settings:
    """進行設定(§8)。"""

    ticks_per_day: int
    event_slots_per_day: int
    real_seconds_per_game_day: float
    max_real_elapsed_seconds: float

    @property
    def ticks_per_slot(self) -> int:
        """1スロットあたりのtick数。"""
        return self.ticks_per_day // self.event_slots_per_day


@dataclass(frozen=True, slots=True)
class CasterDef:
    """検証用キャラの初期定義。通常のCasterは全員成人(§10)。"""

    id: str
    name: str
    traits: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContentPack:
    """定義データ一式。version は定義版としてスナップショットに保存される(§8)。"""

    version: str
    axes: Mapping[str, AxisDef]
    tracks: Mapping[str, TrackDef]
    traits: Mapping[str, TraitDef]
    affinity_rules: tuple[AffinityRule, ...]
    events: Mapping[str, EventDef]
    templates: tuple[TemplateDef, ...]
    settings: Settings
    casters: tuple[CasterDef, ...]

    def find_template(self, result_kind: str, success: bool) -> TemplateDef | None:
        """結果種別と成否に対応するテンプレートを返す。成否指定ありを優先する。"""
        fallback: TemplateDef | None = None
        for template in self.templates:
            if template.result_kind != result_kind:
                continue
            if template.success == success:
                return template
            if template.success is None:
                fallback = template
        return fallback
