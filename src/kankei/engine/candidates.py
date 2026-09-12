"""§7-1: 候補の列挙・正規化・重複排除・ソート・抽選。

- 方向付きイベント(directed)は役割順 `(actor, target)`、ペア共有イベント(pair)は参加者IDの
  ソート済みタプルで正規化する。重複排除キーは `(イベント定義ID, 参加者タプル)`。
- 抽選前に同キーでソートし、集合・辞書の列挙順に依存しない。
- 乱数を消費するのは `draw` のみ。候補が1件以上あるとき `rng.random()` を1回だけ呼ぶ。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from random import Random

from kankei.definitions.schema import ContentPack, EventDef, EventShape, EventTrigger
from kankei.engine.context import EventContext
from kankei.engine.evaluate import Binding, evaluate_all
from kankei.model.ids import CasterId


@dataclass(frozen=True, slots=True)
class Candidate:
    """抽選候補。"""

    event_def_id: str
    participants: tuple[CasterId, ...]
    binding: tuple[tuple[str, CasterId], ...]
    weight: int

    @property
    def key(self) -> tuple[str, tuple[CasterId, ...]]:
        """重複排除・ソートのキー。"""
        return (self.event_def_id, self.participants)

    def binding_map(self) -> dict[str, CasterId]:
        """役割→キャラID。"""
        return dict(self.binding)


def normalize_participants(event_def: EventDef, binding: Binding) -> tuple[CasterId, ...]:
    """イベント形状に応じた参加者タプルの正規化。"""
    ids = tuple(binding[r] for r in event_def.roles)
    match event_def.shape:
        case EventShape.DIRECTED:
            return ids
        case EventShape.PAIR:
            return tuple(sorted(ids))
        case EventShape.WORLD:
            return ()


def enumerate_bindings(
    event_def: EventDef, caster_ids: tuple[CasterId, ...]
) -> Iterator[tuple[tuple[str, CasterId], ...]]:
    """イベントに対する役割割当を安定順で列挙する。"""
    match event_def.shape:
        case EventShape.DIRECTED:
            actor_role, target_role = event_def.roles
            for actor in caster_ids:
                for target in caster_ids:
                    if actor != target:
                        yield ((actor_role, actor), (target_role, target))
        case EventShape.PAIR:
            # pair の役割は順不同。小さいIDを先頭役割に割り当てる(決定的)
            first, second = event_def.roles
            ordered = sorted(caster_ids)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    yield ((first, a), (second, b))
        case EventShape.WORLD:
            yield ()


def collect_candidates(pack: ContentPack, ctx: EventContext) -> tuple[Candidate, ...]:
    """抽選候補を集める(列挙→重複排除→発生条件評価→ソート)。"""
    seen: dict[tuple[str, tuple[CasterId, ...]], Candidate] = {}
    caster_ids = ctx.caster_ids()
    for event_def in pack.events.values():
        if event_def.trigger is not EventTrigger.LOTTERY or event_def.occurrence is None:
            continue
        for binding in enumerate_bindings(event_def, caster_ids):
            bmap = dict(binding)
            key = (event_def.id, normalize_participants(event_def, bmap))
            if key in seen:
                continue
            if not evaluate_all(event_def.occurrence.when, ctx, event_def, bmap):
                continue
            seen[key] = Candidate(
                event_def_id=event_def.id,
                participants=key[1],
                binding=binding,
                weight=event_def.occurrence.weight,
            )
    return tuple(sorted(seen.values(), key=lambda c: c.key))


def draw(candidates: tuple[Candidate, ...], rng: Random) -> Candidate | None:
    """重み付き抽選で最大1件を選ぶ。候補0件なら乱数を消費せず None。"""
    if not candidates:
        return None
    total = sum(c.weight for c in candidates)
    point = rng.random() * total
    acc = 0
    for c in candidates:
        acc += c.weight
        if point < acc:
            return c
    return candidates[-1]
