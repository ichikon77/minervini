"""
Minervini Trend Template Screener
==================================
条件1〜7 + RSレーティング(>=70)でS&P500+NASDAQ100を毎日スクリーニング
出力: HTMLレポート (minervini_report.html)
"""

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import io
import json
from datetime import datetime, timedelta
import warnings
import time
import os

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────
# 設定
# ─────────────────────────────────────────
RS_THRESHOLD = 70          # RSレーティング閾値
PRICE_HISTORY_DAYS = 400   # 取得する日数（200日MA計算のバッファ込み）
BATCH_SIZE = 50            # yfinanceバッチ取得サイズ
MA200_TREND_DAYS = 20      # 条件3: 何営業日前と比較するか

# 履歴ファイル（スクリプトと同じフォルダに保存）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(SCRIPT_DIR, "minervini_history.json")

# ─────────────────────────────────────────
# ユニバース取得
# ─────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

def get_sp500_tickers():
    """WikipediaからS&P500構成銘柄を取得"""
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        html = requests.get(url, headers=HEADERS, timeout=15).text
        tables = pd.read_html(io.StringIO(html))
        df = tables[0]
        tickers = df["Symbol"].tolist()
        # BRK.B → BRK-B など yfinance形式に変換
        tickers = [t.replace(".", "-") for t in tickers]
        return tickers
    except Exception as e:
        print(f"S&P500取得エラー: {e}")
        return []

def get_nasdaq100_tickers():
    """WikipediaからNASDAQ100構成銘柄を取得"""
    try:
        url = "https://en.wikipedia.org/wiki/Nasdaq-100"
        html = requests.get(url, headers=HEADERS, timeout=15).text
        tables = pd.read_html(io.StringIO(html))
        for table in tables:
            cols = [str(c).lower() for c in table.columns]
            if "ticker" in cols:
                col = table.columns[cols.index("ticker")]
                tickers = table[col].dropna().tolist()
                tickers = [str(t).replace(".", "-") for t in tickers]
                return tickers
        return []
    except Exception as e:
        print(f"NASDAQ100取得エラー: {e}")
        return []

def get_universe():
    print("📋 ユニバース取得中...")
    sp500 = get_sp500_tickers()
    ndq100 = get_nasdaq100_tickers()
    universe = list(set(sp500 + ndq100))
    print(f"  S&P500: {len(sp500)}銘柄 / NASDAQ100: {len(ndq100)}銘柄 / 合計(重複除く): {len(universe)}銘柄")
    return universe

