# 第①段階 実装計画(承認待ち)

対象: `docs/design_v6.md` §12「① 最初の動作試作」。本計画は正本を変更しない。正本にない判断は末尾「確認したい点」に列挙し、承認前は仮置きとして扱う。

## 1. ディレクトリ構成

```
pyproject.toml            # ruff / mypy(strict) / pytest 設定
README.md                 # 実行・検証コマンド
content/                  # 定義データ(YAML)。コードにゲーム固有値を置かない
  axes.yaml               # 軸定義(§4): 好感度(latent)・友情度・恋愛度
  tracks.yaml             # トラック定義(§6): 友情(none)・恋愛(none→lovers)
  events.yaml             # イベント定義(§16): 出会い/通常交流/ときめき/告白/再告白/日次
  affinity.yaml           # 相性ルール(§21)
  templates.yaml          # テンプレートログ(結果種別ごとの文面バリエーション)
  casters.yaml            # 検証用キャラ2〜3人(性格タグ数個)
src/kankei/
  __init__.py
  definitions/            # M2 定義データ層
    schema.py             #   frozen dataclass: AxisDef / TrackDef / EventDef / AffinityRule / TemplateDef / ContentPack
    loader.py             #   yaml.safe_load → 型・必須項目・range・参照ID検証(不正は補正せず例外)
    conditions.py         #   条件式の定義型(YAMLの when/accept 等を表す判別共用体)
    errors.py             #   DefinitionError
  model/                  # M3 コアモデル
    ids.py                #   CasterId / EventInstanceId / ResultId / FactId (NewType)
    clock.py              #   GameTime(tick→day/時刻換算)。実時間を持たない
    caster.py             #   Caster(成人Caster。solo属性=性格タグ集合)
    directed.py           #   DirectedStore: (from,to,axis)→stored_value。effective_value窓口
    pair.py               #   PairState: 面識・トラック状態・固定タグ・クールダウン
    result.py             #   EventResult / ParticipantRole / ResultKind
    fact.py               #   Fact / Audience / Knowledge / KnowledgeVia
    history.py            #   AppliedDelta(候補→補正後→適用、前後値、作用ルールID)
    world.py              #   WorldState 集約 + CommitBatch(一括確定の単位)
  affinity.py             # M4 compat(A→B)
  engine/                 # M5 §7 パイプライン
    context.py            #   ステップ1用の読み取りスナップショット(有効値・状態・履歴)
    evaluate.py           #   条件式の評価(定義型 → bool / 数値)
    candidates.py         #   1: 候補列挙・除外・抽選(注入RNG)
    outcome.py            #   2: 結果候補(delta候補・遷移要求・EventResult草案・観測者候補)
    modifiers.py          #   3: 増減補正(恒等)+変化禁止の枠(適用ルールなし)
    apply.py              #   4-5: 仮更新(range内)・遷移評価・有効値再計算
    facts.py              #   fact生成・audience確定・Knowledge作成(§19)
    pipeline.py           #   6: CommitBatch 組立て → WorldState.commit。7 はtext層へ委譲
  text/                   # M6 テキスト化層(シミュレーションを変更しない)
    bands.py              #   expressed有効値→帯ラベル+hint。latentは受け付けない型境界
    render.py             #   EventResult+テンプレ定義 → ログ行(バリエーション選択もRNG注入)
  runtime/                # M6 常駐ループ
    scheduler.py          #   実時間→tick変換、進行速度、停止/再開で基準時刻を置き直す
    daily.py              #   日次処理イベント(履歴化のみ。降格判定は③段階)
    app.py                #   CUIループ(ログ出力・停止・保存)
  persistence/            # M7 保存
    snapshot.py           #   WorldState/GameTime/RNG状態/未処理作業/適用済みID/定義版 ⇄ dict
    atomic.py             #   同一FS一時ファイル→fsync→os.replace
  cli.py                  # エントリポイント(python -m kankei)
tests/
  test_definitions.py     # M2 検証(range逸脱・未知参照・必須欠落)
  test_model.py           # M3
  test_affinity.py        # M4
  test_pipeline.py        # M5
  test_runtime.py         # M6
  test_snapshot.py        # M7
  test_acceptance.py      # §12 シナリオ1・2・11・12 / §22 ①観点
```

パッケージ名 `kankei` は仮。

## 2. 主要な型

