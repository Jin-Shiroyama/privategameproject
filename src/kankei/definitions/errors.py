"""定義データの読み込み・検証エラー。"""


class DefinitionError(Exception):
    """定義データが不正なときに送出する。不正な定義は補正せず、必ずこの例外で止める。"""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message
