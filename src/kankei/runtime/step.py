"""1 tick の進行(計画書§7.2-7.3)。日境界とスロット境界の発火順序をここだけで固定する。

同一 tick に日境界とスロット境界が重なる場合、(1) 日次処理(前日の締め) → (2) 新しい日のスロット。
他の場所から日次処理・スロット抽選を呼ばない。

日次イベントは ID ではなく `trigger: daily` で選ぶ(第①段階はちょうど1件)。

時刻の不変条件: `world.tick_in_progress is None` のとき `world.time` = 処理済みの位置。
tick の開始時に `tick_in_progress` を立て、日次・スロットがすべて確定した後にだけ戻す。
途中で例外(強制中断を含む)が抜けた場合は立ったまま残り、tick 途中で止まった印になる。
"""

from __future__ import annotations

from dataclasses import dataclass

from kankei.engine.errors import PipelineError
from kankei.engine.pipeline import Pipeline
from kankei.model.clock import GameTime
from kankei.model.world import CommitBatch, WorldState


@dataclass(frozen=True, slots=True)
class TickReport:
    """1 tick で確定したバッチ(日次→スロットの順)。無イベントは含まない。"""

    time: GameTime
    batches: tuple[CommitBatch, ...]


def advance_one_tick(world: WorldState, pipeline: Pipeline) -> TickReport:
    """世界時刻を 1 tick 進め、境界処理を行う。"""
    if world.tick_in_progress is not None:
        raise PipelineError(f"tick {world.tick_in_progress.tick} の処理が完了していない世界です")
    settings = pipeline.pack.settings
    next_tick = world.time.tick + 1
    daily_id: str | None = None
    if next_tick % settings.ticks_per_day == 0:
        # 定義の不備は世界を変更する前に検出する(§6.6)
        daily = pipeline.pack.daily_events()
        if len(daily) != 1:
            raise PipelineError(
                f"trigger: daily のイベントはちょうど1件必要です"
                f"(現在 {len(daily)} 件: {[e.id for e in daily]})"
            )
        daily_id = daily[0].id
    world.time = GameTime(next_tick)
    world.tick_in_progress = world.time
    batches: list[CommitBatch] = []
    if daily_id is not None:
        batches.append(pipeline.run_event(world, daily_id, {}))  # (1) 乱数不使用
    if next_tick % settings.ticks_per_slot == 0:
        report = pipeline.run_slot(world)  # (2) 候補ありなら draw で乱数消費
        if report.batch is not None:
            batches.append(report.batch)
    world.tick_in_progress = None
    return TickReport(world.time, tuple(batches))
