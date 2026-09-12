# 第①段階 実装計画(レビュー反映版)

対象: `docs/design_v6.md` §12「① 最初の動作試作」。正本にない判断は末尾「仮置きと採用条件」に列挙する(レビュー済み・採用条件付きで確定)。

## 1. ディレクトリ構成

```
pyproject.toml            # ruff / mypy(strict) / pytest 設定
README.md                 # 実行・検証コマンド
content/                  # 定義データ(YAML)。コードにゲーム固有値を置かない
  pack.yaml               # 定義版(version)
  axes.yaml               # 軸定義(§4): 好感度(latent)・友情度・恋愛度
  traits.yaml             # 性格タグ(solo属性)の登録。参照検証用
  tracks.yaml             # トラック定義(§6): 友情(none)・恋愛(none→lovers)
  events.yaml             # イベント定義(§16): 出会い/通常交流/ときめき/告白/再告白/日次
  affinity.yaml           # 相性ルール(§21)
  templates.yaml          # テンプレートログ(結果種別ごとの文面バリエーション)
  casters.yaml            # 検証用キャラ2〜3人(性格タグ数個)
  settings.yaml           # 進行設定: ticks_per_day / event_slots_per_day / 実時間経過の上限 など
src/kankei/
  __init__.py
  definitions/            # M2 定義データ層
    schema.py             #   frozen dataclass: AxisDef / TrackDef / EventDef / FactSpec / AffinityRule / TemplateDef / Settings / ContentPack
    loader.py             #   yaml.safe_load → 型・必須項目・range・参照ID検証(不正は補正せず例外)
    conditions.py         #   条件式の定義型(YAMLの when/accept 等を表す判別共用体)
    errors.py             #   DefinitionError
  model/                  # M3 コアモデル
    ids.py                #   CasterId(str) / EventInstanceId / ResultId(int 連番) / FactId(int 連番)
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
    context.py            #   ステップ1用の読み取りスナップショット(EventContext)
    evaluate.py           #   条件式の評価(定義型 → bool / 数値)
    candidates.py         #   1: 候補列挙・正規化・重複排除・ソート・抽選(注入RNG)
    outcome.py            #   2: 結果候補(delta候補・遷移要求・EventResult草案・観測者候補)
    modifiers.py          #   3: 増減補正(恒等)+変化禁止の枠(適用ルールなし)
    apply.py              #   4-5: 仮更新(range内)・仮更新後状態での遷移評価・有効値再計算
    facts.py              #   fact生成・audience確定・Knowledge作成(§19)
    pipeline.py           #   6: CommitBatch 組立て → WorldState.commit。7 はtext層へ委譲
  text/                   # M6 テキスト化層(シミュレーションを変更しない・乱数を消費しない)
    bands.py              #   expressed有効値→帯ラベル+hint。latentは受け付けない型境界
    render.py             #   EventResult+テンプレ定義 → ログ行。文面は result_id から決定的に選択
  runtime/                # M6 常駐ループ
    scheduler.py          #   実時間→tick変換、進行速度、経過時間のクランプ、停止/再開で基準時刻を置き直す
    slots.py              #   スロット進行(日次処理→新スロットの固定順、処理済みスロット位置の管理)
    daily.py              #   日次処理イベント(履歴化のみ。降格判定は③段階)
    app.py                #   CUIループ(ログ出力・停止・保存)
  persistence/            # M7 保存
    snapshot.py           #   WorldState/GameTime/RNG状態/処理済みスロット位置/未処理作業/適用済みID/定義版 ⇄ dict
    atomic.py             #   同一FS一時ファイル→fsync→os.replace
  cli.py                  # エントリポイント(python -m kankei)
tests/
  test_definitions.py     # M2 検証(range逸脱・未知参照・必須欠落)
  test_model.py           # M3
  test_affinity.py        # M4
  test_pipeline.py        # M5
  test_runtime.py         # M6(注入した擬似実時間でクランプ・停止再開を検証)
  test_snapshot.py        # M7
  test_determinism.py     # 同一seed連続実行の一致 / 途中保存→再開の一致(主検証)
  test_acceptance.py      # §12 シナリオ1・2・11・12 / §22 ①観点
```

