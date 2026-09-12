"""相性関数 compat(A→B)(§21)。

- solo属性(性格タグ)から導く読み取り専用の導出値。directed の蓄積軸ではなく、delta で増減しない。
- `compat(A→B) = clamp(該当 affinity_rule の value 総和, -100, 100)`。該当なしは 0。乱数を使わない。
- 同一ルールは1方向の計算につき最大1回。`symmetric: true` なら正順または逆順の条件一致で適用し、
  両方一致しても同じ方向へ二重加算しない。
- キャッシュしない(solo属性が変われば都度再計算される)。
- latent 準拠。LLMプロンプト用データには渡さない。
  参照先はイベント発生条件・抽選重み・返答判定条件に限り、直接の delta 補正倍率にしない。
"""

from __future__ import annotations

from collections.abc import Sequence

from kankei.definitions.schema import AffinityRule
from kankei.model.caster import Caster

COMPAT_MIN = -100
COMPAT_MAX = 100


def _rule_applies(rule: AffinityRule, a: Caster, b: Caster) -> bool:
    forward = a.has_trait(rule.a_has) and b.has_trait(rule.b_has)
    if forward:
        return True
    return rule.symmetric and a.has_trait(rule.b_has) and b.has_trait(rule.a_has)


def applied_rules(a: Caster, b: Caster, rules: Sequence[AffinityRule]) -> tuple[AffinityRule, ...]:
    """compat(a→b) に寄与するルールを定義順で返す(各ルール高々1回)。神視点の説明表示用。"""
    return tuple(rule for rule in rules if _rule_applies(rule, a, b))


def compat(a: Caster, b: Caster, rules: Sequence[AffinityRule]) -> int:
    """compat(a→b)。"""
    if a.id == b.id:
        raise ValueError("自分自身との相性は定義しません")
    total = sum(rule.value for rule in applied_rules(a, b, rules))
    return max(COMPAT_MIN, min(COMPAT_MAX, total))
