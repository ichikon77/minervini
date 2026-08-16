"""
Minervini Trend Template Screener
==================================
条件1〜7 + RSレーティング(>=70)でS&P500+NASDAQ100を毎日スクリーニング
出力: HTMLレポート (minervini_report.html) + GitHub Pages自動push
"""

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import io
import json
import subprocess
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

# スクリプトと同じフォルダを基準にする
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(SCRIPT_DIR, "minervini_history.json")
PREV_FILE = os.path.join(SCRIPT_DIR, "minervini_prev.json")

# ─────────────────────────────────────────
# ユニバース取得
# ─────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

def get_sp500_tickers():
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        html = requests.get(url, headers=HEADERS, timeout=15).text
        tables = pd.read_html(io.StringIO(html))
        df = tables[0]
        tickers = df["Symbol"].tolist()
        tickers = [t.replace(".", "-") for t in tickers]
        return tickers
    except Exception as e:
        print("S&P500取得エラー: " + str(e))
        return []

def get_nasdaq100_tickers():
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
        print("NASDAQ100取得エラー: " + str(e))
        return []

def get_universe():
    print("[1/5] ユニバース取得中...")
    sp500 = get_sp500_tickers()
    ndq100 = get_nasdaq100_tickers()
    universe = list(set(sp500 + ndq100))
    print("  S&P500: " + str(len(sp500)) + "銘柄 / NASDAQ100: " + str(len(ndq100)) + "銘柄 / 合計: " + str(len(universe)) + "銘柄")
    return universe

