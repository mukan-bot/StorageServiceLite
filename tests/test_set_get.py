"""
アイテムの set / get 基本操作に関するテスト
"""

import os
import pytest

from storageservicelite import read_storage, item


class TestSetGet:
    """set / get の基本動作テスト。"""

    def test_set_returns_ulid(self, storage):
        """set はULID文字列を返す。"""
        ulid = storage.set(item(name="a", data=b"hello"))
        assert isinstance(ulid, str)
        assert len(ulid) == 26  # ULIDは26文字

    def test_get_binary_data(self, storage):
        """バイナリデータをセット後に取得できる。"""
        original = b"Hello, World!"
        ulid = storage.set(item(name="test", data=original))
        retrieved = storage.get(ulid)
        assert retrieved.data == original
        assert retrieved.name == "test"

    def test_empty_name_uses_ulid(self, storage):
        """nameが空の場合、ULIDがアイテム名として使われる。"""
        ulid = storage.set(item(data=b"data"))
        retrieved = storage.get(ulid)
        assert retrieved.name == ulid

    def test_set_from_file(self, storage, tmp_path):
        """ファイルパスからデータをセットできる。"""
        src_file = tmp_path / "input.bin"
        src_file.write_bytes(b"file content")
        ulid = storage.set(item(name="from_file", file_path=str(src_file)))
        retrieved = storage.get(ulid)
        assert retrieved.data == b"file content"

    def test_get_save_to_file(self, storage, tmp_path):
        """get に path を指定するとファイルとして保存される。"""
        ulid = storage.set(item(name="save_test", data=b"save me"))
        out_path = str(tmp_path / "output.bin")
        retrieved = storage.get(ulid, path=out_path)
        assert os.path.exists(out_path)
        assert open(out_path, "rb").read() == b"save me"
        assert retrieved.file_path == out_path
        assert retrieved.data is None

    def test_get_save_creates_parent_dir(self, storage, tmp_path):
        """get の path に存在しないディレクトリを指定した場合、自動的に作成される。"""
        ulid = storage.set(item(name="nested", data=b"nested"))
        out_path = str(tmp_path / "sub" / "dir" / "out.bin")
        storage.get(ulid, path=out_path)
        assert os.path.exists(out_path)

    def test_large_data(self, storage):
        """大きなデータ (1MB) を正しく保存・取得できる。"""
        large_data = os.urandom(1024 * 1024)
        ulid = storage.set(item(name="large", data=large_data))
        retrieved = storage.get(ulid)
        assert retrieved.data == large_data

    def test_empty_data(self, storage):
        """空バイトのデータを保存・取得できる。"""
        ulid = storage.set(item(name="empty", data=b""))
        retrieved = storage.get(ulid)
        assert retrieved.data == b""

    def test_multiple_items(self, storage):
        """複数のアイテムを独立して保存・取得できる。"""
        ulid1 = storage.set(item(name="a", data=b"aaa"))
        ulid2 = storage.set(item(name="b", data=b"bbb"))
        assert ulid1 != ulid2
        assert storage.get(ulid1).data == b"aaa"
        assert storage.get(ulid2).data == b"bbb"

    def test_get_nonexistent_raises(self, storage):
        """存在しないULIDを get すると KeyError。"""
        with pytest.raises(KeyError):
            storage.get("01HZZZZZZZZZZZZZZZZZZZZZZZ")

    def test_persistence_across_reopen(self, tmp_path):
        """ファイルを閉じて再度開いてもデータが保持される。"""
        path = str(tmp_path / "persist.ssobj")
        s1 = read_storage(path, create_if_not_exists=True)
        ulid = s1.set(item(name="persist", data=b"persistent data"))

        s2 = read_storage(path)
        retrieved = s2.get(ulid)
        assert retrieved.data == b"persistent data"
