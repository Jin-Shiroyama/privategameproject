"""§7 イベントパイプライン(1〜6)。7(テキスト化)は text 層(M6)が確定後の結果から行う。

乱数は `candidates.draw` にだけ渡す。それ以外のステップは乱数を消費しない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from random import Random

from kankei.definitions.schema import ContentPack, EventDef
from kankei.engine.apply import PendingDelta, PendingResult, PendingState
from kankei.engine.candidates import Candidate, collect_candidates, draw
from kankei.engine.context import EventContext
from kankei.engine.errors import PipelineError
from kankei.engine.evaluate import Binding, cooldown_key
from kankei.engine.facts import generate_facts
from kankei.engine.modifiers import BlockPolicy, Modifier, apply_modifiers
from kankei.engine.outcome import OutcomeDraft, build_outcome
from kankei.model.clock import GameTime
from kankei.model.fact import Fact, Knowledge
from kankei.model.history import AppliedDelta
from kankei.model.ids import EventInstanceId
from kankei.model.result import EventResult
from kankei.model.world import CommitBatch, CooldownUpdate, PairChange, WorldState


@dataclass(frozen=True, slots=True)
class SlotReport:
    """1スロットの処理結果。batch が None なら無イベント(確定なし)。"""

    time: GameTime
    candidates: tuple[Candidate, ...]
    chosen: Candidate | None
    batch: CommitBatch | None


def event_instance_id(ctx: EventContext, event_def: EventDef) -> EventInstanceId:
    """イベント実体の安定した識別子。"""
    return EventInstanceId(f"t{ctx.time.tick}-c{ctx.next_commit_seq}-{event_def.id}")


def assemble_batch(
    ctx: EventContext,
    draft: OutcomeDraft,
    pending: PendingState,
    deltas: Sequence[PendingDelta],
    results: Sequence[PendingResult],
) -> CommitBatch:
    """§7-6: 値・状態・成立結果・fact・知識・クールダウンを1つの確定単位にまとめる。"""
    event_def = draft.event_def
    commit_seq = ctx.next_commit_seq
    instance_id = event_instance_id(ctx, event_def)

    event_results: list[EventResult] = []
    for pr in results:
        event_results.append(
            EventResult(
                result_id=draft.result_ids[pr.index],
                event_instance_id=instance_id,
                event_def_id=event_def.id,
                outcome_id=draft.outcome.id,
                kind=pr.spec.kind,
                success=pr.spec.success,
                participants=draft.participants,
                game_time=ctx.time,
                commit_seq=commit_seq,
                observed_by=draft.observed_by,
                definition_version=ctx.definition_version,
                related_result_id=draft.result_ids[0] if pr.index > 0 else None,
                track_change=pr.track_change,
            )
        )

    applied = tuple(
        AppliedDelta(
            result_id=draft.result_ids[d.candidate.result_index],
            source=d.candidate.source,
            target=d.candidate.target,
            axis=d.candidate.axis,
            candidate=d.candidate.value,
            after_modifier=d.after_modifier,
            applied=d.applied,
            stored_before=d.stored_before,
            stored_after=d.stored_after,
            effective_before=d.effective_before,
            effective_after=d.effective_after,
            blocked=d.blocked,
            rule_ids=d.rule_ids,
        )
        for d in deltas
    )

    facts: list[Fact] = []
    knowledge: list[Knowledge] = []
    for pr, er in zip(results, event_results, strict=True):
        new_facts, new_knowledge = generate_facts(er, pr.spec.facts, ctx.next_fact_id + len(facts))
        facts.extend(new_facts)
        knowledge.extend(new_knowledge)

    pair_changes = tuple(
        PairChange(pending.changed_pairs[k]) for k in sorted(pending.changed_pairs)
    )

    cooldowns: tuple[CooldownUpdate, ...] = ()
    cd = event_def.cooldown
    if cd is not None and draft.outcome.id in cd.after_outcomes:
        key = cooldown_key(event_def, draft.binding)
        if key is None:
            raise PipelineError(f"イベント '{event_def.id}' のクールダウンキーを作れません")
        until = ctx.calendar.plus_days(ctx.time, cd.days)
        cooldowns = (CooldownUpdate(key, until),)

    return CommitBatch(
        event_instance_id=instance_id,
        commit_seq=commit_seq,
        deltas=applied,
        pair_changes=pair_changes,
        results=tuple(event_results),
        facts=tuple(facts),
        knowledge=tuple(knowledge),
        cooldowns=cooldowns,
    )


class Pipeline:
    """§7 の 1〜6 を実行する。"""

    def __init__(
        self,
        pack: ContentPack,
        rng: Random,
        modifiers: Sequence[Modifier] = (),
        blocks: Sequence[BlockPolicy] = (),
    ) -> None:
        self.pack = pack
        self._rng = rng
        self.modifiers = tuple(modifiers)
        self.blocks = tuple(blocks)

    def run_slot(self, world: WorldState) -> SlotReport:
        """1スロット: 候補収集→抽選→(成立すれば)イベント実行と確定。"""
        ctx = EventContext.capture(world, self.pack)
        candidates = collect_candidates(self.pack, ctx)
        chosen = draw(candidates, self._rng)
        if chosen is None:
            return SlotReport(ctx.time, candidates, None, None)
        event_def = self.pack.events[chosen.event_def_id]
        batch = self._run(world, ctx, event_def, chosen.binding_map())
        return SlotReport(ctx.time, candidates, chosen, batch)

    def run_event(self, world: WorldState, event_def_id: str, binding: Binding) -> CommitBatch:
        """抽選を経ずに指定イベントを実行する(ステップ2〜6)。テスト・日次処理用。"""
        event_def = self.pack.events[event_def_id]
        if set(binding) != set(event_def.roles):
            raise PipelineError(
                f"イベント '{event_def.id}' の役割 {event_def.roles} と binding が一致しません"
            )
        ctx = EventContext.capture(world, self.pack)
        return self._run(world, ctx, event_def, dict(binding))

    def _run(
        self, world: WorldState, ctx: EventContext, event_def: EventDef, binding: Binding
    ) -> CommitBatch:
        draft = build_outcome(event_def, binding, ctx)  # 2
        modified = apply_modifiers(ctx, draft.delta_candidates, self.modifiers, self.blocks)  # 3
        pending = PendingState(ctx)
        deltas = pending.apply_deltas(modified)  # 4
        results = pending.apply_results(draft)  # 4-5
        deltas = pending.refresh_effective(deltas)  # 5
        batch = assemble_batch(ctx, draft, pending, deltas, results)  # 6
        world.commit(batch)
        return batch