ファイル構成を完成させることより、最小のイベントが抽選から保存まで一貫して動くことを優先する。パッケージ名は `kankei`。

## 2. 主要な型

### 定義データ(`definitions/schema.py`、全て frozen dataclass)

| 型 | 主な項目 |
|---|---|
| `AxisDef` | id, layer(directed), role(expressed/latent), range(min,max), initial, bands(expressedのみ)。latentにbandsがあれば検証エラー |
| `TrackDef` | id, states(順序付き), initial, transitions[{from,to,meaning: promote/demote/dissolve}] (§17: 意味を明記) |
| `EventDef` | id, shape(directed / pair / world: 候補の正規化方法), roles, trigger(lottery / daily), occurrence(条件・重み), cooldown{scope: actor_to_target/pair, days, after_outcomes}, outcomes, observer_policy, content_rating(sfw固定。nsfwは型のみ予約) |
| `OutcomeDef` | 結果分岐。定義順に when を評価し最初に成立した分岐を採用(告白の受諾/拒否はこれで決定的に判定)。最後は条件なしの既定分岐 |
| `ResultSpec` | 1分岐から複数の成立結果を作る(告白経験と関係成立は別)。kind, success, deltas, transition, establish_acquaintance, facts |
| `FactSpec` | fact kind, audience(public/participants_only/explicit), recipients。開示範囲は定義データ側に置き、全告白共通の規則にしない |
| `AffinityRule` | id, when{a_has,b_has}, value, symmetric |
| `TemplateDef` | result_kind(+成否), variants[str](順序付き)。プレースホルダは参加者名・帯ラベルのみ |
| `Settings` | ticks_per_day, event_slots_per_day, max_real_elapsed_seconds(クランプ上限), 進行速度の既定値 |
| `ContentPack` | 上記の集約 + version(定義版) |

条件式(`definitions/conditions.py`)は最小の判別共用体: `AxisAtLeast/AxisBelow(axis, from_role, to_role, value)`, `HasTrait(role, tag)`, `CompatAtLeast(from_role, to_role, value)`, `TrackIs(track, state)`, `Acquainted`, `NotAcquainted`, `CooldownElapsed`, `All/Any/Not`。条件は有効値窓口経由で参照し、保存値を直接読まない。

### コアモデル(`model/`)

| 型 | 内容 |
|---|---|
| `GameTime` | tick(int), ticks_per_day から day / 日内tick を導出。比較可能 |
| `Caster` | id, name, traits(frozenset[str])。成人Caster専用型。ChildEntityは別型として将来追加(本段階では定義しない) |
| `DirectedStore` | `stored(from,to,axis)` / `effective(from,to,axis)` / `set_stored`。effectiveは `EffectiveValueResolver` Protocol 経由で、①段階の実装は保存値素通し |
| `PairState` | pair_key(順序正規化), acquainted(bool + 成立result_id), track_states{track_id: state}, fixed_tags, cooldowns{(event_id, scope_key): 期限GameTime} |
| `EventResult` | result_id(整数連番・保存対象), event_instance_id, event_def_id, result_kind, success, participants[(caster_id, role)], game_time, commit_seq, observed_by, definition_version, related_result_id(返答が元の告白結果を指す) |
| `AppliedDelta` | result_id, from, to, axis, candidate, after_modifier, applied, stored_before/after, effective_before/after, blocked(bool), rule_ids(空) |
| `Fact` | fact_id, source_result_id, kind, subjects[(caster_id, role)], game_time, commit_seq, track/state_after(任意), audience |
| `Knowledge` | owner, fact_id, via(participant/witnessed/told), acquired_at, acquired_seq, learned_from(told用に予約) |
| `CommitBatch` | 適用delta列、pair変更、results、facts、knowledge、cooldown更新、event_instance_id。`WorldState.commit(batch)` が単一の確定単位 |
| `WorldState` | casters, directed, pairs, results, facts, knowledge, applied_event_ids, next_seq, next_id採番、GameTime、処理済みスロット位置 |

### エンジン(§7 の参照情報の使い分け)

