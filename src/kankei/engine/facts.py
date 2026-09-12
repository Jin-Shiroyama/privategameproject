"""§19: fact 生成と知識参照。

入力は `EventResult` と `FactSpec`(定義データ)のみ。delta・軸値・世界の内部値は引数に取れない。
- public: その場の参加者と同席候補(observed_by)へ開示。全世界への配信ではない
- participants_only: 当事者のみ
- explicit: 定義で指定した役割のみ
知識の via は、参加者なら participant、observed_by なら witnessed。
"""

from __future__ import annotations

from collections.abc import Sequence

from kankei.definitions.schema import Audience, FactSpec
from kankei.engine.errors import PipelineError
from kankei.model.fact import Fact, Knowledge, KnowledgeVia
from kankei.model.ids import CasterId, FactId
from kankei.model.result import EventResult


def _audience(result: EventResult, spec: FactSpec) -> tuple[CasterId, ...]:
    participants = result.participant_ids
    match spec.audience:
        case Audience.PUBLIC:
            raw: tuple[CasterId, ...] = participants + result.observed_by
        case Audience.PARTICIPANTS_ONLY:
            raw = participants
        case Audience.EXPLICIT:
            raw = tuple(result.caster_of(role) for role in spec.recipients)
    seen: list[CasterId] = []
    for c in raw:
        if c not in seen:
            seen.append(c)
    return tuple(seen)


def generate_facts(
    result: EventResult, specs: Sequence[FactSpec], next_fact_id: int
) -> tuple[tuple[Fact, ...], tuple[Knowledge, ...]]:
    """成立結果から fact と直接開示先の知識参照を作る。"""
    facts: list[Fact] = []
    knowledge: list[Knowledge] = []
    participants = set(result.participant_ids)
    observers = set(result.observed_by)
    for spec in specs:
        fact_id = FactId(next_fact_id + len(facts))
        audience = _audience(result, spec)
        change = result.track_change
        facts.append(
            Fact(
                fact_id=fact_id,
                source_result_id=result.result_id,
                kind=spec.kind,
                subjects=result.participants,
                game_time=result.game_time,
                commit_seq=result.commit_seq,
                audience=audience,
                track=change.track if change else None,
                state_after=change.state_after if change else None,
            )
        )
        for owner in audience:
            if owner in participants:
                via = KnowledgeVia.PARTICIPANT
            elif owner in observers:
                via = KnowledgeVia.WITNESSED
            else:
                raise PipelineError(
                    f"fact '{spec.kind}' の開示先 {owner} は結果 {result.result_id} の"
                    "参加者でも観測者候補でもありません"
                )
            knowledge.append(
                Knowledge(
                    owner=owner,
                    fact_id=fact_id,
                    via=via,
                    acquired_at=result.game_time,
                    acquired_seq=result.commit_seq,
                )
            )
    return tuple(facts), tuple(knowledge)
