"""M2: 定義データ層の読み込みと検証。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kankei.definitions import ContentPack, DefinitionError, load_content_pack
from kankei.definitions.conditions import (
    Acquainted,
    AnyOf,
    AxisAtLeast,
    CompatAtLeast,
    CooldownElapsed,
    TrackIs,
)
from kankei.definitions.schema import (
    Audience,
    AxisRole,
    CooldownScope,
    EventShape,
    EventTrigger,
    ObserverPolicy,
    TransitionMeaning,
)

type MakePack = Callable[[str, Callable[[dict[str, Any]], None]], Path]


# --- 正常読み込み -----------------------------------------------------------


def test_loads_minimal_content(pack: ContentPack) -> None:
    assert pack.version == "stage1-test-1"
    assert list(pack.axes) == ["favor", "friendship", "romance"]
    assert pack.axes["favor"].role is AxisRole.LATENT
    assert pack.axes["favor"].bands == ()
    assert pack.axes["romance"].role is AxisRole.EXPRESSED
    assert [b.label for b in pack.axes["romance"].bands] == ["恋慕", "好意", "無関心", "拒絶"]
    assert list(pack.tracks) == ["friendship", "romance"]
    romance = pack.tracks["romance"]
    assert romance.states == ("none", "lovers")
    assert romance.transitions[0].meaning is TransitionMeaning.PROMOTE
    assert len(pack.affinity_rules) == 3
    assert [c.id for c in pack.casters] == ["aoi", "haru", "mizuki"]


def test_event_definitions_are_parsed(pack: ContentPack) -> None:
    confession = pack.events["confession"]
    assert confession.shape is EventShape.DIRECTED
    assert confession.roles == ("actor", "target")
    assert confession.trigger is EventTrigger.LOTTERY
    assert confession.observer_policy is ObserverPolicy.OTHERS_PRESENT
    assert confession.occurrence is not None
    assert confession.occurrence.when == (
        Acquainted(),
        TrackIs("romance", "none"),
        AxisAtLeast("romance", "actor", "target", 40),
        CooldownElapsed(),
    )
    assert confession.cooldown is not None
    assert confession.cooldown.scope is CooldownScope.ACTOR_TO_TARGET
    assert confession.cooldown.after_outcomes == ("rejected",)

    accepted, rejected = confession.outcomes
    assert accepted.when == (AxisAtLeast("romance", "target", "actor", 30),)
    assert [r.kind for r in accepted.results] == ["confession", "relationship_established"]
    assert accepted.results[1].transition is not None
    assert accepted.results[1].transition.to_state == "lovers"
    assert [(f.kind, f.audience) for f in accepted.results[0].facts] == [
        ("confession_made", Audience.PUBLIC),
        ("confession_accepted", Audience.PARTICIPANTS_ONLY),
    ]
    assert rejected.when == ()
    assert rejected.results[0].success is False
    assert [f.kind for f in rejected.results[0].facts] == [
        "confession_made",
        "confession_rejected",
    ]

    crush = pack.events["crush"]
    assert crush.occurrence is not None
    assert isinstance(crush.occurrence.when[2], AnyOf)
    assert isinstance(crush.occurrence.when[2].of[1], CompatAtLeast)
    # 内面の恋慕は fact を生まない
    assert crush.outcomes[0].results[0].facts == ()

    daily = pack.events["daily"]
    assert daily.trigger is EventTrigger.DAILY
    assert daily.shape is EventShape.WORLD


def test_find_template_prefers_success_specific(pack: ContentPack) -> None:
    ok = pack.find_template("confession", True)
    ng = pack.find_template("confession", False)
    assert ok is not None and ok.success is True
    assert ng is not None and ng.success is False
    generic = pack.find_template("met", True)
    assert generic is not None and generic.success is None
    assert pack.find_template("unknown", True) is None


def test_settings(pack: ContentPack) -> None:
    assert pack.settings.ticks_per_slot == 3


def test_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(DefinitionError):
        load_content_pack(tmp_path / "nope")


def test_missing_file(tmp_path: Path, content_dir: Path) -> None:
    import shutil

    target = tmp_path / "content"
    shutil.copytree(content_dir, target)
    (target / "traits.yaml").unlink()
    with pytest.raises(DefinitionError, match=r"traits\.yaml"):
        load_content_pack(target)


# --- 検証エラー -------------------------------------------------------------


def _expect_error(
    make: MakePack, file_name: str, mutate: Callable[[dict[str, Any]], None], match: str
) -> None:
    directory = make(file_name, mutate)
    with pytest.raises(DefinitionError, match=match):
        load_content_pack(directory)


def test_axis_range_inverted(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["axes"][0]["range"] = [100, -100]

    _expect_error(mutated_pack, "axes.yaml", mutate, "min は max より小さい")


def test_axis_initial_out_of_range(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["axes"][0]["initial"] = 500

    _expect_error(mutated_pack, "axes.yaml", mutate, "range を逸脱")


def test_latent_axis_rejects_bands(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["axes"][0]["bands"] = [{"min": -100, "label": "x"}]

    _expect_error(mutated_pack, "axes.yaml", mutate, "latent軸に帯")


def test_expressed_axis_requires_bands(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["axes"][1]["bands"] = []

    _expect_error(mutated_pack, "axes.yaml", mutate, "帯\\(bands\\)が必要")


def test_bands_must_cover_range_min(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["axes"][1]["bands"][-1]["min"] = -50

    _expect_error(mutated_pack, "axes.yaml", mutate, "最後の帯の min")


def test_bands_must_be_descending(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["axes"][1]["bands"][1]["min"] = 90

    _expect_error(mutated_pack, "axes.yaml", mutate, "降順")


def test_unknown_key_is_rejected(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["axes"][0]["rnage"] = [0, 1]

    _expect_error(mutated_pack, "axes.yaml", mutate, "未知のキー")


def test_duplicate_axis_id(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["axes"][1]["id"] = "favor"

    _expect_error(mutated_pack, "axes.yaml", mutate, "IDが重複")


def test_track_transition_requires_meaning(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        del d["tracks"][1]["transitions"][0]["meaning"]

    _expect_error(mutated_pack, "tracks.yaml", mutate, "'meaning' がありません")


def test_track_transition_unknown_state(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["tracks"][1]["transitions"][0]["to"] = "married"

    _expect_error(mutated_pack, "tracks.yaml", mutate, "未知の状態")


def test_affinity_unknown_trait(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["affinity_rules"][0]["when"]["a_has"] = "ghost"

    _expect_error(mutated_pack, "affinity.yaml", mutate, "未知の性格タグ")


def test_event_unknown_axis(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["events"][1]["outcomes"][0]["results"][0]["deltas"][0]["axis"] = "trust"

    _expect_error(mutated_pack, "events.yaml", mutate, "未知の軸")


def test_event_unknown_role(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["events"][1]["outcomes"][0]["results"][0]["deltas"][0]["from"] = "c"

    _expect_error(mutated_pack, "events.yaml", mutate, "未知の役割")


def test_event_unknown_track_state(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["events"][2]["occurrence"]["when"][1]["state"] = "engaged"

    _expect_error(mutated_pack, "events.yaml", mutate, "状態 'engaged' はありません")


def test_event_unknown_condition_type(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["events"][2]["occurrence"]["when"][0] = {"type": "friends_with"}

    _expect_error(mutated_pack, "events.yaml", mutate, "未知の条件種別")


def test_event_transition_not_defined_in_track(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["events"][3]["outcomes"][0]["results"][1]["transition"] = {
            "track": "romance",
            "to": "none",
        }

    _expect_error(mutated_pack, "events.yaml", mutate, "への遷移が定義されていません")


def test_event_last_outcome_must_be_default(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["events"][3]["outcomes"][1]["when"] = [{"type": "acquainted"}]

    _expect_error(mutated_pack, "events.yaml", mutate, "既定分岐")


def test_event_cooldown_unknown_outcome(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["events"][3]["cooldown"]["after_outcomes"] = ["ignored"]

    _expect_error(mutated_pack, "events.yaml", mutate, "未知の結果分岐")


def test_event_directed_roles_fixed(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["events"][2]["roles"] = ["x", "y"]

    _expect_error(mutated_pack, "events.yaml", mutate, "actor, target")


def test_event_lottery_requires_occurrence(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        del d["events"][1]["occurrence"]

    _expect_error(mutated_pack, "events.yaml", mutate, "occurrence が必要")


def test_event_explicit_audience_requires_recipients(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["events"][3]["outcomes"][0]["results"][0]["facts"][1]["audience"] = "explicit"

    _expect_error(mutated_pack, "events.yaml", mutate, "recipients が必要")


def test_event_bool_is_not_int(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["events"][1]["occurrence"]["weight"] = True

    _expect_error(mutated_pack, "events.yaml", mutate, "整数が必要")


def test_template_unknown_placeholder(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["templates"][0]["variants"].append("{a}と{c}が出会った")

    _expect_error(mutated_pack, "templates.yaml", mutate, "未知のプレースホルダ")


def test_template_missing_for_result(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["templates"] = [t for t in d["templates"] if t["result_kind"] != "infatuation"]

    _expect_error(mutated_pack, "templates.yaml", mutate, "'infatuation'.*テンプレートがありません")


def test_template_for_unknown_result_kind(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["templates"].append({"result_kind": "breakup", "variants": ["x"]})

    _expect_error(mutated_pack, "templates.yaml", mutate, "生むイベントがありません")


def test_template_empty_variants(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["templates"][0]["variants"] = []

    _expect_error(mutated_pack, "templates.yaml", mutate, "文面が1つ以上")


def test_settings_slots_must_divide_day(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["event_slots_per_day"] = 7

    _expect_error(mutated_pack, "settings.yaml", mutate, "割り切れる")


def test_caster_unknown_trait(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["casters"][0]["traits"] = ["brave"]

    _expect_error(mutated_pack, "casters.yaml", mutate, "未知の性格タグ")


def test_caster_duplicate_id(mutated_pack: MakePack) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["casters"][1]["id"] = "aoi"

    _expect_error(mutated_pack, "casters.yaml", mutate, "IDが重複")
