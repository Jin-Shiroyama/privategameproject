"""共通フィクスチャ。"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from kankei.definitions import ContentPack, load_content_pack

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content"


@pytest.fixture(scope="session")
def content_dir() -> Path:
    """リポジトリ同梱の検証用定義データ。"""
    return CONTENT_DIR


@pytest.fixture(scope="session")
def pack(content_dir: Path) -> ContentPack:
    """検証用定義データを読み込んだ ContentPack。"""
    return load_content_pack(content_dir)


type Mutator = Callable[[dict[str, Any]], None]


@pytest.fixture
def mutated_pack(tmp_path: Path, content_dir: Path) -> Callable[[str, Mutator], Path]:
    """定義データを一時ディレクトリへ複製し、指定ファイルを書き換えたディレクトリを返す。"""

    def _make(file_name: str, mutate: Mutator) -> Path:
        target = tmp_path / "content"
        shutil.copytree(content_dir, target)
        path = target / file_name
        with path.open(encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f)
        mutate(data)
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        return target

    return _make
