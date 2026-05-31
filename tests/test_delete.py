"""
アイテムの削除に関するテスト
"""

import pytest

from storageservicelite import item


class TestDelete:
    """delete 操作のテスト。"""

    def test_delete_removes_item(self, storage):
        """delete 後はアイテムにアクセスできなくなる。"""
        ulid = storage.set(item(name="to_delete", data=b"bye"))
        storage.delete(ulid)
        with pytest.raises(KeyError):
            storage.get(ulid)

    def test_delete_removes_from_list(self, storage):
        """delete 後はアイテムが list() に含まれない。"""
        ulid = storage.set(item(name="to_delete", data=b"bye"))
        assert ulid in storage.list()
        storage.delete(ulid)
        assert ulid not in storage.list()

    def test_delete_nonexistent_raises(self, storage):
        """存在しないULIDを delete すると KeyError。"""
        with pytest.raises(KeyError):
            storage.delete("01HZZZZZZZZZZZZZZZZZZZZZZZ")

    def test_delete_all_versions(self, storage):
        """複数バージョンがあっても delete で全バージョン削除される。"""
        ulid = storage.set(item(name="v1", data=b"v1"))
        storage.set(item(ulid=ulid, name="v2", data=b"v2"))
        storage.set(item(ulid=ulid, name="v3", data=b"v3"))
        storage.delete(ulid)
        with pytest.raises(KeyError):
            storage.get(ulid)
