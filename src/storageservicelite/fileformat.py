"""
ストレージファイルのフォーマット定義と低レベルI/O操作

ファイル構造:
  [マジックバイト 8bytes][バージョン 2bytes][インデックスオフセット 8bytes]
  [データチャンク群 ...]
  [インデックスブロック (JSON, 可変長)]

インデックスブロックは末尾に配置し、ヘッダーのオフセットで位置を示す。
各データチャンクは以下の構造を持つ:
  [データサイズ 8bytes(uint64 little-endian)][データ本体]
"""

from __future__ import annotations

import json
import os
import struct
import time
from typing import Any

# ファイルの先頭に付与するマジックバイト (8 bytes)
MAGIC = b"SSOBJ\x00\x01\x00"
# フォーマットバージョン (2 bytes, little-endian)
FORMAT_VERSION = 1
# ヘッダーの合計サイズ: マジック(8) + バージョン(2) + インデックスオフセット(8)
HEADER_SIZE = 18
# 書き込み中フラグファイルのサフィックス
LOCK_SUFFIX = ".lock"


def _pack_header(index_offset: int) -> bytes:
    """ファイルヘッダーをパックして返す。"""
    return MAGIC + struct.pack("<H", FORMAT_VERSION) + struct.pack("<Q", index_offset)


def _unpack_header(data: bytes) -> tuple[int, int]:
    """
    ヘッダーバイト列をアンパックする。

    Returns:
        (format_version, index_offset)
    """
    if data[:8] != MAGIC:
        raise ValueError("無効なストレージファイルです (マジックバイト不一致)。")
    version = struct.unpack("<H", data[8:10])[0]
    index_offset = struct.unpack("<Q", data[10:18])[0]
    return version, index_offset


def _pack_chunk(data: bytes) -> bytes:
    """データチャンクをパックして返す (サイズプレフィックス付き)。"""
    return struct.pack("<Q", len(data)) + data


def _read_chunk(fp, offset: int) -> bytes:
    """
    指定オフセットからデータチャンクを読み取って返す。
    """
    fp.seek(offset)
    size_bytes = fp.read(8)
    if len(size_bytes) < 8:
        raise IOError("チャンクサイズの読み取りに失敗しました。")
    size = struct.unpack("<Q", size_bytes)[0]
    data = fp.read(size)
    if len(data) < size:
        raise IOError("チャンクデータの読み取りに失敗しました。")
    return data


