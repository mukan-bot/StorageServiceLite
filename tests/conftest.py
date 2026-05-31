"""
テスト用の共通フィクスチャ
"""

import pytest
import tempfile
import os

from storageservicelite import read_storage, item


@pytest.fixture
def tmp_storage_path(tmp_path):
    """一時ディレクトリ内のストレージファイルパスを返すフィクスチャ。"""
    return str(tmp_path / "test.ssobj")


@pytest.fixture
def storage(tmp_storage_path):
    """新規作成されたストレージオブジェクトを返すフィクスチャ。"""
    return read_storage(tmp_storage_path, create_if_not_exists=True)
