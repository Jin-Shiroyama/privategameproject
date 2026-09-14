"""1 tick の進行(計画書§7.2-7.3)。日境界とスロット境界の発火順序をここだけで固定する。

同一 tick に日境界とスロット境界が重なる場合、(1) 日次処理(前日の締め) → (2) 新しい日のスロット。
他の場所から日次処理・スロット抽選を呼ばない。
"""

from __future__ import annotations

from dataclasses import dataclass

from kankei.engine.pipeline import Pipeline
from kankei.model.clock import GameTime
from kankei.model.world import CommitBatch, WorldState

DAILY_EVENT_ID = "daily"


@dataclass(frozen=True, slots=True)
class TickReport:
    """1 tick で確定したバッチ(日次→スロットの順)。無イベントは含まない。"""

    time: GameTime
    batches: tuple[CommitBatch, ...]


def advance_one_tick(world: WorldState, pipeline: Pipeline) -> TickReport:
    """世界時刻を 1 tick 進め、境界処理を行う。"""
    settings = pipeline.pack.settings
    next_tick = world.time.tick + 1
    world.time = GameTime(next_tick)
    batches: list[CommitBatch] = []
    if next_tick % settings.ticks_per_day == 0 and DAILY_EVENT_ID in pipeline.pack.events:
        batches.append(pipeline.run_event(world, DAILY_EVENT_ID, {}))  # (1) 乱数不使用
    if next_tick % settings.ticks_per_slot == 0:
        report = pipeline.run_slot(world)  # (2) 候補ありなら draw で乱数消費
        if report.batch is not None:
            batches.append(report.batch)
    return TickReport(world.time, tuple(batches))
