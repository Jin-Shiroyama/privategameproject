"""成立結果 EventResult(§14)。知識の唯一の源(§19)。"""

from __future__ import annotations

from dataclasses import dataclass

from kankei.model.clock import GameTime
from kankei.model.ids import CasterId, EventInstanceId, ResultId


@dataclass(frozen=True, slots=True)
class Participant:
    """参加者とその役割。参加者は成人 Caster のみ(§11: ChildEntity の ID は入れられない型設計)。"""

    caster: CasterId
    role: str


@dataclass(frozen=True, slots=True)
class TrackChange:
    """結果に伴うトラック遷移。fact の state_after の根拠になる。"""

    track: str
    state_before: str
    state_after: str


@dataclass(frozen=True, slots=True)
class EventResult:
    """実際に成立した結果。役割・日時・定義版・確定順を記録する(§12・§14)。"""

    result_id: ResultId
    event_instance_id: EventInstanceId
    event_def_id: str
    outcome_id: str
    kind: str
    success: bool
    participants: tuple[Participant, ...]
    game_time: GameTime
    commit_seq: int
    observed_by: tuple[CasterId, ...]
    definition_version: str
    related_result_id: ResultId | None = None
    track_change: TrackChange | None = None

    def caster_of(self, role: str) -> CasterId:
        """役割に対応する参加者ID。"""
        for p in self.participants:
            if p.role == role:
                return p.caster
        raise KeyError(f"役割 '{role}' の参加者がいません")

    @property
    def participant_ids(self) -> tuple[CasterId, ...]:
        """参加者IDの列(役割順)。"""
        return tuple(p.caster for p in self.participants)