### 定義データ(`definitions/schema.py`、全て frozen dataclass)

| 型 | 主な項目 |
|---|---|
| `AxisDef` | id, layer(directed), role(expressed/latent), range(min,max), initial, bands(expressedのみ)。latentにbandsがあれば検証エラー |
| `TrackDef` | id, states(順序付き), initial, transitions[{from,to,meaning: promote/demote/dissolve}] (§17: 意味を明記) |
| `EventDef` | id, kind, participants(roles), occurrence(条件・重み), cooldown{scope: actor_to_target/pair, days}, outcomes(結果種別ごとのdelta/遷移/fact定義), observer_policy, content_rating(sfw固定。nsfwは型のみ予約) |
| `ResponseRule` | 告白の受諾条件(受け手→告白者の恋愛有効値の閾値など)。受諾/拒否それぞれの結果定義へ分岐 |
| `FactSpec` | result_kind → fact kind, audience(public/participants_only/explicit), track/state_after |
| `AffinityRule` | id, when{a_has,b_has}, value, symmetric |
| `TemplateDef` | result_kind(+成否), variants[str]。プレースホルダは参加者名・帯ラベルのみ |
| `ContentPack` | 上記の集約 + version(定義版) |

条件式(`definitions/conditions.py`)は最小の判別共用体: `AxisAtLeast/AxisBelow(axis, from_role, to_role, value)`, `HasTrait(role, tag)`, `CompatAtLeast(from_role, to_role, value)`, `TrackIs(track, state)`, `Acquainted`, `NotAcquainted`, `CooldownElapsed`, `All/Any/Not`。条件は有効値窓口経由で参照し、保存値を直接読まない。

### コアモデル(`model/`)

| 型 | 内容 |
|---|---|
| `GameTime` | tick(int), ticks_per_day から day / 日内tick を導出。比較可能 |
| `Caster` | id, name, traits(frozenset[str])。成人Caster専用型。ChildEntityは別型として将来追加(本段階では定義しない) |
| `DirectedStore` | `stored(from,to,axis)` / `effective(from,to,axis)` / `set_stored`。effectiveは `EffectiveValueResolver` Protocol 経由で、①段階の実装は保存値素通し |
| `PairState` | pair_key(順序正規化), acquainted(bool + 成立result_id), track_states{track_id: state}, fixed_tags, cooldowns{(event_id, scope_key): 期限GameTime} |
| `EventResult` | result_id, event_instance_id, event_def_id, result_kind, success, participants[(caster_id, role)], game_time, commit_seq, observed_by, definition_version |
| `AppliedDelta` | result_id, from, to, axis, candidate, after_modifier, applied, stored_before/after, effective_before/after, blocked(bool), rule_ids(空) |
| `Fact` | fact_id, source_result_id, kind, subjects[(caster_id, role)], game_time, commit_seq, track/state_after(任意), audience |
| `Knowledge` | owner, fact_id, via(participant/witnessed/told), acquired_at, acquired_seq, learned_from(told用に予約) |
| `CommitBatch` | 適用delta列、pair変更、results、facts、knowledge、cooldown更新、event_instance_id。`WorldState.commit(batch)` が単一の確定単位 |
| `WorldState` | casters, directed, pairs, results, facts, knowledge, applied_event_ids, next_seq, next_id採番、GameTime |

### エンジン

- `EventContext`(ステップ1の読み取りスナップショット): 有効値・pair状態・履歴参照・compat。以後のステップはこのスナップショットのみを条件判定に使う(適用前値で受諾判定、§16)。
- `OutcomeDraft`(ステップ2): delta候補・遷移要求・EventResult草案。永続化しない。
- `Modifier` Protocol(ステップ3): `(context, delta候補) → 補正後delta`。①段階は恒等実装のみ。変化禁止も `BlockPolicy` Protocol で枠のみ用意し、常に「禁止なし」。
- `Pipeline.run(event_def, binding, context)` が 1→6 を実行し `CommitBatch` を返す。7(テキスト化)は確定後に `text.render` が受け取る。

## 3. マイルストーンごとの要点

