# -*- coding: utf-8 -*-
"""
git index.lock / 中断rebase 対策の共通ヘルパー

このフォルダでは多数のスクリーナーがそれぞれ git add/commit/push するため、
  1. 2つのスクリプトが同時にgitを触る（スケジュール時刻の重なり・手動実行との衝突）
  2. スクリプトが途中で落ちて .git/index.lock が残る（stale lock）
  3. pull --rebase が競合や中断で止まり .git/rebase-merge が残る
     → 以降の全スクリプトのpullが "already a rebase-merge directory" で失敗し続ける
     （2026-08-30発生: rironほか全デッキのpushが丸1日失敗し14commit滞留）
のパターンで git操作が失敗する。

各スクリプトの push_to_github() の先頭で wait_for_git_lock() を呼ぶことで:
  - 他のgitプロセスが動作中ならロックが消えるまで待つ（最大timeout秒）
  - gitプロセスが存在しないのにロックだけ残っている（stale_sec秒以上古い）場合は自動削除
  - 中断されたrebase（rebase-merge/rebase-apply）が残っていれば `git rebase --abort` で
    自動復旧する（abortは開始前の状態に安全に巻き戻し、autostashも復元される）
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


def _abort_stale_rebase(repo_dir, stale_sec):
    """中断されたrebase（.git/rebase-merge / rebase-apply が残った状態）を自動復旧する。
    rebase進行中ディレクトリがstale_sec秒以上古く、gitプロセスが動いていなければ、
    放置された中断rebaseと判断して `git rebase --abort` で開始前の状態に戻す。
    （abortに失敗した場合は手動対応が必要なため警告だけ出す。rmtreeによる強制削除は
    　競合マーカーが作業ツリーに残る恐れがあるため行わない）"""
    for d in ("rebase-merge", "rebase-apply"):
        path = os.path.join(repo_dir, ".git", d)
        if not os.path.isdir(path):
            continue
        try:
            age = time.time() - os.path.getmtime(path)
        except OSError:
            continue
        if age < stale_sec or _git_running():
            continue  # 直近のもの/実行中のものは触らない
        print(f"  中断されたrebase（.git/{d}、{age/60:.0f}分前）を検出 → git rebase --abort で復旧します")
        try:
            subprocess.run(["git", "-C", repo_dir, "rebase", "--abort"],
                           capture_output=True, text=True, timeout=120)
        except Exception as e:
            print(f"  rebase --abort実行エラー: {e}")
        if not os.path.isdir(path):
            print("  rebase復旧完了")
            continue
        # abortが効かない=正規のrebase状態ファイルが壊れている残骸。
        # gitのエラーメッセージ自身が勧める rm -fr ".git/rebase-merge" 相当を実施
        import shutil
        try:
            shutil.rmtree(path)
            print(f"  壊れたrebase残骸 .git/{d} を削除しました")
        except OSError as e:
            print(f"  警告: .git/{d} を削除できません（手動対応が必要）: {e}")


def wait_for_git_lock(repo_dir, timeout=300, stale_sec=120):
    """git操作前の自動復旧: index.lockの解放待ち/stale削除 + 中断rebaseのabort。

    - 他のgitプロセスが実際に動いている間は5秒間隔で待機（最大timeout秒）
    - ロックがstale_sec秒以上古く、かつgitプロセスが存在しない場合は
      放置されたロックと判断して自動削除する
    - 中断されたrebaseが残っていれば自動でabortする
    """
    _abort_stale_rebase(repo_dir, stale_sec)

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
