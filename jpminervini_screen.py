"""
Japan Minervini Trend Template Screener
========================================
条件1〜7 + RSレーティング(>=70)でTOPIXプライムをスクリーニング
RSレーティングは日経225内での相対ランク
出力: HTMLレポート (jpminervini.html) + GitHub Pages自動push
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
RS_THRESHOLD = 70
PRICE_HISTORY_DAYS = 400
BATCH_SIZE = 50
MA200_TREND_DAYS = 20
OP_MARGIN_MIN = 15.0      # 営業利益率の最低閾値 (%)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(SCRIPT_DIR, "jpminervini_history.json")
PREV_FILE    = os.path.join(SCRIPT_DIR, "jpminervini_prev.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36"
}

# ─────────────────────────────────────────
# ユニバース取得
# ─────────────────────────────────────────
def get_topix_tickers():
    """JPX公式ファイルからTOPIX（プライム）銘柄を取得"""
    url_xls = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

    def parse_jpx_df(df):
        df.columns = df.columns.str.strip()
        market_col = next((c for c in df.columns if "市場" in str(c)), None)
        code_col   = next((c for c in df.columns if "コード" in str(c)), None)
        if market_col is None or code_col is None:
            raise ValueError("列が見つかりません: " + str(df.columns.tolist()))
        prime_df = df[df[market_col].astype(str).str.contains("プライム", na=False)]
        codes = prime_df[code_col].astype(str).str.zfill(4).tolist()
        return [c + ".T" for c in codes if c.isdigit() and len(c) == 4]

    try:
        resp = requests.get(url_xls, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        try:
            df = pd.read_excel(io.BytesIO(resp.content), engine="openpyxl")
        except Exception:
            df = pd.read_excel(io.BytesIO(resp.content), engine="xlrd")
        tickers = parse_jpx_df(df)
        print("  TOPIXプライム: " + str(len(tickers)) + "銘柄")
        return tickers
    except Exception as e:
        print("  TOPIX取得エラー: " + str(e))
        return []

def get_nikkei225_tickers():
    """Wikipediaから日経225銘柄を取得（RSレーティング計算用）"""
    try:
        url = "https://en.wikipedia.org/wiki/Nikkei_225"
        html = requests.get(url, headers=HEADERS, timeout=15).text
        tables = pd.read_html(io.StringIO(html))
        best_tickers = []
        best_score = 0
        for table in tables:
            for col in table.columns:
                vals = table[col].dropna().astype(str).tolist()
                candidates = [v.strip() for v in vals if v.strip().isdigit() and len(v.strip()) == 4]
                if len(candidates) < 50:
                    continue
                codes_int = [int(c) for c in candidates]
                thousands = set(c // 1000 for c in codes_int)
                score = len(thousands)
                if score > best_score:
                    best_score = score
                    best_tickers = [c + ".T" for c in candidates]
        if best_tickers and best_score >= 3:
            print("  日経225: " + str(len(best_tickers)) + "銘柄（RS計算用）")
            return best_tickers
        return []
    except Exception as e:
        print("  日経225取得エラー: " + str(e))
        return []

def get_universe():
    print("[1/5] ユニバース取得中...")
    topix   = get_topix_tickers()
    nikkei  = get_nikkei225_tickers()
    # RSレーティング計算用に日経225も含めたユニバースで価格取得
    rs_universe = list(set(topix + nikkei))
    print("  合計（価格取得）: " + str(len(rs_universe)) + "銘柄")
    return topix, rs_universe

# ─────────────────────────────────────────
# 価格データ取得
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
                    all_data[ticker] = raw
            else:
                for ticker in batch:
                    try:
                        df = raw[ticker].dropna(how="all")
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
# RS レーティング計算（日経225ベース）
# ─────────────────────────────────────────
def calc_rs_rating(all_data, nikkei_tickers):
    print("[3/5] RSレーティング計算中（日経225ベース）...")
    scores = {}
    # 日経225に含まれる銘柄のみでスコアを計算
    for ticker, df in all_data.items():
        if ticker not in nikkei_tickers:
            continue
        try:
            close = df["Close"]
            if len(close) < 252:
                continue
            perf_3m  = (close.iloc[-1] / close.iloc[-63]  - 1) * 100
            perf_12m = (close.iloc[-1] / close.iloc[-252] - 1) * 100
            score = (perf_3m * 2 + perf_12m) / 3
            scores[ticker] = score
        except Exception:
            pass

    if not scores:
        print("  日経225データ不足、全銘柄ベースにフォールバック")
        for ticker, df in all_data.items():
            try:
                close = df["Close"]
                if len(close) < 252:
                    continue
                perf_3m  = (close.iloc[-1] / close.iloc[-63]  - 1) * 100
                perf_12m = (close.iloc[-1] / close.iloc[-252] - 1) * 100
                scores[ticker] = (perf_3m * 2 + perf_12m) / 3
            except Exception:
                pass

    score_series = pd.Series(scores)
    rs_ratings = {}
    for ticker, score in scores.items():
        percentile = (score_series < score).sum() / len(score_series) * 98 + 1
        rs_ratings[ticker] = round(float(percentile), 1)

    # TOPIX銘柄でも日経225のスコア分布でパーセンタイルを割り当て
    for ticker, df in all_data.items():
        if ticker in rs_ratings:
            continue
        try:
            close = df["Close"]
            if len(close) < 252:
                continue
            perf_3m  = (close.iloc[-1] / close.iloc[-63]  - 1) * 100
            perf_12m = (close.iloc[-1] / close.iloc[-252] - 1) * 100
            score = (perf_3m * 2 + perf_12m) / 3
            percentile = (score_series < score).sum() / len(score_series) * 98 + 1
            rs_ratings[ticker] = round(float(percentile), 1)
        except Exception:
            pass

    print("  RS計算完了: " + str(len(rs_ratings)) + "銘柄")
    return rs_ratings

# ─────────────────────────────────────────
# トレンドテンプレート判定
# ─────────────────────────────────────────
def check_trend_template(ticker, df):
    close = df["Close"]
    if len(close) < 252:
        return False, {}

    price        = close.iloc[-1]
    ma50         = close.rolling(50).mean().iloc[-1]
    ma150        = close.rolling(150).mean().iloc[-1]
    ma200        = close.rolling(200).mean().iloc[-1]
    ma200_20d    = close.rolling(200).mean().iloc[-MA200_TREND_DAYS - 1]
    high_52w     = close.iloc[-252:].max()
    low_52w      = close.iloc[-252:].min()

    cond1 = bool(price > ma150 and price > ma200)
    cond2 = bool(ma150 > ma200)
    cond3 = bool(ma200 > ma200_20d)
    cond4 = bool(ma50 > ma150 and ma50 > ma200)
    cond5 = bool(price > ma50)
    cond6 = bool(price >= low_52w * 1.30)
    cond7 = bool(price >= high_52w * 0.75)

    passed = all([cond1, cond2, cond3, cond4, cond5, cond6, cond7])

    details = {
        "price":    round(float(price), 0),
        "ma50":     round(float(ma50), 0),
        "ma150":    round(float(ma150), 0),
        "ma200":    round(float(ma200), 0),
        "high_52w": round(float(high_52w), 0),
        "low_52w":  round(float(low_52w), 0),
        "cond1": cond1, "cond2": cond2, "cond3": cond3,
        "cond4": cond4, "cond5": cond5, "cond6": cond6, "cond7": cond7,
        "from_52w_low_pct":  round((float(price) / float(low_52w)  - 1) * 100, 1),
        "from_52w_high_pct": round((float(price) / float(high_52w) - 1) * 100, 1),
    }
    return passed, details

# ─────────────────────────────────────────
# 決算データ取得（営業利益率・前年比成長率）
# ─────────────────────────────────────────
def get_financials(ticker):
    """
    yfinanceの年次決算から直近期の営業利益率(%)を返す。
    取得できない場合は None。
    """
    try:
        t = yf.Ticker(ticker)
        fin = t.financials  # annual, 列=決算期(新→古)
        if fin is None or fin.empty:
            return None

        idx_lower = {str(i).lower(): i for i in fin.index}

        op_keys  = ["operating income", "ebit", "operating profit", "total operating income as reported"]
        rev_keys = ["total revenue", "revenue", "net revenue", "operating revenue"]

        op_row  = next((idx_lower[k] for k in op_keys  if k in idx_lower), None)
        rev_row = next((idx_lower[k] for k in rev_keys if k in idx_lower), None)

        if op_row is None or rev_row is None:
            return None

        op_latest  = float(fin.loc[op_row].iloc[0])
        rev_latest = float(fin.loc[rev_row].iloc[0])

        if rev_latest == 0:
            return None

        op_margin = (op_latest / rev_latest) * 100
        return round(op_margin, 1)
    except Exception:
        return None

# ─────────────────────────────────────────
# 銘柄名取得
# ─────────────────────────────────────────
_name_cache = {}
def get_company_name(ticker):
    if ticker in _name_cache:
        return _name_cache[ticker]
    try:
        info = yf.Ticker(ticker).info
        name = info.get("shortName", "") or info.get("longName", "") or ticker
        _name_cache[ticker] = name[:6]
        return _name_cache[ticker]
    except Exception:
        return ticker[:6]

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
    if os.path.exists(PREV_FILE):
        with open(PREV_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_prev_tickers(tickers):
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
def run_screen(all_data, rs_ratings, topix_tickers):
    print("[4/5] スクリーニング実行中...")
    results = []
    topix_set = set(topix_tickers)

    for ticker, df in all_data.items():
        if ticker not in topix_set:
            continue
        rs = rs_ratings.get(ticker, 0)
        if rs < RS_THRESHOLD:
            continue
        passed, details = check_trend_template(ticker, df)
        if not passed:
            continue

        # 決算条件チェック（年次）
        op_margin = get_financials(ticker)
        if op_margin is None or op_margin < OP_MARGIN_MIN:
            continue

        name = get_company_name(ticker)
        results.append({
            "ticker": ticker,
            "name": name,
            "rs_rating": rs,
            "op_margin": op_margin,
            **details,
        })

    results.sort(key=lambda x: x["rs_rating"], reverse=True)
    print("  通過銘柄: " + str(len(results)) + "銘柄")
    return results

# ─────────────────────────────────────────
# HTML生成
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

        def c(v): return "OK" if v else "NG"

        is_new     = r.get("is_new", False)
        first_seen = r.get("first_seen", "-")
        new_badge  = ' <span class="new-badge">NEW</span>' if is_new else ""
        row_class  = ' class="new-row"' if is_new else ""

        code      = r["ticker"].replace(".T", "")
        yahoo_url = "https://finance.yahoo.co.jp/quote/" + r["ticker"]

        op_margin = r.get("op_margin", 0)
        op_margin_color = "#22c55e" if op_margin >= 25 else "#84cc16" if op_margin >= 15 else "#f59e0b"

        rows += """
        <tr""" + row_class + """>
          <td class="ticker"><a href=\"""" + yahoo_url + """\" target="_blank">""" + code + """</a>""" + new_badge + """</td>
          <td class="name">""" + r.get("name", "") + """</td>
          <td style="color:""" + rs_color + """; font-weight:bold;">""" + str(rs) + """</td>
          <td style="color:""" + op_margin_color + """;">""" + "{:.1f}".format(op_margin) + """%</td>
          <td>""" + "{:,.0f}".format(r["price"]) + """</td>
          <td>""" + "{:,.0f}".format(r["ma50"]) + """</td>
          <td>""" + "{:,.0f}".format(r["ma150"]) + """</td>
          <td>""" + "{:,.0f}".format(r["ma200"]) + """</td>
          <td>""" + "{:,.0f}".format(r["low_52w"]) + """</td>
          <td>""" + "{:,.0f}".format(r["high_52w"]) + """</td>
          <td class="pct">+""" + "{:.1f}".format(r["from_52w_low_pct"]) + """%</td>
          <td class="pct">""" + "{:.1f}".format(r["from_52w_high_pct"]) + """%</td>
          <td>""" + c(r["cond1"]) + c(r["cond2"]) + c(r["cond3"]) + c(r["cond4"]) + c(r["cond5"]) + c(r["cond6"]) + c(r["cond7"]) + """</td>
          <td class="first-seen">""" + first_seen + """</td>
        </tr>"""

    html = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Japan Minervini Screening - """ + date_str + """</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    padding: 24px;
  }
  h1 { font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }
  .subtitle { color: #94a3b8; font-size: 0.9rem; margin-bottom: 16px; }
  .badge {
    display: inline-block;
    background: #1e40af;
    color: #bfdbfe;
    border-radius: 12px;
    padding: 2px 12px;
    font-size: 0.85rem;
    margin-left: 8px;
  }
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
  .ticker a { font-weight: bold; color: #60a5fa; font-size: 0.95rem; text-decoration: none; }
  .ticker a:hover { text-decoration: underline; }
  .name { color: #cbd5e1; font-size: 0.82rem; }
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
    <a href="index.html">米国株 (Minervini)</a>
    <a href="haitou.html">日本株 (配当)</a>
    <a href="jpminervini.html" class="active">日本株 (Minervini)</a>
  </nav>
  <h1>Japan Minervini Screening <span class="badge">""" + str(count) + """ passed</span></h1>
  <p class="subtitle">""" + date_str + """ | TOPIXプライム | RS >= """ + str(RS_THRESHOLD) + """（日経225ベース）</p>
  <div class="legend">
    <span><span class="dot" style="background:#22c55e"></span>RS 90+</span>
    <span><span class="dot" style="background:#84cc16"></span>RS 80-89</span>
    <span><span class="dot" style="background:#f59e0b"></span>RS 70-79</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>コード</th><th>銘柄名</th><th>RS</th><th>営業利益率</th><th>株価</th><th>MA50</th><th>MA150</th><th>MA200</th>
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
    <div class="removed-list">""" + "".join(['<span class="removed-ticker">' + t.replace(".T","") + '</span>' for t in sorted(removed_tickers)]) + """</div>
  </div>
""" if removed_tickers else "") + """
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print("  レポート出力完了: " + output_path)

# ─────────────────────────────────────────
# GitHub Pages自動push
# ─────────────────────────────────────────
def push_to_github():
    print("[5/5] GitHub Pagesに公開中...")
    try:
        today = datetime.today().strftime("%Y-%m-%d")
        subprocess.run(["git", "-C", SCRIPT_DIR, "add", "jpminervini.html"], check=True)
        subprocess.run(["git", "-C", SCRIPT_DIR, "commit", "-m", "update jpminervini report " + today], check=True)
    except subprocess.CalledProcessError as e:
        print("  commit失敗: " + str(e))
        return
    for attempt in range(1, 6):
        try:
            # 未ステージの変更を一時退避してからrebase
            subprocess.run(["git", "-C", SCRIPT_DIR, "stash"], check=False)
            subprocess.run(["git", "-C", SCRIPT_DIR, "pull", "--rebase"], check=True)
            subprocess.run(["git", "-C", SCRIPT_DIR, "stash", "pop"], check=False)
            subprocess.run(["git", "-C", SCRIPT_DIR, "push"], check=True)
            print("  公開完了: https://ichikon77.github.io/minervini/jpminervini.html")
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
    print("  Japan Minervini Trend Template Screener")
    print("=" * 50)

    topix_tickers, rs_universe = get_universe()
    if not topix_tickers:
        print("ERROR: ユニバース取得失敗")
        return

    all_data = download_data(rs_universe)

    nikkei_set = set(get_nikkei225_tickers())
    rs_ratings = calc_rs_rating(all_data, nikkei_set)
    results    = run_screen(all_data, rs_ratings, topix_tickers)

    history = load_history()
    results = update_history(results, history)
    new_count = sum(1 for r in results if r.get("is_new"))
    if new_count:
        print("  NEW: " + str(new_count) + "銘柄が新規エントリー")

    prev_tickers    = load_prev_tickers()
    current_tickers = set(r["ticker"] for r in results)
    removed_tickers = prev_tickers - current_tickers
    if removed_tickers:
        print("  REMOVED: " + str(len(removed_tickers)) + "銘柄が除外 (" + ", ".join(sorted(removed_tickers)) + ")")
    save_prev_tickers(current_tickers)

    output_path = os.path.join(SCRIPT_DIR, "jpminervini.html")
    generate_html(results, output_path, removed_tickers=removed_tickers)

    push_to_github()

    elapsed = time.time() - start_time
    print("=" * 50)
    print("  完了: " + str(round(elapsed, 1)) + "秒")
    print("  URL: https://ichikon77.github.io/minervini/jpminervini.html")
    print("=" * 50)

if __name__ == "__main__":
    main()
