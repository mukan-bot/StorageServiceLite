"""
ストレージファイルの作成・開放に関するテスト
"""

import os
import pytest

from storageservicelite import read_storage, item


class TestReadStorage:
    """read_storage 関数のテスト。"""

    def test_create_new_file(self, tmp_path):
        """create_if_not_exists=True で新規ファイルが作成される。"""
        path = str(tmp_path / "new.ssobj")
        assert not os.path.exists(path)
        storage = read_storage(path, create_if_not_exists=True)
        assert os.path.exists(path)
        assert storage is not None

    def test_open_existing_file(self, tmp_path):
        """既存ファイルを正常に開ける。"""
        path = str(tmp_path / "existing.ssobj")
        read_storage(path, create_if_not_exists=True)
        storage = read_storage(path)
        assert storage is not None

    def test_file_not_found_raises(self, tmp_path):
        """ファイルが存在せず create_if_not_exists=False の場合は FileNotFoundError。"""
        path = str(tmp_path / "nonexistent.ssobj")
        with pytest.raises(FileNotFoundError):
            read_storage(path, create_if_not_exists=False)

    def test_read_only_mode(self, tmp_path):
        """read_only=True で開いた場合、書き込み操作は PermissionError になる。"""
        path = str(tmp_path / "ro.ssobj")
        # まず書き込み可能モードで作成
        s = read_storage(path, create_if_not_exists=True)
        s.set(item(name="init", data=b"data"))

        # 読み取り専用で再度開く
        ro_storage = read_storage(path, read_only=True)
        with pytest.raises(PermissionError):
            ro_storage.set(item(name="fail", data=b"x"))
