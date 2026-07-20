"""
Japan Prime Dividend Screener
==============================
条件: 配当利回り3%以上 かつ PBRが過去1年以内の最低値+10%を下回る
対象: 東証プライム全銘柄
出力: HTMLレポート (haitou.html) + GitHub Pages自動push
"""

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import json
import subprocess
from datetime import datetime, timedelta
import warnings
import time
import os
import io

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────
# 設定
# ─────────────────────────────────────────
DIVIDEND_YIELD_MIN = 3.0        # 配当利回り閾値 (%)
MARKET_CAP_MIN_OKUYEN = 500     # 時価総額フィルター（億円）
PBR_THRESHOLD_RATIO = 0.25      # 閾値係数: (2年最高-最低) × ratio + 最低
PRICE_HISTORY_DAYS = 400        # 取得日数（価格データ用バッファ込み）
PBR_HISTORY_YEARS = 2           # PBR最低値の参照期間（年）
BATCH_SIZE = 50                 # yfinanceバッチサイズ

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(SCRIPT_DIR, "haitou_history.json")
PREV_FILE    = os.path.join(SCRIPT_DIR, "haitou_prev.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36"
}

# ─────────────────────────────────────────
# ユニバース取得（日経225 + TOPIX）
# ─────────────────────────────────────────
def get_universe():
    print("[1/5] ユニバース取得中（TOPIXプライム）...")
    topix = get_topix_from_jpx()
    print("  合計: " + str(len(topix)) + "銘柄")
    return topix

