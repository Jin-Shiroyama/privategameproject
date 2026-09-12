"""パイプラインの例外。"""


class PipelineError(Exception):
    """定義または実装の不備。commit 開始前(PendingState 段階まで)に発生し、世界状態は無傷。

    適用中の部分更新を表す `FatalCommitError` とは別種。
    """
