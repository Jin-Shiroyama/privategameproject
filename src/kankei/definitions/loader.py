"""YAML定義データのローダと読み込み時検証。

- `yaml.safe_load` のみを使う。
- 型・必須項目・未知のキー・参照ID・数値範囲を検証する。
- 不正な定義は補正せず `DefinitionError` にする。
- 列挙順はファイルの記述順を保つ(決定論のため集合の列挙順に依存しない)。
"""

from __future__ import annotations

import string
from collections.abc import Iterable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

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
from kankei.definitions.errors import DefinitionError
from kankei.definitions.schema import (
    AffinityRule,
    Audience,
    AxisDef,
    AxisLayer,
    AxisRole,
    Band,
    CasterDef,
    ContentPack,
    ContentRating,
    CooldownDef,
    CooldownScope,
    DeltaSpec,
    EventDef,
    EventShape,
    EventTrigger,
    FactSpec,
    ObserverPolicy,
    OccurrenceDef,
    OutcomeDef,
    ResultSpec,
    Settings,
    TemplateDef,
    TrackDef,
    TraitDef,
    TransitionDef,
    TransitionMeaning,
    TransitionSpec,
)

PACK_FILES: tuple[str, ...] = (
    "pack.yaml",
    "axes.yaml",
    "tracks.yaml",
    "traits.yaml",
    "affinity.yaml",
    "events.yaml",
    "templates.yaml",
    "settings.yaml",
    "casters.yaml",
)

# 役割名以外に許すテンプレートのプレースホルダ
_COMMON_PLACEHOLDERS: frozenset[str] = frozenset({"day"})

# ---------------------------------------------------------------------------
# 低レベルの読み取りヘルパ
# ---------------------------------------------------------------------------


