"""
item データクラスのバリデーションに関するテスト
"""

import os
import pytest

from storageservicelite import item


class TestItemModel:
    """item データクラスのテスト。"""

    def test_data_and_file_path_exclusive(self):
        """data と file_path を同時に指定すると ValueError。"""
        with pytest.raises(ValueError):
            item(data=b"x", file_path="/some/path")

    def test_resolve_data_from_bytes(self):
        """data フィールドからバイト列を解決できる。"""
        it = item(data=b"hello")
        assert it.resolve_data() == b"hello"

    def test_resolve_data_from_file(self, tmp_path):
        """file_path からファイルデータを解決できる。"""
        f = tmp_path / "test.txt"
        f.write_bytes(b"from file")
        it = item(file_path=str(f))
        assert it.resolve_data() == b"from file"

    def test_resolve_data_file_not_found(self):
        """存在しない file_path は FileNotFoundError。"""
        it = item(file_path="/nonexistent/path/file.bin")
        with pytest.raises(FileNotFoundError):
            it.resolve_data()

    def test_resolve_data_none_returns_empty(self):
        """data も file_path も指定しない場合は空バイト列。"""
        it = item()
        assert it.resolve_data() == b""
