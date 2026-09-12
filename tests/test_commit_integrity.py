"""一括確定の整合性。

M3レビュー(c52dc0e)の回帰テストを恒久化したもの。期待値は設計上の拒否・原子性に固定する。
"""

import shutil
from collections.abc import Callable
from copy import deepcopy
from dataclasses import fields, replace
from pathlib import Path
from random import Random
from typing import Any

import pytest
import yaml

from kankei.definitions import ContentPack, load_content_pack
from kankei.engine import Candidate, EventContext, Pipeline, PipelineError, collect_candidates
from kankei.engine.outcome import DeltaCandidate
from kankei.engine.pipeline import SlotReport
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


# --- 追加防御: 適用中の例外は修復不能として隔離する ------------------------------


def test_apply_failure_marks_world_unrecoverable(
    pack: ContentPack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """検証を通過した後に適用段で例外が起きた場合、FatalCommitError となり以後の確定を拒否する。"""
    from kankei.model import FatalCommitError

    world = _world(pack)
    batch = _batch(world)

    def broken(*args: object, **kwargs: object) -> int:
        raise RuntimeError("simulated apply failure")

    monkeypatch.setattr(world.directed, "set_stored", broken)
    with pytest.raises(FatalCommitError):
        world.commit(batch)
    assert world.integrity_failure is not None
    monkeypatch.undo()
    # 修復不能な世界は、正常なバッチでも確定を受け付けない(保存側はこのフラグで停止する)
    with pytest.raises(CommitError, match="修復不能"):
        world.commit(_batch(world))


# --- 追加防御: via の整合(§19 の二段構造) -------------------------------------------


def test_c_participant_via_requires_being_a_participant(pack: ContentPack) -> None:
    world = _world(pack)
    batch = _batch(world)
    fact = replace(batch.facts[0], audience=(A, B, C))
    # C は開示先だが参加者ではないので participant 経路は不整合
    bad = Knowledge(C, fact.fact_id, KnowledgeVia.PARTICIPANT, world.time, batch.commit_seq)
    batch = replace(batch, facts=(fact,), knowledge=(bad,))
    before = _snapshot(world)
    error = _capture(lambda: world.commit(batch))
    _assert_rejected(error, CommitError, before, _snapshot(world))


def test_c_witnessed_via_requires_being_an_observer_candidate(pack: ContentPack) -> None:
    world = _world(pack)
    batch = _batch(world)
    # A は参加者であり観測者候補ではないので witnessed 経路は不整合
    bad = Knowledge(A, batch.facts[0].fact_id, KnowledgeVia.WITNESSED, world.time, batch.commit_seq)
    batch = replace(batch, knowledge=(bad,))
    before = _snapshot(world)
    error = _capture(lambda: world.commit(batch))
    _assert_rejected(error, CommitError, before, _snapshot(world))


def test_c_told_is_not_accepted_in_stage1(pack: ContentPack) -> None:
    world = _world(pack)
    batch = _batch(world)
    told = Knowledge(
        C, batch.facts[0].fact_id, KnowledgeVia.TOLD, world.time, batch.commit_seq, learned_from=A
    )
    batch = replace(batch, knowledge=(*batch.knowledge, told))
    before = _snapshot(world)
    error = _capture(lambda: world.commit(batch))
    _assert_rejected(error, CommitError, before, _snapshot(world))


# --- M5レビュー: 履歴・版・開始時点の不変性・決定性・境界 -------------------------


def _review_pack(
    content_dir: Path,
    destination: Path,
    version: str,
    favor_deltas: tuple[int, ...] | None = None,
) -> ContentPack:
    """一時領域のYAMLのみを変更し、通常ローダの検証を経由する。"""
    shutil.copytree(content_dir, destination)
    manifest = destination / "pack.yaml"
    metadata: dict[str, Any] = yaml.safe_load(manifest.read_text())
    metadata["version"] = version
    manifest.write_text(yaml.safe_dump(metadata), encoding="utf-8")
    if favor_deltas is not None:
        events_path = destination / "events.yaml"
        data: dict[str, Any] = yaml.safe_load(events_path.read_text())
        chat = next(event for event in data["events"] if event["id"] == "chat")
        chat["outcomes"][0]["results"][0]["deltas"] = [
            {"from": "a", "to": "b", "axis": "favor", "value": value} for value in favor_deltas
        ]
        events_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_content_pack(destination)


@pytest.mark.parametrize(
    ("initial", "increments", "expected"),
    [
        (0, (4, 6), ((0, 4, 4), (4, 10, 6))),
        (95, (4, 6), ((95, 99, 4), (99, 100, 1))),
        (0, (8, -3), ((0, 8, 8), (8, 5, -3))),
    ],
    ids=["successive", "upper-bound", "mixed-signs"],
)
def test_m5_each_delta_retains_its_effective_history(
    content_dir: Path,
    tmp_path: Path,
    initial: int,
    increments: tuple[int, ...],
    expected: tuple[tuple[int, int, int], ...],
) -> None:
    pack = _review_pack(content_dir, tmp_path / "content", "review-v1", increments)
    world = WorldState.new(pack)
    world.directed.set_stored(A, B, "favor", initial)
    batch = Pipeline(pack, Random(7)).run_event(world, "chat", {"a": A, "b": B})
    stored = tuple((d.stored_before, d.stored_after, d.applied) for d in batch.deltas)
    effective = tuple((d.effective_before, d.effective_after, d.applied) for d in batch.deltas)
    print(f"保存履歴={stored}; 有効履歴={effective}; 期待={expected}; 実行例外なし")
    assert world.delta_history == list(batch.deltas)
    assert all(d.result_id == batch.results[0].result_id for d in world.delta_history)
    assert world.results == list(batch.results)
    assert world.directed.stored(A, B, "favor") == expected[-1][1]
    assert world.directed.effective(A, B, "favor") == expected[-1][1]
    assert (stored, effective) == (expected, expected)


@pytest.mark.parametrize("entry", ["run_slot", "run_event"])
@pytest.mark.parametrize("changed_delta", [False, True], ids=["version-only", "version-and-delta"])
def test_m5_version_mismatch_rejected_before_selection(
    content_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
    changed_delta: bool,
) -> None:
    import kankei.engine.pipeline as pipeline_module

    v1 = _review_pack(content_dir, tmp_path / "v1", "v1")
    v2 = _review_pack(content_dir, tmp_path / "v2", "v2", (17,) if changed_delta else None)
    world = WorldState.new(v1)
    Pipeline(v1, Random(0)).run_event(world, "meet", {"a": A, "b": B})
    v2 = replace(v2, events={"chat": v2.events["chat"]})
    rng = Random(7)
    pipeline = Pipeline(v2, rng)
    before = _snapshot(world)
    rng_before = rng.getstate()
    collections: list[bool] = []

    def traced_collect(pack: ContentPack, ctx: EventContext) -> tuple[Candidate, ...]:
        collections.append(True)
        return collect_candidates(pack, ctx)

    monkeypatch.setattr(pipeline_module, "collect_candidates", traced_collect)

    def action() -> object:
        if entry == "run_slot":
            return pipeline.run_slot(world)
        return pipeline.run_event(world, "chat", {"a": A, "b": B})

    error = _capture(action)
    after = _snapshot(world)
    changes = {key: (before[key], after[key]) for key in before if before[key] != after[key]}
    print(f"実際の例外={error!r}; 世界差分={changes!r}")
    print(f"候補収集回数={len(collections)}; RNG不変={rng.getstate() == rng_before}")
    assert (
        isinstance(error, PipelineError),
        after == before,
        rng.getstate() == rng_before,
        collections,
    ) == (True, True, True, [])


@pytest.mark.parametrize("entry", ["run_slot", "run_event"])
def test_m5_matching_version_commits(pack: ContentPack, entry: str) -> None:
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(7))
    if entry == "run_slot":
        batch = pipeline.run_slot(world).batch
    else:
        batch = pipeline.run_event(world, "meet", {"a": A, "b": B})
    assert batch is not None
    assert world.results == list(batch.results)
    assert all(result.definition_version == pack.version for result in batch.results)
    assert world.next_commit_seq == 2


