"""テンプレートによるチャット風ログ生成(§2・§7-7)。

- 入力は確定済みの EventResult・テンプレ定義・名前表・暦・expressed の帯のみ。
  WorldState / DirectedStore / 相性値を受け取れない。
- 文面は `variants[result_id % len(variants)]` で決定的に選ぶ。乱数は消費しない。
  組込み hash() は使わない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from kankei.definitions.schema import ContentPack, TemplateDef
from kankei.model.clock import Calendar
from kankei.model.ids import CasterId
from kankei.model.result import EventResult
from kankei.model.world import CommitBatch
from kankei.text.bands import ExpressedView


class RenderError(Exception):
    """テンプレートが見つからない・置換できない。"""


def time_prefix(result: EventResult, calendar: Calendar) -> str:
    """`[N日目 HH:MM]`。"""
    day = calendar.day(result.game_time) + 1
    minutes = calendar.tick_of_day(result.game_time) * 1440 // calendar.ticks_per_day
    return f"[{day}日目 {minutes // 60:02d}:{minutes % 60:02d}]"


def render_result(
    result: EventResult,
    template: TemplateDef,
    names: Mapping[CasterId, str],
    calendar: Calendar,
    expressed: Sequence[ExpressedView] = (),
) -> str:
    """1件の成立結果を1行の文面にする。"""
    variant = template.variants[result.result_id % len(template.variants)]
    fields: dict[str, str] = {p.role: names[p.caster] for p in result.participants}
    fields["day"] = str(calendar.day(result.game_time) + 1)
    fields["closed_day"] = str(calendar.day(result.game_time))
    for view in expressed:
        fields[f"band_{view.axis_id}"] = view.label
    try:
        body = variant.format(**fields)
    except (KeyError, IndexError, ValueError) as exc:
        raise RenderError(f"テンプレート置換に失敗: {variant!r}: {exc}") from None
    return f"{time_prefix(result, calendar)} {body}"


def render_batch(
    batch: CommitBatch, pack: ContentPack, names: Mapping[CasterId, str], calendar: Calendar
) -> list[str]:
    """確定単位の全結果を文面にする(結果順)。"""
    lines: list[str] = []
    for result in batch.results:
        template = pack.find_template(result.kind, result.success)
        if template is None:
            raise RenderError(
                f"結果種別 '{result.kind}'(success={result.success}) のテンプレートがありません"
            )
        lines.append(render_result(result, template, names, calendar))
    return lines
