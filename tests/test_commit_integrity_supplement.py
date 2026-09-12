"""M3修正後の補足検証。部分更新後の停止と初期ペアキーの不一致を確認する。"""

from copy import deepcopy

import pytest

from kankei.definitions import ContentPack
from kankei.model import CommitError, FatalCommitError, PairState, PairStore, pair_key
from tests.test_commit_integrity import A, B, C, _batch, _snapshot, _world


def test_partial_apply_failure_preserves_cause_and_blocks_further_commit(
    pack: ContentPack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delta・履歴更新後の例外を隔離し、後続バッチによる更新を拒否する。"""
    world = _world(pack)
    batch = _batch(world)
    world.validate(batch)
    before = _snapshot(world)
    previous_history = list(world.delta_history)
    injected = RuntimeError("補足検証: deltaと履歴更新後のペア適用失敗")
    reached: list[PairState] = []
    original_put = world.pairs.put

    def fail_pair_apply(state: PairState) -> None:
        # 例外を出す瞬間に、先行する更新が実際に完了していることを確認する。
        assert world.directed.stored(A, B, "favor") == batch.deltas[0].stored_after
        assert world.delta_history == [*previous_history, *batch.deltas]
        reached.append(state.copy())
        raise injected

    with monkeypatch.context() as patch:
        patch.setattr(world.pairs, "put", fail_pair_apply)
        with pytest.raises(FatalCommitError) as failure:
            world.commit(batch)

    assert reached == [batch.pair_changes[0].state]
    assert world.pairs.put == original_put
    assert failure.value.__cause__ is injected
    assert world.integrity_failure is not None
    assert str(injected) in world.integrity_failure
    assert str(batch.event_instance_id) in world.integrity_failure
    after_failure = _snapshot(world)
    assert {key for key in before if before[key] != after_failure[key]} == {
        "directed",
        "delta_history",
        "integrity_failure",
    }
    assert world.directed.stored(A, B, "favor") == 8
    assert world.delta_history == [*previous_history, *batch.deltas]

    # 現在値に合わせた正常バッチを作り、別世界で正常確定できることも確認する。
    # 検証対象の世界のフラグは解除しない。
    followup = _batch(world)
    control = deepcopy(world)
    control.integrity_failure = None
    control.commit(followup)
    assert control.directed.stored(A, B, "favor") == 12

    with pytest.raises(CommitError, match="修復不能") as rejected:
        world.commit(followup)
    assert _snapshot(world) == after_failure
    print(f"初回例外={failure.value!r}; 元例外={failure.value.__cause__!r}")
    print(f"後続拒否={rejected.value!r}; 拒否後の全状態差分なし")


def test_constructor_rejects_distinct_normalized_keys_without_mutating_input(
    pack: ContentPack,
) -> None:
    """辞書キーもstate.keyも単独では正常だが、互いに異なる入力を拒否する。"""
    store = PairStore(pack.tracks)
    dictionary_key = pair_key(A, B)
    state = store.get(A, C)
    assert dictionary_key == (A, B)
    assert state.key == (A, C)
    assert dictionary_key != state.key
    store.validate(state)
    states = {dictionary_key: state}
    before_dictionary = deepcopy(states)
    before_state = deepcopy(state)
    tracks = state.track_states

    with pytest.raises(ValueError, match="一致しません") as rejected:
        PairStore(pack.tracks, states)

    assert states == before_dictionary
    assert state == before_state
    assert states[dictionary_key] is state
    assert state.track_states is tracks
    print(f"キー不一致の拒否={rejected.value!r}; 入力辞書・PairStateの変更なし")
