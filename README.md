# privategameproject

CUI・チャットベースの放置型「関係性観察ゲーム」。設計の正本は `docs/design_v6.md`、第①段階の実装計画は `docs/implementation_plan_stage1.md`。

## 開発環境

Python 3.12 以上。

```sh
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
```

## 検証コマンド

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest
```

## 定義データ

`content/` にゲーム固有の定義(軸・トラック・性格タグ・相性ルール・イベント・テンプレート・進行設定・検証用キャラ)を YAML で置く。読み込みは `kankei.definitions.load_content_pack(Path("content"))`。不正な定義は補正せず `DefinitionError` で止まる。
