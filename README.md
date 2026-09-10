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