# ─────────────────────────────────────────
# データ取得
# ─────────────────────────────────────────
def download_data(tickers):
    """yfinanceで一括ダウンロード"""
    print(f"\n📥 価格データ取得中（{len(tickers)}銘柄）...")
    end_date = datetime.today()
    start_date = end_date - timedelta(days=PRICE_HISTORY_DAYS)

    all_data = {}
    batches = [tickers[i:i+BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

    for i, batch in enumerate(batches):
        print(f"  バッチ {i+1}/{len(batches)} ({len(batch)}銘柄)...", end="\r")
        try:
            raw = yf.download(
                batch,
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
            if len(batch) == 1:
                ticker = batch[0]
                if not raw.empty:
                    all_data[ticker] = raw
            else:
                for ticker in batch:
                    try:
                        df = raw[ticker].dropna()
                        if len(df) >= 210:
                            all_data[ticker] = df
                    except Exception:
                        pass
        except Exception as e:
            print(f"\n  バッチエラー: {e}")
        time.sleep(0.3)

    print(f"\n  データ取得完了: {len(all_data)}銘柄")
    return all_data

# ─────────────────────────────────────────
# RS レーティング計算
# ─────────────────────────────────────────
def calc_rs_rating(all_data):
    """
    IBD式RSレーティングの近似計算
    過去12ヶ月のパフォーマンスを計算（直近3ヶ月を2倍ウェイト）
    全銘柄のパーセンタイルで1〜99にスケール
    """
    print("\n📊 RSレーティング計算中...")
    scores = {}
    for ticker, df in all_data.items():
        try:
            close = df["Close"]
            if len(close) < 252:
                continue
            # 直近3ヶ月（63営業日）と12ヶ月（252営業日）パフォーマンス
            perf_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100
            perf_12m = (close.iloc[-1] / close.iloc[-252] - 1) * 100
            # 直近3ヶ月を2倍ウェイト（IBDの近似）
            score = (perf_3m * 2 + perf_12m) / 3
            scores[ticker] = score
        except Exception:
            pass

    if not scores:
        return {}

    score_series = pd.Series(scores)
    # パーセンタイルランクに変換（1〜99）
    rs_ratings = {}
    for ticker, score in scores.items():
        percentile = (score_series < score).sum() / len(score_series) * 98 + 1
        rs_ratings[ticker] = round(percentile, 1)

    print(f"  RS計算完了: {len(rs_ratings)}銘柄")
    return rs_ratings

# ─────────────────────────────────────────
# トレンドテンプレート判定
# ─────────────────────────────────────────
def check_trend_template(ticker, df):
    """
    ミネルヴィニのトレンドテンプレート条件1〜7を判定
    Returns: (passed: bool, details: dict)
    """
    close = df["Close"]
    if len(close) < 252:
        return False, {}

    price = close.iloc[-1]

    # 各移動平均
    ma50  = close.rolling(50).mean().iloc[-1]
    ma150 = close.rolling(150).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    ma200_20d_ago = close.rolling(200).mean().iloc[-MA200_TREND_DAYS - 1]

    # 52週高値・安値
    high_52w = close.iloc[-252:].max()
    low_52w  = close.iloc[-252:].min()

    cond1 = bool(price > ma150 and price > ma200)
    cond2 = bool(ma150 > ma200)
    cond3 = bool(ma200 > ma200_20d_ago)
    cond4 = bool(ma50 > ma150 and ma50 > ma200)
    cond5 = bool(price > ma50)
    cond6 = bool(price >= low_52w * 1.30)
    cond7 = bool(price >= high_52w * 0.75)

    passed = all([cond1, cond2, cond3, cond4, cond5, cond6, cond7])

    details = {
        "price": round(float(price), 2),
        "ma50": round(float(ma50), 2),
        "ma150": round(float(ma150), 2),
        "ma200": round(float(ma200), 2),
        "high_52w": round(float(high_52w), 2),
        "low_52w": round(float(low_52w), 2),
        "cond1": cond1, "cond2": cond2, "cond3": cond3,
        "cond4": cond4, "cond5": cond5, "cond6": cond6, "cond7": cond7,
        "from_52w_low_pct": round((float(price) / float(low_52w) - 1) * 100, 1),
        "from_52w_high_pct": round((float(price) / float(high_52w) - 1) * 100, 1),
    }
    return passed, details

# ─────────────────────────────────────────
# 履歴管理
# ─────────────────────────────────────────
def load_history():
    """
    履歴ファイルを読み込む
    形式: { "AAPL": "2025-05-01", "MSFT": "2025-04-20", ... }
    キー=銘柄コード、値=初回通過日
    """
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_history(history):
    """履歴ファイルを保存"""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def update_history(results, history):
    """
    今回の通過銘柄で履歴を更新し、各銘柄にNEWフラグと初回日を付与
    """
    today = datetime.today().strftime("%Y-%m-%d")
    # 前回までの通過銘柄セット（今日より前に記録されたもの）
    prev_tickers = set(history.keys())

    for r in results:
        ticker = r["ticker"]
        if ticker not in history:
            # 初めて通過 → 今日の日付を記録
            history[ticker] = today
            r["first_seen"] = today
            r["is_new"] = True
        else:
            r["first_seen"] = history[ticker]
            # 前回実行時になかった銘柄 = NEW（初回日が今日）
            r["is_new"] = (history[ticker] == today)

    # 今回通過しなかった銘柄は履歴から削除しない（過去の記録として残す）
    # ただし、今回の通過銘柄セットを返して表示に使う
    save_history(history)
    return results

# ─────────────────────────────────────────
# スクリーニング実行
# ─────────────────────────────────────────
def run_screen(all_data, rs_ratings):
    print("\n🔍 スクリーニング実行中...")
    results = []

    for ticker, df in all_data.items():
        rs = rs_ratings.get(ticker, 0)
        if rs < RS_THRESHOLD:
            continue  # RSフィルター

        passed, details = check_trend_template(ticker, df)
        if passed:
            results.append({
                "ticker": ticker,
                "rs_rating": rs,
                **details,
            })

    # RS降順ソート
    results.sort(key=lambda x: x["rs_rating"], reverse=True)
    print(f"  通過銘柄: {len(results)}銘柄")
    return results

# ─────────────────────────────────────────
# HTMLレポート生成
# ─────────────────────────────────────────
def generate_html(results, output_path):
    date_str = datetime.today().strftime("%Y年%m月%d日")
    count = len(results)

    rows = ""
    for r in results:
        rs = r["rs_rating"]
        if rs >= 90:
            rs_color = "#22c55e"
        elif rs >= 80:
            rs_color = "#84cc16"
        else:
            rs_color = "#f59e0b"

        def c(v):
            return "✅" if v else "❌"

        is_new = r.get("is_new", False)
        first_seen = r.get("first_seen", "-")
        new_badge = ' <span class="new-badge">NEW</span>' if is_new else ""
        row_class = ' class="new-row"' if is_new else ""

        rows += f"""
        <tr{row_class}>
          <td class="ticker">{r['ticker']}{new_badge}</td>
          <td style="color:{rs_color}; font-weight:bold;">{rs}</td>
          <td>${r['price']:,.2f}</td>
          <td>${r['ma50']:,.2f}</td>
          <td>${r['ma150']:,.2f}</td>
          <td>${r['ma200']:,.2f}</td>
          <td>${r['low_52w']:,.2f}</td>
          <td>${r['high_52w']:,.2f}</td>
          <td class="pct">+{r['from_52w_low_pct']:.1f}%</td>
          <td class="pct">{r['from_52w_high_pct']:.1f}%</td>
          <td>{c(r['cond1'])}{c(r['cond2'])}{c(r['cond3'])}{c(r['cond4'])}{c(r['cond5'])}{c(r['cond6'])}{c(r['cond7'])}</td>
          <td class="first-seen">{first_seen}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Minervini スクリーニング - {date_str}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .badge {{
    display: inline-block;
    background: #1e40af;
    color: #bfdbfe;
    border-radius: 12px;
    padding: 2px 12px;
    font-size: 0.85rem;
    margin-left: 8px;
  }}
  .legend {{
    display: flex; gap: 16px; margin-bottom: 16px; font-size: 0.8rem; color: #94a3b8;
  }}
  .legend span {{ display: flex; align-items: center; gap: 4px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  thead th {{
    background: #1e293b;
    color: #94a3b8;
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
    position: sticky;
    top: 0;
    white-space: nowrap;
  }}
  tbody tr:hover {{ background: #1e293b; }}
  tbody td {{
    padding: 9px 12px;
    border-bottom: 1px solid #1e293b;
    white-space: nowrap;
  }}
  .ticker {{ font-weight: bold; color: #60a5fa; font-size: 0.95rem; }}
  .pct {{ color: #94a3b8; }}
  .first-seen {{ color: #64748b; font-size: 0.8rem; }}
  .new-badge {{
    display: inline-block;
    background: #dc2626;
    color: white;
    font-size: 0.65rem;
    font-weight: bold;
    padding: 1px 6px;
    border-radius: 4px;
    margin-left: 6px;
    vertical-align: middle;
    letter-spacing: 0.05em;
  }}
  .new-row {{ background: rgba(220, 38, 38, 0.08) !important; }}
  .new-row:hover {{ background: rgba(220, 38, 38, 0.15) !important; }}
  .cond-legend {{ margin-top: 20px; font-size: 0.78rem; color: #64748b; line-height: 1.8; }}
  .updated {{ text-align: right; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
</style>
</head>
<body>
  <h1>📈 Minervini トレンドテンプレート スクリーニング <span class="badge">{count}銘柄通過</span></h1>
  <p class="subtitle">{date_str} ｜ ユニバース: S&P500 + NASDAQ100 ｜ RSレーティング ≥ {RS_THRESHOLD}</p>
  <div class="legend">
    <span><span class="dot" style="background:#22c55e"></span>RS 90以上</span>
    <span><span class="dot" style="background:#84cc16"></span>RS 80〜89</span>
    <span><span class="dot" style="background:#f59e0b"></span>RS 70〜79</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>銘柄</th><th>RS</th><th>現在値</th><th>MA50</th><th>MA150</th><th>MA200</th>
        <th>52W安値</th><th>52W高値</th><th>安値比</th><th>高値比</th><th>条件 1〜7</th><th>初回通過日</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <div class="cond-legend">
    条件チェック順: ①株価>MA150&MA200 ②MA150>MA200 ③MA200上昇中(20営業日比) ④MA50>MA150&MA200 ⑤株価>MA50 ⑥52W安値+30%以上 ⑦52W高値の75%以上
  </div>
  <p class="updated">生成: {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ レポート出力: {output_path}")

# ─────────────────────────────────────────
# メイン
# ─────────────────────────────────────────
def main():
    start_time = time.time()
    print("=" * 50)
    print("  Minervini トレンドテンプレート スクリーナー")
    print("=" * 50)

    tickers = get_universe()
    if not tickers:
        print("❌ ユニバース取得失敗")
        return

    all_data = download_data(tickers)
    rs_ratings = calc_rs_rating(all_data)
    results = run_screen(all_data, rs_ratings)

    # 履歴と照合してNEWフラグ・初回日を付与
    history = load_history()
    results = update_history(results, history)
    new_count = sum(1 for r in results if r.get("is_new"))
    if new_count:
        print(f"  🆕 本日の新規エントリー: {new_count}銘柄")

    output_path = os.path.join(SCRIPT_DIR, "minervini_report.html")
    generate_html(results, output_path)

    elapsed = time.time() - start_time
    print(f"\n⏱️  総処理時間: {elapsed:.1f}秒")
    print(f"📄 レポートを開いてください: {output_path}")

if __name__ == "__main__":
    main()
