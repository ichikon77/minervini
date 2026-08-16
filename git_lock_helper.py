# -*- coding: utf-8 -*-
"""
git index.lock 対策の共通ヘルパー

このフォルダでは多数のスクリーナーがそれぞれ git add/commit/push するため、
  1. 2つのスクリプトが同時にgitを触る（スケジュール時刻の重なり・手動実行との衝突）
  2. スクリプトが途中で落ちて .git/index.lock が残る（stale lock）
の2パターンで "Unable to create index.lock: File exists" が発生する。

各スクリプトの push_to_github() の先頭で wait_for_git_lock() を呼ぶことで:
  - 他のgitプロセスが動作中ならロックが消えるまで待つ（最大timeout秒）
  - gitプロセスが存在しないのにロックだけ残っている（stale_sec秒以上古い）場合は自動削除
"""

import os
import time
import subprocess


def _git_running():
    """git.exe が動作中かどうか（Windows）。判定できない環境では False を返す"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq git.exe"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return "git.exe" in out
    except Exception:
        return False


def wait_for_git_lock(repo_dir, timeout=300, stale_sec=120):
    """`.git/index.lock` が存在する間、消えるまで待つ。

    - 他のgitプロセスが実際に動いている間は5秒間隔で待機（最大timeout秒）
    - ロックがstale_sec秒以上古く、かつgitプロセスが存在しない場合は
      放置されたロックと判断して自動削除する
    """
    lock = os.path.join(repo_dir, ".git", "index.lock")
    t0 = time.time()
    while os.path.exists(lock):
        try:
            age = time.time() - os.path.getmtime(lock)
        except OSError:
            return  # 直前に消えた
        if age > stale_sec and not _git_running():
            try:
                os.remove(lock)
                print(f"  stale index.lock を自動削除しました（{age:.0f}秒前のロック）")
                return
            except OSError:
                pass
        if time.time() - t0 > timeout:
            print("  index.lock の解放待ちがタイムアウトしました（このまま実行し失敗する可能性があります）")
            return
        time.sleep(5)
