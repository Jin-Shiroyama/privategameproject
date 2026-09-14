"""expressed 軸の有効値を帯ラベル+hint に変換する(§4)。

LLM・テンプレートへ渡せるのは `ExpressedView` のみ。latent 軸(好感度)・生数値・相性値は
この型に変換できないため、テキスト化層へ渡せない境界になる。
"""

from __future__ import annotations

from dataclasses import dataclass

from kankei.definitions.schema import AxisDef, AxisRole
from kankei.model.directed import DirectedView
from kankei.model.ids import CasterId


@dataclass(frozen=True, slots=True)
class ExpressedView:
    """expressed 軸の帯。生数値を持たない。"""

    axis_id: str
    label: str
    hint: str


def band_label(axis: AxisDef, effective: int) -> ExpressedView:
    """有効値を帯に変換する。latent 軸は拒否する。"""
    if axis.role is not AxisRole.EXPRESSED:
        raise ValueError(f"軸 '{axis.id}' は latent であり、帯へ変換できません(§4)")
    for band in axis.bands:
        if effective >= band.min:
            return ExpressedView(axis.id, band.label, band.hint)
    raise ValueError(f"軸 '{axis.id}' の帯が値 {effective} を覆っていません")


def read_expressed(
    view: DirectedView, axis: AxisDef, source: CasterId, target: CasterId
) -> ExpressedView:
    """有効値窓口経由で expressed 軸の帯を読む。"""
    return band_label(axis, view.effective(source, target, axis.id))
