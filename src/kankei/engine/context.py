"""§7-1: 開始時点の情報(EventContext)。

スロット開始時の WorldState から directed / pair / cooldowns をコピーした
読み取り専用スナップショット。
ステップ1〜3(候補列挙・発生条件・返答判定・補正)はこれだけを参照し、ステップ4以降の仮更新に影響されない。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from kankei.affinity import compat
from kankei.definitions.schema import ContentPack
from kankei.model.caster import Caster
from kankei.model.clock import Calendar, GameTime
from kankei.model.directed import DirectedStore
from kankei.model.ids import CasterId
from kankei.model.pair import PairState, PairStore
from kankei.model.world import CooldownKey, WorldState


@dataclass(frozen=True, slots=True)
class EventContext:
    """開始時点のスナップショット。"""

    pack: ContentPack
    time: GameTime
    casters: Mapping[CasterId, Caster]
    directed: DirectedStore
    pairs: PairStore
    cooldowns: Mapping[CooldownKey, GameTime]
    next_result_id: int
    next_fact_id: int
    next_commit_seq: int
    definition_version: str

    @classmethod
    def capture(cls, world: WorldState, pack: ContentPack) -> EventContext:
        """WorldState から現在の情報を固定する。"""
        return cls(
            pack=pack,
            time=world.time,
            casters=MappingProxyType(dict(world.casters)),
            directed=world.directed.copy(),
            pairs=world.pairs.copy(),
            cooldowns=MappingProxyType(dict(world.cooldowns)),
            next_result_id=world.next_result_id,
            next_fact_id=world.next_fact_id,
            next_commit_seq=world.next_commit_seq,
            definition_version=world.definition_version,
        )

    @property
    def calendar(self) -> Calendar:
        """日換算。"""
        return Calendar(self.pack.settings.ticks_per_day)

    def caster_ids(self) -> tuple[CasterId, ...]:
        """キャラIDの安定順(登録順)。"""
        return tuple(self.casters)

    def effective(self, source: CasterId, target: CasterId, axis: str) -> int:
        """有効値(条件参照はこの窓口のみ)。"""
        return self.directed.effective(source, target, axis)

    def stored(self, source: CasterId, target: CasterId, axis: str) -> int:
        """保存値(履歴記録用)。条件判定には使わない。"""
        return self.directed.stored(source, target, axis)

    def pair(self, a: CasterId, b: CasterId) -> PairState:
        """ペア状態(コピー)。"""
        return self.pairs.get(a, b)

    def acquainted(self, a: CasterId, b: CasterId) -> bool:
        """面識があるか。"""
        return self.pairs.get(a, b).acquainted

    def track_state(self, a: CasterId, b: CasterId, track: str) -> str:
        """トラックの現在状態。"""
        return self.pairs.get(a, b).state(track)

    def has_trait(self, caster: CasterId, trait: str) -> bool:
        """性格タグを持つか。"""
        return self.casters[caster].has_trait(trait)

    def compat(self, source: CasterId, target: CasterId) -> int:
        """相性 compat(source→target)(§21)。"""
        return compat(self.casters[source], self.casters[target], self.pack.affinity_rules)

    def cooldown_active(self, key: CooldownKey) -> bool:
        """クールダウン中か(期限が現在時刻より後)。"""
        until = self.cooldowns.get(key)
        return until is not None and until > self.time
