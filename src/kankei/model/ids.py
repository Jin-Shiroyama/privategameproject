"""識別子の型。

- `ResultId` / `FactId` は保存される整数連番。テキスト化の文面選択にも使う(`result_id % n`)。
- `EventInstanceId` はイベント実体の安定した識別子。再処理時の二重適用防止に使う(§7)。
"""

from typing import NewType

CasterId = NewType("CasterId", str)
ResultId = NewType("ResultId", int)
FactId = NewType("FactId", int)
EventInstanceId = NewType("EventInstanceId", str)
