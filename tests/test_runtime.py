"""M6: 実時間→tick 変換、常駐ループ、テキスト化、CUI。"""

from __future__ import annotations

import math
import os
import signal
from copy import deepcopy
from dataclasses import fields, replace
from fractions import Fraction
from random import Random
from typing import Any

import pytest

from kankei.cli import EXIT_INTERRUPTED, main, parse_speed, run_app
from kankei.definitions import ContentPack, DefinitionError, load_content_pack
from kankei.definitions.schema import OccurrenceDef
from kankei.engine import EventContext, Pipeline, PipelineError, collect_candidates
from kankei.model import AppliedDelta, Calendar, CasterId, CommitBatch, GameTime, WorldState
from kankei.runtime import (
    EXIT_INTEGRITY,
    EXIT_OK,
    EXIT_PIPELINE,
    App,
    FakeClock,
    NoSleep,
    Pacer,
    advance_one_tick,
    validate_speed,
)
from kankei.text import read_expressed, render_batch, render_result
from kankei.text.bands import band_label

AOI = CasterId("aoi")
HARU = CasterId("haru")


class ListOutput:
    """テスト用の出力先。"""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.errors: list[str] = []

    def line(self, text: str) -> None:
        self.lines.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)


def _snapshot(world: WorldState) -> dict[str, object]:
    out = {
        f.name: deepcopy(getattr(world, f.name))
        for f in fields(world)
        if f.name not in {"directed", "pairs"}
    }
    out["directed"] = dict(world.directed.items())
    out["pairs"] = dict(world.pairs.items())
    return out


# --- (a)-(d) 変換規則 -------------------------------------------------------------


def _pacer(speed: float = 120.0, cap: float = 2.0) -> Pacer:
    p = Pacer(ticks_per_day=24, real_seconds_per_game_day=speed, max_real_elapsed_seconds=cap)
    p.start(0.0)
    return p


def test_a_elapsed_is_clamped_to_upper_bound() -> None:
    p = _pacer(speed=24.0, cap=2.0)  # 1 tick = 1 秒
    assert p.advance(3600.0) == 2  # 1時間の停止後も最大で上限分(2秒=2tick)しか進まない
    assert p.residual_seconds == 0
    assert p.advance(3600.5) == 0  # 0.5秒経過は端数として残る
    assert p.residual_seconds == Fraction(1, 2)
    assert p.advance(3600.5 + 100.0) == 2  # 再び上限分。端数 0.5 は保持される
    assert p.residual_seconds == Fraction(1, 2)


def test_b_negative_elapsed_counts_as_zero() -> None:
    p = _pacer(speed=24.0)
    assert p.advance(1.0) == 1
    assert p.advance(-50.0) == 0  # 巻き戻り: 0 扱い。基準は -50 に置き直される
    assert p.residual_seconds == 0
    assert p.advance(-49.0) == 1  # 置き直した基準から 1 秒


def test_c_fractional_ticks_accumulate_without_drift() -> None:
    p = _pacer(speed=120.0)  # 1 tick = 5 秒。0.4 tick = 2 秒
    now = 0.0
    ticks = []
    for _ in range(5):
        now += 2.0
        ticks.append(p.advance(now))
    assert ticks == [0, 0, 1, 0, 1]
    assert sum(ticks) == 2
    assert p.residual_seconds == 0
    # 二進で表せない端数でも Fraction で累積し、1000 回で正確に floor(1000*0.3) tick
    q = _pacer(speed=24.0)  # 1 tick = 1 秒
    now = 0.0
    total = 0
    for _ in range(1000):
        now += 0.3
        total += q.advance(now)
    assert total == 300


def test_d_speed_change_affects_only_game_progress() -> None:
    p = _pacer(speed=120.0, cap=100.0)  # 5 秒/tick
    assert p.advance(10.0) == 2
    p.set_speed(48.0)  # 2 秒/tick
    assert p.advance(20.0) == 5
    assert p.seconds_per_tick == 2
    # クランプ上限は速度に依存しない
    fast = _pacer(speed=1.0, cap=2.0)  # 1/24 秒/tick
    assert fast.advance(3600.0) == 48  # 上限2秒 → 48 tick


