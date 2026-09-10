"""fact と知識参照(§19)。

- fact は成立結果のうち開示可能な出来事からのみ生成する。delta・軸値は fact 化しない。
- `audience` は当該発生時の直接開示先。後の伝聞は別経路(`KnowledgeVia.TOLD`)として記録する。
- Knowledge はキャラ×fact の参照。同じ出来事の fact を複製しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kankei.model.clock import GameTime
from kankei.model.ids import CasterId, FactId, ResultId
from kankei.model.result import Participant


@dataclass(frozen=True, slots=True)
class Fact:
    """開示可能な出来事。"""

    fact_id: FactId
    source_result_id: ResultId
    kind: str
    subjects: tuple[Participant, ...]
    game_time: GameTime
    commit_seq: int
    audience: tuple[CasterId, ...]
    track: str | None = None
    state_after: str | None = None


class KnowledgeVia(StrEnum):
    """知識の獲得経路。"""

    PARTICIPANT = "participant"
    WITNESSED = "witnessed"
    TOLD = "told"


@dataclass(frozen=True, slots=True)
class Knowledge:
    """キャラが fact を知っているという参照。"""

    owner: CasterId
    fact_id: FactId
    via: KnowledgeVia
    acquired_at: GameTime
    acquired_seq: int
    learned_from: CasterId | None = None
