"""M1: パッケージが読み込めることの確認。"""

import kankei


def test_package_importable() -> None:
    assert kankei.__version__ == "0.1.0"