def test_pacer_requires_start_and_rejects_bad_settings() -> None:
    p = Pacer(24, 120.0, 2.0)
    with pytest.raises(RuntimeError):
        p.advance(1.0)
    with pytest.raises(ValueError):
        Pacer(24, 0.0, 2.0)
    with pytest.raises(ValueError):
        p.set_speed(-1.0)


def test_loader_requires_loop_interval_below_clamp(
    mutated_pack: Any,
) -> None:
    directory = mutated_pack("settings.yaml", lambda d: d.update(loop_interval_seconds=5.0))
    with pytest.raises(DefinitionError, match="loop_interval_seconds"):
        load_content_pack(directory)


# --- 発火順序 --------------------------------------------------------------------------


def test_daily_runs_before_slot_at_same_tick(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(1))
    world.time = GameTime(23)
    report = advance_one_tick(world, pipeline)
    assert world.time == GameTime(24)
    assert len(report.batches) == 2
    daily, slot = report.batches
    assert daily.results[0].kind == "day_closed"
    assert slot.results[0].kind == "met"
    assert daily.commit_seq < slot.commit_seq
    assert daily.results[0].game_time == slot.results[0].game_time == GameTime(24)


def test_non_boundary_tick_does_nothing(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(1))
    world.time = GameTime(1)
    report = advance_one_tick(world, pipeline)
    assert world.time == GameTime(2)
    assert report.batches == ()
    assert world.next_commit_seq == 1


# --- 乱数消費なし ----------------------------------------------------------------------


