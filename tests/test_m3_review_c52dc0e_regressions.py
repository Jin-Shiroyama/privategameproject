"""c52dc0e のレビュー回帰検証。期待値は設計上の拒否・原子性に固定する。"""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import fields, replace

import pytest

from kankei.definitions import ContentPack
from kankei.model import (
    AppliedDelta,
    CasterId,
    CommitBatch,
    CommitError,
    CooldownUpdate,
    EventInstanceId,
    EventResult,
    Fact,
    FactId,
    GameTime,
    Knowledge,
    KnowledgeVia,
    PairChange,
    PairState,
    PairStore,
    Participant,
    ResultId,
    WorldState,
)
from kankei.model.pair import PairKey

A = CasterId("aoi")
B = CasterId("haru")
C = CasterId("mizuki")


def _snapshot(world: WorldState) -> dict[str, object]:
    """ストア内部値と全フィールドをコピーし、履歴・採番・時刻も比較する。"""
    result = {
        item.name: deepcopy(getattr(world, item.name))
        for item in fields(world)
        if item.name not in {"directed", "pairs"}
    }
    result["directed"] = dict(world.directed.items())
    result["pairs"] = dict(world.pairs.items())
    return result


def _capture(action: Callable[[], object]) -> Exception | None:
    """異なる例外でも状態比較を省略せず、診断として記録する。"""
    try:
        action()
    except Exception as exc:
        return exc
    return None


def _assert_rejected(
    error: Exception | None,
    expected: type[Exception],
    before: dict[str, object],
    after: dict[str, object],
) -> None:
    changes = {
        key: {"before": before[key], "after": after[key]}
        for key in before
        if before[key] != after[key]
    }
    diagnostic = f"実際の例外={error!r}\n残った状態差分={changes!r}"
    print(diagnostic)
    assert (isinstance(error, expected), after == before) == (True, True), diagnostic


def _batch(world: WorldState) -> CommitBatch:
    """全更新対象を持つ正常バッチ。A・Bのみにfactを直接開示する。"""
    seq = world.next_commit_seq
    result = EventResult(
        result_id=ResultId(world.next_result_id),
        event_instance_id=EventInstanceId(f"review-{seq}"),
        event_def_id="meet",
        outcome_id="met",
        kind="met",
        success=True,
        participants=(Participant(A, "a"), Participant(B, "b")),
        game_time=world.time,
        commit_seq=seq,
        observed_by=(C,),
        definition_version=world.definition_version,
    )
    fact = Fact(
        fact_id=FactId(world.next_fact_id),
        source_result_id=result.result_id,
        kind="met",
        subjects=result.participants,
        game_time=world.time,
        commit_seq=seq,
        audience=(A, B),
    )
    before = world.directed.stored(A, B, "favor")
    delta = AppliedDelta(
        result.result_id, A, B, "favor", 4, 4, 4, before, before + 4, before, before + 4, False
    )
    pair = world.pairs.get(A, B)
    pair.acquainted_by = result.result_id
    return CommitBatch(
        event_instance_id=result.event_instance_id,
        commit_seq=seq,
        deltas=(delta,),
        pair_changes=(PairChange(pair),),
        results=(result,),
        facts=(fact,),
        knowledge=(Knowledge(A, fact.fact_id, KnowledgeVia.PARTICIPANT, world.time, seq),),
        cooldowns=(CooldownUpdate(("confession", (A, B)), GameTime(world.time.tick + 72)),),
    )


def _world(pack: ContentPack) -> WorldState:
    """既存の履歴・採番・登録済みペアを持つ世界を用意する。"""
    world = WorldState.new(pack)
    world.commit(_batch(world))
    world.time = GameTime(24)
    return world


def test_a_commit_rejects_self_delta_without_any_partial_update(pack: ContentPack) -> None:
    world = _world(pack)
    batch = _batch(world)
    initial = world.directed.stored(A, A, "favor")
    invalid = replace(
        batch.deltas[0],
        target=A,
        stored_before=initial,
        stored_after=initial + 4,
        effective_before=initial,
        effective_after=initial + 4,
    )
    batch = replace(batch, deltas=(*batch.deltas, invalid))
    before = _snapshot(world)
    error = _capture(lambda: world.commit(batch))
    _assert_rejected(error, CommitError, before, _snapshot(world))


