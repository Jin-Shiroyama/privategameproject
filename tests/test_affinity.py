"""M4: 相性関数 compat(A→B)(§21)。"""

from __future__ import annotations

import pytest

from kankei.affinity import applied_rules, compat
from kankei.definitions import ContentPack
from kankei.definitions.schema import AffinityRule
from kankei.model import Caster, CasterId, WorldState


def _caster(name: str, *traits: str) -> Caster:
    return Caster(id=CasterId(name), name=name, traits=frozenset(traits))


SYM = AffinityRule(
    id="caretaker_pampered", a_has="caretaker", b_has="pampered", value=20, symmetric=True
)
ASYM = AffinityRule(
    id="leader_follower", a_has="leader", b_has="follower", value=15, symmetric=False
)
NEG = AffinityRule(id="neat_vs_sloppy", a_has="neat", b_has="sloppy", value=-20, symmetric=True)


def test_no_matching_rule_is_zero() -> None:
    a, b = _caster("a", "neat"), _caster("b", "pampered")
    assert compat(a, b, [SYM, ASYM]) == 0
    assert compat(a, b, []) == 0
    assert applied_rules(a, b, [SYM, ASYM]) == ()


def test_symmetric_rule_applies_in_either_order_once_per_direction() -> None:
    a, b = _caster("a", "caretaker"), _caster("b", "pampered")
    assert compat(a, b, [SYM]) == 20  # 正順一致
    assert compat(b, a, [SYM]) == 20  # 逆順一致(対称)


def test_symmetric_rule_is_not_double_counted_when_both_orders_match() -> None:
    """§22 ①相性: 両向きの条件が一致しても同方向へ二重加算しない。"""
    a = _caster("a", "caretaker", "pampered")
    b = _caster("b", "caretaker", "pampered")
    assert compat(a, b, [SYM]) == 20
    assert compat(b, a, [SYM]) == 20
    assert [r.id for r in applied_rules(a, b, [SYM])] == ["caretaker_pampered"]


def test_asymmetric_rule_applies_forward_only() -> None:
    a, b = _caster("a", "leader"), _caster("b", "follower")
    assert compat(a, b, [ASYM]) == 15
    assert compat(b, a, [ASYM]) == 0


def test_rules_sum_and_clamp() -> None:
    a = _caster("a", "caretaker", "neat")
    b = _caster("b", "pampered", "sloppy")
    assert compat(a, b, [SYM, NEG]) == 0  # 20 + (-20)
    big = [
        AffinityRule(id=f"r{i}", a_has="caretaker", b_has="pampered", value=60, symmetric=False)
        for i in range(3)
    ]
    assert compat(a, b, big) == 100
    negative = [
        AffinityRule(id=f"n{i}", a_has="neat", b_has="sloppy", value=-60, symmetric=False)
        for i in range(3)
    ]
    assert compat(a, b, negative) == -100


def test_recomputed_when_solo_changes() -> None:
    a, b = _caster("a", "caretaker"), _caster("b", "pampered")
    assert compat(a, b, [SYM]) == 20
    a2 = a.with_traits(frozenset({"neat"}))
    assert compat(a2, b, [SYM]) == 0
    b2 = b.with_traits(frozenset({"pampered", "sloppy"}))
    assert compat(a2, b2, [SYM, NEG]) == -20


def test_self_compat_is_rejected() -> None:
    a = _caster("a", "caretaker")
    with pytest.raises(ValueError):
        compat(a, a, [SYM])


def test_compat_with_content_pack(pack: ContentPack) -> None:
    world = WorldState.new(pack)
    aoi, haru, mizuki = (world.caster(CasterId(c)) for c in ("aoi", "haru", "mizuki"))
    rules = pack.affinity_rules
    assert compat(aoi, haru, rules) == 20  # 世話好き×甘え上手
    assert compat(haru, aoi, rules) == 20
    assert compat(aoi, mizuki, rules) == 10  # 社交的×社交的
    assert compat(mizuki, aoi, rules) == 10
    assert compat(haru, mizuki, rules) == -20  # ずぼら×潔癖(逆順一致)
    assert compat(mizuki, haru, rules) == -20
