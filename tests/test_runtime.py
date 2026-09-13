"""M6: 実時間→tick 変換、常駐ループ、テキスト化、CUI。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, replace
from fractions import Fraction
from random import Random
from typing import Any

import pytest

from kankei.cli import main
from kankei.definitions import ContentPack, DefinitionError, load_content_pack
from kankei.engine import Pipeline, PipelineError
from kankei.model import Calendar, CasterId, GameTime, WorldState
from kankei.runtime import (
    EXIT_INTEGRITY,
    EXIT_OK,
    EXIT_PIPELINE,
    App,
    FakeClock,
    NoSleep,
    Pacer,
    advance_one_tick,
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
