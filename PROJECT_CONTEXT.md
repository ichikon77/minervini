# Minervini スクリーナー プロジェクト 引き継ぎメモ

## 概要
東証・米国株のスクリーニングを毎日自動実行してGitHub Pagesに公開するシステム。

- GitHub Pages URL: https://ichikon77.github.io/minervini/
- リポジトリ: https://github.com/ichikon77/minervini
- ローカルフォルダ: C:\Users\ichik\Documents\minervini\

---

## スクリーナー一覧

### 1. 米国株 Minervini (index.html)
- スクリプト: `minervini_screen.py`
- 実行bat: `run.bat`
- 対象: S&P500 + NASDAQ100
- 条件: Minervini トレンドテンプレート7条件 + RSレーティング >= 70
- タスクスケジューラー: 毎日 8:00（タスク名: MinerviniScreener）
- ログ: `log.txt`

### 2. 日本株 配当スクリーニング (haitou.html)
- スクリプト: `haitou_screen.py`
- 実行bat: `haitou_run.bat`
- 対象: TOPIXプライム（JPX公式Excelから取得）
- 条件: 配当利回り >= 3% かつ 現在PBR < 過去2年最低PBR × 1.10
- タスクスケジューラー: 毎日 16:00（タスク名: HaitouScreener）
- ログ: `haitou_log.txt`

### 3. 日本株 Minervini (jpminervini.html)
- スクリプト: `jpminervini_screen.py`
- 実行bat: `jpminervini_run.bat`
- 対象: TOPIXプライム（スクリーニング）
- 条件: Minervini 7条件 + RSレーティング >= 70（TOPIXプライム全銘柄ベース）+ 営業利益率 >= 15%（年次）
- タスクスケジューラー: 毎日 17:30（タスク名: JpMinerviniScreener）

---

## タスクスケジューラー設定
3つのタスクはすべて「StartWhenAvailable = True」に設定済み。
PCがスケジュール時刻に起動していなかった場合、次回起動時に自動実行される。

確認コマンド（PowerShell）:
```powershell
Get-ScheduledTask -TaskName "MinerviniScreener" | Get-ScheduledTaskInfo
Get-ScheduledTask -TaskName "HaitouScreener" | Get-ScheduledTaskInfo
Get-ScheduledTask -TaskName "JpMinerviniScreener" | Get-ScheduledTaskInfo
```

---

## 重要な技術的メモ

### yfinance のデータ形式（v1.3.0）
- 複数銘柄ダウンロード時はMultiIndex形式: `raw["6857.T"]["Close"]`
- 最終行（当日）がNaNになることがある → `dropna(subset=["Close"])` で除去

### 配当利回りの単位変換
yfinanceの`dividendYield`は銘柄によって小数(0.03)と%値(0.94)が混在する:
```python
if dividend_yield <= 0.1:
    dividend_yield = dividend_yield * 100
```

### git push 競合対策
複数スクリプトが同時実行されるため、push前にstash/rebaseを実施:
```python
subprocess.run(["git", "stash"], check=False)
subprocess.run(["git", "pull", "--rebase"], check=True)
subprocess.run(["git", "stash", "pop"], check=False)
subprocess.run(["git", "push"], check=True)
```

### 日本語バッチファイルのエンコード
Windowsのbatファイルは CP932(Shift-JIS) でエンコードする必要がある。

### JPX公式ファイル（TOPIXプライム銘柄取得）
URL: https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls
- openpyxl で読み込み（xlrd はフォールバック）
- 「市場」列に「プライム」を含む行を抽出
- 「コード」列を4桁ゼロ埋めして `.T` を付与

### Nikkei225取得（Wikipediaから）
偽コード対策として、4桁コードの千の位の種類数でスコアリング:
- 本物の株コード: 1xxx, 2xxx, 4xxx, 6xxx, 9xxx など複数千の位 → スコア高
- 西暦年(1915-2030)が混入した場合: 千の位が1か2のみ → スコア低
- スコア >= 3 のものだけ採用

---

## gitignore 設定
以下は追跡対象外:
- `*_history.json`, `*_prev.json`（履歴・状態ファイル）
- `*.txt`（ログファイル）
- `debug_*.py`, `debug_*.bat`, `patch_*.py`, `setup_*.bat`, `fix_*.bat`（一時スクリプト）

---

## Python / 依存ライブラリ
- Python: C:\Users\ichik\AppData\Local\Programs\Python\Python314\python.exe
- 主要ライブラリ: yfinance, pandas, numpy, requests, openpyxl, xlrd

インストールコマンド:
```powershell
pip install yfinance pandas numpy requests openpyxl xlrd
```

---

## 過去の主なトラブルと解決策

| 問題 | 原因 | 解決策 |
|------|------|--------|
| 通過銘柄0件 | yfinance最終行がNaN | `dropna(subset=["Close"])` |
| 配当利回りが94%など異常値 | yfinanceの単位混在 | 0.1以下なら×100 |
| git push失敗（unstaged changes） | .pyファイルが未コミット状態 | stash → rebase → stash pop |
| batファイル文字化け | CP932エンコード必要 | Python側でCP932でエンコードして書き出し |
| quarterly_financials が空 | 日本株はquarterlyデータ少ない | `t.financials`（年次）に切り替え |
| git index.lock エラー | 前のgitプロセスが異常終了 | PowerShellで `Remove-Item` |
| Nikkei225に西暦年が混入 | Wikipedia表の構造変化 | 千の位の分布でスコアリング |
| 手動編集(nav文言・リンク修正等)が2回消失(2026-08頃) | pushする前に日次自動実行スクリプトのgit操作(stash/pull --rebase --autostash等)が走り、未commitの変更が巻き込まれて消える。消えた後の古いテンプレートで翌朝HTMLが再生成され「リンクが古い」状態になる | 運用ルール: 一括編集した日はその場でcommit+pushまで完了させる（未commitで放置しない）。commit済みなら自動実行で消えることはない |

## kijitsu(信用期日)デッキ 検証メモ

- 2026-08-15時点、日本株全938銘柄（時価総額1,000億円以上+直近四半期営業黒字、過去10年日足、サンプリングなし）で信用期日サイクルの勝率/平均リターン/平均DDマトリクスを検証済み。結果はkijitsu.html本体に反映済み（打診・ナンピンの目安セクション、検証日付き）。
  - 谷=期日通過180-209日後（最後の投げが出るタイミング。期日直前ではなく通過直後）
  - 本命の買い場=240日以降×4〜6ヶ月保有（勝率59-66%、ピークは330-359日×4ヶ月で66.4%）
  - 打診・ナンピン目安: 1回目=240日以降、2回目=そこから-10%下（≒330-419日帯）、撤退条件=510日超で浮上しなければ構造問題を疑う（720日超×6ヶ月は勝率63%でも平均リターン-1.3%という「勝率は高いが稀な大負けで平均が壊れる」ゾーン）
  - 米国株分は別HTML化する方針（kijitsu本体には混ぜない、まだ未着手の可能性あり要確認）
  - このデータは2026-08-15時点のスナップショットなので、半年〜1年後を目安に最新データへの更新が必要（ユーザーからの要望）