「開始時点のスナップショット」と「仮更新後の状態」を分け、各処理がどちらを参照するかを固定する。

| 判定・処理 | 参照する情報 | 実装上の入力 |
|---|---|---|
| 1. 発生条件・候補除外・抽選 | 開始時点 | `EventContext` |
| 2. 結果候補作成、告白の受諾/拒否判定 | 開始時点 | `EventContext` |
| 3. 今回のdeltaへの補正・変化禁止 | 開始時点 | `EventContext` |
| 4. 仮更新後の遷移評価 | 仮更新した状態 | `PendingState`(仮更新中の保存値・有効値・pair状態) |
| 5. 状態変更後の有効値再計算 | 変更後の状態 | `PendingState` |
| 6. 一括確定 | 4-5 の結果 | `CommitBatch` |

- `EventContext`: ステップ1で作る読み取り専用スナップショット。有効値・pair状態・履歴参照・compat。ステップ2〜3はこれのみを参照し、告白の返答を更新後の値で再判定しない(§16)。
- `PendingState`: ステップ4で `EventContext` から派生させる仮更新用の可変状態。永続化しない。遷移は各トラック最大1回。
- `OutcomeDraft`(ステップ2): delta候補・遷移要求・EventResult草案。永続化しない。
- `Modifier` Protocol(ステップ3): `(context, delta候補) → 補正後delta`。①段階は恒等実装のみ。変化禁止も `BlockPolicy` Protocol で枠のみ用意し、常に「禁止なし」。
- `Pipeline.run(event_def, binding, context)` が 1→6 を実行し `CommitBatch` を返す。7(テキスト化)は確定後に `text.render` が受け取る。
- fact生成(`engine/facts.py`)の入力は `EventResult` と `FactSpec`(定義データ)。世界の内部値(`AppliedDelta`・保存値・有効値)は引数に取らない。

### 候補の正規化・重複排除・順序固定(ステップ1)

- 重複排除キーは `(イベント定義ID, 正規化した参加者タプル)`。
  - 方向付きイベント(告白・ときめき等): 役割順の参加者IDタプル `(actor, target)`。A→B と B→A は別候補。
  - ペア共有イベント(出会い・通常交流等): 参加者IDをソートしたタプル。A-B の1候補のみ(重みが2倍にならない)。
- 抽選前に候補一覧を `(イベント定義ID, 参加者タプル)` でソートしてから重み付き抽選する。`set`/`dict` の列挙順に依存しない。
- 候補なしのスロットはイベントなしで進める(再抽選しない)。

## 3. マイルストーンごとの要点

- **M1**(完了) pyproject、`ruff check`, `mypy --strict`, `pytest` が空パッケージで通る。`[project.scripts]` は M6 で実体と同時に追加する。
- **M2**(完了) YAMLスキーマ+ローダ+検証。検証エラー: range逸脱、初期値がrange外、未知の軸/トラック/状態/イベント/性格タグ参照、latentへのbands付与、遷移の意味欠落、テンプレの未知プレースホルダ、variants空。
- **M3**(完了) 上記モデル。面識はpairの成立結果参照として保持し、友情状態と独立(§6)。`ResultId`・`FactId` は保存される整数連番。`WorldState.commit` はバッチ全体を先に検証し、失敗時は何も更新しない(`CommitError`)。事前検証は適用段で拒否され得る条件(自己参照delta・未知の軸・非正規化ペアキー・開示先外の直接認知・viaの不整合)をすべて先回りする。万一、検証通過後の適用中に例外が起きた場合は `FatalCommitError` とし `integrity_failure` に記録する。以後その世界状態は確定を受け付けず、M7の保存側もこのフラグが立っていれば保存せず停止する(壊れた状態をセーブに混入させない)。
- **M4**(完了) `compat(a, b, rules)`: 該当ruleの総和をclamp(-100,100)。symmetricは正順/逆順どちらかが一致すれば1回のみ加算。solo変化で再計算(キャッシュなし、都度計算)。`applied_rules` で寄与ルールを神視点表示用に列挙できる。
- **M5** イベントとfact:
  - 出会い: 面識成立。fact `met`(public)。
  - 通常交流: 好感度delta。factなし(内部delta)。
  - ときめき: 好感度またはcompat条件→片側恋愛delta。EventResultは残すがfactなし(内面の恋慕は開示しない)。
  - 告白(受諾): fact `confession_made`(public) / `confession_accepted`(participants_only、元の告白結果を `related_result_id` で参照) / `relationship_established`(public、track=romance, state_after=lovers)。
  - 告白(拒否): fact `confession_made`(public) / `confession_rejected`(participants_only、同上)。
  - 返答の種別を分割し、fact kind から受諾/拒否を特定できるようにする。開示範囲は `FactSpec`(定義データ)側に置く。
  - 再告白: 拒否時刻から指定日数のクールダウン経過後、同じ告白定義が候補に戻る。状況改善を要求しない。
  - 受諾条件は受け手→告白者の恋愛有効値≥閾値(閾値は定義データ)。受諾時のみ恋愛トラック none→lovers。
