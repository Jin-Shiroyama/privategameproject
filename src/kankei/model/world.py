"""世界状態と一括確定(§7-6)。

`CommitBatch` が確定単位。`WorldState.commit` はバッチ全体を先に検証し、
検証に失敗した場合は何も更新しない。検証後の適用は失敗し得ない単純な代入のみで構成する。
スナップショットは確定単位の途中では採取しない(§8)。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from kankei.definitions.schema import ContentPack
from kankei.model.caster import Caster
from kankei.model.clock import GameTime
from kankei.model.directed import DirectedStore, EffectiveValueResolver
from kankei.model.fact import Fact, Knowledge
from kankei.model.history import AppliedDelta
from kankei.model.ids import CasterId, EventInstanceId, FactId
from kankei.model.pair import PairKey, PairState, PairStore
from kankei.model.result import EventResult

type CooldownKey = tuple[str, tuple[CasterId, ...]]


class CommitError(Exception):
    """一括確定の検証失敗。世界状態は変更されない。"""


@dataclass(frozen=True, slots=True)
class PairChange:
    """ペア状態の変更要求(確定後の状態をそのまま持つ)。"""

    state: PairState


@dataclass(frozen=True, slots=True)
class CooldownUpdate:
    """クールダウンの設定。until は期限のゲーム内時刻。"""

    key: CooldownKey
    until: GameTime


@dataclass(frozen=True, slots=True)
class CommitBatch:
    """一括確定の単位。値・状態・成立結果・fact・知識・クールダウンを同時に確定する。"""

    event_instance_id: EventInstanceId
    commit_seq: int
    deltas: tuple[AppliedDelta, ...] = ()
    pair_changes: tuple[PairChange, ...] = ()
    results: tuple[EventResult, ...] = ()
    facts: tuple[Fact, ...] = ()
    knowledge: tuple[Knowledge, ...] = ()
    cooldowns: tuple[CooldownUpdate, ...] = ()


@dataclass(slots=True)
class WorldState:
    """世界の実状態(神視点)。キャラ発話用の既知情報とは分ける(§19)。"""

    casters: dict[CasterId, Caster]
    directed: DirectedStore
    pairs: PairStore
    time: GameTime
    definition_version: str
    results: list[EventResult] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    knowledge: list[Knowledge] = field(default_factory=list)
    delta_history: list[AppliedDelta] = field(default_factory=list)
    cooldowns: dict[CooldownKey, GameTime] = field(default_factory=dict)
    applied_event_ids: set[EventInstanceId] = field(default_factory=set)
    next_result_id: int = 1
    next_fact_id: int = 1
    next_commit_seq: int = 1

    @classmethod
    def new(cls, pack: ContentPack, resolver: EffectiveValueResolver | None = None) -> WorldState:
        """定義データから初期状態を作る。"""
        casters = {
            CasterId(c.id): Caster(id=CasterId(c.id), name=c.name, traits=frozenset(c.traits))
            for c in pack.casters
        }
        return cls(
            casters=casters,
            directed=DirectedStore(pack.axes, resolver),
            pairs=PairStore(pack.tracks),
            time=GameTime(0),
            definition_version=pack.version,
        )

    # --- 参照 -----------------------------------------------------------

    def caster(self, caster_id: CasterId) -> Caster:
        """キャラ。"""
        return self.casters[caster_id]

    def caster_ids(self) -> tuple[CasterId, ...]:
        """キャラIDの安定順(登録順)。"""
        return tuple(self.casters)

    def knows(self, owner: CasterId, fact_id: FactId) -> bool:
        """owner が fact を知っているか。"""
        return any(k.owner == owner and k.fact_id == fact_id for k in self.knowledge)

    def cooldown_until(self, key: CooldownKey) -> GameTime | None:
        """クールダウンの期限。未設定なら None。"""
        return self.cooldowns.get(key)

    # --- 一括確定 -------------------------------------------------------

    def validate(self, batch: CommitBatch) -> None:
        """バッチ全体を検証する。失敗時は CommitError。状態は変更しない。"""
        if batch.commit_seq != self.next_commit_seq:
            raise CommitError(
                f"確定順が不正です: expected {self.next_commit_seq}, got {batch.commit_seq}"
            )
        if batch.event_instance_id in self.applied_event_ids:
            raise CommitError(f"イベント実体 {batch.event_instance_id} は適用済みです")
        self._validate_ids(batch)
        self._validate_deltas(batch.deltas)
        self._validate_pairs(batch.pair_changes)
        self._validate_knowledge(batch)
        for cd in batch.cooldowns:
            if cd.until < self.time:
                raise CommitError(f"クールダウン期限が過去です: {cd.key}")

    def _validate_ids(self, batch: CommitBatch) -> None:
        expected_result = self.next_result_id
        for r in batch.results:
            if r.result_id != expected_result:
                raise CommitError(f"result_id が連番ではありません: {r.result_id}")
            if r.commit_seq != batch.commit_seq:
                raise CommitError(f"result {r.result_id} の commit_seq が一致しません")
            if r.event_instance_id != batch.event_instance_id:
                raise CommitError(f"result {r.result_id} のイベント実体IDが一致しません")
            if r.definition_version != self.definition_version:
                raise CommitError(f"result {r.result_id} の定義版が一致しません")
            for p in r.participants:
                if p.caster not in self.casters:
                    raise CommitError(f"未知の参加者 {p.caster}")
            expected_result += 1
        result_ids = {r.result_id for r in batch.results}
        expected_fact = self.next_fact_id
        for f in batch.facts:
            if f.fact_id != expected_fact:
                raise CommitError(f"fact_id が連番ではありません: {f.fact_id}")
            if f.source_result_id not in result_ids:
                raise CommitError(f"fact {f.fact_id} の元結果が同じバッチにありません")
            if f.commit_seq != batch.commit_seq:
                raise CommitError(f"fact {f.fact_id} の commit_seq が一致しません")
            expected_fact += 1
        for d in batch.deltas:
            if d.result_id not in result_ids:
                raise CommitError("delta の元結果が同じバッチにありません")

    def _validate_deltas(self, deltas: Iterable[AppliedDelta]) -> None:
        # 同じ (source, target, axis) に複数の delta がある場合は順に連鎖することを検証する
        current: dict[tuple[CasterId, CasterId, str], int] = {}
        for d in deltas:
            if d.source not in self.casters or d.target not in self.casters:
                raise CommitError(f"未知のキャラへの delta: {d.source}->{d.target}")
            if d.axis not in self.directed.axes:
                raise CommitError(f"未知の軸への delta: {d.axis}")
            key = (d.source, d.target, d.axis)
            before = current.get(key, self.directed.stored(*key))
            if d.stored_before != before:
                raise CommitError(
                    f"delta の stored_before が現在値と一致しません: "
                    f"{key} {d.stored_before} != {before}"
                )
            axis = self.directed.axes[d.axis]
            if axis.clamp(d.stored_after) != d.stored_after:
                raise CommitError(f"stored_after が軸範囲外です: {key} {d.stored_after}")
            current[key] = d.stored_after

    def _validate_pairs(self, changes: Iterable[PairChange]) -> None:
        seen: set[PairKey] = set()
        for change in changes:
            state = change.state
            if state.key in seen:
                raise CommitError(f"同じペアの変更が重複しています: {state.key}")
            seen.add(state.key)
            for cid in state.key:
                if cid not in self.casters:
                    raise CommitError(f"未知のキャラのペア: {state.key}")
            # PairStore.put と同じ検証を事前に行う(適用時に失敗させない)
            try:
                self.pairs.validate(state)
            except (KeyError, ValueError) as exc:
                raise CommitError(f"ペア状態が不正です: {state.key}: {exc}") from None

    def _validate_knowledge(self, batch: CommitBatch) -> None:
        fact_ids = {f.fact_id for f in batch.facts}
        seen: set[tuple[CasterId, FactId]] = set()
        for k in batch.knowledge:
            if k.fact_id not in fact_ids:
                raise CommitError(f"knowledge の fact が同じバッチにありません: {k.fact_id}")
            if k.owner not in self.casters:
                raise CommitError(f"未知のキャラの knowledge: {k.owner}")
            pair = (k.owner, k.fact_id)
            if pair in seen or self.knows(k.owner, k.fact_id):
                raise CommitError(f"knowledge が重複しています: {pair}")
            seen.add(pair)
            if k.acquired_seq != batch.commit_seq:
                raise CommitError("knowledge の取得順が commit_seq と一致しません")

    def commit(self, batch: CommitBatch) -> None:
        """検証してから一括で適用する。部分適用は起きない。"""
        self.validate(batch)
        for d in batch.deltas:
            self.directed.set_stored(d.source, d.target, d.axis, d.stored_after)
            self.delta_history.append(d)
        for change in batch.pair_changes:
            self.pairs.put(change.state)
        self.results.extend(batch.results)
        self.facts.extend(batch.facts)
        self.knowledge.extend(batch.knowledge)
        for cd in batch.cooldowns:
            self.cooldowns[cd.key] = cd.until
        self.applied_event_ids.add(batch.event_instance_id)
        self.next_result_id += len(batch.results)
        self.next_fact_id += len(batch.facts)
        self.next_commit_seq += 1

    def snapshot_directed(self) -> Mapping[tuple[CasterId, CasterId, str], int]:
        """保存値の読み取りスナップショット(検証・比較用)。"""
        return dict(self.directed.items())
