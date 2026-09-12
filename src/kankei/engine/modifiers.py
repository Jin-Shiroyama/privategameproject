"""§7-3: 増減補正と変化禁止(§5)。

第①段階は補正なし(恒等)・禁止なし。順序だけ固定する:
補正をすべて適用した後、最後に変化禁止を適用する(補正による加算で禁止が復活しないため)。
cap・保護・スキル倍率は後段で `Modifier` / `BlockPolicy` の実装として差し込む接続点。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from kankei.engine.context import EventContext
from kankei.engine.outcome import DeltaCandidate


class Modifier(Protocol):
    """増減量補正。value(補正途中の値)を受けて補正後の値を返す。"""

    @property
    def rule_id(self) -> str:
        """履歴に残すルールID。"""
        ...

    def adjust(self, ctx: EventContext, delta: DeltaCandidate, value: int) -> int:
        """補正後の値。"""
        ...


class BlockPolicy(Protocol):
    """変化禁止。補正後の値に対して禁止なら True。"""

    @property
    def rule_id(self) -> str:
        """履歴に残すルールID。"""
        ...

    def blocks(self, ctx: EventContext, delta: DeltaCandidate, value: int) -> bool:
        """禁止するか。"""
        ...


@dataclass(frozen=True, slots=True)
class ModifiedDelta:
    """補正・禁止判定後の delta。"""

    candidate: DeltaCandidate
    after_modifier: int
    blocked: bool
    rule_ids: tuple[str, ...]


def apply_modifiers(
    ctx: EventContext,
    candidates: Sequence[DeltaCandidate],
    modifiers: Sequence[Modifier] = (),
    blocks: Sequence[BlockPolicy] = (),
) -> tuple[ModifiedDelta, ...]:
    """補正 → 変化禁止の順で適用する。"""
    out: list[ModifiedDelta] = []
    for cand in candidates:
        value = cand.value
        rule_ids: list[str] = []
        for m in modifiers:
            new_value = m.adjust(ctx, cand, value)
            if new_value != value:
                rule_ids.append(m.rule_id)
            value = new_value
        blocked = False
        for b in blocks:
            if b.blocks(ctx, cand, value):
                blocked = True
                rule_ids.append(b.rule_id)
        out.append(ModifiedDelta(cand, value, blocked, tuple(rule_ids)))
    return tuple(out)
