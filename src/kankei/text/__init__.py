"""テキスト化層(§2)。確定済みの結果を変更せず、乱数を消費しない。"""

from kankei.text.bands import ExpressedView, read_expressed
from kankei.text.render import render_batch, render_result

__all__ = ["ExpressedView", "read_expressed", "render_batch", "render_result"]
