"""
ストレージサービスのメインAPIモジュール

`read_storage` 関数でストレージを開き、返された `Storage` オブジェクト経由で操作します。
"""

from __future__ import annotations

import os
from typing import Optional

from ulid import ULID

from .fileformat import StorageFile, create_storage_file
from .models import item


class Storage:
    """
    オブジェクトストレージサービスのメインクラス。

    ファイルの読み書きは内部の StorageFile に委譲する。
    """

    def __init__(self, storage_file: StorageFile) -> None:
        self._sf = storage_file

    # ------------------------------------------------------------------ #
    # アイテムの書き込み
    # ------------------------------------------------------------------ #

    def set(self, it: item) -> str:
        """
        アイテムをストレージに保存する。

        新規アイテムの場合はULIDを生成して返す。
        既存アイテム (it.ulid が指定済み) の場合は新しいバージョンとして追記する。

        Args:
            it: 保存するアイテム。

        Returns:
            アイテムのULID文字列。
        """
        data = it.resolve_data()

        if it.ulid is None:
            # 新規: ULIDを生成
            ulid_str = str(ULID())
        else:
            ulid_str = it.ulid

        # nameが空の場合はULIDをアイテム名として使用
        name = it.name if it.name else ulid_str

        self._sf.write_item(ulid_str, name, data)
        return ulid_str

    # ------------------------------------------------------------------ #
    # アイテムの読み取り
    # ------------------------------------------------------------------ #

    def get(
        self,
        ulid: str,
        path: Optional[str] = None,
        history: int = 1,
    ) -> item:
        """
        アイテムをストレージから取得する。

        Args:
            ulid: 取得するアイテムのULID文字列。
            path: 指定した場合、データをそのパスにファイルとして保存する。
            history: 取得するヒストリー番号 (1=最新)。

        Returns:
            取得した item オブジェクト。path を指定した場合は data=None で file_path が設定される。
        """
        name, data = self._sf.read_item(ulid, history=history)

        if path is not None:
            # ディレクトリが存在しない場合は作成
            dir_name = os.path.dirname(path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(path, "wb") as fp:
                fp.write(data)
            return item(ulid=ulid, name=name, file_path=path)

        return item(ulid=ulid, name=name, data=data)

    # ------------------------------------------------------------------ #
    # ヒストリー操作
    # ------------------------------------------------------------------ #

    def get_history(self, ulid: str) -> list[int]:
        """
        アイテムの有効なヒストリー番号一覧を返す。

        Returns:
            [1, 2, 3, ...] のようなリスト。1が最新。
        """
        count = self._sf.get_history_count(ulid)
        return list(range(1, count + 1))

    def change_head(self, ulid: str, history: Optional[int] = None) -> None:
        """
        ヘッドを変更することでアイテムを「削除」またはロールバックする。

        Args:
            ulid: 操作するアイテムのULID。
            history: None の場合は最新バージョンを1つ削除する。
                     指定した場合はそのヒストリーの状態にロールバックする
                     (それより新しいバージョンへのアクセスが無効化される)。
        """
        self._sf.change_head(ulid, history=history)

    # ------------------------------------------------------------------ #
    # アイテムの完全削除
    # ------------------------------------------------------------------ #

    def delete(self, ulid: str) -> None:
        """
        アイテムをすべてのバージョンごと完全に削除する。

        Args:
            ulid: 削除するアイテムのULID。
        """
        self._sf.delete_item(ulid)

    # ------------------------------------------------------------------ #
    # メタデータ取得
    # ------------------------------------------------------------------ #

    def get_metadata(self, ulid: str) -> dict:
        """
        アイテムのメタデータを取得する。

        Returns:
            name, size, created_at, history_count, ulid を含む辞書。
        """
        return self._sf.get_metadata(ulid)

    # ------------------------------------------------------------------ #
    # 一覧取得
    # ------------------------------------------------------------------ #

    def list(self) -> list[str]:
        """有効な全アイテムのULID一覧を返す。"""
        return self._sf.list_ulids()


def read_storage(
    path: str,
    create_if_not_exists: bool = False,
    read_only: bool = False,
) -> Storage:
    """
    ストレージファイルを開いて Storage オブジェクトを返す。

    Args:
        path: ストレージファイルのパス。
        create_if_not_exists: ファイルが存在しない場合に新規作成するかどうか。
        read_only: 読み取り専用で開くかどうか。

    Returns:
        Storage オブジェクト。

    Raises:
        FileNotFoundError: ファイルが存在せず create_if_not_exists=False の場合。
    """
    if not os.path.exists(path):
        if create_if_not_exists:
            create_storage_file(path)
        else:
            raise FileNotFoundError(f"ストレージファイルが見つかりません: {path}")

    sf = StorageFile(path, read_only=read_only)
    return Storage(sf)
