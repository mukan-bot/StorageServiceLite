"""
StorageServiceLite を使ったファイルアップロード・ダウンロードの FastAPI サンプル

認証なしでシンプルに使えるサンプルアプリケーションです。

起動方法:
    pip install fastapi uvicorn python-multipart
    uvicorn sample.app:app --reload

エンドポイント一覧:
    POST   /files                      ファイルをアップロード
    GET    /files                      ファイル一覧を取得
    GET    /files/{ulid}               ファイルをダウンロード
    GET    /files/{ulid}/metadata      メタデータを取得
    GET    /files/{ulid}/history       ヒストリー一覧を取得
    POST   /files/{ulid}               ファイルを更新（新バージョン追加）
    POST   /files/{ulid}/rollback      指定バージョンへロールバック
    DELETE /files/{ulid}               ファイルを完全削除
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from storageservicelite import item, read_storage
from storageservicelite.storage import Storage

# ストレージファイルのパス (環境変数で上書き可能)
STORAGE_PATH = os.environ.get("STORAGE_PATH", "storage.ssobj")

# グローバルストレージインスタンス
_storage: Storage | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリ起動時にストレージを開き、終了時に何もしない。"""
    global _storage
    _storage = read_storage(STORAGE_PATH, create_if_not_exists=True)
    yield


app = FastAPI(
    title="StorageServiceLite サンプル API",
    description="単一ファイルオブジェクトストレージを使ったファイル管理 API",
    version="0.1.0",
    lifespan=lifespan,
)


def get_storage() -> Storage:
    """ストレージインスタンスを返す。未初期化の場合は 503 を返す。"""
    if _storage is None:
        raise HTTPException(status_code=503, detail="ストレージが初期化されていません。")
    return _storage


# ------------------------------------------------------------------ #
# ファイルアップロード
# ------------------------------------------------------------------ #

@app.post("/files", status_code=201, summary="ファイルをアップロード")
async def upload_file(
    file: Annotated[UploadFile, File(description="アップロードするファイル")],
    name: Annotated[str, Form(description="アイテム名 (省略時はファイル名)")] = "",
):
    """
    ファイルを新規アイテムとしてストレージに保存します。

    - **file**: アップロードするファイル
    - **name**: アイテム名。省略した場合はアップロードされたファイル名が使われます。
    """
    storage = get_storage()
    data = await file.read()
    item_name = name or file.filename or ""
    ulid = storage.set(item(name=item_name, data=data))
    return {"ulid": ulid, "name": item_name, "size": len(data)}


# ------------------------------------------------------------------ #
# ファイル一覧
# ------------------------------------------------------------------ #

@app.get("/files", summary="ファイル一覧を取得")
def list_files():
    """
    ストレージに保存されている全ファイルのメタデータ一覧を返します。
    """
    storage = get_storage()
    result = []
    for ulid in storage.list():
        try:
            meta = storage.get_metadata(ulid)
            result.append(meta)
        except KeyError:
            pass  # change_head で空になったアイテムはスキップ
    return {"files": result}


# ------------------------------------------------------------------ #
# ファイルダウンロード
# ------------------------------------------------------------------ #

@app.get("/files/{ulid}", summary="ファイルをダウンロード")
def download_file(
    ulid: str,
    history: Annotated[int, Query(description="ヒストリー番号 (1=最新)", ge=1)] = 1,
):
    """
    指定 ULID のファイルをダウンロードします。

    - **ulid**: アイテムの ULID
    - **history**: ヒストリー番号。1 が最新。省略時は最新バージョン。
    """
    storage = get_storage()
    try:
        retrieved = storage.get(ulid, history=history)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"アイテム '{ulid}' が見つかりません。")
    except IndexError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # ファイル名として name を使用
    filename = retrieved.name or ulid
    return Response(
        content=retrieved.data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ------------------------------------------------------------------ #
# メタデータ取得
# ------------------------------------------------------------------ #

@app.get("/files/{ulid}/metadata", summary="メタデータを取得")
def get_metadata(ulid: str):
    """
    指定 ULID のアイテムメタデータ（名前・サイズ・作成日時・バージョン数）を返します。
    """
    storage = get_storage()
    try:
        return storage.get_metadata(ulid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"アイテム '{ulid}' が見つかりません。")


# ------------------------------------------------------------------ #
# ヒストリー一覧
# ------------------------------------------------------------------ #

@app.get("/files/{ulid}/history", summary="ヒストリー一覧を取得")
def get_history(ulid: str):
    """
    指定 ULID のアイテムが持つヒストリー番号の一覧を返します。
    数値が小さいほど新しいバージョンです (1 = 最新)。
    """
    storage = get_storage()
    try:
        history_list = storage.get_history(ulid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"アイテム '{ulid}' が見つかりません。")
    return {"ulid": ulid, "history": history_list}


# ------------------------------------------------------------------ #
# ファイル更新（新バージョン追加）
# ------------------------------------------------------------------ #

@app.post("/files/{ulid}", summary="ファイルを更新")
async def update_file(
    ulid: str,
    file: Annotated[UploadFile, File(description="新しいファイル")],
    name: Annotated[str, Form(description="アイテム名 (省略時は変更なし)")] = "",
):
    """
    既存アイテムに新しいバージョンを追加します。

    - **ulid**: 更新するアイテムの ULID
    - **file**: 新しいファイル
    - **name**: 新しいアイテム名。省略時は現在の名前を引き継ぎます。
    """
    storage = get_storage()

    # アイテムが存在するか確認
    try:
        current_meta = storage.get_metadata(ulid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"アイテム '{ulid}' が見つかりません。")

    data = await file.read()
    item_name = name or current_meta["name"]
    storage.set(item(ulid=ulid, name=item_name, data=data))
    return {"ulid": ulid, "name": item_name, "size": len(data)}


# ------------------------------------------------------------------ #
# ロールバック
# ------------------------------------------------------------------ #

@app.post("/files/{ulid}/rollback", summary="バージョンをロールバック")
def rollback(
    ulid: str,
    history: Annotated[
        int | None,
        Query(description="ロールバック先のヒストリー番号 (省略時は最新1件を削除)", ge=1),
    ] = None,
):
    """
    アイテムのヘッドを巻き戻します。

    - **history** 省略時: 最新バージョンを1件削除します。
    - **history=N** 指定時: 旧ヒストリー N が新しい最新 (history=1) になります。
      つまり直近 N-1 件のバージョンが削除されます。
    """
    storage = get_storage()
    try:
        storage.change_head(ulid, history=history)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"アイテム '{ulid}' が見つかりません。")
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ulid": ulid, "message": "ロールバックしました。"}


# ------------------------------------------------------------------ #
# 完全削除
# ------------------------------------------------------------------ #

@app.delete("/files/{ulid}", status_code=204, summary="ファイルを完全削除")
def delete_file(ulid: str):
    """
    指定 ULID のアイテムをすべてのバージョンごと完全に削除します。
    この操作は元に戻せません。
    """
    storage = get_storage()
    try:
        storage.delete(ulid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"アイテム '{ulid}' が見つかりません。")
