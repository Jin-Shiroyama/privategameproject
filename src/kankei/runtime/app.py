"""常駐ループ(App)。計画書§7.2。

- 周期ごとに: 整合性確認 → 停止判定 → 実時間→tick 変換 → 1 tick ずつ進行 → 描写 → sleep。
- `integrity_failure` が立った世界では新しいスロットを開始せず停止する(保存拒否は M7)。
- 例外(PipelineError / CommitError / FatalCommitError)は捕まえて続行しない。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from kankei.definitions.schema import ContentPack
from kankei.engine.errors import PipelineError
from kankei.engine.pipeline import Pipeline
from kankei.model.clock import Calendar
from kankei.model.world import CommitError, FatalCommitError, WorldState
from kankei.runtime.clock import Clock, Sleeper
from kankei.runtime.scheduler import Pacer
from kankei.runtime.step import advance_one_tick
from kankei.text.render import render_batch

EXIT_OK = 0
EXIT_INTEGRITY = 2
EXIT_PIPELINE = 3


class Output(Protocol):
    """ログ出力先。"""

    def line(self, text: str) -> None:
        """1行出力。"""
        ...

    def error(self, text: str) -> None:
        """エラー出力。"""
        ...


class App:
    """単一スレッドの常駐ループ。乱数は Pipeline だけが持つ。"""

    def __init__(
        self,
        pack: ContentPack,
        world: WorldState,
        pipeline: Pipeline,
        clock: Clock,
        sleeper: Sleeper,
        out: Output,
        stop_when: Callable[[WorldState], bool] | None = None,
    ) -> None:
        self.pack = pack
        self.world = world
        self.pipeline = pipeline
        self.clock = clock
        self.sleeper = sleeper
        self.out = out
        self.stop_when = stop_when
        settings = pack.settings
        self.pacer = Pacer(
            settings.ticks_per_day,
            settings.real_seconds_per_game_day,
            settings.max_real_elapsed_seconds,
        )
        self.calendar = Calendar(settings.ticks_per_day)
        self.names = {cid: c.name for cid, c in world.casters.items()}
        self.stop_requested = False

    def request_stop(self) -> None:
        """次の周期で停止する。"""
        self.stop_requested = True

    def _should_stop(self) -> bool:
        return self.stop_requested or (self.stop_when is not None and self.stop_when(self.world))

    def run_one_cycle(self) -> int | None:
        """1周期。終了コードを返せば停止、None なら継続。"""
        if self.world.integrity_failure is not None:
            self.out.error(f"世界状態が修復不能のため停止します: {self.world.integrity_failure}")
            return EXIT_INTEGRITY
        if self._should_stop():
            return EXIT_OK
        ticks = self.pacer.advance(self.clock.now())
        for _ in range(ticks):
            if self.world.integrity_failure is not None or self._should_stop():
                break
            try:
                report = advance_one_tick(self.world, self.pipeline)
            except FatalCommitError as exc:
                self.out.error(f"適用中に例外が起き、世界状態が修復不能になりました: {exc}")
                return EXIT_INTEGRITY
            except (PipelineError, CommitError) as exc:
                self.out.error(f"パイプラインの不備により停止します: {exc}")
                return EXIT_PIPELINE
            for batch in report.batches:
                for line in render_batch(batch, self.pack, self.names, self.calendar):
                    self.out.line(line)
        return None

    def run(self) -> int:
        """停止条件まで周期を回す。"""
        self.pacer.start(self.clock.now())
        while True:
            code = self.run_one_cycle()
            if code is not None:
                return code
            self.sleeper.sleep(self.pack.settings.loop_interval_seconds)
