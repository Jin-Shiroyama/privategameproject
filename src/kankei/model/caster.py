"""Caster(solo層、§3)。

通常の関係シミュレーションを担う Caster は全員成人(§10)。
将来の ChildEntity は本型とは別の型として定義し、恋愛・性愛系のフィールドや
イベント参加を型として持たせない(§11)。本モジュールに年齢や子どもフラグを追加しないこと。
"""

from __future__ import annotations

from dataclasses import dataclass

from kankei.model.ids import CasterId


@dataclass(frozen=True, slots=True)
class Caster:
    """成人 Caster。solo属性は性格タグの集合のみ(第①段階)。"""

    id: CasterId
    name: str
    traits: frozenset[str]

    def has_trait(self, trait: str) -> bool:
        """性格タグを持つか。"""
        return trait in self.traits

    def with_traits(self, traits: frozenset[str]) -> Caster:
        """solo属性を差し替えた Caster を返す(相性の再計算検証用)。"""
        return Caster(id=self.id, name=self.name, traits=traits)