def test_m5_context_readonly_structure_diagnostic(pack: ContentPack) -> None:
    """恒等補正とは別の構造診断。観測値50を正常な期待値として固定しない。"""
    reads: list[int] = []

    class Writer:
        rule_id = "review-context-writer"

        def adjust(self, ctx: EventContext, delta: DeltaCandidate, value: int) -> int:
            ctx.directed.set_stored(A, B, "romance", 50)
            return value

    class Reader:
        rule_id = "review-context-reader"

        def adjust(self, ctx: EventContext, delta: DeltaCandidate, value: int) -> int:
            if (delta.source, delta.target, delta.axis) == (A, B, "favor"):
                observed = ctx.effective(A, B, "romance")
                reads.append(observed)
                return observed
            return value

    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(7), modifiers=(Writer(), Reader()))
    error = _capture(lambda: pipeline.run_event(world, "chat", {"a": A, "b": B}))
    actual_favor = world.directed.stored(A, B, "favor")
    print(
        f"構造診断: 読取={reads}; 確定favor={actual_favor}; "
        f"世界romance={world.directed.stored(A, B, 'romance')}; 例外={error!r}"
    )
    assert error is None
    assert world.directed.stored(A, B, "romance") == 0
    assert (reads, actual_favor) == ([0], 0), "開始時点スナップショットが補正中に変化した"