def get_nikkei225():
    """Wikipediaから日経225銘柄を取得（コード分布で妥当性を検証）"""
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
                # 妥当性チェック: 本物の株コードは千の位が複数にまたがる
                # (例: 1332, 4063, 6758, 9984 など)
                # 西暦年のようなデータは 1900-2030 に集中するためスコアが低くなる
                codes_int = [int(c) for c in candidates]
                thousands = set(c // 1000 for c in codes_int)
                score = len(thousands)
                if score > best_score:
                    best_score = score
                    best_tickers = [c + ".T" for c in candidates]

        if best_tickers and best_score >= 3:
            print("  日経225: " + str(len(best_tickers)) + "銘柄")
            return best_tickers
        print("  日経225: Wikipedia取得失敗、JPXにフォールバック")
        return []
    except Exception as e:
        print("  日経225取得エラー: " + str(e))
        return []

def get_topix_from_jpx():
    """JPX公式ファイルからTOPIX（プライム市場）銘柄を取得"""
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
        # openpyxl を優先（xlrd より互換性が高い）
        try:
            df = pd.read_excel(io.BytesIO(resp.content), engine="openpyxl")
        except Exception:
            df = pd.read_excel(io.BytesIO(resp.content), engine="xlrd")
        tickers = parse_jpx_df(df)
        print("  TOPIX(プライム): " + str(len(tickers)) + "銘柄")
        return tickers
    except Exception as e:
        print("  TOPIX取得エラー: " + str(e))
        return []

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
                        if len(df) >= 50:
                            all_data[ticker] = df
                    except Exception:
                        pass
        except Exception as e:
            print("\n  バッチエラー: " + str(e))
        time.sleep(0.5)

    print("\n  データ取得完了: " + str(len(all_data)) + "銘柄")
    return all_data

# ─────────────────────────────────────────
# ファンダメンタルデータ取得
# ─────────────────────────────────────────
def get_fundamental(ticker):
    """yfinance で配当利回り・PBR・銘柄名・時価総額を取得"""
    try:
        info = yf.Ticker(ticker).info
        dividend_yield = info.get("dividendYield", None)
        pbr = info.get("priceToBook", None)
        if dividend_yield is not None:
            # 0.1以下なら小数（例: 0.0598）→ ×100して%に変換
            # 0.1より大きければすでに%単位（例: 0.94 = 0.94%）→ そのまま
            if dividend_yield <= 0.1:
                dividend_yield = dividend_yield * 100
        # 銘柄名: shortNameを優先、なければlongName、先頭6文字
        name = info.get("shortName", "") or info.get("longName", "") or ""
        name = name[:6]
        # 時価総額（円）
        market_cap = info.get("marketCap", None)
        return dividend_yield, pbr, name, market_cap
    except Exception:
        return None, None, "", None

# ─────────────────────────────────────────
# PBR 過去1年最低値の計算
# ─────────────────────────────────────────
def get_pbr_history_minmax(ticker, current_pbr):
    """
    yfinance の quarterly balance sheet から BPS を計算し、
    過去2年間の株価÷BPS で PBR 推移を求め最低値・最高値を返す。
    BPS が取れない場合は current_pbr をそのまま使う。
    """
    try:
        t = yf.Ticker(ticker)
        bs = t.quarterly_balance_sheet
        if bs is None or bs.empty:
            return current_pbr, current_pbr

        # BPS = 株主資本 / 発行済み株式数
        equity_rows = [r for r in bs.index if "Stockholders" in str(r) or "stockholders" in str(r) or "Equity" in str(r)]
        if not equity_rows:
            return current_pbr, current_pbr
        equity = bs.loc[equity_rows[0]]  # 直近

        shares_info = t.info.get("sharesOutstanding", None)
        if not shares_info or shares_info == 0:
            return current_pbr, current_pbr

        bps = float(equity.iloc[0]) / shares_info

        # 過去2年の株価を取得して PBR 時系列を作成
        hist = t.history(period=str(PBR_HISTORY_YEARS) + "y")
        if hist.empty:
            return current_pbr, current_pbr

        pbr_series = hist["Close"] / bps
        pbr_min = float(pbr_series.min())
        pbr_max = float(pbr_series.max())
        if pbr_min <= 0:
            return current_pbr, current_pbr
        return pbr_min, pbr_max
    except Exception:
        return current_pbr, current_pbr

# ─────────────────────────────────────────
# スクリーニング実行
# ─────────────────────────────────────────
def run_screen(all_data):
    print("[3/5] ファンダメンタル取得＆スクリーニング中...")
    results = []
    total = len(all_data)

    for idx, (ticker, df) in enumerate(all_data.items()):
        print("  " + str(idx+1) + "/" + str(total) + " " + ticker + "          ", end="\r")

        div_yield, pbr, name, market_cap = get_fundamental(ticker)

        # 配当利回り3%以上チェック
        if div_yield is None or div_yield < DIVIDEND_YIELD_MIN:
            continue
        if pbr is None or pbr <= 0:
            continue

        # PBR 過去2年最低値・最高値を取得
        pbr_1y_min, pbr_1y_max = get_pbr_history_minmax(ticker, pbr)

        # 閾値: (2年最高PBR - 2年最低PBR) × PBR_THRESHOLD_RATIO + 2年最低PBR
        pbr_threshold = (pbr_1y_max - pbr_1y_min) * PBR_THRESHOLD_RATIO + pbr_1y_min
        if pbr >= pbr_threshold:
            continue

        # 株価情報
        price = float(df["Close"].iloc[-1])

        # 時価総額（億円）
        market_cap_oku = round(market_cap / 1e8) if market_cap else None

        # 時価総額フィルター
        if market_cap_oku is None or market_cap_oku < MARKET_CAP_MIN_OKUYEN:
            continue

        pbr_range = pbr_1y_max - pbr_1y_min
        zone_pct = round((pbr - pbr_1y_min) / pbr_range * 100, 1) if pbr_range > 0 else None

        results.append({
            "ticker": ticker,
            "name": name,
            "market_cap_oku": market_cap_oku,
            "price": price,
            "dividend_yield": round(div_yield, 2),
            "pbr": round(pbr, 2),
            "pbr_1y_min": round(pbr_1y_min, 2),
            "pbr_1y_max": round(pbr_1y_max, 2),
            "pbr_threshold": round(pbr_threshold, 2),
            "zone_pct": zone_pct,
        })

        time.sleep(0.2)  # API負荷軽減

    results.sort(key=lambda x: x["dividend_yield"], reverse=True)
    print("\n  通過銘柄: " + str(len(results)) + "銘柄")
    return results

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
# 銘柄名取得（yfinance shortName）
# ─────────────────────────────────────────
_name_cache = {}
def get_company_name(ticker):
    if ticker in _name_cache:
        return _name_cache[ticker]
    try:
        info = yf.Ticker(ticker).info
        name = info.get("shortName", "") or info.get("longName", "") or ticker
        # 末尾の ".T" や英語表記を短縮
        _name_cache[ticker] = name[:20]
        return _name_cache[ticker]
    except Exception:
        return ticker

# ─────────────────────────────────────────
# JPX 銘柄別信用取引週末残高（制度信用倍率）
# ─────────────────────────────────────────
JPX_MARGIN_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"
JPX_BASE = "https://www.jpx.co.jp"
MARGIN_CACHE = os.path.join(SCRIPT_DIR, "jpx_margin_cache.json")


def fetch_margin_ratios():
    """JPX週次PDFから全銘柄の制度信用倍率 {code: (倍率, 売残, 買残, 基準日)} を返す。

    PDFは週1更新なので、基準日ごとにJSONへキャッシュして再パースを避ける。
    行形式: ... 新証券コード5桁 ISIN(JP+10桁) 数値12個
    （売残計,前週比,買残計,前週比,一般売,前週比,制度売,前週比,一般買,前週比,制度買,前週比）
    """
    import re as _re
    try:
        html = requests.get(JPX_MARGIN_PAGE, headers=HEADERS, timeout=30).text
        links = _re.findall(r'href="([^"]*syumatsu(\d{8})\d{2}\.pdf)"', html)
        if not links:
            raise RuntimeError("PDFリンクが見つかりません")
        path, ymd = max(links, key=lambda x: x[1])  # 最新週
        date_str = ymd[:4] + "-" + ymd[4:6] + "-" + ymd[6:8]
    except Exception as e:
        print("  JPX残高ページ取得失敗（信用倍率カラムは-表示）: " + str(e))
        return {}, None

    # キャッシュ確認
    if os.path.exists(MARGIN_CACHE):
        try:
            with open(MARGIN_CACHE, encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("date") == date_str:
                print("  制度信用倍率: キャッシュ利用 (" + date_str + ", " + str(len(cache["data"])) + "銘柄)")
                return {k: tuple(v) for k, v in cache["data"].items()}, date_str
        except Exception:
            pass

    try:
        import io as _io
        import tempfile
        import pdfplumber
        r = requests.get(JPX_BASE + path, headers=HEADERS, timeout=90)
        r.raise_for_status()
        tmp = os.path.join(tempfile.gettempdir(), "_jpx_margin_tmp.pdf")
        with open(tmp, "wb") as f:
            f.write(r.content)
        out = {}
        with pdfplumber.open(tmp) as pdf:
            for page in pdf.pages:
                txt = page.extract_text() or ""
                for line in txt.splitlines():
                    nospace = line.replace(" ", "")
                    m = _re.search(r"JP[A-Z0-9]{10}", nospace)
                    if not m:
                        continue
                    mc = _re.search(r"(\d{4})0$", nospace[:nospace.find(m.group(0))])
                    if not mc:
                        continue
                    code = mc.group(1)
                    post = line[line.find("JP"):].replace("▲ ", "-").replace("▲", "-")
                    toks = _re.findall(r"-?[\d,]+", post[12:])
                    if len(toks) >= 12:
                        try:
                            sell = int(toks[6].replace(",", ""))
                            buy = int(toks[10].replace(",", ""))
                        except ValueError:
                            continue
                        ratio = round(buy / sell, 2) if sell > 0 else None
                        out[code] = (ratio, sell, buy, date_str)
        try:
            os.remove(tmp)
        except OSError:
            pass
        print("  制度信用倍率: " + date_str + "分 " + str(len(out)) + "銘柄をパース")
        with open(MARGIN_CACHE, "w", encoding="utf-8") as f:
            json.dump({"date": date_str, "data": out}, f, ensure_ascii=False)
        return out, date_str
    except Exception as e:
        print("  JPX残高PDFのパース失敗（信用倍率カラムは-表示）: " + str(e))
        return {}, None


def margin_cell(code, margin):
    """制度信用倍率のセル。<1=水色（踏み上げ期待）/ >=20=赤（買い過熱）"""
    info = margin.get(code)
    if not info or info[0] is None:
        return "<td>-</td>"
    ratio, sell, buy, d = info
    style = ""
    if ratio < 1.0:
        style = ' style="background:rgba(56,189,248,0.28); color:#7dd3fc; font-weight:bold;"'
    elif ratio >= 20.0:
        style = ' style="background:rgba(220,38,38,0.28); color:#fca5a5; font-weight:bold;"'
    tip = ' title="制度買残 ' + "{:,}".format(buy) + ' / 制度売残 ' + "{:,}".format(sell) + ' (' + d + '申込時点)"'
    return "<td" + style + tip + ">" + "{:.2f}".format(ratio) + "x</td>"


# ─────────────────────────────────────────
# HTMLレポート生成
# ─────────────────────────────────────────
def generate_html(results, output_path, removed_tickers=None, margin=None):
    margin = margin or {}
    date_str = "最終更新: " + datetime.today().strftime("%Y-%m-%d %H:%M")
    count = len(results)

    rows = ""
    for r in results:
        div = r["dividend_yield"]
        if div >= 5.0:
            div_color = "#22c55e"
        elif div >= 4.0:
            div_color = "#84cc16"
        else:
            div_color = "#f59e0b"

        is_new = r.get("is_new", False)
        first_seen = r.get("first_seen", "-")
        new_badge = ' <span class="new-badge">NEW</span>' if is_new else ""
        row_class = ' class="new-row"' if is_new else ""

        code = r["ticker"].replace(".T", "")
        yahoo_url = "https://finance.yahoo.co.jp/quote/" + r["ticker"]
        pbr_diff_pct = round((r["pbr"] / r["pbr_1y_min"] - 1) * 100, 1)

        name = r.get("name", "")
        market_cap_oku = r.get("market_cap_oku", None)
        market_cap_str = "{:,}億円".format(market_cap_oku) if market_cap_oku else "-"

        rows += """
        <tr""" + row_class + """>
          <td class="ticker"><a href=\"""" + yahoo_url + """\" target="_blank">""" + code + """</a>""" + new_badge + """</td>
          <td class="name">""" + name + """</td>
          <td style="color:#94a3b8; text-align:right;">""" + market_cap_str + """</td>
          <td style="color:""" + div_color + """; font-weight:bold;">""" + str(div) + """%</td>
          <td>""" + "{:,.0f}".format(r["price"]) + """円</td>
          """ + margin_cell(code, margin) + """
          <td>""" + str(r["pbr"]) + """x</td>
          <td>""" + str(r["pbr_1y_min"]) + """x</td>
          <td>""" + str(r["pbr_1y_max"]) + """x</td>
          <td>""" + str(r["pbr_threshold"]) + """x</td>
          <td class="pct" style="color:#a78bfa;">""" + (str(r["zone_pct"]) + "%" if r["zone_pct"] is not None else "-") + """</td>
          <td class="pct" style="color:#f87171;">""" + str(pbr_diff_pct) + """%</td>
          <td class="first-seen">""" + first_seen + """</td>
        </tr>"""

    html = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>配当スクリーニング（東証プライム） - """ + date_str + """</title>
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
    background: #065f46;
    color: #6ee7b7;
    border-radius: 12px;
    padding: 2px 12px;
    font-size: 0.85rem;
    margin-left: 8px;
  }
  .nav {
    display: flex;
    gap: 12px;
    margin-bottom: 20px;
    font-size: 0.85rem; flex-wrap: wrap; }
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
  .ticker a { font-weight: bold; color: #34d399; font-size: 0.95rem; text-decoration: none; }
  .ticker a:hover { text-decoration: underline; }
  .name { color: #cbd5e1; font-size: 0.82rem; max-width: 80px; overflow: hidden; text-overflow: ellipsis; }
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
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="map.html" style="border-color:#94a3b8">デッキの見方</a>
    <a href="calendar.html" style="border-color:#94a3b8">イベント予定</a>
    <a href="fedwatch.html" style="border-color:#7c3aed">FRB利上げ確率</a>
    <a href="cpi.html" style="border-color:#7c3aed">米インフレと雇用</a>
    <a href="totan.html" style="border-color:#7c3aed">日銀利上げ確率</a>
    <a href="kinri.html" style="border-color:#7c3aed">金利と為替</a>
    <a href="spriron.html" style="border-color:#7c3aed">SP500理論株価</a>
    <a href="riron.html" style="border-color:#7c3aed">日経理論株価</a>
    <a href="gaikoku.html" style="border-color:#2563eb">海外投資家</a>
    <a href="saitei.html" style="border-color:#2563eb">裁定取引</a>
    <a href="shutai.html" style="border-color:#2563eb">投資主体別</a>
    <a href="margin.html" style="border-color:#2563eb">銘柄別信用倍率</a>
    <a href="shinyou.html" style="border-color:#d97706">信用評価率</a>
    <a href="touraku.html" style="border-color:#d97706">騰落レシオ</a>
    <a href="karauri.html" style="border-color:#d97706">空売り比率</a>
    <a href="vix.html" style="border-color:#d97706">VIX温度計</a>
    <a href="flow.html" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" class="active" style="border-color:#db2777">日本株 (配当)</a>
  </nav>
  <h1>配当スクリーニング <span class="badge">""" + str(count) + """ passed</span></h1>
  <p class="subtitle">""" + date_str + """ | 東証プライム(TOPIX) | 配当利回り >= """ + str(DIVIDEND_YIELD_MIN) + """% かつ PBR が閾値以内 かつ 時価総額 """ + str(MARKET_CAP_MIN_OKUYEN) + """億円以上</p>
  <div class="legend">
    <span><span class="dot" style="background:#22c55e"></span>配当5%+</span>
    <span><span class="dot" style="background:#84cc16"></span>配当4-5%</span>
    <span><span class="dot" style="background:#f59e0b"></span>配当3-4%</span>
    <span style="margin-left:12px">|</span>
    <span><span class="dot" style="background:#38bdf8"></span>制度信用倍率&lt;1 踏み上げ期待</span>
    <span><span class="dot" style="background:#dc2626"></span>&ge;20 信用買い過熱</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>コード</th>
        <th>銘柄名</th>
        <th>時価総額</th>
        <th>配当利回り</th>
        <th>株価</th>
        <th>制度信用倍率</th>
        <th>現在PBR</th>
        <th>2年最低PBR</th>
        <th>2年最高PBR</th>
        <th>閾値</th>
        <th>ゾーン％</th>
        <th>最低比</th>
        <th>初回検出</th>
      </tr>
    </thead>
    <tbody>""" + rows + """</tbody>
  </table>
  <div class="cond-legend">
    条件: 配当利回り """ + str(DIVIDEND_YIELD_MIN) + """% 以上 ／ 現在PBR &lt; (2年最高PBR－2年最低PBR)×0.3＋2年最低PBR ／ 時価総額 """ + str(MARKET_CAP_MIN_OKUYEN) + """億円以上<br>
    制度信用倍率 = 制度信用買残÷売残（JPX銘柄別信用取引週末残高、週次）。1未満=売り方過多で踏み上げが起こりやすい／大きく膨らむと急落時に追証連鎖の警戒。セルにカーソルで残高内訳。
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
# TradingView用リスト生成
# ─────────────────────────────────────────
def generate_tradingview_list(results, output_path):
    """ティッカーをTradingView用テキストファイルに出力（.T除去、NEWバッジ除去）"""
    lines = []
    for r in results:
        ticker = r["ticker"].strip().replace(".T", "")
        lines.append(ticker + "\t,")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("  TradingViewリスト出力: " + output_path)

# ─────────────────────────────────────────
# GitHub Pages自動push
# ─────────────────────────────────────────
def push_to_github():
    print("[5/5] GitHub Pagesに公開中...")
    today = datetime.today().strftime("%Y-%m-%d")
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", "haitou.html"], check=True)
    result = subprocess.run(["git", "-C", SCRIPT_DIR, "commit", "-m", "update haitou report " + today], capture_output=True)
    if result.returncode != 0:
        msg = result.stdout.decode(errors="ignore") + result.stderr.decode(errors="ignore")
        if "nothing to commit" in msg:
            print("  変更なし（既にコミット済み）、pushのみ実行")
        else:
            print("  commit失敗: " + msg)
            return
    for attempt in range(1, 6):
        try:
            subprocess.run(["git", "-C", SCRIPT_DIR, "pull", "--rebase", "--autostash"], check=True)
            subprocess.run(["git", "-C", SCRIPT_DIR, "push"], check=True)
            print("  公開完了: https://ichikon77.github.io/minervini/haitou.html")
            return
        except subprocess.CalledProcessError as e:
            print("  push失敗（試行" + str(attempt) + "/5）: " + str(e))
            time.sleep(10)
    print("  push最終失敗")

# ─────────────────────────────────────────
# メイン
# ─────────────────────────────────────────
def main():
    import time as _time
    start_time = _time.time()
    print("=" * 50)
    print("  Japan Dividend Screener (Nikkei225 + TOPIX)")
    print("=" * 50)

    tickers = get_universe()
    if not tickers:
        print("ERROR: ユニバース取得失敗")
        return

    all_data = download_data(tickers)
    results = run_screen(all_data)

    print("[4/5] 履歴更新中...")
    history = load_history()
    results = update_history(results, history)
    new_count = sum(1 for r in results if r.get("is_new"))
    if new_count:
        print("  NEW: " + str(new_count) + "銘柄が新規エントリー")

    prev_tickers = load_prev_tickers()
    current_tickers = set(r["ticker"] for r in results)
    removed_tickers = prev_tickers - current_tickers
    if removed_tickers:
        print("  REMOVED: " + str(len(removed_tickers)) + "銘柄が除外 (" + ", ".join(sorted(removed_tickers)) + ")")
    save_prev_tickers(current_tickers)

    margin, margin_date = fetch_margin_ratios()

    output_path = os.path.join(SCRIPT_DIR, "haitou.html")
    generate_html(results, output_path, removed_tickers=removed_tickers, margin=margin)

    tv_path = os.path.join(SCRIPT_DIR, "txt", "Japan High Divident.txt")
    generate_tradingview_list(results, tv_path)

    push_to_github()

    elapsed = _time.time() - start_time
    print("=" * 50)
    print("  完了: " + str(round(elapsed, 1)) + "秒")
    print("  URL: https://ichikon77.github.io/minervini/haitou.html")
    print("=" * 50)

if __name__ == "__main__":
    main()
