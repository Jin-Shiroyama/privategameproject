"""M3: コアモデル(directed / pair / 結果・fact・知識 / 一括確定)。"""

from __future__ import annotations

import pytest

from kankei.definitions import ContentPack
from kankei.definitions.schema import AxisDef
from kankei.model import (
    AppliedDelta,
    Calendar,
    CasterId,
    CommitBatch,
    CommitError,
    CooldownUpdate,
    DirectedStore,
    EventInstanceId,
    EventResult,
    Fact,
    FactId,
    GameTime,
    Knowledge,
    KnowledgeVia,
    PairChange,
    PairStore,
    Participant,
    ResultId,
    WorldState,
    pair_key,
)

AOI = CasterId("aoi")
HARU = CasterId("haru")
MIZUKI = CasterId("mizuki")


# --- 時刻 ---------------------------------------------------------------------


def test_calendar_conversions() -> None:
    cal = Calendar(ticks_per_day=24)
    t = GameTime(50)
    assert cal.day(t) == 2
    assert cal.tick_of_day(t) == 2
    assert cal.plus_days(t, 3) == GameTime(122)
    assert cal.start_of_day(2) == GameTime(48)
    assert GameTime(3) < GameTime(4)
    with pytest.raises(ValueError):
        GameTime(-1)


# --- directed ------------------------------------------------------------------


def test_directed_values_are_independent_per_direction(pack: ContentPack) -> None:
    store = DirectedStore(pack.axes)
    assert store.stored(AOI, HARU, "romance") == 0
    store.set_stored(AOI, HARU, "romance", 40)
    assert store.stored(AOI, HARU, "romance") == 40
    assert store.stored(HARU, AOI, "romance") == 0
    assert store.stored(AOI, HARU, "favor") == 0


def test_directed_clamps_to_axis_range(pack: ContentPack) -> None:
    store = DirectedStore(pack.axes)
    assert store.set_stored(AOI, HARU, "favor", 500) == 100
    assert store.set_stored(AOI, HARU, "favor", -500) == -100
    with pytest.raises(ValueError):
        store.set_stored(AOI, AOI, "favor", 1)
    with pytest.raises(KeyError):
        store.stored(AOI, HARU, "trust")


def test_effective_passes_through_stored_by_default(pack: ContentPack) -> None:
    store = DirectedStore(pack.axes)
    store.set_stored(AOI, HARU, "romance", 80)
    assert store.effective(AOI, HARU, "romance") == 80


def test_effective_resolver_is_the_cap_hook(pack: ContentPack) -> None:
    """cap の接続点。テスト内でのみ上限30の resolver を差し込み、保存値は保持される。"""

    class CapAt30:
        def resolve(self, source: CasterId, target: CasterId, axis: AxisDef, stored: int) -> int:
            return min(stored, 30) if axis.id == "romance" else stored

    store = DirectedStore(pack.axes, resolver=CapAt30())
    store.set_stored(AOI, HARU, "romance", 80)
    assert store.stored(AOI, HARU, "romance") == 80
    assert store.effective(AOI, HARU, "romance") == 30
    assert store.effective(AOI, HARU, "favor") == 0


def test_directed_items_are_sorted(pack: ContentPack) -> None:
    store = DirectedStore(pack.axes)
    store.set_stored(MIZUKI, AOI, "favor", 1)
    store.set_stored(AOI, HARU, "favor", 2)
    assert [k for k, _ in store.items()] == [(AOI, HARU, "favor"), (MIZUKI, AOI, "favor")]


# --- pair ------------------------------------------------------------------------


def test_pair_key_is_order_independent() -> None:
    assert pair_key(HARU, AOI) == pair_key(AOI, HARU) == (AOI, HARU)
    with pytest.raises(ValueError):
        pair_key(AOI, AOI)