- **M6** 常駐ループとテキスト化:
  - スケジューラはループ毎に `elapsed = min(実測経過, 上限)` で進める(クランプ方式、正本§8)。仕様として (a) 復帰・再開時は最大で上限分だけゲームが進む、(b) 上限を超える高負荷が続く間は実時間に対してゲーム進行が遅れることを許容する、(c) 上限値は `content/settings.yaml` の設定。
  - 明示的な一時停止・終了後の再起動では、従来どおり実時間の計測基準を置き直す。把握できている停止時間を後から進めない。クランプはこれを代替しない。
  - 実時間の取得は `Clock` Protocol で注入し、テストでは擬似実時間で「長い実測経過が上限に丸められる」「一時停止→再開で停止分が進まない」を検証する。
  - 日境界と抽選スロットが同時刻の場合の処理順は「日次処理→新しい日のスロット」に固定する(日次は前日の締め)。
  - テキスト化は乱数を消費しない。文面は `variants[result_id % len(variants)]` で決定的に選択する。組込み `hash()` は使わない。再描写・過去ログ再生成でもシミュレーションの乱数系列に影響しない。
- **M7** 保存と受け入れテスト:
  - スナップショット: WorldState + GameTime + `random.getstate()` + 処理済みスロット位置(明示保存) + 未処理作業(①段階では空だが枠を保存) + applied_event_ids + ID採番の次値 + 定義版。
  - 保存できるタイミングはスロット処理の間(直前の `commit` 完了後、次の抽選前)のみ。確定単位の途中では保存しない。
  - `WorldState.integrity_failure` が設定されている世界は保存しない(FatalCommitError 後は停止し、直前の正常なセーブを残す)。
  - 書込みは同一ファイルシステムの一時ファイル→fsync→`os.replace`。
  - 完成条件:
    1. `commit` 途中で検証に失敗しても、世界状態の一部だけが更新されない(検証を先に完了してから差し替える)。
    2. セーブに含まれる世界状態・RNG状態・ID採番・処理済みスロット位置が互いに一致している。
    3. イベントなしのスロットも含め、再開後に抽選を重複実行しない。
    4. 定義版が違うセーブを、黙って現在の定義で再開しない(エラーにする)。
    5. 保存に失敗しても、直前の正常なセーブを読み込める。
    6. `integrity_failure` が設定された世界状態のスナップショット保存が拒否され、既存のセーブが上書きされない(M3からの引き継ぎ。M3時点では計画追記のみで未実装。pytestで明示的に検証する)。
  - 主検証(`test_determinism.py`): 同一seedの連続実行でイベント列・ログが完全一致。途中保存→再開したイベント列・ログが、保存せず続けた実行と完全一致。前提条件はテンプレート内容・並び順・定義版が同一であること。
  - 補助検証: `src/` で `random` モジュールのグローバル関数・`datetime.now`・組込み `hash()` が使われていないことのgrep検査。
  - 受け入れテスト: §12の1・2・11・12、§22 ①観点(発言と小声の返答で開示先が分かれる、内面delta非fact化、保存再開での時刻とRNG系列維持、相性の二重加算なし)。

## 4. 不変条件の守り方(構造)