- **M1** pyproject(hatch/setuptools最小)、`ruff check`, `mypy --strict src tests`, `pytest` が空パッケージで通る。
- **M2** YAMLスキーマ+ローダ+検証。検証エラー: range逸脱、初期値がrange外、未知の軸/トラック/状態/イベント/性格タグ参照、latentへのbands付与、遷移の意味欠落、テンプレの未知プレースホルダ。
- **M3** 上記モデル。面識はpairの成立結果参照として保持し、友情状態と独立(§6)。
- **M4** `compat(a, b, rules)`: 該当ruleの総和をclamp(-100,100)。symmetricは正順/逆順どちらかが一致すれば1回のみ加算。solo変化で再計算(キャッシュなし、都度計算)。
- **M5** イベント: 出会い(面識成立)、通常交流(好感度delta)、ときめき(好感度またはcompat条件→片側恋愛delta、factなし)、告白(受諾条件=受け手→告白者の恋愛有効値≥閾値、受諾時のみ恋愛トラックnone→lovers、`confession_made`/`confession_reply`/`relationship_established` の3fact)、再告白(クールダウン後に同じ告白定義が候補に戻る。状況改善を要求しない)。
- **M6** tickスケジューラ: 実時間経過×速度→tick。停止・再開は基準実時刻を置き直し、停止分をtickに変換しない。日次処理イベントは日境界で1件のEventResultを残す。テンプレログはYAMLのvariantsから注入RNGで選択。
- **M7** スナップショット: WorldState + GameTime + `random.getstate()` + 未処理作業(①段階では空だが枠を保存) + applied_event_ids + 定義版。書込みは一時ファイル→`os.replace`。受け入れテスト: §12の1・2・11・12、§22 ①観点(開示範囲分離・内面delta非fact化・保存再開での時刻とRNG系列維持・相性の二重加算なし)。

## 4. 不変条件の守り方(構造)

- 乱数: `random.Random` を `Pipeline` / `render` / `scheduler` にコンストラクタ注入。テストで `random` モジュールのグローバル関数と `datetime.now` の未使用をgrepで検証する。
- 時刻: シミュレーション層は `GameTime` のみ。`time.monotonic()` は `runtime/scheduler.py` の外側制御に限定。
- LLM境界(§4・§21): `text/bands.py` は `ExpressedView` 型を返し、latent軸・生数値・compatを受け取らない引数型にする。プロンプト組立ては未実装だが、この型のみを入力とする接続点を予約。
- 知識の源(§19): `engine/facts.py` は `EventResult` のみを入力にし、`AppliedDelta` を参照できない関数シグネチャにする。
- ChildEntity(§11): `Caster` を成人専用型とし、`participants` の型を `CasterId` に限定。`EventDef.content_rating` を予約し、将来 `nsfw` イベントの参加者検証を差し込む位置をpipelineに残す(本段階では常に `sfw`)。
- 有効値窓口(§4): `DirectedStore.effective` を唯一の参照口にし、条件評価・帯変換はここだけを通す。

## 5. 確認したい点(正本に明記がない・仮置きしたもの)

1. **イベント発火の刻み**: tickは純粋なスケジューラ(§8)だが、何tickごとにイベント抽選を行うかは未記載。仮置き: `content/`側の設定 `event_slots_per_day`(検証用に例えば8)で、日内を等分したスロットごとに全順序ペアから候補を集め、重み付き抽選で最大1件実行(候補なしなら何も起こさない、§17「必ず成立するまで再抽選しない」に準拠)。
2. **観測者候補の決め方**: `observed_by` は「同席した非参加者」(§19)だが、①段階に場所・同席の概念はない。仮置き: `EventDef.observer_policy` に `none` / `others_present` を持たせ、①段階では「その世界の非参加者全員が同席」とみなす。場所の概念は導入しない。
3. **友情トラックの遷移**: M5のイベント一覧に「意気投合」がないため、①段階では友情トラックは定義のみで `none` に留まる(シナリオ1「友情関係なしのまま恋人」に必要な範囲)。意気投合イベントを①段階に含めるかは追加しない方針で進める。
4. **クールダウンの起点と単位**: 告白のクールダウンは「告白者→相手」方向、単位はゲーム内日数(§16の実装案に沿う)。拒否・受諾どちらでも起点にするか未記載のため、仮置きで拒否時のみ設定する(受諾後は恋人状態で告白が候補条件から外れるため実質不要)。
5. **日次処理イベントの内容**: ①段階では日境界を履歴化するだけ(降格・持続判定は③段階)。クールダウン残日数の減算は日次ではなくGameTime比較で行う。
6. **パッケージ名** `kankei` の可否。
