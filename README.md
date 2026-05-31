# StorageServiceLite

python:3.12以降で動作する。

オブジェクトストレージサービスを提供するライブラリです。

SQLiteのように単一ファイルとして管理されるストレージサービスを提供します。

## 使い方

```python
from storageservicelite import item, read_storage
# ファイルパスを指定してストレージを読み込む
storage = read_storage(
    path="storage.ssobj",
    create_if_not_exists=True,  # ファイルが存在しない場合は新規作成する
    read_only=False              # 読み取り専用で開くかどうか
)
# アイテムを追加する(バイナリデータ)
item1 = item(
    name="item1",           # 空の場合はkeyがアイテム名と同じになる
    data=b"Hello, World!"   # バイナリデータを指定する場合はbytes型で渡す必要があります
)
ulid_key = storage.set(item1)

# アイテムを追加する（ローカルのファイルを追加）
item2 = item(
    name="item2",
    file_path="path/to/local/file.txt"  # ローカルのファイルパスを指定する場合はfile_pathを使用します
)
ulid_key2 = storage.set(item2)

# アイテムを更新する（バイナリデータで更新）
item1_updated = item(
    ulid=ulid_key,          # 更新するアイテムのULIDを指定します
    name="item1_updated",   # アイテム名を更新する場合はnameを指定します
    data=b"Updated data!"   # バイナリデータを更新する場合はdataを指定します
)
storage.set(item1_updated)

# アイテムを取得する（バイナリデータとして取得）
retrieved_item1 = storage.get(ulid_key)
print(retrieved_item1.data)  # b"Hello, World!"

# アイテムを取得する（ローカルのファイルとして取得）
retrieved_item2 = storage.get(ulid_key2, path="path/to/save/file.txt")  # ローカルのファイルとして保存する場合はpathを指定します

# アイテムのヒストリーを指定して取得する
retrieved_item1_v1 = storage.get(ulid_key, history=1)

# アイテムのヒストリーを取得する
history = storage.get_history(ulid_key)
print(history)  # [1, 2, 3, ...]

# アイテムを削除する（最新を削除）
storage.change_head(ulid_key)
# アイテムを削除する（history=1,2を削除しhistory=3を最新にする）
storage.change_head(ulid_key, history=3)

# アイテムを完全に削除する（すべてのバージョンを削除）
storage.delete(ulid_key)

# アイテムのメタデータを取得する
metadata = storage.get_metadata(ulid_key)
print(metadata)  # {'name': 'item1', 'size': 13, 'created_at': '2024-06-01T12:00:00Z', ...}
```

ヒストリーはアイテムをバージョン管理するための機能で、過去の状態を追跡したり、特定のバージョンに戻すことができます。
ヒストリーは最新を1とし、過去に遡るごとに数値が増えていきます。例えば、最新の状態がヒストリー1であれば、その前の状態はヒストリー2、さらにその前はヒストリー3となります。

## 内部実装

内部的には、ストレージは単一のファイル（例: `storage.ssobj`）として管理されます。このファイルは、アイテムのデータとメタデータを効率的に格納するための独自のフォーマットで構成されています。
アイテムは、ULID（Universally Unique Lexicographically Sortable Identifier）をキーとして管理されます。これにより、アイテムの一意性と順序性が保証されます。


### 実装方針について

- **単一ファイル管理**: ストレージ全体を単一のファイルで管理することで、データの整合性を保ちつつ、簡単にバックアップや移行が可能になります。
- **ULIDの使用**: ULIDをキーとして使用することで、アイテムの一意性を確保し、同時に生成されたアイテムの順序も保証されます。
- **ヒストリー管理**: アイテムのバージョン管理を行うことで、過去の状態を追跡し、必要に応じて特定のバージョンに戻すことができます。
- **効率的なデータ格納**: アイテムのデータとメタデータを効率的に格納するための独自のフォーマットを採用しています。これにより、読み書きのパフォーマンスを向上させています
    - 圧縮率よりも高速な読み書きを優先するため、データは非圧縮で格納されます。（将来的に圧縮対応の可能性あり）
- **エラーハンドリング**: ファイルの読み書きやアイテムの操作において、適切なエラーハンドリングを実装することで、安定した動作を保証します。
- **衝突回避**: 同時に複数のプロセスがストレージファイルにアクセスする可能性を考え、ストレージファイルに書き込み中フラグを設けることでデータの読み取り、書き込みの衝突を回避します。