- 乱数: `random.Random` を `Pipeline` と `scheduler` のみにコンストラクタ注入。テキスト化・表示は乱数を受け取らない。
- 時刻: シミュレーション層は `GameTime` のみ。実時間は `runtime/scheduler.py` の `Clock` Protocol 経由に限定。
- LLM境界(§4・§21): `text/bands.py` は `ExpressedView` 型を返し、latent軸・生数値・compatを受け取らない引数型にする。プロンプト組立ては未実装だが、この型のみを入力とする接続点を予約。
- 知識の源(§19): fact生成は `EventResult` と `FactSpec` のみを入力にし、`AppliedDelta`・軸値を参照できない関数シグネチャにする。
- ChildEntity(§11): `Caster` を成人専用型とし、`participants` の型を `CasterId` に限定。`EventDef.content_rating` を予約し、将来 `nsfw` イベントの参加者検証を差し込む位置をpipelineに残す(本段階では常に `sfw`)。
- 有効値窓口(§4): `DirectedStore.effective` を唯一の参照口にし、条件評価・帯変換はここだけを通す。

## 5. 仮置きと採用条件(レビュー済み)

1. **イベント発火の刻み**: `settings.yaml` の `event_slots_per_day` で日内を等分し、スロットごとに全順序ペアから候補を集め、§2の正規化・重複排除・ソート後に重み付き抽選で最大1件実行。候補なしなら無イベント。
2. **観測者候補**: `EventDef.observer_policy`(`none` / `others_present`)で表現し、①段階は「初期キャラ全員が同じ場にいる」という検証用前提に限定する。これは世界全体への公開を一般仕様にするものではない。正本§9の遠征・遭遇・キャンプの枠は「誰がその場にいるか」を決める将来の入力であり、`observer_policy` の解決時に場の参加者集合を差し替える接続点として残す。
3. **友情トラック**: 定義のみで `none` に留める(意気投合イベントは①段階に含めない)。
4. **クールダウン**: 告白者→相手の方向付き。拒否時のみ設定し、期限は拒否時刻から指定日数経過(翌日境界ではない)。逆方向(相手→告白者)の告白は止めない。
5. **日次処理**: 日境界の履歴化のみ。クールダウンはGameTime比較で判定。同時刻の処理順は日次処理→新スロット。
6. **パッケージ名** `kankei`。

## 6. M5 イベントパイプライン設計(実装前提示)

### 6.1 §7 の処理順とモジュール・関数の対応

| §7 | 責務 | モジュール | 関数・型 |
|---|---|---|---|
| 1 | 開始時点の情報取得 | `engine/context.py` | `EventContext.capture(world, pack, resolver)`: 時刻・キャラ・directed(コピー)・pair(コピー)・cooldowns(コピー)・相性・次のID/確定順を固定した読み取り専用スナップショット |
| 1 | 候補除外と抽選 | `engine/candidates.py` | `collect_candidates(pack, ctx) -> tuple[Candidate, ...]`(列挙→正規化→重複排除→発生条件評価→ソート)、`draw(candidates, rng) -> Candidate \| None`(重み付き抽選、最大1件) |
| 1・2 | 条件式の評価 | `engine/evaluate.py` | `evaluate(cond, ctx, event_def, binding) -> bool`。有効値窓口(`ctx.effective`)のみを参照 |
| 2 | 結果候補の作成 | `engine/outcome.py` | `build_outcome(event_def, binding, ctx) -> OutcomeDraft`: 分岐を定義順に評価して最初に成立した `OutcomeDef` を採用(告白の返答はここで決定的に確定、追加抽選なし)。`DeltaCandidate` 列・結果草案・観測者候補(`observer_policy` 解決)を持つ。永続化しない |
| 3 | 増減補正・変化禁止 | `engine/modifiers.py` | `Modifier` Protocol(`adjust`)・`BlockPolicy` Protocol(`blocks`)、既定実装 `IdentityModifier` / `NoBlock`。`apply_modifiers(ctx, candidates, modifiers, blocks) -> tuple[ModifiedDelta, ...]`。補正の後に変化禁止を最後に適用する順序だけ固定(第①段階は恒等・禁止なし) |
| 4 | 値の仮更新 | `engine/apply.py` | `PendingState.from_context(ctx)`(directed/pair の可変コピー)、`PendingState.apply_deltas(modified) -> tuple[PendingDelta, ...]`: 軸範囲内で保存値を更新し、有効値窓口で有効値を再計算。保護下限なし |
| 4・5 | 遷移評価・状態更新 | `engine/apply.py` | `PendingState.apply_results(draft) -> tuple[PendingResult, ...]`: 仮更新後の pair 状態に対して遷移(`TrackDef.transitions` に存在する from→to のみ、各トラック1イベント最大1回)と面識成立を反映。不整合は `PipelineError`。状態変更後に有効値を再計算(pass-through では値は不変だが窓口を再度通す) |
| 6 | 一括確定 | `engine/pipeline.py` + `model/world.py` | `assemble_batch(ctx, draft, pending, ...) -> CommitBatch`: ID採番(result_id / fact_id は ctx が固定した次値から連番)、EventResult 生成、fact・知識生成(`engine/facts.py`)、クールダウン更新を1つの `CommitBatch` にまとめ、`WorldState.commit(batch)` で確定 |
| 6 | fact・知識 | `engine/facts.py` | `generate_facts(result, specs, next_fact_id, commit_seq) -> tuple[tuple[Fact, ...], tuple[Knowledge, ...]]`。入力は `EventResult` と `FactSpec`(定義データ)のみ。開示範囲: public=参加者+observed_by、participants_only=参加者、explicit=指定役割。知識の via は参加者→participant、observed_by→witnessed |
| 7 | テキスト化 | `text/render.py`(M6) | 確定後の `EventResult` とテンプレ定義から文面を決定的に選択。M5 では呼ばない |

