"""
StorageServiceLite パッケージ

単一ファイルで管理するオブジェクトストレージサービスを提供します。
"""

from .storage import read_storage
from .models import item

__all__ = ["read_storage", "item"]
