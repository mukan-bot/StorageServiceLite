"""
同時アクセス時の挙動に関するテスト

ロック機構 (.lock ファイル) と複数スレッド / 複数インスタンスからの
並行操作が正しく機能することを確認する。
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from storageservicelite import read_storage, item
from storageservicelite.fileformat import LOCK_SUFFIX


class TestConcurrentWritesSameInstance:
    """同一 Storage インスタンスへの並行書き込みテスト。"""

    def test_parallel_set_all_items_saved(self, tmp_storage_path):
        """複数スレッドから並行して set しても全アイテムが保存される。"""
        storage = read_storage(tmp_storage_path, create_if_not_exists=True)
        n_threads = 10
        ulids = []
        errors = []
        lock = threading.Lock()

        def write(i):
            try:
                ulid = storage.set(item(name=f"item-{i}", data=f"data-{i}".encode()))
                with lock:
                    ulids.append(ulid)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"例外が発生しました: {errors}"
        assert len(ulids) == n_threads
        # 全ULIDが一意であること
        assert len(set(ulids)) == n_threads

    def test_parallel_set_data_is_readable(self, tmp_storage_path):
        """並行 set 後、全アイテムが正しく読み取れる。"""
        storage = read_storage(tmp_storage_path, create_if_not_exists=True)
        n_threads = 10
        written = {}
        lock = threading.Lock()

        def write(i):
            data = f"value-{i}".encode()
            ulid = storage.set(item(name=f"item-{i}", data=data))
            with lock:
                written[ulid] = data

        threads = [threading.Thread(target=write, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 書き込まれた全アイテムを正しく読み取れること
        for ulid, expected_data in written.items():
            result = storage.get(ulid)
            assert result.data == expected_data


class TestConcurrentWritesMultipleInstances:
    """複数の Storage インスタンス (同一ファイル) からの並行書き込みテスト。"""

    def test_multiple_instances_parallel_set(self, tmp_storage_path):
        """複数インスタンスから並行 set しても全データがファイルに保存される。"""
        # まずファイルを作成
        read_storage(tmp_storage_path, create_if_not_exists=True)

        n_threads = 8
        results = []
        errors = []
        lock = threading.Lock()

        def write_with_new_instance(i):
            try:
                st = read_storage(tmp_storage_path)
                ulid = st.set(item(name=f"inst-{i}", data=f"inst-data-{i}".encode()))
                with lock:
                    results.append((i, ulid))
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [
            threading.Thread(target=write_with_new_instance, args=(i,))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"例外が発生しました: {errors}"
        assert len(results) == n_threads

    def test_lock_prevents_simultaneous_writes(self, tmp_storage_path):
        """ロックファイルが存在する間は書き込みがブロックされ、解放後に成功する。"""
        storage = read_storage(tmp_storage_path, create_if_not_exists=True)
        lock_path = tmp_storage_path + LOCK_SUFFIX

        # 手動でロックファイルを作成し、少し後に解放する
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)

        write_started_at = None
        write_finished_at = None

        def delayed_write():
            nonlocal write_started_at, write_finished_at
            write_started_at = time.monotonic()
            storage.set(item(name="delayed", data=b"delayed-data"))
            write_finished_at = time.monotonic()

        t = threading.Thread(target=delayed_write)
        t.start()

        # 少し待ってからロックを解放
        time.sleep(0.2)
        os.remove(lock_path)

        t.join(timeout=6.0)
        assert not t.is_alive(), "書き込みスレッドがタイムアウトしました"
        assert write_finished_at is not None
        # ロック解放後 (~0.2秒後) に書き込みが完了していること
        assert (write_finished_at - write_started_at) >= 0.15

    def test_lock_timeout_raises(self, tmp_storage_path):
        """ロックが5秒以上保持され続けると TimeoutError が発生する。"""
        storage = read_storage(tmp_storage_path, create_if_not_exists=True)
        lock_path = tmp_storage_path + LOCK_SUFFIX

        # ロックを手動で取得したまま解放しない
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)

        try:
            with pytest.raises(TimeoutError):
                storage.set(item(name="timeout_test", data=b"should timeout"))
        finally:
            # テスト後はロックを必ず解放する
            try:
                os.remove(lock_path)
            except FileNotFoundError:
                pass


class TestConcurrentReads:
    """並行読み取りテスト。"""

    def test_parallel_reads_return_correct_data(self, tmp_storage_path):
        """複数スレッドからの並行 get が全て正しいデータを返す。"""
        storage = read_storage(tmp_storage_path, create_if_not_exists=True)
        ulid = storage.set(item(name="shared", data=b"shared-data"))

        n_threads = 20
        results = []
        errors = []
        lock = threading.Lock()

        def read():
            try:
                result = storage.get(ulid)
                with lock:
                    results.append(result.data)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=read) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"例外が発生しました: {errors}"
        assert len(results) == n_threads
        assert all(d == b"shared-data" for d in results)

    def test_parallel_reads_multiple_items(self, tmp_storage_path):
        """複数アイテムを並行して読み取れる。"""
        storage = read_storage(tmp_storage_path, create_if_not_exists=True)
        n_items = 10
        written = {
            storage.set(item(name=f"item-{i}", data=f"data-{i}".encode())): f"data-{i}".encode()
            for i in range(n_items)
        }

        results = {}
        errors = []
        lock = threading.Lock()

        def read(ulid, expected):
            try:
                result = storage.get(ulid)
                with lock:
                    results[ulid] = result.data
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [
            threading.Thread(target=read, args=(ulid, data))
            for ulid, data in written.items()
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"例外が発生しました: {errors}"
        for ulid, expected in written.items():
            assert results[ulid] == expected


class TestConcurrentMixedOperations:
    """書き込みと読み取りが混在する並行操作テスト。"""

    def test_write_then_parallel_read_write(self, tmp_storage_path):
        """並行した読み書き混在操作でもデータが壊れない。"""
        storage = read_storage(tmp_storage_path, create_if_not_exists=True)
        # 事前にアイテムを書き込んでおく
        pre_ulid = storage.set(item(name="pre", data=b"pre-data"))

        errors = []
        written_ulids = []
        lock = threading.Lock()

        def writer(i):
            try:
                ulid = storage.set(item(name=f"w-{i}", data=f"write-{i}".encode()))
                with lock:
                    written_ulids.append(ulid)
            except Exception as e:
                with lock:
                    errors.append(("write", i, e))

        def reader(i):
            try:
                result = storage.get(pre_ulid)
                if result.data != b"pre-data":
                    with lock:
                        errors.append(("read", i, f"データ不整合: {result.data!r}"))
            except Exception as e:
                with lock:
                    errors.append(("read", i, e))

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"エラーが発生しました: {errors}"
        assert len(written_ulids) == 5

    def test_concurrent_updates_to_same_item(self, tmp_storage_path):
        """同一アイテムへの並行更新でヒストリーが正しく積まれる。"""
        storage = read_storage(tmp_storage_path, create_if_not_exists=True)
        ulid = storage.set(item(name="v0", data=b"version-0"))

        n_updates = 6
        errors = []
        lock = threading.Lock()

        def update(i):
            try:
                storage.set(item(ulid=ulid, name=f"v{i + 1}", data=f"version-{i + 1}".encode()))
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=update, args=(i,)) for i in range(n_updates)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"例外が発生しました: {errors}"
        # 初回 + n_updates 回のヒストリーが存在する
        history = storage.get_history(ulid)
        assert len(history) == n_updates + 1


class TestConcurrentWithThreadPoolExecutor:
    """ThreadPoolExecutor を使った並行アクセスのテスト。"""

    def test_threadpool_parallel_set(self, tmp_storage_path):
        """ThreadPoolExecutor で並行 set した全結果が成功する。"""
        storage = read_storage(tmp_storage_path, create_if_not_exists=True)
        n = 15

        def write(i):
            return storage.set(item(name=f"tp-{i}", data=f"tp-data-{i}".encode()))

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(write, i) for i in range(n)]
            ulids = [f.result() for f in as_completed(futures)]

        assert len(ulids) == n
        assert len(set(ulids)) == n  # 全ULIDが一意

    def test_threadpool_parallel_set_and_get(self, tmp_storage_path):
        """ThreadPoolExecutor で set/get を混在させてもエラーが起きない。"""
        storage = read_storage(tmp_storage_path, create_if_not_exists=True)
        base_ulid = storage.set(item(name="base", data=b"base-data"))

        errors = []

        def task(i):
            if i % 2 == 0:
                storage.set(item(name=f"even-{i}", data=f"even-{i}".encode()))
            else:
                result = storage.get(base_ulid)
                assert result.data == b"base-data"

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(task, i) for i in range(12)]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    errors.append(e)

        assert not errors, f"エラーが発生しました: {errors}"
