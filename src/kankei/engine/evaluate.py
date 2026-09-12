"""条件式の評価(§7-1・2)。EventContext(開始時点)のみを参照する。"""

from __future__ import annotations

from collections.abc import Mapping

from kankei.definitions.conditions import (
    Acquainted,
    AllOf,
    AnyOf,
    AxisAtLeast,
    AxisBelow,
    CompatAtLeast,
    Condition,
    CooldownElapsed,
    HasTrait,
    Not,
    NotAcquainted,
    TrackIs,
)
from kankei.definitions.schema import CooldownScope, EventDef
from kankei.engine.context import EventContext
from kankei.engine.errors import PipelineError
from kankei.model.ids import CasterId
from kankei.model.pair import pair_key
from kankei.model.world import CooldownKey

type Binding = Mapping[str, CasterId]


def pair_of(event_def: EventDef, binding: Binding) -> tuple[CasterId, CasterId]:
    """2者イベントの参加者(役割順)。"""
    if len(event_def.roles) != 2:
        raise PipelineError(f"イベント '{event_def.id}' は2者イベントではありません")
    return binding[event_def.roles[0]], binding[event_def.roles[1]]


def cooldown_key(event_def: EventDef, binding: Binding) -> CooldownKey | None:
    """イベントのクールダウンキー。クールダウン未定義なら None。"""
    if event_def.cooldown is None:
        return None
    a, b = pair_of(event_def, binding)
    match event_def.cooldown.scope:
        case CooldownScope.ACTOR_TO_TARGET:
            return (event_def.id, (a, b))
        case CooldownScope.PAIR:
            return (event_def.id, pair_key(a, b))


def evaluate(cond: Condition, ctx: EventContext, event_def: EventDef, binding: Binding) -> bool:
    """条件を評価する。"""
    match cond:
        case Acquainted():
            return ctx.acquainted(*pair_of(event_def, binding))
        case NotAcquainted():
            return not ctx.acquainted(*pair_of(event_def, binding))
        case TrackIs(track=track, state=state):
            a, b = pair_of(event_def, binding)
            return ctx.track_state(a, b, track) == state
        case AxisAtLeast(axis=axis, from_role=fr, to_role=to, value=value):
            return ctx.effective(binding[fr], binding[to], axis) >= value
        case AxisBelow(axis=axis, from_role=fr, to_role=to, value=value):
            return ctx.effective(binding[fr], binding[to], axis) < value
        case HasTrait(role=role, trait=trait):
            return ctx.has_trait(binding[role], trait)
        case CompatAtLeast(from_role=fr, to_role=to, value=value):
            return ctx.compat(binding[fr], binding[to]) >= value
        case CooldownElapsed():
            key = cooldown_key(event_def, binding)
            return key is None or not ctx.cooldown_active(key)
        case AllOf(of=items):
            return all(evaluate(c, ctx, event_def, binding) for c in items)
        case AnyOf(of=items):
            return any(evaluate(c, ctx, event_def, binding) for c in items)
        case Not(cond=inner):
            return not evaluate(inner, ctx, event_def, binding)


def evaluate_all(
    conds: tuple[Condition, ...], ctx: EventContext, event_def: EventDef, binding: Binding
) -> bool:
    """すべての条件が成立するか(空なら真)。"""
    return all(evaluate(c, ctx, event_def, binding) for c in conds)