def test_daily_does_not_consume_rng(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    rng = Random(4)
    pipeline = Pipeline(pack, rng)
    before = rng.getstate()
    batch = pipeline.run_event(world, "daily", {})
    assert rng.getstate() == before
    assert batch.results[0].kind == "day_closed" and batch.facts == ()


def test_render_is_deterministic_and_side_effect_free(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    rng = Random(4)
    pipeline = Pipeline(pack, rng)
    batch = pipeline.run_event(world, "meet", {"a": AOI, "b": HARU})
    names = {cid: c.name for cid, c in world.casters.items()}
    cal = Calendar(pack.settings.ticks_per_day)
    rng_before = rng.getstate()
    world_before = _snapshot(world)
    first = render_batch(batch, pack, names, cal)
    for _ in range(5):
        assert render_batch(batch, pack, names, cal) == first
    assert rng.getstate() == rng_before
    assert _snapshot(world) == world_before
    template = pack.find_template("met", True)
    assert template is not None
    result = batch.results[0]
    expected = template.variants[result.result_id % len(template.variants)]
    assert first == [f"[1日目 00:00] {expected.format(a='葵', b='春')}"]


def test_render_selects_variant_by_result_id(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(0))
    names = {cid: c.name for cid, c in world.casters.items()}
    cal = Calendar(pack.settings.ticks_per_day)
    template = pack.find_template("interaction", True)
    assert template is not None
    pipeline.run_event(world, "meet", {"a": AOI, "b": HARU})
    seen = []
    for _ in range(len(template.variants)):
        batch = pipeline.run_event(world, "chat", {"a": AOI, "b": HARU})
        r = batch.results[0]
        line = render_result(r, template, names, cal)
        assert line.endswith(
            template.variants[r.result_id % len(template.variants)].format(a="葵", b="春")
        )
        seen.append(line)
    assert len(set(seen)) == len(template.variants)


def test_closed_day_placeholder_and_time_prefix(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(0))
    names = {cid: c.name for cid, c in world.casters.items()}
    cal = Calendar(pack.settings.ticks_per_day)
    world.time = GameTime(48)
    batch = pipeline.run_event(world, "daily", {})
    assert render_batch(batch, pack, names, cal) == ["[3日目 00:00] ―― 2日目が終わった ――"]


def test_bands_reject_latent(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    world.directed.set_stored(AOI, HARU, "romance", 45)
    view = Pipeline(pack, Random(0)).pack  # 型確認用に pack を再利用
    assert view is pack
    from kankei.engine import EventContext

    ctx = EventContext.capture(world, pack)
    assert read_expressed(ctx.directed, pack.axes["romance"], AOI, HARU).label == "好意"
    assert band_label(pack.axes["romance"], -100).label == "拒絶"
    with pytest.raises(ValueError, match="latent"):
        read_expressed(ctx.directed, pack.axes["favor"], AOI, HARU)


# --- ループ -------------------------------------------------------------------------------


def _app(
    pack: ContentPack, seed: int, world: WorldState | None = None
) -> tuple[App, FakeClock, ListOutput, Random]:
    world = world or WorldState.new(pack)
    rng = Random(seed)
    clock = FakeClock()
    out = ListOutput()
    app = App(pack, world, Pipeline(pack, rng), clock, NoSleep(), out)
    app.pacer.start(clock.now())
    return app, clock, out, rng


def _drive(app: App, clock: FakeClock, steps: list[float]) -> list[int | None]:
    codes = []
    for dt in steps:
        clock.advance(dt)
        codes.append(app.run_one_cycle())
    return codes


def test_loop_is_deterministic_with_same_tick_sequence(pack: ContentPack) -> None:
    steps = [1.0, 2.5, 0.3, 40.0, 1.9, 1.9, 1.9, 5.0] * 40  # 上限2秒: 5.0 や 40.0 はクランプされる
    runs = []
    for _ in range(2):
        app, clock, out, rng = _app(pack, seed=9)
        codes = _drive(app, clock, steps)
        assert set(codes) == {None}
        runs.append((out.lines, _snapshot(app.world), rng.getstate()))
    assert runs[0] == runs[1]
    lines = runs[0][0]
    assert any("日目が終わった" in line for line in lines)
    assert len(lines) > 20
    assert runs[0][1]["time"] == GameTime(int(sum(min(s, 2.0) for s in steps) // 5))


def test_loop_never_skips_boundaries_on_large_elapsed(pack: ContentPack) -> None:
    fast = replace(pack, settings=replace(pack.settings, real_seconds_per_game_day=1.0))
    app, clock, out, _ = _app(fast, seed=2)
    clock.advance(2.0)  # 1/24 秒/tick × 上限2秒 = 48 tick = 2日
    assert app.run_one_cycle() is None
    assert app.world.time == GameTime(48)
    closed = [r for r in app.world.results if r.kind == "day_closed"]
    assert [r.game_time for r in closed] == [GameTime(24), GameTime(48)]
    assert sum("日目が終わった" in line for line in out.lines) == 2


def test_no_event_slots_are_not_displayed(pack: ContentPack) -> None:
    meet_only = replace(pack, events={"meet": pack.events["meet"], "daily": pack.events["daily"]})
    app, clock, out, rng = _app(meet_only, seed=2)
    state_before = rng.getstate()
    _drive(app, clock, [2.0] * 200)  # 400 tick
    met_lines = [line for line in out.lines if "日目が終わった" not in line]
    assert len(met_lines) == 3  # 出会いは3ペア分だけ。以後の無イベントスロットは表示されない
    assert rng.getstate() != state_before  # 出会いのあるスロットでは消費
    state_after = rng.getstate()
    _drive(app, clock, [2.0] * 50)
    assert rng.getstate() == state_after  # 候補なしのスロットと日次では消費しない


def test_loop_stops_on_integrity_failure_without_new_slots(pack: ContentPack) -> None:
    app, clock, out, rng = _app(pack, seed=1)
    _drive(app, clock, [2.0, 2.0])
    app.world.integrity_failure = "テスト用に設定"
    before = _snapshot(app.world)
    rng_before = rng.getstate()
    clock.advance(2.0)
    assert app.run_one_cycle() == EXIT_INTEGRITY
    assert out.errors and "修復不能" in out.errors[0]
    assert _snapshot(app.world) == before
    assert rng.getstate() == rng_before
    assert app.run() == EXIT_INTEGRITY


def test_loop_stops_on_pipeline_error(pack: ContentPack, monkeypatch: pytest.MonkeyPatch) -> None:
    import kankei.runtime.app as app_mod

    app, clock, out, _ = _app(pack, seed=1)

    def boom(world: WorldState, pipeline: Pipeline) -> object:
        raise PipelineError("定義の不備")

    monkeypatch.setattr(app_mod, "advance_one_tick", boom)
    codes = _drive(app, clock, [2.0, 2.0, 2.0])  # 6秒 ≥ 5秒/tick で1tick進む
    assert codes[-1] == EXIT_PIPELINE
    assert out.errors and "定義の不備" in out.errors[0]


def test_request_stop_and_stop_when(pack: ContentPack) -> None:
    app, _clock, _out, _ = _app(pack, seed=1)
    app.request_stop()
    assert app.run() == EXIT_OK
    cal = Calendar(pack.settings.ticks_per_day)
    world = WorldState.new(pack)
    app2 = App(
        pack,
        world,
        Pipeline(pack, Random(1)),
        FakeClock(),
        NoSleep(),
        ListOutput(),
        stop_when=lambda w: cal.day(w.time) >= 1,
    )
    clock2 = app2.clock
    assert isinstance(clock2, FakeClock)
    app2.pacer.start(0.0)
    clock2.advance(2.0)
    assert app2.run_one_cycle() is None
    for _ in range(100):
        clock2.advance(2.0)
        if app2.run_one_cycle() == EXIT_OK:
            break
    assert cal.day(world.time) == 1


# --- CUI -----------------------------------------------------------------------------------


def test_cli_run_for_one_day(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "--seed", "5", "--speed", "0.01", "--days", "1"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "=== 開始" in captured.out and "1日目が終わった" in captured.out
    assert "終了(コード 0)" in captured.out


def test_cli_rejects_bad_content(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "--content", str(tmp_path / "nope")])
    assert code == EXIT_PIPELINE
    assert "定義データを読み込めません" in capsys.readouterr().err


# --- 修正B: 日次イベントは trigger で選ぶ -------------------------------------------------------


def _one_random_call(seed: int) -> object:
    """seed から rng.random() を1回だけ呼んだ後の状態。"""
    r = Random(seed)
    r.random()
    return r.getstate()


def test_daily_selected_by_trigger_even_if_id_changes(mutated_pack: Any) -> None:
    def rename(d: dict[str, Any]) -> None:
        daily = next(e for e in d["events"] if e.get("trigger") == "daily")
        daily["id"] = "day_end"

    renamed = load_content_pack(mutated_pack("events.yaml", rename))
    assert "daily" not in renamed.events
    world = WorldState.new(renamed)
    rng = Random(1)
    pipeline = Pipeline(renamed, rng)
    world.time = GameTime(23)
    report = advance_one_tick(world, pipeline)
    daily, slot = report.batches
    assert daily.results[0].kind == "day_closed"
    assert "day_end" in str(daily.event_instance_id)
    assert daily.commit_seq < slot.commit_seq  # 同 tick のスロットより先に確定
    # 乱数の消費はスロットの draw の1回だけ(日次は消費しない)
    assert rng.getstate() == _one_random_call(1)
    lines = render_batch(daily, renamed, {AOI: "葵", HARU: "晴"}, Calendar(24))
    assert any("1日目が終わった" in line for line in lines)


def test_daily_event_is_never_a_slot_candidate(pack: ContentPack) -> None:
    """occurrence を無理に付けても(ローダを迂回)、trigger: daily は抽選候補にならない。"""
    daily = pack.daily_events()[0]
    forced = replace(daily, occurrence=OccurrenceDef(weight=1000, when=()))
    forced_pack = replace(pack, events={**pack.events, daily.id: forced})
    world = WorldState.new(forced_pack)
    ctx = EventContext.capture(world, forced_pack)
    candidates = collect_candidates(forced_pack, ctx)
    assert candidates
    assert all(c.event_def_id != daily.id for c in candidates)


@pytest.mark.parametrize("count", [0, 2])
def test_step_rejects_pack_without_exactly_one_daily(pack: ContentPack, count: int) -> None:
    """ローダを迂回したパックでも、日境界で黙って省略せず世界を変える前に止まる。"""
    daily = pack.daily_events()[0]
    others = {k: v for k, v in pack.events.items() if v.trigger is not daily.trigger}
    extra = {f"daily{i}": replace(daily, id=f"daily{i}") for i in range(count)}
    bad = replace(pack, events={**others, **extra})
    world = WorldState.new(bad)
    world.time = GameTime(23)
    rng = Random(1)
    before = _snapshot(world)
    with pytest.raises(PipelineError, match="ちょうど1件"):
        advance_one_tick(world, Pipeline(bad, rng))
    assert _snapshot(world) == before
    assert rng.getstate() == Random(1).getstate()


# --- 修正C: --speed の値域 ---------------------------------------------------------------------


BAD_SPEEDS = ["0", "-1", "nan", "inf"]


@pytest.mark.parametrize("text", BAD_SPEEDS)
def test_cli_rejects_bad_speed_without_traceback(
    text: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import kankei.runtime.app as app_mod

    ticks: list[object] = []
    monkeypatch.setattr(app_mod, "advance_one_tick", lambda *a: ticks.append(a))
    with pytest.raises(SystemExit) as exited:
        main(["run", "--speed", text, "--days", "1"])
    captured = capsys.readouterr()
    assert exited.value.code == 2
    assert "--speed" in captured.err and "有限の正の数" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
    assert ticks == []  # イベントを1件も実行しない


@pytest.mark.parametrize("value", [0.0, -1.0, math.nan, math.inf, -math.inf])
def test_set_speed_rejects_same_range_outside_cli(value: float) -> None:
    p = _pacer(speed=120.0)
    before = p.seconds_per_tick
    with pytest.raises(ValueError, match="有限の正の数"):
        validate_speed(value)
    with pytest.raises(ValueError, match="有限の正の数"):
        p.set_speed(value)
    assert p.seconds_per_tick == before


def test_positive_finite_speed_is_accepted() -> None:
    assert parse_speed("0.01") == 0.01
    assert validate_speed(48.0) == 48.0
    p = _pacer(speed=120.0)
    p.set_speed(48.0)
    assert p.seconds_per_tick == Fraction(2)


# --- 修正A: Ctrl+C(実際の SIGINT)の2段階停止 --------------------------------------------------


def _sigint() -> None:
    os.kill(os.getpid(), signal.SIGINT)


class _TickingSleep:
    """sleep のたびに FakeClock を進める(実時間は待たない)。on_sleep で割込みを差し込める。"""

    def __init__(self, clock: FakeClock, step: float = 2.0) -> None:
        self.clock = clock
        self.step = step
        self.calls = 0
        self.on_sleep: Any = None

    def sleep(self, seconds: float) -> None:
        self.calls += 1
        if self.on_sleep is not None:
            self.on_sleep(self.calls)
        self.clock.advance(self.step)


def _signal_app(pack: ContentPack, seed: int = 3) -> tuple[App, _TickingSleep, ListOutput]:
    clock = FakeClock()
    sleeper = _TickingSleep(clock)
    out = ListOutput()
    guard_day = Calendar(pack.settings.ticks_per_day)
    # 安全弁: シグナルが効かなかった場合に無限ループしない(効いていれば到達しない)
    app = App(
        pack,
        WorldState.new(pack),
        Pipeline(pack, Random(seed)),
        clock,
        sleeper,
        out,
        stop_when=lambda w: guard_day.day(w.time) >= 30,
    )
    return app, sleeper, out


class _KillOnAppend(list[AppliedDelta]):
    """delta 履歴への追加直後に SIGINT を送る(commit 適用の途中)。"""

    def __init__(self, items: list[AppliedDelta], kills: int, world: WorldState) -> None:
        super().__init__(items)
        self.kills = kills
        self.world = world
        self.fired_at: GameTime | None = None

    def append(self, item: AppliedDelta) -> None:
        super().append(item)
        if self.fired_at is None:
            self.fired_at = self.world.tick_in_progress
            for _ in range(self.kills):
                _sigint()


def _assert_consistent(world: WorldState) -> None:
    result_ids = {r.result_id for r in world.results}
    assert all(d.result_id in result_ids for d in world.delta_history)


def test_first_sigint_during_commit_completes_tick_and_exits_0(pack: ContentPack) -> None:
    app, _sleeper, out = _signal_app(pack)
    world = app.world
    history = _KillOnAppend([], kills=1, world=world)
    world.delta_history = history
    original = signal.getsignal(signal.SIGINT)
    code = run_app(app, out)
    assert signal.getsignal(signal.SIGINT) is original  # ハンドラは元に戻る
    assert code == EXIT_OK
    assert history.fired_at is not None
    assert world.integrity_failure is None and world.tick_in_progress is None
    _assert_consistent(world)  # 部分更新なし: 中断した commit の結果も登録済み
    assert world.time == history.fired_at  # その tick を完了して停止
    assert out.lines[-1] == "=== 停止(Ctrl+C) ==="


def test_first_sigint_after_daily_before_slot_completes_tick(
    pack: ContentPack, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _sleeper, out = _signal_app(pack)
    world = app.world
    pipeline = app.pipeline
    original_run_event = pipeline.run_event
    fired: list[CommitBatch] = []

    def run_event_then_sigint(w: WorldState, event_id: str, binding: Any) -> CommitBatch:
        batch = original_run_event(w, event_id, binding)
        if not fired:
            fired.append(batch)
            _sigint()  # 日次確定後・同 tick のスロット開始前
        return batch

    monkeypatch.setattr(pipeline, "run_event", run_event_then_sigint)
    assert run_app(app, out) == EXIT_OK
    daily_time = fired[0].results[0].game_time
    assert world.time == daily_time  # 停止後の時刻 = 処理済みの位置
    assert world.tick_in_progress is None and world.integrity_failure is None
    same_tick = [r for r in world.results if r.game_time == daily_time]
    assert same_tick[0].kind == "day_closed"
    assert len(same_tick) >= 2  # 同 tick のスロットも処理された
    assert max(r.game_time for r in world.results) == daily_time


def test_first_sigint_during_sleep_stops_normally(pack: ContentPack) -> None:
    app, sleeper, out = _signal_app(pack)
    stopped_at: list[GameTime] = []

    def on_sleep(calls: int) -> None:
        if calls == 7:
            stopped_at.append(app.world.time)
            _sigint()

    sleeper.on_sleep = on_sleep
    assert run_app(app, out) == EXIT_OK
    assert app.world.time == stopped_at[0]  # 処理外の停止要求では以後の tick を進めない
    assert app.world.integrity_failure is None and app.world.tick_in_progress is None
    _assert_consistent(app.world)


def test_second_sigint_during_commit_marks_integrity_failure(pack: ContentPack) -> None:
    app, _sleeper, out = _signal_app(pack)
    world = app.world
    history = _KillOnAppend([], kills=2, world=world)
    world.delta_history = history
    original = signal.getsignal(signal.SIGINT)
    code = run_app(app, out)
    assert signal.getsignal(signal.SIGINT) is original
    assert code == EXIT_INTERRUPTED == 130
    assert world.integrity_failure is not None and "中断" in world.integrity_failure
    assert world.tick_in_progress == history.fired_at
    assert out.errors and "強制中断" in out.errors[-1]
    assert "=== 停止(Ctrl+C) ===" not in out.lines


def test_second_sigint_after_daily_leaves_tick_in_progress(
    pack: ContentPack, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _sleeper, out = _signal_app(pack)
    world = app.world
    pipeline = app.pipeline
    original_run_event = pipeline.run_event

    def run_event_then_two_sigints(w: WorldState, event_id: str, binding: Any) -> CommitBatch:
        batch = original_run_event(w, event_id, binding)
        _sigint()
        _sigint()
        return batch

    monkeypatch.setattr(pipeline, "run_event", run_event_then_two_sigints)
    assert run_app(app, out) == EXIT_INTERRUPTED
    assert world.integrity_failure is None  # commit の外なので部分更新なし
    assert world.tick_in_progress == world.time == GameTime(pack.settings.ticks_per_day)
    assert [r.kind for r in world.results if r.game_time == world.time] == ["day_closed"]


def test_second_sigint_during_sleep_sets_no_marks(pack: ContentPack) -> None:
    app, sleeper, out = _signal_app(pack)

    def on_sleep(calls: int) -> None:
        if calls == 7:
            _sigint()
            _sigint()

    sleeper.on_sleep = on_sleep
    assert run_app(app, out) == EXIT_INTERRUPTED
    assert app.world.integrity_failure is None and app.world.tick_in_progress is None
    _assert_consistent(app.world)


def test_app_as_library_does_not_touch_sigint(pack: ContentPack) -> None:
    original = signal.getsignal(signal.SIGINT)
    app, _clock, _out, _ = _app(pack, seed=1)
    app.request_stop()
    assert app.run() == EXIT_OK
    assert signal.getsignal(signal.SIGINT) is original
