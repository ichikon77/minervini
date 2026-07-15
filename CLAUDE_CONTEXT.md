# Claude引き継ぎ用コンテキスト

このファイルをClaudeに読み込ませることで、過去の作業経緯を把握した状態で会話を再開できます。

---

## プロジェクト概要

`C:\Users\ichik\Documents\minervini` にある株スクリーナー群。
毎朝自動実行してGitHub Pagesに公開している。

**URL**: https://ichikon77.github.io/minervini/

---

## ファイル構成

| ファイル | 役割 |
|---|---|
| `minervini_screen_v2.py` | 米国株ミネルヴィニスクリーナー（メイン・使用中） |
| `jpminervini_screen.py` | 日本株ミネルヴィニスクリーナー |
| `haitou_screen.py` | 日本株高配当スクリーナー |
| `minervini_screen.py` | 旧バージョン（使用しない） |
| `txt/Minervini.txt` | TradingView用リスト（米国株） |
| `txt/Japan Minervini.txt` | TradingView用リスト（日本株ミネルヴィニ） |
| `txt/Japan High Divident.txt` | TradingView用リスト（高配当） |

---

## 各スクリーナーの条件

### minervini_screen_v2.py（米国株）
- ユニバース: S&P500
- ミネルヴィニ・トレンドテンプレート7条件
- RSレーティング >= 70
- VCP（Volatility Contraction Pattern）検出付き

### jpminervini_screen.py（日本株ミネルヴィニ）
- ユニバース: TOPIXプライム（JPX公式XLSから取得）
- ミネルヴィニ・トレンドテンプレート7条件
- RSレーティング >= 70（TOPIXプライム全銘柄ベース）
- 営業利益率 >= 15%
- VCP検出付き（米国版と同一ロジック）

### haitou_screen.py（日本株高配当）
- ユニバース: TOPIXプライム
- 配当利回り >= 3.0%
- 時価総額 >= 500億円
- 現在PBR < (2年最高PBR - 2年最低PBR) × 0.25 + 2年最低PBR
- カラム: コード | 銘柄名 | 時価総額 | 配当利回り | 株価 | 現在PBR | 2年最低PBR | 2年最高PBR | 閾値 | ゾーン% | 最低比 | 初回検出

---

## VCPロジック（米国・日本共通）

- `WIN=5`: Close基準の±5営業日ウィンドウでピーク/トラフ検出
- `ZIGZAG_SWING=2.0%`: ジグザグ末尾補完の閾値
- `VCP_MIN_CONTRACTION_PCT=5.0%`: 収縮としてカウントする最小深さ
- `VCP_LOOKBACK_DAYS=252`: 検出対象期間
- ピーク/トラフはClose基準で検出、High/Lowを保存して収縮深さを計算
- 収縮率の単調減少のみで有効性判定
- T0概念は廃止、代わりに`pivot_breakout`(BO)カラムを使用
- T1/T2/T3のStart日・Trough日をデバッグカラムとして出力

---

## TradingViewリスト自動生成

各スクリーナー実行後、`txt/`フォルダに自動生成される。  
フォーマット: `ティッカー[TAB],[改行]` の繰り返し（日本株は`.T`除去済み）

---

## ナビゲーションバー（全ページ共通）

```
米国株(Minervini) → minervini_report_v2.html
日本株(配当)     → haitou.html
日本株(Minervini) → jpminervini.html
```
※旧 `index.html` へのリンクは廃止済み

---

## スケジュール実行

- `setup_minervini_v2_task.bat` / `setup_jpminervini_task.bat` / `setup_haitou_task.bat`
- **管理者権限のPowerShellで実行が必要**
- 毎朝8:05に自動実行

---

## 注意事項

- 日本株ティッカーは `1234.T` または `285A.T`（英数字混合コード対応済み）
- JPXのXLSパース時のフィルタ: `re.match(r'^[0-9]{3}[0-9A-Z]$', c)`
- 株価表示は円建て（小数なし）