入口: `Pipeline(pack, rng, resolver=None, modifiers=(), blocks=())`。`Pipeline.run_slot(world) -> SlotReport`(候補収集→抽選→`run_event`)、`Pipeline.run_event(world, event_def, binding) -> CommitBatch`(2〜6。テストから直接呼べる)。

### 6.2 各ステップが参照する状態

| 処理 | 参照 | 実装上の入力 | 計画書 §2 の表との対応 |
|---|---|---|---|
| 1 候補列挙・発生条件・クールダウン判定・抽選 | 開始時点 | `EventContext` | 1 |
| 2 結果分岐の判定(告白の受諾/拒否を含む) | 開始時点 | `EventContext` | 2 |
| 3 増減補正・変化禁止 | 開始時点 | `EventContext` | 3 |
| 4 保存値の仮更新・有効値の再計算 | 仮更新中 | `PendingState`(ctx から派生) | 4 |
| 4 遷移の評価(現在状態→遷移先が定義に存在するか) | 仮更新後 | `PendingState` | 4 |
| 5 状態(面識・トラック)の更新、更新後の有効値再計算 | 変更後 | `PendingState` | 5 |
| 6 CommitBatch 組立て・確定 | 4-5 の結果 → 確定後 | `PendingState` → `CommitBatch` → `WorldState` | 6 |
| 7 テキスト化(M6) | 確定後 | `WorldState.results` | 7 |

- `EventContext` は `WorldState` から directed / pair / cooldowns をコピーして作る。スロット内でステップ2〜3が読む値は、ステップ4の仮更新に影響されない。
- 受諾/拒否は `build_outcome`(ステップ2)で `ctx` の有効値のみから決まる。ステップ4以降で結果分岐を再評価しない(§16)。
- `PendingState` は永続化しない。`WorldState` に触るのは `commit` のみ。

### 6.3 データフロー