def test_pair_default_state_and_acquaintance_independent_of_friendship(pack: ContentPack) -> None:
    pairs = PairStore(pack.tracks)
    state = pairs.get(HARU, AOI)
    assert state.acquainted is False
    assert state.track_states == {"friendship": "none", "romance": "none"}
    state.acquainted_by = ResultId(1)
    pairs.put(state)
    again = pairs.get(AOI, HARU)
    assert again.acquainted is True
    assert again.acquainted_by == ResultId(1)
    assert again.state("friendship") == "none"


def test_pair_get_returns_copy(pack: ContentPack) -> None:
    pairs = PairStore(pack.tracks)
    state = pairs.get(AOI, HARU)
    state.track_states["romance"] = "lovers"
    assert pairs.get(AOI, HARU).state("romance") == "none"


def test_pair_put_rejects_unknown_state(pack: ContentPack) -> None:
    pairs = PairStore(pack.tracks)
    state = pairs.get(AOI, HARU)
    state.track_states["romance"] = "married"
    with pytest.raises(ValueError):
        pairs.put(state)
    state.track_states["romance"] = "none"
    state.track_states["trust"] = "x"
    with pytest.raises(KeyError):
        pairs.put(state)


# --- 一括確定 ----------------------------------------------------------------------


def _result(world: WorldState, kind: str, **kw: object) -> EventResult:
    base: dict[str, object] = {
        "result_id": ResultId(world.next_result_id),
        "event_instance_id": EventInstanceId("ev-1"),
        "event_def_id": "meet",
        "outcome_id": "met",
        "kind": kind,
        "success": True,
        "participants": (Participant(AOI, "a"), Participant(HARU, "b")),
        "game_time": world.time,
        "commit_seq": world.next_commit_seq,
        "observed_by": (MIZUKI,),
        "definition_version": world.definition_version,
    }
    base.update(kw)
    return EventResult(**base)  # type: ignore[arg-type]


