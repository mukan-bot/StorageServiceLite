"""
メタデータ取得と一覧に関するテスト
"""

import pytest
import time

from storageservicelite import item


class TestMetadata:
    """get_metadata / list のテスト。"""

    def test_get_metadata_fields(self, storage):
        """get_metadata が必要なフィールドを返す。"""
        ulid = storage.set(item(name="meta_test", data=b"12345"))
        meta = storage.get_metadata(ulid)
        assert meta["name"] == "meta_test"
        assert meta["size"] == 5
        assert meta["ulid"] == ulid
        assert meta["history_count"] == 1
        assert isinstance(meta["created_at"], float)

    def test_metadata_size_reflects_latest(self, storage):
        """更新後のメタデータは最新バージョンのサイズを反映する。"""
        ulid = storage.set(item(name="v1", data=b"short"))
        storage.set(item(ulid=ulid, name="v2", data=b"much longer data here"))
        meta = storage.get_metadata(ulid)
        assert meta["size"] == len(b"much longer data here")
        assert meta["history_count"] == 2

    def test_metadata_nonexistent_raises(self, storage):
        """存在しないULIDのメタデータ取得は KeyError。"""
        with pytest.raises(KeyError):
            storage.get_metadata("01HZZZZZZZZZZZZZZZZZZZZZZZ")

    def test_list_returns_all_ulids(self, storage):
        """list は保存された全ULIDを返す。"""
        ulid1 = storage.set(item(name="a", data=b"a"))
        ulid2 = storage.set(item(name="b", data=b"b"))
        ulids = storage.list()
        assert ulid1 in ulids
        assert ulid2 in ulids

    def test_list_excludes_deleted(self, storage):
        """削除済みアイテムは list に含まれない。"""
        ulid1 = storage.set(item(name="keep", data=b"keep"))
        ulid2 = storage.set(item(name="delete", data=b"del"))
        storage.delete(ulid2)
        assert ulid1 in storage.list()
        assert ulid2 not in storage.list()

    def test_list_empty_storage(self, storage):
        """空のストレージの list は空リストを返す。"""
        assert storage.list() == []