```
WorldState ──capture──▶ EventContext                                   [1]
EventContext ──collect_candidates──▶ (Candidate…) ──draw(rng)──▶ Candidate | None
   Candidate = (event_def_id, participants(正規化タプル), binding{role→CasterId}, weight)
   None → SlotReport(event=None)。WorldState は変更なし、commit なし
Candidate + EventContext ──build_outcome──▶ OutcomeDraft                [2]
   OutcomeDraft = (event_def, outcome, binding, observed_by,
                   delta_candidates(DeltaCandidate: source,target,axis,value,result_index),
                   result_specs(定義))
OutcomeDraft.delta_candidates ──apply_modifiers──▶ (ModifiedDelta…)      [3]
   ModifiedDelta = (candidate, after_modifier, blocked)
EventContext ──PendingState.from_context──▶ PendingState                [4]
PendingState.apply_deltas(ModifiedDelta…) ──▶ (PendingDelta…)
   PendingDelta = AppliedDelta の result_id 未定版(stored/effective の前後を含む)
PendingState.apply_results(OutcomeDraft) ──▶ (PendingResult…)           [4-5]
   PendingResult = (spec, track_change | None, acquaintance_established)
   pair 変更は PendingState.pairs に反映(変更されたペアのみ PairChange 化)
assemble_batch ──▶ CommitBatch                                          [6]
   result_id = ctx.next_result_id から連番、commit_seq = ctx.next_commit_seq
   EventResult(…, observed_by, related_result_id=同分岐の先頭結果, track_change)
   AppliedDelta = PendingDelta + result_id
   generate_facts(EventResult, FactSpec…) → Fact(audience 確定) + Knowledge
   CooldownUpdate: 採用分岐 ∈ cooldown.after_outcomes のとき
     key=(event_id, scope 参加者タプル), until = ctx.time + days×ticks_per_day
   event_instance_id = f"t{tick}-c{commit_seq}-{event_id}"(安定)
WorldState.commit(CommitBatch)                                           [6]
   検証失敗 → CommitError、WorldState 不変。成功 → 値・pair・results・facts・knowledge・
   cooldowns・applied_event_ids・採番を同時に確定
```

- 候補の正規化: directed は `(actor, target)`(役割順)、pair は参加者IDのソート済みタプル。重複排除キーは `(event_def_id, participants)`。抽選前に同キーでソート。
- 告白受諾時の結果は `confession`(success=True)と `relationship_established` の2件。返答 fact(`confession_accepted` / `confession_rejected`)は `confession` 結果から生成され、`source_result_id` がその告白結果(= `confession_made` の元)を指す。`relationship_established` 結果は `related_result_id` で告白結果を参照する。
- クールダウンの経過判定: `CooldownElapsed` は `ctx.cooldowns[(event_id, scope参加者)]` が未設定、または `until <= ctx.time` なら真。逆方向は別キーなので阻まれない。

### 6.4 乱数を消費する箇所

| 箇所 | 消費 |
|---|---|
| `engine/candidates.py: draw(candidates, rng)` | 候補が1件以上あるとき `rng.random()` を1回だけ呼び、重みの累積で1件を選ぶ。候補0件なら呼ばない |

上記以外のコード(`context` / `evaluate` / `outcome` / `modifiers` / `apply` / `facts` / `pipeline` / `model` / `affinity` / `text`)は `Random` を引数に取らず、乱数を消費しない。`Pipeline` が保持する `rng` は `draw` にだけ渡す。テストで (a) 候補0件のスロット前後で `rng.getstate()` が不変、(b) 候補ありのスロットで `random()` 1回分だけ進む、(c) 同一seed・同一定義の2回実行でイベント列・確定内容が完全一致、を検証する。

### 6.5 M5 のテスト計画

- 決定性: 同一seed・同一定義で複数スロットを2回実行し、results / facts / knowledge / delta_history / cooldowns / directed / pairs が完全一致。
- 候補: 重複排除(pair 共有イベントが A-B で1候補)、抽選前ソートの安定性(キャラ登録順やイベント定義順を入れ替えても候補列が同じ)、候補0件で無イベント。
- 告白: 返答が適用前値で判定される(target→actor が閾値未満なら成功時 delta を足せば届く値でも拒否)。同じ入力なら同じ返答。
- クールダウン: 拒否→期限内は候補から除外→期限経過で復帰。逆方向の告白は阻まれない。
- 一括確定: fact・知識・クールダウン・履歴・pair 変更が同一 commit_seq で確定。検証失敗(テストで意図的に破壊)時に何も残らない。
- 開示範囲: 告白の `confession_made`(public)は同席者(observed_by)も知り、`confession_accepted/rejected`(participants_only)は当事者のみが知る。ときめき・通常交流は fact を生まない。
