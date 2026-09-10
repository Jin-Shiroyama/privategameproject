"""イベント履歴の delta 記録(§5)。候補 → 補正後 → 適用、保存値・有効値の前後、作用ルール。"""

from __future__ import annotations

from dataclasses import dataclass

from kankei.model.ids import CasterId, ResultId


@dataclass(frozen=True, slots=True)
class AppliedDelta:
    """1本の delta の適用記録。fact 化しない(§19)。"""

    result_id: ResultId
    source: CasterId
    target: CasterId
    axis: str
    candidate: int
    after_modifier: int
    applied: int
    stored_before: int
    stored_after: int
    effective_before: int
    effective_after: int
    blocked: bool
    rule_ids: tuple[str, ...] = ()