@pytest.mark.parametrize("key", [(B, A), (A, A)], ids=["reversed", "self"])
def test_b_pair_put_rejects_invalid_key(pack: ContentPack, key: PairKey) -> None:
    world = _world(pack)
    invalid = replace(world.pairs.get(A, B), key=key)
    before = _snapshot(world)
    error = _capture(lambda: world.pairs.put(invalid))
    _assert_rejected(error, ValueError, before, _snapshot(world))


@pytest.mark.parametrize("key", [(B, A), (A, A)], ids=["reversed", "self"])
def test_b_commit_rejects_invalid_pair_key(pack: ContentPack, key: PairKey) -> None:
    world = _world(pack)
    batch = _batch(world)
    invalid = replace(batch.pair_changes[0].state, key=key)
    batch = replace(batch, pair_changes=(PairChange(invalid),))
    before = _snapshot(world)
    error = _capture(lambda: world.commit(batch))
    _assert_rejected(error, CommitError, before, _snapshot(world))


@pytest.mark.parametrize("key", [(B, A), (A, A)], ids=["reversed", "self"])
def test_b_constructor_rejects_invalid_pair_key(pack: ContentPack, key: PairKey) -> None:
    world = _world(pack)
    invalid = replace(world.pairs.get(A, B), key=key)
    states = dict(world.pairs.items())
    states[key] = invalid
    before = {"world": _snapshot(world), "input": deepcopy(states)}
    created: list[PairStore] = []

    def construct() -> None:
        created.append(PairStore(pack.tracks, states))

    error = _capture(construct)
    print(f"生成されたストア={[dict(store.items()) for store in created]!r}")
    _assert_rejected(error, ValueError, before, {"world": _snapshot(world), "input": states})


def test_b_normalized_key_is_accessible_in_both_orders(pack: ContentPack) -> None:
    state = PairState(
        key=(A, B),
        acquainted_by=ResultId(1),
        track_states={track.id: track.initial for track in pack.tracks.values()},
    )
    store = PairStore(pack.tracks, {(A, B): state})
    assert store.get(A, B) == store.get(B, A) == state
    store.put(state)
    assert store.get(A, B) == store.get(B, A) == state
    world = _world(pack)
    batch = _batch(world)
    world.commit(batch)
    assert world.pairs.get(A, B) == world.pairs.get(B, A) == batch.pair_changes[0].state


def test_c_witness_outside_fact_audience_rejects_entire_batch(pack: ContentPack) -> None:
    world = _world(pack)
    batch = _batch(world)
    forbidden = Knowledge(
        C, batch.facts[0].fact_id, KnowledgeVia.WITNESSED, world.time, batch.commit_seq
    )
    batch = replace(batch, knowledge=(*batch.knowledge, forbidden))
    before = _snapshot(world)
    error = _capture(lambda: world.commit(batch))
    _assert_rejected(error, CommitError, before, _snapshot(world))


@pytest.mark.parametrize(
    ("owner", "via"),
    [(A, KnowledgeVia.PARTICIPANT), (B, KnowledgeVia.PARTICIPANT), (C, KnowledgeVia.WITNESSED)],
    ids=["participant-a", "participant-b", "authorized-witness-c"],
)
def test_c_direct_recipient_in_audience_succeeds(
    pack: ContentPack, owner: CasterId, via: KnowledgeVia
) -> None:
    world = _world(pack)
    batch = _batch(world)
    fact = batch.facts[0]
    if owner == C:
        fact = replace(fact, audience=(A, B, C))
    knowledge = Knowledge(owner, fact.fact_id, via, world.time, batch.commit_seq)
    batch = replace(batch, facts=(fact,), knowledge=(knowledge,))
    world.commit(batch)
    assert world.knows(owner, fact.fact_id)
    assert world.knowledge[-1] == knowledge
    assert world.results[-1] == batch.results[0]
    assert world.facts[-1] == fact
    assert world.delta_history[-1] == batch.deltas[0]
    assert world.directed.stored(A, B, "favor") == batch.deltas[0].stored_after
    assert world.next_result_id == 3
    assert world.next_fact_id == 3
    assert world.next_commit_seq == 3
