"""
データモデル定義

ストレージに格納するアイテムのデータモデルを定義します。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class item:
    """
    ストレージに格納するアイテムを表すデータクラス。

    Attributes:
        ulid: アイテムを一意に識別するULID文字列。新規作成時はNone。
        name: アイテムの名前。空の場合はulidがアイテム名として扱われる。
        data: バイナリデータ。file_pathと排他。
        file_path: ローカルファイルパス。setメソッド呼び出し時にデータを読み込む。
    """

    ulid: Optional[str] = None
    name: str = ""
    data: Optional[bytes] = None
    file_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.data is not None and self.file_path is not None:
            raise ValueError("data と file_path を同時に指定することはできません。")

    def resolve_data(self) -> bytes:
        """
        バイナリデータを解決して返す。

        file_path が指定されている場合はファイルを読み込む。
        どちらも指定されていない場合は空のバイト列を返す。
        """
        if self.data is not None:
            return self.data
        if self.file_path is not None:
            if not os.path.isfile(self.file_path):
                raise FileNotFoundError(f"ファイルが見つかりません: {self.file_path}")
            with open(self.file_path, "rb") as f:
                return f.read()
        return b""