def test_world_new_from_pack(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    assert world.caster_ids() == (AOI, HARU, MIZUKI)
    assert world.caster(AOI).traits == frozenset({"caretaker", "sociable"})
    assert world.time == GameTime(0)
    assert world.definition_version == pack.version
    assert world.next_result_id == 1 and world.next_commit_seq == 1


def test_commit_applies_everything_in_one_unit(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    result = _result(world, "met")
    fact = Fact(
        fact_id=FactId(1),
        source_result_id=result.result_id,
        kind="met",
        subjects=result.participants,
        game_time=world.time,
        commit_seq=1,
        audience=(AOI, HARU, MIZUKI),
    )
    knowledge = tuple(
        Knowledge(owner=o, fact_id=FactId(1), via=via, acquired_at=world.time, acquired_seq=1)
        for o, via in (
            (AOI, KnowledgeVia.PARTICIPANT),
            (HARU, KnowledgeVia.PARTICIPANT),
            (MIZUKI, KnowledgeVia.WITNESSED),
        )
    )
    pair = world.pairs.get(AOI, HARU)
    pair.acquainted_by = result.result_id
    delta = AppliedDelta(
        result_id=result.result_id,
        source=AOI,
        target=HARU,
        axis="favor",
        candidate=4,
        after_modifier=4,
        applied=4,
        stored_before=0,
        stored_after=4,
        effective_before=0,
        effective_after=4,
        blocked=False,
    )
    batch = CommitBatch(
        event_instance_id=EventInstanceId("ev-1"),
        commit_seq=1,
        deltas=(delta,),
        pair_changes=(PairChange(pair),),
        results=(result,),
        facts=(fact,),
        knowledge=knowledge,
        cooldowns=(CooldownUpdate(("confession", (AOI, HARU)), GameTime(72)),),
    )
    world.commit(batch)

    assert world.directed.stored(AOI, HARU, "favor") == 4
    assert world.pairs.get(AOI, HARU).acquainted_by == ResultId(1)
    assert world.results == [result]
    assert world.facts == [fact]
    assert world.knows(MIZUKI, FactId(1)) and world.knows(AOI, FactId(1))
    assert world.cooldown_until(("confession", (AOI, HARU))) == GameTime(72)
    assert EventInstanceId("ev-1") in world.applied_event_ids
    assert world.next_result_id == 2
    assert world.next_fact_id == 2
    assert world.next_commit_seq == 2
    assert world.delta_history == [delta]


def _snapshot(world: WorldState) -> tuple[object, ...]:
    return (
        dict(world.directed.items()),
        list(world.pairs.items()),
        list(world.results),
        list(world.facts),
        list(world.knowledge),
        list(world.delta_history),
        dict(world.cooldowns),
        set(world.applied_event_ids),
        world.next_result_id,
        world.next_fact_id,
        world.next_commit_seq,
    )


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_event",
        "wrong_seq",
        "stale_before",
        "noncontiguous_result_id",
        "fact_without_result",
        "knowledge_without_fact",
        "duplicate_knowledge",
        "bad_pair_state",
        "wrong_version",
    ],
)
def test_commit_rejects_invalid_batch_without_partial_update(pack: ContentPack, case: str) -> None:
    world = WorldState.new(pack)
    world.applied_event_ids.add(EventInstanceId("done"))
    before = _snapshot(world)

    result = _result(world, "met")
    fact = Fact(
        fact_id=FactId(1),
        source_result_id=ResultId(1),
        kind="met",
        subjects=result.participants,
        game_time=world.time,
        commit_seq=1,
        audience=(AOI, HARU),
    )
    know = Knowledge(AOI, FactId(1), KnowledgeVia.PARTICIPANT, world.time, 1)
    delta = AppliedDelta(ResultId(1), AOI, HARU, "favor", 4, 4, 4, 0, 4, 0, 4, False)
    pair = world.pairs.get(AOI, HARU)
    batch = CommitBatch(
        EventInstanceId("ev-1"), 1, (delta,), (PairChange(pair),), (result,), (fact,), (know,)
    )

    match case:
        case "duplicate_event":
            batch = CommitBatch(
                EventInstanceId("done"),
                1,
                results=(_result(world, "met", event_instance_id=EventInstanceId("done")),),
            )
        case "wrong_seq":
            batch = CommitBatch(EventInstanceId("ev-1"), 5)
        case "stale_before":
            stale = AppliedDelta(ResultId(1), AOI, HARU, "favor", 4, 4, 4, 10, 14, 10, 14, False)
            batch = CommitBatch(
                EventInstanceId("ev-1"), 1, (stale,), (), (result,), (fact,), (know,)
            )
        case "noncontiguous_result_id":
            batch = CommitBatch(
                EventInstanceId("ev-1"), 1, results=(_result(world, "met", result_id=ResultId(7)),)
            )
        case "fact_without_result":
            batch = CommitBatch(EventInstanceId("ev-1"), 1, facts=(fact,))
        case "knowledge_without_fact":
            batch = CommitBatch(EventInstanceId("ev-1"), 1, results=(result,), knowledge=(know,))
        case "duplicate_knowledge":
            batch = CommitBatch(
                EventInstanceId("ev-1"), 1, results=(result,), facts=(fact,), knowledge=(know, know)
            )
        case "bad_pair_state":
            pair.track_states["romance"] = "married"
            batch = CommitBatch(EventInstanceId("ev-1"), 1, pair_changes=(PairChange(pair),))
        case "wrong_version":
            batch = CommitBatch(
                EventInstanceId("ev-1"),
                1,
                results=(_result(world, "met", definition_version="other"),),
            )

    with pytest.raises(CommitError):
        world.commit(batch)
    assert _snapshot(world) == before


def test_commit_chains_multiple_deltas_on_same_key(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    result = _result(world, "interaction")
    d1 = AppliedDelta(ResultId(1), AOI, HARU, "favor", 4, 4, 4, 0, 4, 0, 4, False)
    d2 = AppliedDelta(ResultId(1), AOI, HARU, "favor", 3, 3, 3, 4, 7, 4, 7, False)
    world.commit(CommitBatch(EventInstanceId("ev-1"), 1, (d1, d2), results=(result,)))
    assert world.directed.stored(AOI, HARU, "favor") == 7
