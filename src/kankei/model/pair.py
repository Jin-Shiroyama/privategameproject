"""pair層(A-B、§3・§6)。面識・トラック状態・固定タグ。

- 面識は出会いの成立結果への参照として持ち、友情トラックとは独立(§6)。
- 片思いは directed 値から導出する状況であり、pair の状態として持たない(§3)。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from kankei.definitions.schema import TrackDef
from kankei.model.ids import CasterId, ResultId

type PairKey = tuple[CasterId, CasterId]


def pair_key(a: CasterId, b: CasterId) -> PairKey:
    """順序に依存しないペアのキー(ソート済み)。"""
    if a == b:
        raise ValueError("自分自身とのペアは持てません")
    return (a, b) if a < b else (b, a)


@dataclass(slots=True)
class PairState:
    """ペアの客観的な関係状態。"""

    key: PairKey
    acquainted_by: ResultId | None = None
    track_states: dict[str, str] = field(default_factory=dict)
    fixed_tags: frozenset[str] = frozenset()

    @property
    def acquainted(self) -> bool:
        """面識があるか(出会いの成立結果があるか)。"""
        return self.acquainted_by is not None

    def state(self, track_id: str) -> str:
        """トラックの現在状態。"""
        return self.track_states[track_id]

    def copy(self) -> PairState:
        """複製。"""
        return PairState(
            key=self.key,
            acquainted_by=self.acquainted_by,
            track_states=dict(self.track_states),
            fixed_tags=self.fixed_tags,
        )


class PairStore:
    """ペアキー → PairState。未登録のペアはトラック初期状態で生成する。"""

    def __init__(
        self, tracks: Mapping[str, TrackDef], states: Mapping[PairKey, PairState] | None = None
    ) -> None:
        self._tracks = tracks
        self._states: dict[PairKey, PairState] = {}
        if states:
            for key, state in states.items():
                self._states[key] = state.copy()

    def _default(self, key: PairKey) -> PairState:
        return PairState(key=key, track_states={t.id: t.initial for t in self._tracks.values()})

    def get(self, a: CasterId, b: CasterId) -> PairState:
        """ペア状態(読み取り用。未登録なら初期状態のコピーを返す)。"""
        key = pair_key(a, b)
        state = self._states.get(key)
        return state.copy() if state is not None else self._default(key)

    def validate(self, state: PairState) -> None:
        """登録可能か検証する(未知のトラック・状態を拒否)。状態は変更しない。"""
        if set(state.track_states) != set(self._tracks):
            raise KeyError("トラックの集合が定義と一致しません")
        for track_id, value in state.track_states.items():
            if value not in self._tracks[track_id].states:
                raise ValueError(f"トラック '{track_id}' に状態 '{value}' はありません")

    def put(self, state: PairState) -> None:
        """ペア状態を登録・置換する。"""
        self.validate(state)
        self._states[state.key] = state.copy()

    def items(self) -> Iterator[tuple[PairKey, PairState]]:
        """登録済みペアをキーの安定順で列挙する。"""
        for key in sorted(self._states):
            yield key, self._states[key].copy()

    def copy(self) -> PairStore:
        """複製。"""
        return PairStore(self._tracks, self._states)
