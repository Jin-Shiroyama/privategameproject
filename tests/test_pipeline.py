"""M5: イベントパイプライン(§7・§16・§19)。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, replace
from random import Random

import pytest

from kankei.definitions import ContentPack
from kankei.definitions.schema import EventDef, OutcomeDef
from kankei.engine import Candidate, EventContext, Pipeline, PipelineError, collect_candidates, draw
from kankei.engine.pipeline import SlotReport
from kankei.model import (
    Calendar,
    CasterId,
    CommitError,
    GameTime,
    KnowledgeVia,
    ResultId,
    WorldState,
)

AOI = CasterId("aoi")
HARU = CasterId("haru")
MIZUKI = CasterId("mizuki")


# --- ヘルパ ---------------------------------------------------------------------


def _snapshot(world: WorldState) -> dict[str, object]:
    out = {
        f.name: deepcopy(getattr(world, f.name))
        for f in fields(world)
        if f.name not in {"directed", "pairs"}
    }
    out["directed"] = dict(world.directed.items())
    out["pairs"] = dict(world.pairs.items())
    return out


def _run_slots(pack: ContentPack, seed: int, slots: int) -> tuple[WorldState, list[SlotReport]]:
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(seed))
    reports: list[SlotReport] = []
    for i in range(slots):
        world.time = GameTime(i * pack.settings.ticks_per_slot)
        reports.append(pipeline.run_slot(world))
    return world, reports


def _acquaint(pipeline: Pipeline, world: WorldState, a: CasterId, b: CasterId) -> None:
    pipeline.run_event(world, "meet", {"a": a, "b": b})


def _set_romance(world: WorldState, src: CasterId, dst: CasterId, value: int) -> None:
    world.directed.set_stored(src, dst, "romance", value)


def _candidate_keys(pack: ContentPack, world: WorldState) -> list[tuple[str, tuple[CasterId, ...]]]:
    return [c.key for c in collect_candidates(pack, EventContext.capture(world, pack))]


# --- 決定性 -----------------------------------------------------------------------


def test_same_seed_same_definitions_reproduce_everything(pack: ContentPack) -> None:
    w1, r1 = _run_slots(pack, seed=7, slots=80)
    w2, r2 = _run_slots(pack, seed=7, slots=80)
    assert [(r.chosen, r.candidates) for r in r1] == [(r.chosen, r.candidates) for r in r2]
    assert _snapshot(w1) == _snapshot(w2)
    kinds = {r.kind for r in w1.results}
    # 検証用定義で一通りのイベントが実際に起きていること
    assert {"met", "interaction", "infatuation", "confession"} <= kinds


def test_different_seed_diverges(pack: ContentPack) -> None:
    w1, _ = _run_slots(pack, seed=1, slots=40)
    w2, _ = _run_slots(pack, seed=2, slots=40)
    assert [r.kind for r in w1.results] != [r.kind for r in w2.results]


# --- 乱数消費 ---------------------------------------------------------------------


def test_rng_untouched_when_no_candidates(pack: ContentPack) -> None:
    empty = replace(pack, events={}, templates=())
    world = WorldState.new(empty)
    rng = Random(3)
    before = rng.getstate()
    report = Pipeline(empty, rng).run_slot(world)
    assert report.chosen is None and report.batch is None
    assert rng.getstate() == before
    assert world.results == [] and world.next_commit_seq == 1


def test_rng_consumed_exactly_once_per_slot_with_candidates(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    rng = Random(11)
    Pipeline(pack, rng).run_slot(world)
    expected = Random(11)
    expected.random()
    assert rng.getstate() == expected.getstate()


def test_no_module_other_than_draw_takes_rng() -> None:
    """乱数を消費する箇所は candidates.draw のみ(補助検査)。"""
    import inspect

    from kankei.engine import apply, candidates, context, evaluate, facts, modifiers, outcome
    from kankei.engine import pipeline as pipeline_mod

    for module in (apply, context, evaluate, facts, modifiers, outcome):
        assert "Random" not in inspect.getsource(module)
    src = inspect.getsource(pipeline_mod)
    assert src.count("self._rng") == 2  # __init__ での保持と draw への受け渡しのみ
    assert src.count("draw(candidates, self._rng)") == 1
    assert "Random" in inspect.getsource(candidates)


# --- 候補の正規化・重複排除・順序 ---------------------------------------------------


def test_pair_events_are_deduplicated_and_sorted(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    keys = _candidate_keys(pack, world)
    assert keys == [
        ("meet", (AOI, HARU)),
        ("meet", (AOI, MIZUKI)),
        ("meet", (HARU, MIZUKI)),
    ]


def test_candidate_order_is_independent_of_registration_order(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(0))
    _acquaint(pipeline, world, AOI, HARU)
    _acquaint(pipeline, world, AOI, MIZUKI)
    _set_romance(world, AOI, HARU, 50)
    _set_romance(world, HARU, AOI, 50)
    world.directed.set_stored(AOI, MIZUKI, "favor", 20)
    reversed_world = deepcopy(world)
    reversed_world.casters = dict(reversed(list(world.casters.items())))
    shuffled_events = replace(pack, events=dict(reversed(list(pack.events.items()))))

    baseline = collect_candidates(pack, EventContext.capture(world, pack))
    assert baseline == collect_candidates(pack, EventContext.capture(reversed_world, pack))
    assert baseline == collect_candidates(
        shuffled_events, EventContext.capture(world, shuffled_events)
    )
    keys = [c.key for c in baseline]
    assert keys == sorted(keys)
    assert ("confession", (AOI, HARU)) in keys and ("confession", (HARU, AOI)) in keys
    assert ("crush", (AOI, MIZUKI)) in keys  # favor 20 ≥ 12
    assert ("crush", (MIZUKI, AOI)) not in keys  # mizuki→aoi の favor は 0、compat は 10 < 20
    assert ("chat", (AOI, HARU)) in keys and ("chat", (HARU, AOI)) not in keys


def test_draw_is_weighted_and_deterministic() -> None:
    a = Candidate("x", (AOI,), (("actor", AOI),), weight=1)
    b = Candidate("y", (HARU,), (("actor", HARU),), weight=99)
    picks = [draw((a, b), Random(s)) for s in range(50)]
    assert picks.count(b) > 40
    assert draw((a, b), Random(5)) == draw((a, b), Random(5))
    assert draw((), Random(5)) is None


# --- 告白: 適用前値による決定的判定 --------------------------------------------------


def _confession_world(pack: ContentPack, target_to_actor: int) -> tuple[Pipeline, WorldState]:
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(0))
    _acquaint(pipeline, world, AOI, HARU)
    _set_romance(world, AOI, HARU, 50)
    _set_romance(world, HARU, AOI, target_to_actor)
    return pipeline, world


def test_confession_reply_uses_pre_application_values(pack: ContentPack) -> None:
    # 閾値30に対し29。成功時 delta(+5) を足せば届くが、適用前値で判定するので拒否
    pipeline, world = _confession_world(pack, 29)
    batch = pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})
    assert [(r.kind, r.success) for r in batch.results] == [("confession", False)]
    assert world.pairs.get(AOI, HARU).state("romance") == "none"
    assert world.directed.stored(HARU, AOI, "romance") == 29
    assert world.directed.stored(AOI, HARU, "romance") == 45  # 拒否 delta -5、ゼロにはしない

    pipeline, world = _confession_world(pack, 30)
    batch = pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})
    assert [(r.kind, r.success) for r in batch.results] == [
        ("confession", True),
        ("relationship_established", True),
    ]
    assert batch.results[1].related_result_id == batch.results[0].result_id
    assert batch.results[1].track_change is not None
    assert batch.results[1].track_change.state_after == "lovers"
    assert world.pairs.get(AOI, HARU).state("romance") == "lovers"
    assert world.directed.stored(HARU, AOI, "romance") == 35


def test_same_inputs_same_reply(pack: ContentPack) -> None:
    replies = []
    for _ in range(3):
        pipeline, world = _confession_world(pack, 30)
        batch = pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})
        replies.append(tuple((r.kind, r.success) for r in batch.results))
    assert len(set(replies)) == 1


# --- クールダウンと再告白 ------------------------------------------------------------


def test_rejection_sets_directed_cooldown_and_re_confession_returns(pack: ContentPack) -> None:
    pipeline, world = _confession_world(pack, 10)
    cal = Calendar(pack.settings.ticks_per_day)
    world.time = GameTime(5)
    pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})
    assert world.cooldowns == {("confession", (AOI, HARU)): cal.plus_days(GameTime(5), 3)}

    world.time = GameTime(5)
    assert ("confession", (AOI, HARU)) not in _candidate_keys(pack, world)
    world.time = cal.plus_days(GameTime(5), 3).plus_ticks(-1)
    assert ("confession", (AOI, HARU)) not in _candidate_keys(pack, world)
    # 拒否時刻から指定日数経過で復帰。状況改善は要求しない(恋愛度は45のまま)
    world.time = cal.plus_days(GameTime(5), 3)
    assert ("confession", (AOI, HARU)) in _candidate_keys(pack, world)


def test_reverse_direction_confession_is_not_blocked(pack: ContentPack) -> None:
    pipeline, world = _confession_world(pack, 10)
    pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})
    _set_romance(world, HARU, AOI, 50)
    keys = _candidate_keys(pack, world)
    assert ("confession", (HARU, AOI)) in keys
    assert ("confession", (AOI, HARU)) not in keys


def test_acceptance_sets_no_cooldown(pack: ContentPack) -> None:
    pipeline, world = _confession_world(pack, 30)
    pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})
    assert world.cooldowns == {}


# --- 一括確定 ------------------------------------------------------------------------


def test_everything_commits_in_one_unit(pack: ContentPack) -> None:
    pipeline, world = _confession_world(pack, 30)
    seq_before = world.next_commit_seq
    batch = pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})
    seq = batch.commit_seq
    assert seq == seq_before and world.next_commit_seq == seq + 1
    assert {r.commit_seq for r in batch.results} == {seq}
    assert {f.commit_seq for f in batch.facts} == {seq}
    assert {k.acquired_seq for k in batch.knowledge} == {seq}
    assert [d.result_id for d in batch.deltas] == [batch.results[0].result_id]
    assert world.results[-2:] == list(batch.results)
    assert world.facts[-3:] == list(batch.facts)
    assert world.delta_history[-1] == batch.deltas[0]
    assert batch.event_instance_id in world.applied_event_ids
    assert [c.state.key for c in batch.pair_changes] == [(AOI, HARU)]


def test_commit_failure_leaves_nothing(pack: ContentPack, monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline, world = _confession_world(pack, 30)
    before = _snapshot(world)
    import kankei.engine.pipeline as pipeline_mod

    original = pipeline_mod.assemble_batch

    def broken(*args: object, **kwargs: object) -> object:
        batch = original(*args, **kwargs)  # type: ignore[arg-type]
        return replace(batch, commit_seq=batch.commit_seq + 100)

    monkeypatch.setattr(pipeline_mod, "assemble_batch", broken)
    with pytest.raises(CommitError):
        pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})
    assert _snapshot(world) == before


# --- 開示範囲と知識 -----------------------------------------------------------------


def test_disclosure_splits_statement_and_reply(pack: ContentPack) -> None:
    pipeline, world = _confession_world(pack, 30)
    batch = pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})
    by_kind = {f.kind: f for f in batch.facts}
    assert set(by_kind) == {"confession_made", "confession_accepted", "relationship_established"}
    assert by_kind["confession_made"].audience == (AOI, HARU, MIZUKI)
    assert by_kind["confession_accepted"].audience == (AOI, HARU)
    assert by_kind["relationship_established"].audience == (AOI, HARU, MIZUKI)
    assert by_kind["relationship_established"].state_after == "lovers"
    assert by_kind["confession_accepted"].source_result_id == batch.results[0].result_id

    def via(owner: CasterId, kind: str) -> KnowledgeVia | None:
        for k in batch.knowledge:
            if k.owner == owner and k.fact_id == by_kind[kind].fact_id:
                return k.via
        return None

    assert via(AOI, "confession_made") is KnowledgeVia.PARTICIPANT
    assert via(MIZUKI, "confession_made") is KnowledgeVia.WITNESSED
    assert via(MIZUKI, "confession_accepted") is None
    assert via(HARU, "confession_accepted") is KnowledgeVia.PARTICIPANT
    assert not world.knows(MIZUKI, by_kind["confession_accepted"].fact_id)


def test_rejected_reply_is_participants_only(pack: ContentPack) -> None:
    pipeline, world = _confession_world(pack, 10)
    batch = pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})
    kinds = [f.kind for f in batch.facts]
    assert kinds == ["confession_made", "confession_rejected"]
    assert batch.facts[1].audience == (AOI, HARU)
    assert batch.facts[0].audience == (AOI, HARU, MIZUKI)


def test_internal_deltas_do_not_become_facts(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(0))
    _acquaint(pipeline, world, AOI, HARU)
    world.directed.set_stored(AOI, HARU, "favor", 20)
    chat = pipeline.run_event(world, "chat", {"a": AOI, "b": HARU})
    crush = pipeline.run_event(world, "crush", {"actor": AOI, "target": HARU})
    assert chat.facts == () and chat.knowledge == ()
    assert crush.facts == () and crush.knowledge == ()
    assert len(chat.deltas) == 4 and len(crush.deltas) == 1
    assert crush.results[0].observed_by == ()
    assert world.directed.stored(AOI, HARU, "romance") == 15


def test_meet_establishes_acquaintance_only(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    batch = Pipeline(pack, Random(0)).run_event(world, "meet", {"a": HARU, "b": AOI})
    state = world.pairs.get(AOI, HARU)
    assert state.acquainted_by == batch.results[0].result_id
    assert state.track_states == {"friendship": "none", "romance": "none"}
    assert batch.facts[0].kind == "met" and batch.facts[0].audience == (HARU, AOI, MIZUKI)


# --- 定義の不備は commit 前に停止する ---------------------------------------------------


def test_undefined_transition_stops_before_commit(pack: ContentPack) -> None:
    pipeline, world = _confession_world(pack, 30)
    pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})  # 恋人になる
    before = _snapshot(world)
    with pytest.raises(PipelineError) as exc:
        pipeline.run_event(world, "confession", {"actor": AOI, "target": HARU})
    msg = str(exc.value)
    for needle in ("confession", "accepted", "aoi", "haru", "romance", "lovers", "lovers→lovers"):
        assert needle in msg
    assert _snapshot(world) == before
    assert world.integrity_failure is None


def test_re_meeting_acquainted_pair_stops_before_commit(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(0))
    _acquaint(pipeline, world, AOI, HARU)
    before = _snapshot(world)
    with pytest.raises(PipelineError, match="既に面識"):
        pipeline.run_event(world, "meet", {"a": AOI, "b": HARU})
    assert _snapshot(world) == before


def test_no_matching_outcome_stops_before_commit(pack: ContentPack) -> None:
    from kankei.definitions.conditions import AxisAtLeast

    chat: EventDef = pack.events["chat"]
    guarded = replace(chat.outcomes[0], when=(AxisAtLeast("favor", "a", "b", 1000),))
    broken_chat = replace(chat, outcomes=(guarded,))
    broken = replace(pack, events={**pack.events, "chat": broken_chat})
    world = WorldState.new(broken)
    pipeline = Pipeline(broken, Random(0))
    _acquaint(pipeline, world, AOI, HARU)
    before = _snapshot(world)
    with pytest.raises(PipelineError, match="結果分岐がどれも成立しません"):
        pipeline.run_event(world, "chat", {"a": AOI, "b": HARU})
    assert _snapshot(world) == before


def test_run_event_rejects_wrong_binding(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    with pytest.raises(PipelineError):
        Pipeline(pack, Random(0)).run_event(world, "confession", {"a": AOI, "b": HARU})


def test_outcome_type_is_definition(pack: ContentPack) -> None:
    assert isinstance(pack.events["confession"].outcomes[0], OutcomeDef)
    assert ResultId(1) == 1