# ─────────────────────────────────────────
# データ取得
# ─────────────────────────────────────────
def download_data(tickers):
    print("[2/5] 価格データ取得中（" + str(len(tickers)) + "銘柄）...")
    end_date = datetime.today()
    start_date = end_date - timedelta(days=PRICE_HISTORY_DAYS)

    all_data = {}
    batches = [tickers[i:i+BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

    for i, batch in enumerate(batches):
        print("  バッチ " + str(i+1) + "/" + str(len(batches)) + "...", end="\r")
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
                    all_data[ticker] = raw.dropna(subset=["Close"])
            else:
                for ticker in batch:
                    try:
                        df = raw[ticker].dropna(subset=["Close"])
                        if len(df) >= 210:
                            all_data[ticker] = df
                    except Exception:
                        pass
        except Exception as e:
            print("\n  バッチエラー: " + str(e))
        time.sleep(0.3)

    print("\n  データ取得完了: " + str(len(all_data)) + "銘柄")
    return all_data

# ─────────────────────────────────────────
# RS レーティング計算
# ─────────────────────────────────────────
def calc_rs_rating(all_data):
    print("[3/5] RSレーティング計算中...")
    scores = {}
    for ticker, df in all_data.items():
        try:
            close = df["Close"]
            if len(close) < 252:
                continue
            perf_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100
            perf_12m = (close.iloc[-1] / close.iloc[-252] - 1) * 100
            score = (perf_3m * 2 + perf_12m) / 3
            scores[ticker] = score
        except Exception:
            pass

    if not scores:
        return {}

    score_series = pd.Series(scores)
    rs_ratings = {}
    for ticker, score in scores.items():
        percentile = (score_series < score).sum() / len(score_series) * 98 + 1
        rs_ratings[ticker] = round(percentile, 1)

    print("  RS計算完了: " + str(len(rs_ratings)) + "銘柄")
    return rs_ratings

# ─────────────────────────────────────────
# トレンドテンプレート判定
# ─────────────────────────────────────────
def check_trend_template(ticker, df):
    close = df["Close"]
    if len(close) < 252:
        return False, {}

    price = close.iloc[-1]
    ma50  = close.rolling(50).mean().iloc[-1]
    ma150 = close.rolling(150).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    ma200_20d_ago = close.rolling(200).mean().iloc[-MA200_TREND_DAYS - 1]
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
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def load_prev_tickers():
    """前回のリスト銘柄を読み込む"""
    if os.path.exists(PREV_FILE):
        with open(PREV_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_prev_tickers(tickers):
    """今回のリスト銘柄を保存（次回の比較用）"""
    with open(PREV_FILE, "w", encoding="utf-8") as f:
        json.dump(list(tickers), f, ensure_ascii=False, indent=2)

def update_history(results, history):
    today = datetime.today().strftime("%Y-%m-%d")
    for r in results:
        ticker = r["ticker"]
        if ticker not in history:
            history[ticker] = today
            r["first_seen"] = today
            r["is_new"] = True
        else:
            r["first_seen"] = history[ticker]
            r["is_new"] = (history[ticker] == today)
    save_history(history)
    return results

# ─────────────────────────────────────────
# スクリーニング実行
# ─────────────────────────────────────────
def run_screen(all_data, rs_ratings):
    print("[4/5] スクリーニング実行中...")
    results = []

    for ticker, df in all_data.items():
        rs = rs_ratings.get(ticker, 0)
        if rs < RS_THRESHOLD:
            continue

        passed, details = check_trend_template(ticker, df)
        if passed:
            results.append({
                "ticker": ticker,
                "rs_rating": rs,
                **details,
            })

    results.sort(key=lambda x: x["rs_rating"], reverse=True)
    print("  通過銘柄: " + str(len(results)) + "銘柄")
    return results

# ─────────────────────────────────────────
# HTMLレポート生成
# ─────────────────────────────────────────
def generate_html(results, output_path, removed_tickers=None):
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
            return "OK" if v else "NG"

        is_new = r.get("is_new", False)
        first_seen = r.get("first_seen", "-")
        new_badge = ' <span class="new-badge">NEW</span>' if is_new else ""
        row_class = ' class="new-row"' if is_new else ""

        rows += """
        <tr""" + row_class + """>
          <td class="ticker">""" + r['ticker'] + new_badge + """</td>
          <td style="color:""" + rs_color + """; font-weight:bold;">""" + str(rs) + """</td>
          <td>$""" + "{:,.2f}".format(r['price']) + """</td>
          <td>$""" + "{:,.2f}".format(r['ma50']) + """</td>
          <td>$""" + "{:,.2f}".format(r['ma150']) + """</td>
          <td>$""" + "{:,.2f}".format(r['ma200']) + """</td>
          <td>$""" + "{:,.2f}".format(r['low_52w']) + """</td>
          <td>$""" + "{:,.2f}".format(r['high_52w']) + """</td>
          <td class="pct">+""" + "{:.1f}".format(r['from_52w_low_pct']) + """%</td>
          <td class="pct">""" + "{:.1f}".format(r['from_52w_high_pct']) + """%</td>
          <td>""" + c(r['cond1']) + c(r['cond2']) + c(r['cond3']) + c(r['cond4']) + c(r['cond5']) + c(r['cond6']) + c(r['cond7']) + """</td>
          <td class="first-seen">""" + first_seen + """</td>
        </tr>"""

    html = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Minervini Screening - """ + date_str + """</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    padding: 24px;
  }
  h1 { font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }
  .subtitle { color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }
  .badge {
    display: inline-block;
    background: #1e40af;
    color: #bfdbfe;
    border-radius: 12px;
    padding: 2px 12px;
    font-size: 0.85rem;
    margin-left: 8px;
  }
  .legend { display: flex; gap: 16px; margin-bottom: 16px; font-size: 0.8rem; color: #94a3b8; }
  .legend span { display: flex; align-items: center; gap: 4px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  thead th {
    background: #1e293b;
    color: #94a3b8;
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
    position: sticky;
    top: 0;
    white-space: nowrap;
  }
  tbody tr:hover { background: #1e293b; }
  tbody td { padding: 9px 12px; border-bottom: 1px solid #1e293b; white-space: nowrap; }
  .ticker { font-weight: bold; color: #60a5fa; font-size: 0.95rem; }
  .pct { color: #94a3b8; }
  .first-seen { color: #64748b; font-size: 0.8rem; }
  .new-badge {
    display: inline-block;
    background: #dc2626;
    color: white;
    font-size: 0.65rem;
    font-weight: bold;
    padding: 1px 6px;
    border-radius: 4px;
    margin-left: 6px;
    vertical-align: middle;
  }
  .new-row { background: rgba(220, 38, 38, 0.08) !important; }
  .new-row:hover { background: rgba(220, 38, 38, 0.15) !important; }
  .cond-legend { margin-top: 20px; font-size: 0.78rem; color: #64748b; line-height: 1.8; }
  .nav {
    display: flex;
    gap: 12px;
    margin-bottom: 20px;
    font-size: 0.85rem;
  }
  .nav a {
    color: #60a5fa;
    text-decoration: none;
    background: #1e293b;
    padding: 5px 14px;
    border-radius: 6px;
    border: 1px solid #334155;
  }
  .nav a:hover { background: #334155; }
  .nav a.active { background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }
  .updated { text-align: right; font-size: 0.78rem; color: #475569; margin-top: 12px; }
  .removed-section { margin-top: 28px; border-top: 1px solid #1e293b; padding-top: 16px; }
  .removed-title { font-size: 0.85rem; color: #94a3b8; margin-bottom: 10px; font-weight: 600; }
  .removed-list { display: flex; flex-wrap: wrap; gap: 8px; }
  .removed-ticker {
    background: #1e293b;
    color: #f87171;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 0.85rem;
    font-weight: bold;
  }
</style>
</head>
<body>
  <nav class="nav">
    <a href="index.html" class="active">米国株 (Minervini)</a>
    <a href="haitou.html">日本株 (配当)</a>
    <a href="jpminervini.html">日本株 (Minervini)</a>
    <a href="minervini_report_v2.html">米国株 v2 (押し目分析)</a>
  </nav>
  <h1>Minervini Trend Template Screening <span class="badge">""" + str(count) + """ passed</span></h1>
  <p class="subtitle">""" + date_str + """ | S&P500 + NASDAQ100 | RS >= """ + str(RS_THRESHOLD) + """</p>
  <div class="legend">
    <span><span class="dot" style="background:#22c55e"></span>RS 90+</span>
    <span><span class="dot" style="background:#84cc16"></span>RS 80-89</span>
    <span><span class="dot" style="background:#f59e0b"></span>RS 70-79</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>Ticker</th><th>RS</th><th>Price</th><th>MA50</th><th>MA150</th><th>MA200</th>
        <th>52W Low</th><th>52W High</th><th>vs Low</th><th>vs High</th><th>Cond 1-7</th><th>First Seen</th>
      </tr>
    </thead>
    <tbody>""" + rows + """</tbody>
  </table>
  <div class="cond-legend">
    Cond: 1=Price>MA150&MA200 2=MA150>MA200 3=MA200 uptrend 4=MA50>MA150&MA200 5=Price>MA50 6=+30% from 52W Low 7=within 25% of 52W High
  </div>
  <p class="updated">Generated: """ + datetime.now().strftime("%Y-%m-%d %H:%M") + """</p>
""" + ("""
  <div class="removed-section">
    <div class="removed-title">本日リストから除外された銘柄</div>
    <div class="removed-list">""" + "".join(['<span class="removed-ticker">' + t + '</span>' for t in sorted(removed_tickers)]) + """</div>
  </div>
""" if removed_tickers else "") + """
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    index_path = os.path.join(os.path.dirname(output_path), "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print("  レポート出力完了")

# ─────────────────────────────────────────
# GitHub Pages自動push
# ─────────────────────────────────────────
def push_to_github():
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)  # 他スクリプトとのgit競合・放置ロック対策
    print("[5/5] GitHub Pagesに公開中...")
    today = datetime.today().strftime("%Y-%m-%d")
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", "index.html", "minervini_report.html"], check=True)
    result = subprocess.run(["git", "-C", SCRIPT_DIR, "commit", "-m", "update report " + today], capture_output=True)
    if result.returncode != 0:
        msg = result.stdout.decode(errors="ignore") + result.stderr.decode(errors="ignore")
        if "nothing to commit" in msg:
            print("  変更なし（既にコミット済み）、pushのみ実行")
        else:
            print("  commit失敗: " + msg)
            return
    # pushはリトライ付き（他スクリプトとの衝突対策）
    for attempt in range(1, 6):
        try:
            subprocess.run(["git", "-C", SCRIPT_DIR, "stash"], check=False)
            subprocess.run(["git", "-C", SCRIPT_DIR, "pull", "--rebase"], check=True)
            subprocess.run(["git", "-C", SCRIPT_DIR, "stash", "pop"], check=False)
            subprocess.run(["git", "-C", SCRIPT_DIR, "push"], check=True)
            print("  公開完了: https://ichikon77.github.io/minervini/")
            return
        except subprocess.CalledProcessError as e:
            print("  push失敗（試行" + str(attempt) + "/5）: " + str(e))
            time.sleep(10)
    print("  push最終失敗")

# ─────────────────────────────────────────
# メイン
# ─────────────────────────────────────────
def main():
    start_time = time.time()
    print("=" * 50)
    print("  Minervini Trend Template Screener")
    print("=" * 50)

    tickers = get_universe()
    if not tickers:
        print("ERROR: ユニバース取得失敗")
        return

    all_data = download_data(tickers)
    rs_ratings = calc_rs_rating(all_data)
    results = run_screen(all_data, rs_ratings)

    history = load_history()
    results = update_history(results, history)
    new_count = sum(1 for r in results if r.get("is_new"))
    if new_count:
        print("  NEW: " + str(new_count) + "銘柄が新規エントリー")

    # 前回リストとの差分（除外銘柄）を計算
    prev_tickers = load_prev_tickers()
    current_tickers = set(r["ticker"] for r in results)
    removed_tickers = prev_tickers - current_tickers
    if removed_tickers:
        print("  REMOVED: " + str(len(removed_tickers)) + "銘柄が除外 (" + ", ".join(sorted(removed_tickers)) + ")")
    save_prev_tickers(current_tickers)

    output_path = os.path.join(SCRIPT_DIR, "minervini_report.html")
    generate_html(results, output_path, removed_tickers=removed_tickers)

    push_to_github()

    elapsed = time.time() - start_time
    print("=" * 50)
    print("  完了: " + str(round(elapsed, 1)) + "秒")
    print("  URL: https://ichikon77.github.io/minervini/")
    print("=" * 50)

if __name__ == "__main__":
    main()
