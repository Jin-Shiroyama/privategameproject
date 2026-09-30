"""CUI エントリポイント: `kankei run`。

Ctrl+C(SIGINT)は2段階で扱う(計画書§7.6)。
- 1回目: 停止要求のフラグを立てるだけ。処理中の tick を完了してから終了コード 0 で止まる。
- 2回目: 即時中断。終了コード 130 で、正常終了の経路を通らずに抜ける(保存しない)。
  commit 適用中なら `integrity_failure`、tick 途中なら `tick_in_progress` が世界に残る。
SIGINT ハンドラは `run_app` の間だけ登録し、終了時に元へ戻す。
"""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from random import Random
from types import FrameType

from kankei.definitions import DefinitionError, load_content_pack
from kankei.engine.pipeline import Pipeline
from kankei.model.clock import Calendar
from kankei.model.world import WorldState
from kankei.runtime.app import EXIT_OK, EXIT_PIPELINE, App, Output
from kankei.runtime.clock import MonotonicClock, TimeSleeper
from kankei.runtime.scheduler import validate_speed

EXIT_INTERRUPTED = 130  # 2回目の SIGINT による強制中断(128 + SIGINT)

DEFAULT_CONTENT = Path(__file__).resolve().parent.parent.parent / "content"


class StdOutput:
    """標準出力/標準エラーへの出力。"""

    def line(self, text: str) -> None:
        """1行出力。"""
        print(text, flush=True)

    def error(self, text: str) -> None:
        """エラー出力。"""
        print(text, file=sys.stderr, flush=True)


def parse_speed(text: str) -> float:
    """`--speed` の変換。値域の検証は `validate_speed`(Pacer 側の正本)に委ねる。"""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"数値ではありません: {text!r}") from None
    try:
        return validate_speed(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


class SigintStopper:
    """SIGINT ハンドラ。1回目は停止要求のみ、2回目は `KeyboardInterrupt` で即時中断。"""

    def __init__(self, app: App) -> None:
        self.app = app
        self.count = 0

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        """シグナル受信。"""
        self.count += 1
        if self.count == 1:
            self.app.request_stop()
            return
        raise KeyboardInterrupt


def run_app(app: App, out: Output) -> int:
    """SIGINT ハンドラを登録して常駐ループを回し、終了時にハンドラを戻す。"""
    stopper = SigintStopper(app)
    previous = signal.signal(signal.SIGINT, stopper)
    try:
        code = app.run()
    except KeyboardInterrupt:
        out.error("=== 強制中断(2回目の Ctrl+C)。保存せずに終了します ===")
        return EXIT_INTERRUPTED
    finally:
        signal.signal(signal.SIGINT, previous)
    if stopper.count > 0 and code == EXIT_OK:
        out.line("=== 停止(Ctrl+C) ===")
    return code


def build_parser() -> argparse.ArgumentParser:
    """引数定義。"""
    parser = argparse.ArgumentParser(prog="kankei", description="関係性観察ゲーム(第①段階試作)")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="常駐ループを開始し、ログを流し続ける(Ctrl+C で停止)")
    run.add_argument(
        "--content", type=Path, default=DEFAULT_CONTENT, help="定義データのディレクトリ"
    )
    run.add_argument("--seed", type=int, default=0, help="乱数 seed")
    run.add_argument(
        "--speed", type=parse_speed, default=None, help="進行速度: 1ゲーム日あたりの実秒"
    )
    run.add_argument("--days", type=int, default=None, help="N ゲーム日で自動終了(検証用)")
    return parser


def run_command(args: argparse.Namespace) -> int:
    """`kankei run`。"""
    out = StdOutput()
    try:
        pack = load_content_pack(args.content)
    except DefinitionError as exc:
        out.error(f"定義データを読み込めません: {exc}")
        return EXIT_PIPELINE
    world = WorldState.new(pack)
    pipeline = Pipeline(pack, Random(args.seed))
    calendar = Calendar(pack.settings.ticks_per_day)
    stop_when: Callable[[WorldState], bool] | None = None
    if args.days is not None:
        limit = int(args.days)

        def stop_when(w: WorldState) -> bool:
            return calendar.day(w.time) >= limit

    app = App(pack, world, pipeline, MonotonicClock(), TimeSleeper(), out, stop_when)
    if args.speed is not None:
        app.pacer.set_speed(args.speed)
    out.line(f"=== 開始: 定義版 {pack.version} / seed {args.seed} ===")
    code = run_app(app, out)
    if code == EXIT_INTERRUPTED:
        return code
    out.line(f"=== 終了(コード {code}) ===")
    return code


def main(argv: Sequence[str] | None = None) -> int:
    """エントリポイント。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.error("未知のコマンド")
    return run_command(args)


if __name__ == "__main__":
    sys.exit(main())
