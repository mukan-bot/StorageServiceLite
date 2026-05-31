"""
アイテムの更新とヒストリー管理に関するテスト
"""

import pytest

from storageservicelite import read_storage, item


class TestUpdateAndHistory:
    """アイテム更新・ヒストリー操作のテスト。"""

    def test_update_item(self, storage):
        """既存アイテムを更新できる。"""
        ulid = storage.set(item(name="v1", data=b"version1"))
        storage.set(item(ulid=ulid, name="v2", data=b"version2"))
        retrieved = storage.get(ulid)
        assert retrieved.data == b"version2"
        assert retrieved.name == "v2"

    def test_history_count_increases_on_update(self, storage):
        """更新するたびにヒストリー数が増える。"""
        ulid = storage.set(item(name="v1", data=b"v1"))
        assert storage.get_history(ulid) == [1]

        storage.set(item(ulid=ulid, name="v2", data=b"v2"))
        assert storage.get_history(ulid) == [1, 2]

        storage.set(item(ulid=ulid, name="v3", data=b"v3"))
        assert storage.get_history(ulid) == [1, 2, 3]

    def test_get_old_version(self, storage):
        """ヒストリー番号を指定して古いバージョンを取得できる。"""
        ulid = storage.set(item(name="v1", data=b"version1"))
        storage.set(item(ulid=ulid, name="v2", data=b"version2"))
        storage.set(item(ulid=ulid, name="v3", data=b"version3"))

        assert storage.get(ulid, history=1).data == b"version3"  # 最新
        assert storage.get(ulid, history=2).data == b"version2"  # 1つ前
        assert storage.get(ulid, history=3).data == b"version1"  # 最古

    def test_get_history_out_of_range(self, storage):
        """範囲外のヒストリーを取得しようとすると IndexError。"""
        ulid = storage.set(item(name="v1", data=b"v1"))
        with pytest.raises(IndexError):
            storage.get(ulid, history=2)

    def test_change_head_removes_latest(self, storage):
        """change_head(ulid) で最新バージョンが1つ削除される。"""
        ulid = storage.set(item(name="v1", data=b"v1"))
        storage.set(item(ulid=ulid, name="v2", data=b"v2"))
        storage.set(item(ulid=ulid, name="v3", data=b"v3"))

        storage.change_head(ulid)  # v3を削除
        assert storage.get(ulid).data == b"v2"
        assert storage.get_history(ulid) == [1, 2]

    def test_change_head_to_specific_history(self, storage):
        """change_head(ulid, history=N) で旧 history=N が新しい history=1 になる。"""
        ulid = storage.set(item(name="v1", data=b"v1"))
        storage.set(item(ulid=ulid, name="v2", data=b"v2"))
        storage.set(item(ulid=ulid, name="v3", data=b"v3"))
        storage.set(item(ulid=ulid, name="v4", data=b"v4"))
        # 4バージョン: history=1=v4, 2=v3, 3=v2, 4=v1

        # history=2 を指定 → v4(history=1) を削除し、v3 を新しい history=1 にする
        storage.change_head(ulid, history=2)
        assert storage.get_history(ulid) == [1, 2, 3]
        assert storage.get(ulid, history=1).data == b"v3"  # 旧 history=2 が最新
        assert storage.get(ulid, history=2).data == b"v2"  # 旧 history=3
        assert storage.get(ulid, history=3).data == b"v1"  # 旧 history=4 (最古)

    def test_change_head_history1_is_noop(self, storage):
        """change_head(ulid, history=1) は現在の最新がそのままなので変化なし。"""
        ulid = storage.set(item(name="v1", data=b"v1"))
        storage.set(item(ulid=ulid, name="v2", data=b"v2"))

        storage.change_head(ulid, history=1)  # 削除0個 = 変化なし
        assert storage.get_history(ulid) == [1, 2]
        assert storage.get(ulid, history=1).data == b"v2"

    def test_change_head_to_zero_makes_item_deleted(self, storage):
        """change_head で head が 0 になるとアイテムが削除済みとなる。"""
        ulid = storage.set(item(name="v1", data=b"v1"))
        storage.change_head(ulid)  # head を 0 に

        with pytest.raises(KeyError):
            storage.get(ulid)

    def test_history_persists_after_reopen(self, tmp_path):
        """ファイルを再度開いてもヒストリーが保持される。"""
        path = str(tmp_path / "hist.ssobj")
        s1 = read_storage(path, create_if_not_exists=True)
        ulid = s1.set(item(name="v1", data=b"v1"))
        s1.set(item(ulid=ulid, name="v2", data=b"v2"))

        s2 = read_storage(path)
        assert s2.get_history(ulid) == [1, 2]
        assert s2.get(ulid, history=2).data == b"v1"