@pytest.mark.parametrize("only_meet", [False, True], ids=["full-content", "including-empty-slots"])
def test_m5_two_independent_2000_slot_runs_match(pack: ContentPack, only_meet: bool) -> None:
    if only_meet:
        pack = replace(pack, events={"meet": pack.events["meet"]})

    def run() -> tuple[list[SlotReport], dict[str, object], object]:
        independent_pack = deepcopy(pack)
        world = WorldState.new(independent_pack)
        rng = Random(7)
        pipeline = Pipeline(independent_pack, rng)
        reports = []
        for _ in range(2000):
            reports.append(pipeline.run_slot(world))
            world.time = GameTime(world.time.tick + pack.settings.ticks_per_slot)
        return reports, _snapshot(world), rng.getstate()

    first_reports, first_world, first_rng = run()
    second_reports, second_world, second_rng = run()
    assert first_reports == second_reports  # 候補・選択・None・時刻・CommitBatchすべて
    assert first_world == second_world  # 結果列・確定順・全採番・全世界フィールド
    assert first_rng == second_rng
    no_events = sum(report.batch is None for report in first_reports)
    if only_meet:
        assert no_events == 1997
    assert first_world["time"] == GameTime(2000 * pack.settings.ticks_per_slot)
    print(
        f"2000スロット×独立2回: 経過日数={2000 / pack.settings.event_slots_per_day}; "
        f"最終時刻={first_world['time']}; 無イベント={no_events}; 全比較一致"
    )


def _assert_empty_slot(pack: ContentPack, world: WorldState, rng: Random) -> None:
    """呼出前の時刻を基準にし、テスト側の時間更新とエンジンの変更を区別する。"""
    before = _snapshot(world)
    rng_before = rng.getstate()
    assert pack.events
    assert collect_candidates(pack, EventContext.capture(world, pack)) == ()
    report = Pipeline(pack, rng).run_slot(world)
    assert report.candidates == ()
    assert report.batch is None and report.chosen is None
    assert _snapshot(world) == before
    assert rng.getstate() == rng_before


def test_m5_existing_event_without_eligible_candidates(pack: ContentPack) -> None:
    chat_only = replace(pack, events={"chat": pack.events["chat"]})
    _assert_empty_slot(chat_only, WorldState.new(chat_only), Random(7))


def test_m5_cooldown_slot_before_and_at_deadline(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(0))
    pipeline.run_event(world, "meet", {"a": A, "b": B})
    world.directed.set_stored(A, B, "romance", 50)
    pipeline.run_event(world, "confession", {"actor": A, "target": B})
    deadline = world.cooldowns[("confession", (A, B))]
    confession_only = replace(pack, events={"confession": pack.events["confession"]})
    rng = Random(7)
    # 期限直前のスロットと、1tick直前の両方を確認する。
    for offset in (pack.settings.ticks_per_slot, 1):
        world.time = GameTime(deadline.tick - offset)
        _assert_empty_slot(confession_only, world, rng)
    world.time = deadline
    candidates = collect_candidates(confession_only, EventContext.capture(world, confession_only))
    assert [candidate.key for candidate in candidates] == [("confession", (A, B))]
    before_seq = world.next_commit_seq
    expected_rng = deepcopy(rng)
    expected_rng.random()
    report = Pipeline(confession_only, rng).run_slot(world)
    assert report.chosen == candidates[0] and report.batch is not None
    assert world.next_commit_seq == before_seq + 1
    assert rng.getstate() == expected_rng.getstate()
    assert world.time == deadline
    print(f"クールダウン期限={deadline}; 直前は無更新、期限一致時に再告白確定")


def test_m5_all_pairs_ineligible_across_slots(pack: ContentPack) -> None:
    meet_only = replace(pack, events={"meet": pack.events["meet"]})
    world = WorldState.new(meet_only)
    rng = Random(7)
    pipeline = Pipeline(meet_only, rng)
    for a, b in ((A, B), (A, C), (B, C)):
        pipeline.run_event(world, "meet", {"a": a, "b": b})
    before = _snapshot(world)
    rng_before = rng.getstate()
    for _ in range(8):
        world.time = GameTime(world.time.tick + pack.settings.ticks_per_slot)
        _assert_empty_slot(meet_only, world, rng)
    after = _snapshot(world)
    assert after.pop("time") == GameTime(8 * pack.settings.ticks_per_slot)
    before.pop("time")
    assert after == before
    assert rng.getstate() == rng_before
