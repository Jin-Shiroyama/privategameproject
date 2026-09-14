"""CUI エントリポイント: `kankei run`。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from random import Random

from kankei.definitions import DefinitionError, load_content_pack
from kankei.engine.pipeline import Pipeline
from kankei.model.clock import Calendar
from kankei.model.world import WorldState
from kankei.runtime.app import EXIT_OK, EXIT_PIPELINE, App
from kankei.runtime.clock import MonotonicClock, TimeSleeper

DEFAULT_CONTENT = Path(__file__).resolve().parent.parent.parent / "content"


class StdOutput:
    """標準出力/標準エラーへの出力。"""

    def line(self, text: str) -> None:
        """1行出力。"""
        print(text, flush=True)

    def error(self, text: str) -> None:
        """エラー出力。"""
        print(text, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    """引数定義。"""
    parser = argparse.ArgumentParser(prog="kankei", description="関係性観察ゲーム(第①段階試作)")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="常駐ループを開始し、ログを流し続ける(Ctrl+C で停止)")
    run.add_argument(
        "--content", type=Path, default=DEFAULT_CONTENT, help="定義データのディレクトリ"
    )
    run.add_argument("--seed", type=int, default=0, help="乱数 seed")
    run.add_argument("--speed", type=float, default=None, help="進行速度: 1ゲーム日あたりの実秒")
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
    try:
        code = app.run()
    except KeyboardInterrupt:
        out.line("=== 停止(Ctrl+C) ===")
        return EXIT_OK
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