class StorageFile:
    """
    ストレージファイルの低レベルI/Oを担当するクラス。

    インデックスはメモリ上に辞書として保持し、変更時にファイルへ書き戻す。
    インデックスの構造:
      {
        "<ulid>": {
          "name": str,
          "chunks": [           # インデックス0が最古、末尾が最新 (history=1)
            {"offset": int, "size": int, "created_at": float}
          ]
        }
      }
    chunks が空の場合はそのアイテムは「削除済み」として扱われる。
    """

    def __init__(self, path: str, read_only: bool = False) -> None:
        self.path = path
        self.read_only = read_only
        # インデックス: ulid -> メタデータ辞書
        self._index: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # ファイルのロード・初期化
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        """ストレージファイルを読み込み、インデックスをメモリに展開する。"""
        lock_path = self.path + LOCK_SUFFIX
        deadline = time.monotonic() + 5.0

        while True:
            if os.path.exists(lock_path):
                if time.monotonic() > deadline:
                    raise TimeoutError("ストレージファイルの読み込み待機がタイムアウトしました。")
                time.sleep(0.01)
                continue

            try:
                self._index = self._read_index_from_disk()
                return
            except (json.JSONDecodeError, IOError, ValueError):
                # 書き込みロックの遷移直後に中間状態を読んだ場合は短時間リトライする。
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.01)

    def _load_locked(self) -> None:
        """
        ロック取得済み状態でストレージファイルを読み込み、インデックスを更新する。

        呼び出し側が同一ファイルの書き込みロックを保持している前提。
        """
        self._index = self._read_index_from_disk()

    def _read_index_from_disk(self) -> dict[str, Any]:
        """ディスク上のインデックスを読み込んで返す。"""
        with open(self.path, "rb") as fp:
            header = fp.read(HEADER_SIZE)
            if len(header) < HEADER_SIZE:
                raise IOError("ヘッダーの読み取りに失敗しました。")
            _version, index_offset = _unpack_header(header)
            fp.seek(index_offset)
            index_bytes = fp.read()
        return json.loads(index_bytes.decode("utf-8")) if index_bytes else {}

    # ------------------------------------------------------------------ #
    # ファイルへの書き込み
    # ------------------------------------------------------------------ #

    def _acquire_lock(self) -> None:
        """
        書き込みロックファイルを作成する。

        既にロックが存在する場合は最大5秒待機して再試行する。
        """
        lock_path = self.path + LOCK_SUFFIX
        deadline = time.monotonic() + 5.0
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise TimeoutError("ストレージファイルのロック取得がタイムアウトしました。")
                time.sleep(0.05)

    def _release_lock(self) -> None:
        """書き込みロックファイルを削除する。"""
        lock_path = self.path + LOCK_SUFFIX
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass  # 既に削除されていても問題なし

    def _flush(self) -> None:
        """
        メモリ上のインデックスをファイルに書き戻す。

        データチャンクはそのまま保持し、インデックスブロックのみ末尾に再書き込みする。
        ロックは呼び出し元が取得済みであることを前提とする(_flush_locked を参照)。
        """
        if self.read_only:
            raise PermissionError("読み取り専用モードでは書き込みできません。")

        self._acquire_lock()
        try:
            self._flush_locked()
        finally:
            self._release_lock()

    def _flush_locked(self) -> None:
        """
        ロック取得済み状態でインデックスをファイルに書き戻す内部メソッド。
        """
        index_bytes = json.dumps(self._index, ensure_ascii=False).encode("utf-8")
        data_end = self._calc_data_end()
        with open(self.path, "r+b") as fp:
            fp.seek(data_end)
            fp.truncate()
            index_offset = fp.tell()
            fp.write(index_bytes)
        # ヘッダーのインデックスオフセットを更新
        with open(self.path, "r+b") as fp:
            fp.seek(0)
            fp.write(_pack_header(index_offset))

    def _calc_data_end(self) -> int:
        """
        全チャンクのオフセット+サイズから、データ領域の末尾位置を計算する。
        チャンクがなければ HEADER_SIZE を返す。
        """
        end = HEADER_SIZE
        for entry in self._index.values():
            for chunk in entry.get("chunks", []):
                chunk_end = chunk["offset"] + 8 + chunk["size"]
                if chunk_end > end:
                    end = chunk_end
        return end

    # ------------------------------------------------------------------ #
    # 公開 API
    # ------------------------------------------------------------------ #

    def write_item(self, ulid: str, name: str, data: bytes) -> None:
        """
        アイテムのデータをファイルに追記し、インデックスを更新する。

        既にulidが存在する場合は新しいバージョン(ヒストリー)として追加する。
        """
        if self.read_only:
            raise PermissionError("読み取り専用モードでは書き込みできません。")

        self._acquire_lock()
        try:
            # 別インスタンスが先に書き込んだ最新状態を取り込んでから更新する。
            # これにより、古いメモリ上インデックスに基づく追記位置計算を防ぐ。
            self._load_locked()

            # データチャンクをファイル末尾のデータ領域に追記
            data_end = self._calc_data_end()
            chunk = _pack_chunk(data)
            with open(self.path, "r+b") as fp:
                fp.seek(data_end)
                fp.write(chunk)

            offset = data_end
            created_at = time.time()

            if ulid in self._index:
                # 既存アイテムへの更新: 末尾に新チャンクを追加
                self._index[ulid]["name"] = name
                self._index[ulid]["chunks"].append({
                    "offset": offset,
                    "size": len(data),
                    "created_at": created_at,
                })
            else:
                # 新規アイテム
                self._index[ulid] = {
                    "name": name,
                    "chunks": [{"offset": offset, "size": len(data), "created_at": created_at}],
                }

            # ロック保持中にインデックスを書き戻す
            self._flush_locked()
        finally:
            self._release_lock()

    def read_item(self, ulid: str, history: int = 1) -> tuple[str, bytes]:
        """
        指定ULIDのアイテムを読み取る。

        Args:
            ulid: アイテムのULID文字列。
            history: 取得するヒストリー番号 (1=最新、2=1つ前、...)。

        Returns:
            (name, data) のタプル。
        """
        entry = self._get_entry(ulid)
        chunks = entry["chunks"]

        # chunks が空の場合は削除済み
        if not chunks:
            raise KeyError(f"アイテム '{ulid}' は削除されています。")

        # history は 1 始まり。chunks[-1] が最新 (history=1)。
        # history=1 → chunks[-1], history=2 → chunks[-2], ...
        if history < 1 or history > len(chunks):
            raise IndexError(
                f"ヒストリー {history} は存在しません (利用可能: 1〜{len(chunks)})。"
            )

        chunk_index = len(chunks) - history

        chunk_meta = chunks[chunk_index]
        with open(self.path, "rb") as fp:
            data = _read_chunk(fp, chunk_meta["offset"])

        return entry["name"], data

    def get_history_count(self, ulid: str) -> int:
        """アイテムの有効なヒストリー数 (= chunks の長さ) を返す。"""
        entry = self._get_entry(ulid)
        return len(entry["chunks"])

    def change_head(self, ulid: str, history: int | None = None) -> None:
        """
        ヘッドを変更してアイテムを「削除」または特定バージョンに戻す。

        Args:
            history: None の場合は最新バージョンを削除 (head を1減らす)。
                     指定した場合はそのヒストリーより前を破棄する。
        """
        if self.read_only:
            raise PermissionError("読み取り専用モードでは書き込みできません。")

        entry = self._get_entry(ulid)
        chunks = entry["chunks"]

        if history is None:
            # 最新バージョン (末尾) を1つ削除
            new_chunks = chunks[:-1]
        else:
            # history=N を新しい最新(history=1)にする
            # 末尾から (N-1) 個を削除する
            # 例: 4バージョン, history=2 → 末尾1個削除 → 3バージョン残る
            if history < 1 or history > len(chunks):
                raise IndexError(
                    f"ヒストリー {history} は範囲外です (利用可能: 1〜{len(chunks)})。"
                )
            new_len = len(chunks) - (history - 1)
            new_chunks = chunks[:new_len]

        self._index[ulid]["chunks"] = new_chunks
        self._flush()

    def delete_item(self, ulid: str) -> None:
        """アイテムを完全に削除する (インデックスから除去)。"""
        if self.read_only:
            raise PermissionError("読み取り専用モードでは書き込みできません。")
        self._get_entry(ulid)  # 存在確認
        del self._index[ulid]
        self._flush()

    def get_metadata(self, ulid: str) -> dict:
        """
        アイテムのメタデータを返す。

        Returns:
            name, size (最新バージョン), created_at (最新バージョン), history_count を含む辞書。
        """
        entry = self._get_entry(ulid)
        chunks = entry["chunks"]
        if not chunks:
            raise KeyError(f"アイテム '{ulid}' は削除されています。")

        latest_chunk = chunks[-1]  # 末尾が最新
        return {
            "name": entry["name"],
            "size": latest_chunk["size"],
            "created_at": latest_chunk["created_at"],
            "history_count": len(chunks),
            "ulid": ulid,
        }

    def list_ulids(self) -> list[str]:
        """有効な全アイテムのULID一覧を返す。"""
        return [
            ulid for ulid, entry in self._index.items()
            if len(entry["chunks"]) > 0
        ]

    # ------------------------------------------------------------------ #
    # 内部ヘルパー
    # ------------------------------------------------------------------ #

    def _get_entry(self, ulid: str) -> dict:
        """インデックスからエントリを取得する。存在しない場合はKeyErrorを送出。"""
        entry = self._index.get(ulid)
        if entry is None:
            raise KeyError(f"アイテム '{ulid}' が見つかりません。")
        return entry


def create_storage_file(path: str) -> None:
    """
    新規ストレージファイルを作成する。

    空のインデックスを持つ最小構成のファイルを書き出す。
    """
    index_bytes = b"{}"
    index_offset = HEADER_SIZE
    with open(path, "wb") as fp:
        fp.write(_pack_header(index_offset))
        fp.write(index_bytes)