def _as_map(obj: object, path: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise DefinitionError(path, "マッピングが必要です")
    for key in obj:
        if not isinstance(key, str):
            raise DefinitionError(path, f"キーは文字列である必要があります: {key!r}")
    return obj


def _as_list(obj: object, path: str) -> list[Any]:
    if not isinstance(obj, list):
        raise DefinitionError(path, "リストが必要です")
    return obj


def _check_keys(m: dict[str, Any], allowed: Iterable[str], path: str) -> None:
    unknown = sorted(set(m) - set(allowed))
    if unknown:
        raise DefinitionError(path, f"未知のキーがあります: {', '.join(unknown)}")


def _req(m: dict[str, Any], key: str, path: str) -> Any:
    if key not in m:
        raise DefinitionError(path, f"必須項目 '{key}' がありません")
    return m[key]


def _str(obj: object, path: str) -> str:
    if not isinstance(obj, str) or not obj:
        raise DefinitionError(path, "空でない文字列が必要です")
    return obj


def _int(obj: object, path: str) -> int:
    # bool は int のサブクラスなので明示的に除外する
    if isinstance(obj, bool) or not isinstance(obj, int):
        raise DefinitionError(path, "整数が必要です")
    return obj


def _float(obj: object, path: str) -> float:
    if isinstance(obj, bool) or not isinstance(obj, int | float):
        raise DefinitionError(path, "数値が必要です")
    return float(obj)


def _bool(obj: object, path: str) -> bool:
    if not isinstance(obj, bool):
        raise DefinitionError(path, "真偽値が必要です")
    return obj


def _req_str(m: dict[str, Any], key: str, path: str) -> str:
    return _str(_req(m, key, path), f"{path}.{key}")


def _req_int(m: dict[str, Any], key: str, path: str) -> int:
    return _int(_req(m, key, path), f"{path}.{key}")


def _opt_str(m: dict[str, Any], key: str, path: str, default: str) -> str:
    return default if key not in m else _str(m[key], f"{path}.{key}")


def _opt_int(m: dict[str, Any], key: str, path: str, default: int) -> int:
    return default if key not in m else _int(m[key], f"{path}.{key}")


def _opt_bool(m: dict[str, Any], key: str, path: str, default: bool) -> bool:
    return default if key not in m else _bool(m[key], f"{path}.{key}")


def _str_list(obj: object, path: str) -> tuple[str, ...]:
    return tuple(_str(item, f"{path}[{i}]") for i, item in enumerate(_as_list(obj, path)))


def _enum[E: StrEnum](enum_type: type[E], obj: object, path: str) -> E:
    text = _str(obj, path)
    try:
        return enum_type(text)
    except ValueError:
        choices = ", ".join(str(member) for member in enum_type)
        raise DefinitionError(path, f"'{text}' は不正です(候補: {choices})") from None


def _unique_ids(ids: Sequence[str], path: str) -> None:
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            raise DefinitionError(path, f"IDが重複しています: {item}")
        seen.add(item)


def _read_yaml(directory: Path, name: str) -> dict[str, Any]:
    file_path = directory / name
    if not file_path.is_file():
        raise DefinitionError(name, "定義ファイルが見つかりません")
    with file_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return _as_map(data, name)


# ---------------------------------------------------------------------------
# 各ファイルのパース
# ---------------------------------------------------------------------------


def _parse_pack(m: dict[str, Any]) -> str:
    path = "pack.yaml"
    _check_keys(m, ("version",), path)
    version = _req(m, "version", path)
    if isinstance(version, bool) or not isinstance(version, str | int):
        raise DefinitionError(f"{path}.version", "文字列または整数が必要です")
    return str(version)


def _parse_band(obj: object, path: str) -> Band:
    m = _as_map(obj, path)
    _check_keys(m, ("min", "label", "hint"), path)
    return Band(
        min=_req_int(m, "min", path),
        label=_req_str(m, "label", path),
        hint=_opt_str(m, "hint", path, ""),
    )


def _parse_axis(obj: object, path: str) -> AxisDef:
    m = _as_map(obj, path)
    _check_keys(
        m, ("id", "name", "layer", "role", "range", "initial", "bands", "description"), path
    )
    axis_id = _req_str(m, "id", path)
    path = f"{path}({axis_id})"
    rng = _as_list(_req(m, "range", path), f"{path}.range")
    if len(rng) != 2:
        raise DefinitionError(f"{path}.range", "[min, max] の2要素が必要です")
    min_value = _int(rng[0], f"{path}.range[0]")
    max_value = _int(rng[1], f"{path}.range[1]")
    if min_value >= max_value:
        raise DefinitionError(f"{path}.range", "min は max より小さい必要があります")
    initial = _opt_int(m, "initial", path, 0)
    if not min_value <= initial <= max_value:
        raise DefinitionError(f"{path}.initial", f"初期値 {initial} が range を逸脱しています")
    role = _enum(AxisRole, _req(m, "role", path), f"{path}.role")
    bands = tuple(
        _parse_band(b, f"{path}.bands[{i}]")
        for i, b in enumerate(_as_list(m.get("bands", []), f"{path}.bands"))
    )
    if role is AxisRole.LATENT and bands:
        raise DefinitionError(f"{path}.bands", "latent軸に帯(bands)は定義できません(§4)")
    if role is AxisRole.EXPRESSED:
        if not bands:
            raise DefinitionError(f"{path}.bands", "expressed軸には帯(bands)が必要です(§4)")
        for i, band in enumerate(bands):
            if not min_value <= band.min <= max_value:
                raise DefinitionError(f"{path}.bands[{i}].min", "range を逸脱しています")
            if i > 0 and band.min >= bands[i - 1].min:
                raise DefinitionError(f"{path}.bands[{i}].min", "帯は min の降順で並べてください")
        if bands[-1].min != min_value:
            raise DefinitionError(
                f"{path}.bands", "最後の帯の min は range の min と一致させてください"
            )
    return AxisDef(
        id=axis_id,
        name=_opt_str(m, "name", path, axis_id),
        layer=_enum(AxisLayer, m.get("layer", "directed"), f"{path}.layer"),
        role=role,
        min_value=min_value,
        max_value=max_value,
        initial=initial,
        bands=bands,
        description=_opt_str(m, "description", path, ""),
    )


def _parse_axes(m: dict[str, Any]) -> dict[str, AxisDef]:
    path = "axes.yaml"
    _check_keys(m, ("axes",), path)
    axes = [
        _parse_axis(a, f"{path}.axes[{i}]")
        for i, a in enumerate(_as_list(_req(m, "axes", path), f"{path}.axes"))
    ]
    _unique_ids([a.id for a in axes], f"{path}.axes")
    return {a.id: a for a in axes}


def _parse_track(obj: object, path: str) -> TrackDef:
    m = _as_map(obj, path)
    _check_keys(m, ("id", "name", "states", "initial", "transitions"), path)
    track_id = _req_str(m, "id", path)
    path = f"{path}({track_id})"
    states = _str_list(_req(m, "states", path), f"{path}.states")
    if not states:
        raise DefinitionError(f"{path}.states", "状態が1つ以上必要です")
    _unique_ids(states, f"{path}.states")
    initial = _req_str(m, "initial", path)
    if initial not in states:
        raise DefinitionError(f"{path}.initial", f"未知の状態 '{initial}'")
    transitions: list[TransitionDef] = []
    for i, t in enumerate(_as_list(m.get("transitions", []), f"{path}.transitions")):
        tpath = f"{path}.transitions[{i}]"
        tm = _as_map(t, tpath)
        _check_keys(tm, ("from", "to", "meaning"), tpath)
        from_state = _req_str(tm, "from", tpath)
        to_state = _req_str(tm, "to", tpath)
        for s in (from_state, to_state):
            if s not in states:
                raise DefinitionError(tpath, f"未知の状態 '{s}'")
        if from_state == to_state:
            raise DefinitionError(tpath, "from と to が同じ遷移は定義できません")
        meaning = _enum(TransitionMeaning, _req(tm, "meaning", tpath), f"{tpath}.meaning")
        transitions.append(TransitionDef(from_state, to_state, meaning))
    return TrackDef(
        id=track_id,
        name=_opt_str(m, "name", path, track_id),
        states=states,
        initial=initial,
        transitions=tuple(transitions),
    )


def _parse_tracks(m: dict[str, Any]) -> dict[str, TrackDef]:
    path = "tracks.yaml"
    _check_keys(m, ("tracks",), path)
    tracks = [
        _parse_track(t, f"{path}.tracks[{i}]")
        for i, t in enumerate(_as_list(_req(m, "tracks", path), f"{path}.tracks"))
    ]
    _unique_ids([t.id for t in tracks], f"{path}.tracks")
    return {t.id: t for t in tracks}


def _parse_traits(m: dict[str, Any]) -> dict[str, TraitDef]:
    path = "traits.yaml"
    _check_keys(m, ("traits",), path)
    traits: list[TraitDef] = []
    for i, t in enumerate(_as_list(_req(m, "traits", path), f"{path}.traits")):
        tpath = f"{path}.traits[{i}]"
        tm = _as_map(t, tpath)
        _check_keys(tm, ("id", "name", "description"), tpath)
        trait_id = _req_str(tm, "id", tpath)
        traits.append(
            TraitDef(
                id=trait_id,
                name=_opt_str(tm, "name", tpath, trait_id),
                description=_opt_str(tm, "description", tpath, ""),
            )
        )
    _unique_ids([t.id for t in traits], f"{path}.traits")
    return {t.id: t for t in traits}


def _parse_affinity(m: dict[str, Any], traits: dict[str, TraitDef]) -> tuple[AffinityRule, ...]:
    path = "affinity.yaml"
    _check_keys(m, ("affinity_rules",), path)
    rules: list[AffinityRule] = []
    for i, r in enumerate(_as_list(_req(m, "affinity_rules", path), f"{path}.affinity_rules")):
        rpath = f"{path}.affinity_rules[{i}]"
        rm = _as_map(r, rpath)
        _check_keys(rm, ("id", "when", "value", "symmetric"), rpath)
        rule_id = _req_str(rm, "id", rpath)
        rpath = f"{rpath}({rule_id})"
        when = _as_map(_req(rm, "when", rpath), f"{rpath}.when")
        _check_keys(when, ("a_has", "b_has"), f"{rpath}.when")
        a_has = _req_str(when, "a_has", f"{rpath}.when")
        b_has = _req_str(when, "b_has", f"{rpath}.when")
        for tag in (a_has, b_has):
            if tag not in traits:
                raise DefinitionError(f"{rpath}.when", f"未知の性格タグ '{tag}'")
        rules.append(
            AffinityRule(
                id=rule_id,
                a_has=a_has,
                b_has=b_has,
                value=_req_int(rm, "value", rpath),
                symmetric=_opt_bool(rm, "symmetric", rpath, False),
            )
        )
    _unique_ids([r.id for r in rules], f"{path}.affinity_rules")
    return tuple(rules)


class _EventScope:
    """イベント内の参照検証に使う文脈。"""

    def __init__(
        self,
        roles: tuple[str, ...],
        axes: dict[str, AxisDef],
        tracks: dict[str, TrackDef],
        traits: dict[str, TraitDef],
    ) -> None:
        self.roles = roles
        self.axes = axes
        self.tracks = tracks
        self.traits = traits

    def role(self, name: str, path: str) -> str:
        if name not in self.roles:
            raise DefinitionError(path, f"未知の役割 '{name}'(定義済み: {', '.join(self.roles)})")
        return name

    def axis(self, name: str, path: str) -> str:
        if name not in self.axes:
            raise DefinitionError(path, f"未知の軸 '{name}'")
        return name

    def track_state(self, track: str, state: str, path: str) -> None:
        if track not in self.tracks:
            raise DefinitionError(path, f"未知のトラック '{track}'")
        if state not in self.tracks[track].states:
            raise DefinitionError(path, f"トラック '{track}' に状態 '{state}' はありません")

    def trait(self, name: str, path: str) -> str:
        if name not in self.traits:
            raise DefinitionError(path, f"未知の性格タグ '{name}'")
        return name


def _parse_condition(obj: object, path: str, scope: _EventScope) -> Condition:
    m = _as_map(obj, path)
    kind = _req_str(m, "type", path)
    match kind:
        case "acquainted":
            _check_keys(m, ("type",), path)
            return Acquainted()
        case "not_acquainted":
            _check_keys(m, ("type",), path)
            return NotAcquainted()
        case "cooldown_elapsed":
            _check_keys(m, ("type",), path)
            return CooldownElapsed()
        case "track_is":
            _check_keys(m, ("type", "track", "state"), path)
            track = _req_str(m, "track", path)
            state = _req_str(m, "state", path)
            scope.track_state(track, state, path)
            return TrackIs(track, state)
        case "axis_at_least" | "axis_below":
            _check_keys(m, ("type", "axis", "from", "to", "value"), path)
            axis = scope.axis(_req_str(m, "axis", path), f"{path}.axis")
            from_role = scope.role(_req_str(m, "from", path), f"{path}.from")
            to_role = scope.role(_req_str(m, "to", path), f"{path}.to")
            if from_role == to_role:
                raise DefinitionError(path, "from と to は別の役割にしてください")
            value = _req_int(m, "value", path)
            if kind == "axis_at_least":
                return AxisAtLeast(axis, from_role, to_role, value)
            return AxisBelow(axis, from_role, to_role, value)
        case "has_trait":
            _check_keys(m, ("type", "role", "trait"), path)
            return HasTrait(
                scope.role(_req_str(m, "role", path), f"{path}.role"),
                scope.trait(_req_str(m, "trait", path), f"{path}.trait"),
            )
        case "compat_at_least":
            _check_keys(m, ("type", "from", "to", "value"), path)
            from_role = scope.role(_req_str(m, "from", path), f"{path}.from")
            to_role = scope.role(_req_str(m, "to", path), f"{path}.to")
            if from_role == to_role:
                raise DefinitionError(path, "from と to は別の役割にしてください")
            return CompatAtLeast(from_role, to_role, _req_int(m, "value", path))
        case "all" | "any":
            _check_keys(m, ("type", "of"), path)
            items = _parse_conditions(_req(m, "of", path), f"{path}.of", scope)
            if not items:
                raise DefinitionError(f"{path}.of", "条件が1つ以上必要です")
            return AllOf(items) if kind == "all" else AnyOf(items)
        case "not":
            _check_keys(m, ("type", "cond"), path)
            return Not(_parse_condition(_req(m, "cond", path), f"{path}.cond", scope))
        case _:
            raise DefinitionError(f"{path}.type", f"未知の条件種別 '{kind}'")


def _parse_conditions(obj: object, path: str, scope: _EventScope) -> tuple[Condition, ...]:
    return tuple(
        _parse_condition(c, f"{path}[{i}]", scope) for i, c in enumerate(_as_list(obj, path))
    )


def _parse_delta(obj: object, path: str, scope: _EventScope) -> DeltaSpec:
    m = _as_map(obj, path)
    _check_keys(m, ("from", "to", "axis", "value"), path)
    from_role = scope.role(_req_str(m, "from", path), f"{path}.from")
    to_role = scope.role(_req_str(m, "to", path), f"{path}.to")
    if from_role == to_role:
        raise DefinitionError(path, "自分自身への delta は定義できません")
    return DeltaSpec(
        from_role=from_role,
        to_role=to_role,
        axis=scope.axis(_req_str(m, "axis", path), f"{path}.axis"),
        value=_req_int(m, "value", path),
    )


def _parse_fact(obj: object, path: str, scope: _EventScope) -> FactSpec:
    m = _as_map(obj, path)
    _check_keys(m, ("kind", "audience", "recipients"), path)
    audience = _enum(Audience, _req(m, "audience", path), f"{path}.audience")
    recipients = tuple(
        scope.role(r, f"{path}.recipients[{i}]")
        for i, r in enumerate(_str_list(m.get("recipients", []), f"{path}.recipients"))
    )
    if audience is Audience.EXPLICIT and not recipients:
        raise DefinitionError(path, "audience: explicit には recipients が必要です")
    if audience is not Audience.EXPLICIT and recipients:
        raise DefinitionError(path, "recipients は audience: explicit のときだけ指定できます")
    return FactSpec(kind=_req_str(m, "kind", path), audience=audience, recipients=recipients)


def _parse_result(obj: object, path: str, scope: _EventScope) -> ResultSpec:
    m = _as_map(obj, path)
    _check_keys(
        m, ("kind", "success", "deltas", "transition", "establish_acquaintance", "facts"), path
    )
    transition: TransitionSpec | None = None
    if m.get("transition") is not None:
        tpath = f"{path}.transition"
        tm = _as_map(m["transition"], tpath)
        _check_keys(tm, ("track", "to"), tpath)
        track = _req_str(tm, "track", tpath)
        to_state = _req_str(tm, "to", tpath)
        scope.track_state(track, to_state, tpath)
        if not any(t.to_state == to_state for t in scope.tracks[track].transitions):
            raise DefinitionError(
                tpath, f"トラック '{track}' に '{to_state}' への遷移が定義されていません"
            )
        transition = TransitionSpec(track, to_state)
    return ResultSpec(
        kind=_req_str(m, "kind", path),
        success=_opt_bool(m, "success", path, True),
        deltas=tuple(
            _parse_delta(d, f"{path}.deltas[{i}]", scope)
            for i, d in enumerate(_as_list(m.get("deltas", []), f"{path}.deltas"))
        ),
        transition=transition,
        establish_acquaintance=_opt_bool(m, "establish_acquaintance", path, False),
        facts=tuple(
            _parse_fact(f, f"{path}.facts[{i}]", scope)
            for i, f in enumerate(_as_list(m.get("facts", []), f"{path}.facts"))
        ),
    )


def _parse_outcome(obj: object, path: str, scope: _EventScope) -> OutcomeDef:
    m = _as_map(obj, path)
    _check_keys(m, ("id", "when", "results"), path)
    outcome_id = _req_str(m, "id", path)
    path = f"{path}({outcome_id})"
    results = tuple(
        _parse_result(r, f"{path}.results[{i}]", scope)
        for i, r in enumerate(_as_list(_req(m, "results", path), f"{path}.results"))
    )
    if not results:
        raise DefinitionError(f"{path}.results", "結果が1つ以上必要です")
    return OutcomeDef(
        id=outcome_id,
        when=_parse_conditions(m.get("when", []), f"{path}.when", scope),
        results=results,
    )


def _parse_event(
    obj: object,
    path: str,
    axes: dict[str, AxisDef],
    tracks: dict[str, TrackDef],
    traits: dict[str, TraitDef],
) -> EventDef:
    m = _as_map(obj, path)
    _check_keys(
        m,
        (
            "id",
            "name",
            "shape",
            "roles",
            "trigger",
            "observer_policy",
            "content_rating",
            "occurrence",
            "cooldown",
            "outcomes",
        ),
        path,
    )
    event_id = _req_str(m, "id", path)
    path = f"{path}({event_id})"
    shape = _enum(EventShape, _req(m, "shape", path), f"{path}.shape")
    roles = _str_list(m.get("roles", []), f"{path}.roles")
    _unique_ids(roles, f"{path}.roles")
    match shape:
        case EventShape.DIRECTED:
            if roles != ("actor", "target"):
                raise DefinitionError(f"{path}.roles", "directed の役割は [actor, target] 固定です")
        case EventShape.PAIR:
            if len(roles) != 2:
                raise DefinitionError(f"{path}.roles", "pair の役割は2つ必要です")
        case EventShape.WORLD:
            if roles:
                raise DefinitionError(f"{path}.roles", "world イベントに役割は指定できません")
    for role in roles:
        if role in _COMMON_PLACEHOLDERS:
            raise DefinitionError(f"{path}.roles", f"役割名 '{role}' は予約語です")
    scope = _EventScope(roles, axes, tracks, traits)
    trigger = _enum(EventTrigger, m.get("trigger", "lottery"), f"{path}.trigger")

    occurrence: OccurrenceDef | None = None
    if m.get("occurrence") is not None:
        opath = f"{path}.occurrence"
        om = _as_map(m["occurrence"], opath)
        _check_keys(om, ("weight", "when"), opath)
        weight = _req_int(om, "weight", opath)
        if weight <= 0:
            raise DefinitionError(f"{opath}.weight", "重みは正の整数にしてください")
        occurrence = OccurrenceDef(
            weight=weight, when=_parse_conditions(om.get("when", []), f"{opath}.when", scope)
        )
    if trigger is EventTrigger.LOTTERY and occurrence is None:
        raise DefinitionError(path, "trigger: lottery のイベントには occurrence が必要です")
    if trigger is EventTrigger.DAILY and occurrence is not None:
        raise DefinitionError(path, "trigger: daily のイベントに occurrence は指定できません")
    if trigger is EventTrigger.DAILY and shape is not EventShape.WORLD:
        raise DefinitionError(path, "trigger: daily のイベントは shape: world にしてください")

    outcomes = tuple(
        _parse_outcome(o, f"{path}.outcomes[{i}]", scope)
        for i, o in enumerate(_as_list(_req(m, "outcomes", path), f"{path}.outcomes"))
    )
    if not outcomes:
        raise DefinitionError(f"{path}.outcomes", "結果分岐が1つ以上必要です")
    _unique_ids([o.id for o in outcomes], f"{path}.outcomes")
    if outcomes[-1].when:
        raise DefinitionError(
            f"{path}.outcomes", "最後の結果分岐は条件なし(既定分岐)にしてください"
        )
    if shape is EventShape.WORLD:
        for o in outcomes:
            for r in o.results:
                if r.deltas or r.transition is not None or r.establish_acquaintance:
                    raise DefinitionError(
                        f"{path}.outcomes({o.id})", "world イベントは delta・遷移・面識を持てません"
                    )

    cooldown: CooldownDef | None = None
    if m.get("cooldown") is not None:
        cpath = f"{path}.cooldown"
        cm = _as_map(m["cooldown"], cpath)
        _check_keys(cm, ("scope", "days", "after_outcomes"), cpath)
        scope_value = _enum(CooldownScope, _req(cm, "scope", cpath), f"{cpath}.scope")
        if scope_value is CooldownScope.ACTOR_TO_TARGET and shape is not EventShape.DIRECTED:
            raise DefinitionError(f"{cpath}.scope", "actor_to_target は directed イベント専用です")
        if shape is EventShape.WORLD:
            raise DefinitionError(cpath, "world イベントにクールダウンは指定できません")
        days = _req_int(cm, "days", cpath)
        if days <= 0:
            raise DefinitionError(f"{cpath}.days", "日数は正の整数にしてください")
        after = _str_list(_req(cm, "after_outcomes", cpath), f"{cpath}.after_outcomes")
        if not after:
            raise DefinitionError(f"{cpath}.after_outcomes", "結果分岐IDが1つ以上必要です")
        known = {o.id for o in outcomes}
        for name in after:
            if name not in known:
                raise DefinitionError(f"{cpath}.after_outcomes", f"未知の結果分岐 '{name}'")
        cooldown = CooldownDef(scope=scope_value, days=days, after_outcomes=after)

    return EventDef(
        id=event_id,
        name=_opt_str(m, "name", path, event_id),
        shape=shape,
        roles=roles,
        trigger=trigger,
        observer_policy=_enum(
            ObserverPolicy, m.get("observer_policy", "none"), f"{path}.observer_policy"
        ),
        content_rating=_enum(
            ContentRating, m.get("content_rating", "sfw"), f"{path}.content_rating"
        ),
        occurrence=occurrence,
        cooldown=cooldown,
        outcomes=outcomes,
    )


def _parse_events(
    m: dict[str, Any],
    axes: dict[str, AxisDef],
    tracks: dict[str, TrackDef],
    traits: dict[str, TraitDef],
) -> dict[str, EventDef]:
    path = "events.yaml"
    _check_keys(m, ("events",), path)
    events = [
        _parse_event(e, f"{path}.events[{i}]", axes, tracks, traits)
        for i, e in enumerate(_as_list(_req(m, "events", path), f"{path}.events"))
    ]
    _unique_ids([e.id for e in events], f"{path}.events")
    return {e.id: e for e in events}


def _placeholders(text: str, path: str) -> set[str]:
    names: set[str] = set()
    try:
        for _, field_name, _, _ in string.Formatter().parse(text):
            if field_name is None:
                continue
            if not field_name or not field_name.isidentifier():
                raise DefinitionError(path, f"不正なプレースホルダ '{{{field_name}}}'")
            names.add(field_name)
    except ValueError as exc:
        raise DefinitionError(path, f"テンプレート書式が不正です: {exc}") from None
    return names


def _parse_templates(m: dict[str, Any], events: dict[str, EventDef]) -> tuple[TemplateDef, ...]:
    path = "templates.yaml"
    _check_keys(m, ("templates",), path)

    # (result_kind, success) → その結果を生む全イベントの役割集合(プレースホルダ検証用)
    producers: dict[tuple[str, bool], list[tuple[str, ...]]] = {}
    for event in events.values():
        for outcome in event.outcomes:
            for result in outcome.results:
                producers.setdefault((result.kind, result.success), []).append(event.roles)

    templates: list[TemplateDef] = []
    seen: set[tuple[str, bool | None]] = set()
    for i, t in enumerate(_as_list(_req(m, "templates", path), f"{path}.templates")):
        tpath = f"{path}.templates[{i}]"
        tm = _as_map(t, tpath)
        _check_keys(tm, ("result_kind", "success", "variants"), tpath)
        kind = _req_str(tm, "result_kind", tpath)
        success: bool | None = (
            None if "success" not in tm else _bool(tm["success"], f"{tpath}.success")
        )
        key = (kind, success)
        if key in seen:
            raise DefinitionError(tpath, f"テンプレートが重複しています: {key}")
        seen.add(key)
        variants = _str_list(_req(tm, "variants", tpath), f"{tpath}.variants")
        if not variants:
            raise DefinitionError(f"{tpath}.variants", "文面が1つ以上必要です")
        matching = [
            roles
            for (k, s), role_sets in producers.items()
            if k == kind and (success is None or s == success)
            for roles in role_sets
        ]
        if not matching:
            raise DefinitionError(tpath, f"結果種別 '{kind}' を生むイベントがありません")
        for j, text in enumerate(variants):
            names = _placeholders(text, f"{tpath}.variants[{j}]")
            for roles in matching:
                unknown = sorted(names - set(roles) - _COMMON_PLACEHOLDERS)
                if unknown:
                    raise DefinitionError(
                        f"{tpath}.variants[{j}]", f"未知のプレースホルダ: {', '.join(unknown)}"
                    )
        templates.append(TemplateDef(result_kind=kind, success=success, variants=variants))

    # すべての成立結果にテンプレートがあること(ログに出せない結果を作らない)
    for kind, success in producers:
        if (kind, success) not in seen and (kind, None) not in seen:
            raise DefinitionError(
                path, f"結果種別 '{kind}'(success={success}) のテンプレートがありません"
            )
    return tuple(templates)


def _parse_settings(m: dict[str, Any]) -> Settings:
    path = "settings.yaml"
    _check_keys(
        m,
        (
            "ticks_per_day",
            "event_slots_per_day",
            "real_seconds_per_game_day",
            "max_real_elapsed_seconds",
        ),
        path,
    )
    ticks_per_day = _req_int(m, "ticks_per_day", path)
    slots = _req_int(m, "event_slots_per_day", path)
    if ticks_per_day <= 0 or slots <= 0:
        raise DefinitionError(path, "ticks_per_day と event_slots_per_day は正の整数にしてください")
    if ticks_per_day % slots != 0:
        raise DefinitionError(
            path, "ticks_per_day は event_slots_per_day で割り切れる必要があります"
        )
    real_seconds = _float(
        _req(m, "real_seconds_per_game_day", path), f"{path}.real_seconds_per_game_day"
    )
    max_elapsed = _float(
        _req(m, "max_real_elapsed_seconds", path), f"{path}.max_real_elapsed_seconds"
    )
    if real_seconds <= 0 or max_elapsed <= 0:
        raise DefinitionError(path, "実時間の設定は正の数にしてください")
    return Settings(
        ticks_per_day=ticks_per_day,
        event_slots_per_day=slots,
        real_seconds_per_game_day=real_seconds,
        max_real_elapsed_seconds=max_elapsed,
    )


def _parse_casters(m: dict[str, Any], traits: dict[str, TraitDef]) -> tuple[CasterDef, ...]:
    path = "casters.yaml"
    _check_keys(m, ("casters",), path)
    casters: list[CasterDef] = []
    for i, c in enumerate(_as_list(_req(m, "casters", path), f"{path}.casters")):
        cpath = f"{path}.casters[{i}]"
        cm = _as_map(c, cpath)
        _check_keys(cm, ("id", "name", "traits"), cpath)
        caster_id = _req_str(cm, "id", cpath)
        tags = _str_list(cm.get("traits", []), f"{cpath}.traits")
        _unique_ids(tags, f"{cpath}.traits")
        for tag in tags:
            if tag not in traits:
                raise DefinitionError(f"{cpath}.traits", f"未知の性格タグ '{tag}'")
        casters.append(CasterDef(id=caster_id, name=_req_str(cm, "name", cpath), traits=tags))
    _unique_ids([c.id for c in casters], f"{path}.casters")
    if len(casters) < 2:
        raise DefinitionError(f"{path}.casters", "キャラは2人以上必要です")
    return tuple(casters)


# ---------------------------------------------------------------------------
# 公開API
# ---------------------------------------------------------------------------


def load_content_pack(directory: Path) -> ContentPack:
    """定義データのディレクトリを読み込み、検証済みの `ContentPack` を返す。"""
    if not directory.is_dir():
        raise DefinitionError(str(directory), "定義データのディレクトリがありません")
    raw = {name: _read_yaml(directory, name) for name in PACK_FILES}
    version = _parse_pack(raw["pack.yaml"])
    axes = _parse_axes(raw["axes.yaml"])
    tracks = _parse_tracks(raw["tracks.yaml"])
    traits = _parse_traits(raw["traits.yaml"])
    affinity_rules = _parse_affinity(raw["affinity.yaml"], traits)
    events = _parse_events(raw["events.yaml"], axes, tracks, traits)
    templates = _parse_templates(raw["templates.yaml"], events)
    settings = _parse_settings(raw["settings.yaml"])
    casters = _parse_casters(raw["casters.yaml"], traits)
    return ContentPack(
        version=version,
        axes=axes,
        tracks=tracks,
        traits=traits,
        affinity_rules=affinity_rules,
        events=events,
        templates=templates,
        settings=settings,
        casters=casters,
    )
