"""§7-4・5: 仮更新と状態更新(PendingState)。永続化しない。

- 保存値を軸範囲内で更新し、有効値窓口で有効値を求める(保護下限は第③段階)。
- 遷移は仮更新後の pair 状態に対して評価する。現在状態から定義されていない遷移は
  定義の不備として `PipelineError` で停止する(commit 前・世界状態は無傷)。
- 各トラックの遷移確定は1イベント最大1回。
"""

from __future__ import annotations

from dataclasses import dataclass

from kankei.definitions.schema import ResultSpec
from kankei.engine.context import EventContext
from kankei.engine.errors import PipelineError
from kankei.engine.modifiers import ModifiedDelta
from kankei.engine.outcome import DeltaCandidate, OutcomeDraft
from kankei.model.ids import CasterId
from kankei.model.pair import PairKey, PairState, pair_key
from kankei.model.result import TrackChange


@dataclass(frozen=True, slots=True)
class PendingDelta:
    """AppliedDelta の result_id 未確定版。"""

    candidate: DeltaCandidate
    after_modifier: int
    applied: int
    stored_before: int
    stored_after: int
    effective_before: int
    effective_after: int
    blocked: bool
    rule_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PendingResult:
    """結果ごとの状態変更。"""

    index: int
    spec: ResultSpec
    track_change: TrackChange | None
    acquaintance_established: bool


class PendingState:
    """EventContext から派生した仮更新用の可変状態。"""

    def __init__(self, ctx: EventContext) -> None:
        self.ctx = ctx
        self.directed = ctx.directed.copy()
        self.pairs = ctx.pairs.copy()
        self.changed_pairs: dict[PairKey, PairState] = {}

    # --- 4: 値の仮更新 -----------------------------------------------------

    def apply_deltas(self, modified: tuple[ModifiedDelta, ...]) -> tuple[PendingDelta, ...]:
        """保存値を軸範囲内で更新し、有効値を求める。"""
        out: list[PendingDelta] = []
        for m in modified:
            c = m.candidate
            stored_before = self.directed.stored(c.source, c.target, c.axis)
            effective_before = self.directed.effective(c.source, c.target, c.axis)
            if m.blocked:
                stored_after = stored_before
            else:
                stored_after = self.directed.set_stored(
                    c.source, c.target, c.axis, stored_before + m.after_modifier
                )
            out.append(
                PendingDelta(
                    candidate=c,
                    after_modifier=m.after_modifier,
                    applied=stored_after - stored_before,
                    stored_before=stored_before,
                    stored_after=stored_after,
                    effective_before=effective_before,
                    effective_after=self.directed.effective(c.source, c.target, c.axis),
                    blocked=m.blocked,
                    rule_ids=m.rule_ids,
                )
            )
        return tuple(out)

    # --- 4-5: 遷移評価と状態更新 --------------------------------------------

    def _pair_of(self, draft: OutcomeDraft) -> tuple[CasterId, CasterId]:
        if len(draft.participants) != 2:
            raise PipelineError(
                f"イベント '{draft.event_def.id}' は2者イベントではないため pair を変更できません"
            )
        return draft.participants[0].caster, draft.participants[1].caster

    def apply_results(self, draft: OutcomeDraft) -> tuple[PendingResult, ...]:
        """面識成立とトラック遷移を仮更新後の状態に反映する。"""
        out: list[PendingResult] = []
        transitioned: set[str] = set()
        for i, spec in enumerate(draft.outcome.results):
            change: TrackChange | None = None
            acquainted = False
            if spec.establish_acquaintance or spec.transition is not None:
                a, b = self._pair_of(draft)
                key = pair_key(a, b)
                state = self.changed_pairs.get(key) or self.pairs.get(a, b)
                if spec.establish_acquaintance:
                    if state.acquainted:
                        raise PipelineError(
                            f"イベント '{draft.event_def.id}' 分岐 '{draft.outcome.id}': "
                            f"ペア {key} は既に面識があります(result {state.acquainted_by})"
                        )
                    state.acquainted_by = draft.result_ids[i]
                    acquainted = True
                if spec.transition is not None:
                    track = self.ctx.pack.tracks[spec.transition.track]
                    if track.id in transitioned:
                        raise PipelineError(
                            f"イベント '{draft.event_def.id}' 分岐 '{draft.outcome.id}': "
                            f"トラック '{track.id}' の遷移は1イベント1回までです"
                        )
                    current = state.state(track.id)
                    to_state = spec.transition.to_state
                    if not any(
                        t.from_state == current and t.to_state == to_state
                        for t in track.transitions
                    ):
                        raise PipelineError(
                            f"イベント '{draft.event_def.id}' 分岐 '{draft.outcome.id}': "
                            f"ペア {key} のトラック '{track.id}' は現在 '{current}' であり、"
                            f"'{current}→{to_state}' の遷移は定義されていません"
                        )
                    state.track_states[track.id] = to_state
                    transitioned.add(track.id)
                    change = TrackChange(track.id, current, to_state)
                self.changed_pairs[key] = state
                self.pairs.put(state)
            out.append(PendingResult(i, spec, change, acquainted))
        return tuple(out)

    def refresh_effective(self, deltas: tuple[PendingDelta, ...]) -> tuple[PendingDelta, ...]:
        """状態変更後の制約で有効値を再計算する(§7-5)。第①段階では値は変わらないが窓口を通す。"""
        out: list[PendingDelta] = []
        for d in deltas:
            c = d.candidate
            effective_after = self.directed.effective(c.source, c.target, c.axis)
            out.append(
                PendingDelta(
                    candidate=c,
                    after_modifier=d.after_modifier,
                    applied=d.applied,
                    stored_before=d.stored_before,
                    stored_after=d.stored_after,
                    effective_before=d.effective_before,
                    effective_after=effective_after,
                    blocked=d.blocked,
                    rule_ids=d.rule_ids,
                )
            )
        return tuple(out)
