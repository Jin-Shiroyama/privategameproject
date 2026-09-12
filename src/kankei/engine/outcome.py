"""§7-2: 結果候補の作成(OutcomeDraft)。永続化しない。

結果分岐は定義順に評価し、最初に成立した分岐を採用する。告白の受諾/拒否はここで
開始時点(EventContext)の有効値のみから決まり、追加抽選も以後の再評価もしない(§16)。
"""

from __future__ import annotations

from dataclasses import dataclass

from kankei.definitions.schema import EventDef, ObserverPolicy, OutcomeDef
from kankei.engine.context import EventContext
from kankei.engine.errors import PipelineError
from kankei.engine.evaluate import Binding, evaluate_all
from kankei.model.ids import CasterId, ResultId
from kankei.model.result import Participant


@dataclass(frozen=True, slots=True)
class DeltaCandidate:
    """delta 候補。result_index は分岐内の結果の位置。"""

    source: CasterId
    target: CasterId
    axis: str
    value: int
    result_index: int


@dataclass(frozen=True, slots=True)
class OutcomeDraft:
    """結果候補。"""

    event_def: EventDef
    outcome: OutcomeDef
    binding: dict[str, CasterId]
    participants: tuple[Participant, ...]
    observed_by: tuple[CasterId, ...]
    result_ids: tuple[ResultId, ...]
    delta_candidates: tuple[DeltaCandidate, ...]


def select_outcome(event_def: EventDef, binding: Binding, ctx: EventContext) -> OutcomeDef:
    """最初に成立した結果分岐。どれも成立しなければ定義の不備として停止する。"""
    for outcome in event_def.outcomes:
        if evaluate_all(outcome.when, ctx, event_def, binding):
            return outcome
    raise PipelineError(
        f"イベント '{event_def.id}' の結果分岐がどれも成立しません(binding={dict(binding)})"
    )


def resolve_observers(
    event_def: EventDef, binding: Binding, ctx: EventContext
) -> tuple[CasterId, ...]:
    """観測者候補(observed_by)。第①段階は「初期キャラ全員が同じ場にいる」前提の簡略実装(§19)。"""
    match event_def.observer_policy:
        case ObserverPolicy.NONE:
            return ()
        case ObserverPolicy.OTHERS_PRESENT:
            bound = set(binding.values())
            return tuple(c for c in ctx.caster_ids() if c not in bound)


def build_outcome(event_def: EventDef, binding: Binding, ctx: EventContext) -> OutcomeDraft:
    """結果候補を作る。"""
    outcome = select_outcome(event_def, binding, ctx)
    participants = tuple(Participant(binding[r], r) for r in event_def.roles)
    deltas = tuple(
        DeltaCandidate(binding[d.from_role], binding[d.to_role], d.axis, d.value, i)
        for i, spec in enumerate(outcome.results)
        for d in spec.deltas
    )
    return OutcomeDraft(
        event_def=event_def,
        outcome=outcome,
        binding=dict(binding),
        participants=participants,
        observed_by=resolve_observers(event_def, binding, ctx),
        result_ids=tuple(ResultId(ctx.next_result_id + i) for i in range(len(outcome.results))),
        delta_candidates=deltas,
    )
