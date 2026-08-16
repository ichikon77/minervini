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
import re

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────
# 設定
# ─────────────────────────────────────────
RS_THRESHOLD = 70
PRICE_HISTORY_DAYS = 400
BATCH_SIZE = 50
MA200_TREND_DAYS = 20
OP_MARGIN_MIN = 15.0      # 営業利益率の最低閾値 (%)

VCP_LOOKBACK_DAYS       = 252
VCP_MIN_CONTRACTION_PCT = 5.0
VCP_MAX_STAGES          = 6

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
        return [c + ".T" for c in codes if re.match(r'^[0-9]{3}[0-9A-Z]$', c)]

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


def get_universe():
    print("[1/5] ユニバース取得中...")
    topix = get_topix_tickers()
    print("  合計（価格取得）: " + str(len(topix)) + "銘柄")
    return topix

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
                    df = raw.copy()
                    if hasattr(df.columns, "levels"):
                        df = df.xs(ticker, axis=1, level=0) if ticker in df.columns.get_level_values(0) else df
                    df = df.dropna(subset=["Close"])
                    if len(df) >= 210:
                        all_data[ticker] = df
            else:
                top_level = raw.columns.get_level_values(0).unique().tolist() if hasattr(raw.columns, "levels") else []
                for ticker in batch:
                    try:
                        if hasattr(raw.columns, "levels"):
                            if ticker not in top_level:
                                continue
                            df = raw[ticker].dropna(subset=["Close"])
                        else:
                            df = raw.dropna(subset=["Close"])
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
# RS レーティング計算（TOPIXプライム全銘柄ベース）
# ─────────────────────────────────────────
def calc_rs_rating(all_data):
    print("[3/5] RSレーティング計算中（TOPIXプライム全銘柄ベース）...")
    scores = {}
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

    print("  RS計算完了: " + str(len(rs_ratings)) + "銘柄")
    return rs_ratings

# ─────────────────────────────────────────
# 決算データ取得（営業利益率）
# ─────────────────────────────────────────
def get_financials(ticker):
    try:
        t = yf.Ticker(ticker)
        fin = t.financials
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
# 銘柄名取得（margin_all_history.jsonの日本語名を優先、なければyfinance英語名）
# ─────────────────────────────────────────
_name_cache = {}
_jp_names = None

def _load_jp_names():
    """JPX由来の日本語銘柄名辞書 {4桁コード: 日本語名}（margin_screen.pyが蓄積）"""
    global _jp_names
    if _jp_names is None:
        _jp_names = {}
        try:
            path = os.path.join(SCRIPT_DIR, "margin_all_history.json")
            with open(path, encoding="utf-8") as f:
                _jp_names = json.load(f).get("names", {})
        except Exception:
            pass
    return _jp_names

def get_company_name(ticker):
    if ticker in _name_cache:
        return _name_cache[ticker]
    code = ticker.replace(".T", "").strip()
    jp = _load_jp_names().get(code)
    if jp:
        _name_cache[ticker] = jp[:8]
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
# トレンドテンプレート判定
# ─────────────────────────────────────────
def check_trend_template(ticker, df):
    close = df["Close"]
    if len(close) < 252:
        return False, {}

    price        = close.iloc[-1]
    ma5          = close.rolling(5).mean().iloc[-1]
    ma25         = close.rolling(25).mean().iloc[-1]
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
        "ma5":      round(float(ma5), 0),
        "ma25":     round(float(ma25), 0),
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
# 押し目・モメンタム指標計算
# ─────────────────────────────────────────
def calculate_pullback_metrics(r):
    price    = r["price"]
    high_52w = r["high_52w"]
    ma5      = r["ma5"]
    ma25     = r["ma25"]
    rs       = r["rs_rating"]

    pullback_depth = round((high_52w - price) / high_52w * 100, 1)

    if pullback_depth <= 8:
        pullback_health = "Ideal"
    elif pullback_depth <= 25:
        pullback_health = "Healthy"
    elif pullback_depth <= 30:
        pullback_health = "Caution"
    else:
        pullback_health = "Danger"

    if price > ma25 and ma25 > ma5:
        short_term_momentum = "Strong"
    elif price > ma5:
        short_term_momentum = "Recovering"
    else:
        short_term_momentum = "Weak"

    is_healthy = pullback_health in ("Ideal", "Healthy")
    is_rs_ok   = rs >= 75
    is_mom_ok  = short_term_momentum in ("Strong", "Recovering")

    if is_healthy and is_rs_ok and is_mom_ok:
        reversal_potential = "High"
    elif pullback_health == "Danger":
        reversal_potential = "Low"
    else:
        reversal_potential = "Medium"

    parts = []
    if pullback_health == "Ideal":
        parts.append("高値圏で推移中")
    elif pullback_health == "Healthy":
        parts.append("健全な押し目")
    elif pullback_health == "Caution":
        parts.append("やや深い押し目")
    else:
        parts.append("深押し注意")

    if short_term_momentum == "Strong":
        parts.append("短期MA良好")
    elif short_term_momentum == "Recovering":
        parts.append("短期MA回復中")
    else:
        parts.append("短期MA弱め注意")

    if rs >= 90:
        parts.append("RS超強力")
    elif rs >= 80:
        parts.append("RS強い")

    return {
        "pullback_depth_pct":  pullback_depth,
        "pullback_health":     pullback_health,
        "short_term_momentum": short_term_momentum,
        "reversal_potential":  reversal_potential,
        "comments":            " / ".join(parts),
    }

# ─────────────────────────────────────────
# VCPパターン検出
# ─────────────────────────────────────────
def detect_vcp(df):
    close  = df["Close"].values
    high   = df["High"].values   if "High"   in df.columns else df["Close"].values
    low    = df["Low"].values    if "Low"    in df.columns else df["Close"].values
    volume = df["Volume"].values
    n      = len(close)

    no_vcp = {
        "vcp_pattern":        False,
        "vcp_stage":          "",
        "vcp_contractions":   "",
        "vcp_highest_price":  None,
        "pivot_price":        None,
        "vol_contraction":    False,
        "oneil_breakout":     False,
        "minervini_breakout": False,
        "above_pivot":        False,
        "above_vcp_high":     False,
        "breakout_alert":     "-",
        "pivot_breakout":     False,
        "t1_start_date":      "",
        "t1_trough_date":     "",
        "t2_start_date":      "",
        "t2_trough_date":     "",
        "t3_start_date":      "",
        "t3_trough_date":     "",
    }

    if n < 60:
        return no_vcp

    lookback = min(VCP_LOOKBACK_DAYS, n)
    c   = close[-lookback:]
    h   = high[-lookback:]
    lo  = low[-lookback:]
    vol = volume[-lookback:]
    m   = len(c)

    dates = df.index
    def idx_to_date(arr_idx):
        real_idx = (n - lookback) + arr_idx
        if 0 <= real_idx < len(dates):
            d = dates[real_idx]
            try:
                return d.strftime("%m/%d")
            except Exception:
                return str(d)[:5]
        return ""

    WIN = 5
    peaks  = []
    troughs= []
    for i in range(WIN, m - WIN):
        if c[i] == max(c[i-WIN:i+WIN+1]):
            peaks.append((i, c[i], h[i]))
        if c[i] == min(c[i-WIN:i+WIN+1]):
            troughs.append((i, c[i], lo[i]))

    if len(peaks) < 1:
        return no_vcp

    ZIGZAG_SWING = 2.0

    last_peak_idx   = peaks[-1][0]   if peaks   else -1
    last_trough_idx = troughs[-1][0] if troughs else -1

    if last_peak_idx >= last_trough_idx:
        tail_scan_start = last_peak_idx
        direction       = "trough"
        extreme_price   = float(c[last_peak_idx])
        extreme_idx     = last_peak_idx
    else:
        tail_scan_start = last_trough_idx
        direction       = "peak"
        extreme_price   = float(c[last_trough_idx])
        extreme_idx     = last_trough_idx

    for j in range(tail_scan_start + 1, m):
        c_j = float(c[j])
        if direction == "trough":
            if c_j < extreme_price:
                extreme_price = c_j
                extreme_idx   = j
            elif extreme_price > 0 and c_j > extreme_price * (1 + ZIGZAG_SWING / 100):
                if not (troughs and troughs[-1][0] == extreme_idx):
                    troughs.append((extreme_idx, float(c[extreme_idx]), float(lo[extreme_idx])))
                direction     = "peak"
                extreme_price = c_j
                extreme_idx   = j
        else:
            if c_j > extreme_price:
                extreme_price = c_j
                extreme_idx   = j
            elif extreme_price > 0 and c_j < extreme_price * (1 - ZIGZAG_SWING / 100):
                if not (peaks and peaks[-1][0] == extreme_idx):
                    peaks.append((extreme_idx, float(c[extreme_idx]), float(h[extreme_idx])))
                direction     = "trough"
                extreme_price = c_j
                extreme_idx   = j

    if direction == "trough":
        if not (troughs and troughs[-1][0] == extreme_idx):
            troughs.append((extreme_idx, float(c[extreme_idx]), float(lo[extreme_idx])))

    if not peaks:
        return no_vcp

    events = sorted(peaks + troughs, key=lambda x: x[0])

    contractions = []
    last_peak = None
    for item in events:
        idx = item[0]
        is_peak   = any(p[0] == idx for p in peaks)
        is_trough = any(t[0] == idx for t in troughs)
        if is_peak:
            peak_high = item[2]
            if last_peak is None or peak_high > last_peak[1]:
                last_peak = (idx, peak_high)
        elif is_trough and last_peak is not None:
            trough_low = item[2]
            depth_pct = (last_peak[1] - trough_low) / last_peak[1] * 100
            if depth_pct >= VCP_MIN_CONTRACTION_PCT:
                contractions.append((last_peak[0], last_peak[1], idx, trough_low, round(depth_pct, 1)))
            last_peak = None

    if last_peak is not None:
        current_as_trough_depth = (last_peak[1] - lo[-1]) / last_peak[1] * 100
        if current_as_trough_depth >= VCP_MIN_CONTRACTION_PCT:
            contractions.append((last_peak[0], last_peak[1], m - 1, lo[-1], round(current_as_trough_depth, 1)))

    if not contractions:
        return no_vcp

    valid_contractions = [contractions[-1]]
    for i in range(len(contractions) - 2, -1, -1):
        prev_depth = contractions[i][4]
        curr_depth = valid_contractions[0][4]
        if prev_depth > curr_depth:
            valid_contractions.insert(0, contractions[i])
        else:
            break

    stage = min(len(valid_contractions), VCP_MAX_STAGES)
    if stage == 0:
        return no_vcp

    vcp_highest_price   = float(max(ct[1] for ct in valid_contractions))
    pivot_price         = float(valid_contractions[-1][1])
    contraction_start   = valid_contractions[0][0]
    contraction_end     = valid_contractions[-1][2]
    contraction_vol     = vol[contraction_start:contraction_end + 1]
    current_price       = float(c[-1])
    current_vol         = float(vol[-1])
    vol_ma50            = float(np.mean(vol[-50:])) if n >= 50 else float(np.mean(vol))
    avg_contraction_vol = float(np.mean(contraction_vol)) if len(contraction_vol) > 0 else vol_ma50

    if stage == 1:
        vol_contraction = avg_contraction_vol < vol_ma50
    else:
        stage_vols = []
        for pc_idx, pc_price, tr_idx, tr_price, depth in valid_contractions:
            seg_vol = vol[pc_idx:tr_idx + 1]
            stage_vols.append(float(np.mean(seg_vol)) if len(seg_vol) > 0 else 0)
        vol_contraction = all(stage_vols[i] > stage_vols[i+1] for i in range(len(stage_vols)-1))

    vcp_stage_str   = "T" + str(stage)
    contraction_str = "→".join(str(ct[4]) + "%" for ct in valid_contractions)
    t1_start_date   = idx_to_date(valid_contractions[0][0])
    t1_trough_date  = idx_to_date(valid_contractions[0][2])
    t2_start_date   = idx_to_date(valid_contractions[1][0]) if len(valid_contractions) >= 2 else ""
    t2_trough_date  = idx_to_date(valid_contractions[1][2]) if len(valid_contractions) >= 2 else ""
    t3_start_date   = idx_to_date(valid_contractions[2][0]) if len(valid_contractions) >= 3 else ""
    t3_trough_date  = idx_to_date(valid_contractions[2][2]) if len(valid_contractions) >= 3 else ""

    current_high       = float(h[-1])
    oneil_breakout     = current_vol > vol_ma50 * 1.5
    minervini_breakout = current_vol > avg_contraction_vol * 1.5
    above_pivot        = current_high > pivot_price
    above_vcp_high     = current_high > vcp_highest_price
    pivot_breakout     = current_high > pivot_price

    both_breakout   = oneil_breakout and minervini_breakout
    either_breakout = oneil_breakout or minervini_breakout
    if both_breakout:
        breakout_alert = "STRONG"
    elif either_breakout:
        breakout_alert = "WATCH"
    else:
        breakout_alert = "-"

    return {
        "vcp_pattern":        True,
        "vcp_stage":          vcp_stage_str,
        "vcp_contractions":   contraction_str,
        "vcp_highest_price":  round(vcp_highest_price, 0),
        "pivot_price":        round(pivot_price, 0),
        "vol_contraction":    vol_contraction,
        "oneil_breakout":     oneil_breakout,
        "minervini_breakout": minervini_breakout,
        "pivot_breakout":     pivot_breakout,
        "t1_start_date":      t1_start_date,
        "t1_trough_date":     t1_trough_date,
        "t2_start_date":      t2_start_date,
        "t2_trough_date":     t2_trough_date,
        "t3_start_date":      t3_start_date,
        "t3_trough_date":     t3_trough_date,
        "above_pivot":        above_pivot,
        "above_vcp_high":     above_vcp_high,
        "breakout_alert":     breakout_alert,
    }

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

        op_margin = get_financials(ticker)
        if op_margin is None or op_margin < OP_MARGIN_MIN:
            continue

        name = get_company_name(ticker)
        record = {
            "ticker": ticker,
            "name": name,
            "rs_rating": rs,
            "op_margin": op_margin,
            **details,
        }
        record.update(calculate_pullback_metrics(record))
        record.update(detect_vcp(df))
        results.append(record)

    results.sort(key=lambda x: x["rs_rating"], reverse=True)
    print("  通過銘柄: " + str(len(results)) + "銘柄")
    return results

# ─────────────────────────────────────────
# HTML生成
# ─────────────────────────────────────────
VCP_STAGE_COLORS = {
    "T1": {"bg": "#1e3a5f", "fg": "#bfdbfe"},
    "T2": {"bg": "#1e3a5f", "fg": "#bfdbfe"},
    "T3": {"bg": "#166534", "fg": "#bbf7d0"},
    "T4": {"bg": "#166534", "fg": "#bbf7d0"},
    "T5": {"bg": "#166534", "fg": "#bbf7d0"},
    "T6": {"bg": "#166534", "fg": "#bbf7d0"},
}
PULLBACK_COLORS = {
    "Ideal":   {"bg": "#166534", "fg": "#bbf7d0"},
    "Healthy": {"bg": "#1e3a5f", "fg": "#bfdbfe"},
    "Caution": {"bg": "#78350f", "fg": "#fde68a"},
    "Danger":  {"bg": "#7f1d1d", "fg": "#fecaca"},
}
REVERSAL_COLORS = {
    "High":   {"bg": "#166534", "fg": "#bbf7d0"},
    "Medium": {"bg": "#1e3a5f", "fg": "#bfdbfe"},
    "Low":    {"bg": "#7f1d1d", "fg": "#fecaca"},
}
MOMENTUM_COLORS = {
    "Strong":     {"bg": "#166534", "fg": "#bbf7d0"},
    "Recovering": {"bg": "#1e3a5f", "fg": "#bfdbfe"},
    "Weak":       {"bg": "#7f1d1d", "fg": "#fecaca"},
}

def badge(label, cmap):
    c = cmap.get(label, {"bg": "#1e293b", "fg": "#94a3b8"})
    return (
        '<span style="background:{bg};color:{fg};padding:2px 8px;'
        'border-radius:4px;font-size:0.78rem;font-weight:600;">{lbl}</span>'
    ).format(bg=c["bg"], fg=c["fg"], lbl=label)

# ─────────────────────────────────────────
# JPX 銘柄別信用取引週末残高（制度信用倍率）
# haitou_screen.pyと同じ仕組み・同じキャッシュ(jpx_margin_cache.json)を共用
# ─────────────────────────────────────────
JPX_MARGIN_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"
JPX_BASE = "https://www.jpx.co.jp"
MARGIN_CACHE = os.path.join(SCRIPT_DIR, "jpx_margin_cache.json")


def fetch_margin_ratios():
    """JPX週次PDFから全銘柄の制度信用倍率 {code: (倍率, 売残, 買残, 基準日)} を返す"""
    try:
        html = requests.get(JPX_MARGIN_PAGE, headers=HEADERS, timeout=30).text
        links = re.findall(r'href="([^"]*syumatsu(\d{8})\d{2}\.pdf)"', html)
        if not links:
            raise RuntimeError("PDFリンクが見つかりません")
        path, ymd = max(links, key=lambda x: x[1])
        date_str = ymd[:4] + "-" + ymd[4:6] + "-" + ymd[6:8]
    except Exception as e:
        print("  JPX残高ページ取得失敗（信用倍率カラムは-表示）: " + str(e))
        return {}, None

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
                    m = re.search(r"JP[A-Z0-9]{10}", nospace)
                    if not m:
                        continue
                    mc = re.search(r"(\d{4})0$", nospace[:nospace.find(m.group(0))])
                    if not mc:
                        continue
                    code = mc.group(1)
                    post = line[line.find("JP"):].replace("▲ ", "-").replace("▲", "-")
                    toks = re.findall(r"-?[\d,]+", post[12:])
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


def generate_html(results, output_path, removed_tickers=None, margin=None):
    margin = margin or {}
    date_str = "最終更新: " + datetime.today().strftime("%Y-%m-%d %H:%M")
    count = len(results)

    rows = ""
    for r in results:
        rs = r["rs_rating"]
        rs_color = "#22c55e" if rs >= 90 else ("#84cc16" if rs >= 80 else "#f59e0b")

        def c(v): return "OK" if v else "NG"

        is_new     = r.get("is_new", False)
        first_seen = r.get("first_seen", "-")
        new_badge_html = ' <span class="new-badge">NEW</span>' if is_new else ""
        row_cls    = ' class="new-row"' if is_new else ""

        code      = r["ticker"].replace(".T", "")
        yahoo_url = "https://finance.yahoo.co.jp/quote/" + r["ticker"]

        op_margin = r.get("op_margin", 0)
        op_margin_color = "#22c55e" if op_margin >= 25 else "#84cc16" if op_margin >= 15 else "#f59e0b"

        pb_badge  = badge(r["pullback_health"],     PULLBACK_COLORS)
        rev_badge = badge(r["reversal_potential"],  REVERSAL_COLORS)
        mom_badge = badge(r["short_term_momentum"], MOMENTUM_COLORS)

        vcp_stage_badge = badge(r["vcp_stage"], VCP_STAGE_COLORS) if r["vcp_pattern"] else '<span style="color:#475569">-</span>'
        pivot_str   = ("{:,.0f}".format(r["pivot_price"])) if r["pivot_price"] else "-"
        vcphigh_str = ("{:,.0f}".format(r["vcp_highest_price"])) if r["vcp_highest_price"] else "-"

        def yn(v):
            return '<span style="color:#22c55e;font-weight:bold;">Y</span>' if v else '<span style="color:#475569">N</span>'
        def bo_badge_fn(v):
            if v:
                return '<span style="background:#b45309;color:#fef3c7;padding:2px 8px;border-radius:4px;font-size:0.78rem;font-weight:700;">BO</span>'
            return '<span style="color:#475569">-</span>'

        vol_cont_yn  = yn(r.get("vol_contraction",    False))
        oneil_yn     = yn(r.get("oneil_breakout",     False))
        minervini_yn = yn(r.get("minervini_breakout", False))
        pivot_bo     = bo_badge_fn(r.get("pivot_breakout", False))

        rows += (
            "\n        <tr" + row_cls + ">"
            + '<td class="ticker sticky-col sticky-ticker"><a href="' + yahoo_url + '" target="_blank">' + code + "</a>" + new_badge_html + "</td>"
            + '<td class="name sticky-col sticky-name">' + r.get("name", "") + "</td>"
            + '<td class="sticky-col sticky-rs" style="color:' + rs_color + ';font-weight:bold;">' + str(rs) + "</td>"
            + '<td style="color:' + op_margin_color + ';">' + "{:.1f}".format(op_margin) + "%</td>"
            + "<td>" + "{:,.0f}".format(r["price"])   + "</td>"
            + margin_cell(code, margin)
            + "<td>" + "{:,.0f}".format(r["ma50"])    + "</td>"
            + "<td>" + "{:,.0f}".format(r["ma150"])   + "</td>"
            + "<td>" + "{:,.0f}".format(r["ma200"])   + "</td>"
            + "<td>" + "{:,.0f}".format(r["low_52w"])  + "</td>"
            + "<td>" + "{:,.0f}".format(r["high_52w"]) + "</td>"
            + '<td class="pct">+' + "{:.1f}".format(r["from_52w_low_pct"])  + "%</td>"
            + '<td class="pct">'  + "{:.1f}".format(r["from_52w_high_pct"]) + "%</td>"
            + "<td>" + c(r["cond1"]) + c(r["cond2"]) + c(r["cond3"]) + c(r["cond4"]) + c(r["cond5"]) + c(r["cond6"]) + c(r["cond7"]) + "</td>"
            + '<td class="first-seen">' + first_seen + "</td>"
            + '<td class="pct col-divider">' + "{:.1f}".format(r["pullback_depth_pct"]) + "%</td>"
            + "<td>" + pb_badge  + "</td>"
            + "<td>" + mom_badge + "</td>"
            + "<td>" + rev_badge + "</td>"
            + '<td style="color:#94a3b8;font-size:0.78rem;">' + r["comments"] + "</td>"
            + "<td class='col-divider'>" + vcp_stage_badge + "</td>"
            + "<td>" + pivot_bo + "</td>"
            + '<td style="color:#94a3b8;font-size:0.78rem;">' + (r.get("t1_start_date")  or "-") + "</td>"
            + '<td style="color:#64748b;font-size:0.78rem;">' + (r.get("t1_trough_date") or "-") + "</td>"
            + '<td style="color:#94a3b8;font-size:0.78rem;">' + (r.get("t2_start_date")  or "-") + "</td>"
            + '<td style="color:#64748b;font-size:0.78rem;">' + (r.get("t2_trough_date") or "-") + "</td>"
            + '<td style="color:#94a3b8;font-size:0.78rem;">' + (r.get("t3_start_date")  or "-") + "</td>"
            + '<td style="color:#64748b;font-size:0.78rem;">' + (r.get("t3_trough_date") or "-") + "</td>"
            + '<td style="color:#94a3b8;font-size:0.78rem;">' + r["vcp_contractions"] + "</td>"
            + '<td style="color:#e2e8f0;">' + pivot_str   + "</td>"
            + '<td style="color:#e2e8f0;">' + vcphigh_str + "</td>"
            + "<td>" + vol_cont_yn  + "</td>"
            + "<td>" + oneil_yn     + "</td>"
            + "<td>" + minervini_yn + "</td>"
            + "</tr>"
        )

    removed_html = ""
    if removed_tickers:
        chips = "".join(['<span class="removed-ticker">' + t.replace(".T", "") + "</span>" for t in sorted(removed_tickers)])
        removed_html = (
            '\n  <div class="removed-section">'
            + '\n    <div class="removed-title">本日リストから除外された銘柄</div>'
            + '\n    <div class="removed-list">' + chips + "</div>"
            + "\n  </div>"
        )

    html = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Japan Minervini Screening - {date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .badge {{
    display: inline-block; background: #1e40af; color: #bfdbfe;
    border-radius: 12px; padding: 2px 12px; font-size: 0.85rem; margin-left: 8px;
  }}
  .legend {{ display: flex; gap: 16px; margin-bottom: 16px; font-size: 0.8rem; color: #94a3b8; flex-wrap: wrap; }}
  .legend span {{ display: flex; align-items: center; gap: 4px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .table-wrap {{
    overflow: auto;
    max-height: calc(100vh - 180px);
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 10px 12px;
    text-align: left; font-weight: 600;
    position: sticky; top: 0; white-space: nowrap; z-index: 2;
  }}
  thead th.new-col {{ color: #a78bfa; }}
  thead th.vcp-col {{ color: #34d399; }}
  th.sticky-col {{ z-index: 3; }}
  td.sticky-col {{
    position: sticky; background: #0f172a; z-index: 1;
  }}
  tr:hover               td.sticky-col {{ background: #1e293b; }}
  .new-row               td.sticky-col {{ background: #130e0e; }}
  .new-row:hover         td.sticky-col {{ background: #1e293b; }}
  th.sticky-ticker, td.sticky-ticker {{ left: 0; min-width: 70px; }}
  th.sticky-name,   td.sticky-name   {{ left: 70px; min-width: 80px; }}
  th.sticky-rs,     td.sticky-rs     {{ left: 150px; min-width: 52px; border-right: 2px solid #334155; }}
  tbody tr:hover {{ background: #1e293b; }}
  tbody td {{ padding: 9px 12px; border-bottom: 1px solid #1e293b; white-space: nowrap; }}
  .ticker a {{ font-weight: bold; color: #60a5fa; font-size: 0.95rem; text-decoration: none; }}
  .ticker a:hover {{ text-decoration: underline; }}
  .name {{ color: #cbd5e1; font-size: 0.82rem; }}
  .pct {{ color: #94a3b8; }}
  .first-seen {{ color: #64748b; font-size: 0.8rem; }}
  .col-divider {{ border-left: 2px solid #334155 !important; }}
  .new-badge {{
    display: inline-block; background: #dc2626; color: white;
    font-size: 0.65rem; font-weight: bold; padding: 1px 6px;
    border-radius: 4px; margin-left: 6px; vertical-align: middle;
  }}
  .new-row {{ background: rgba(220,38,38,0.08) !important; }}
  .new-row:hover {{ background: rgba(220,38,38,0.15) !important; }}
  .cond-legend {{ margin-top: 20px; font-size: 0.78rem; color: #64748b; line-height: 1.8; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .updated {{ text-align: right; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .removed-section {{ margin-top: 28px; border-top: 1px solid #1e293b; padding-top: 16px; }}
  .removed-title {{ font-size: 0.85rem; color: #94a3b8; margin-bottom: 10px; font-weight: 600; }}
  .removed-list {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .removed-ticker {{
    background: #1e293b; color: #f87171; border: 1px solid #374151;
    border-radius: 6px; padding: 3px 10px; font-size: 0.85rem; font-weight: bold;
  }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="map.html" style="border-color:#94a3b8">デッキの見方</a>
    <a href="calendar.html" style="border-color:#94a3b8">イベント予定</a>
    <a href="cpi.html" style="border-color:#7c3aed">米インフレと雇用</a>
    <a href="fedwatch.html" style="border-color:#7c3aed">FRB利上げ確率</a>
    <a href="totan.html" style="border-color:#7c3aed">日銀利上げ確率</a>
    <a href="kinri.html" style="border-color:#7c3aed">金利と為替</a>
    <a href="spriron.html" style="border-color:#7c3aed">SP500理論株価</a>
    <a href="riron.html" style="border-color:#7c3aed">日経理論株価</a>
    <a href="gaikoku.html" style="border-color:#2563eb">海外投資家</a>
    <a href="saitei.html" style="border-color:#2563eb">裁定取引</a>
    <a href="shutai.html" style="border-color:#2563eb">投資主体別</a>
    <a href="shinyou.html" style="border-color:#d97706">信用評価率</a>
    <a href="touraku.html" style="border-color:#d97706">騰落レシオ</a>
    <a href="karauri.html" style="border-color:#d97706">空売り比率</a>
    <a href="vix.html" style="border-color:#d97706">VIX温度計</a>
    <a href="sns.html" style="border-color:#d97706">SNS恐怖温度計</a>
    <a href="flow.html" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" class="active" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
    <a href="insider.html" style="border-color:#db2777">インサイダー売買</a>
    <a href="margin.html" style="border-color:#db2777">銘柄チェッカー</a>
    <a href="buffett.html" style="border-color:#db2777">バフェット</a>
    <a href="cramer.html" style="border-color:#db2777">クレイマー</a>
    <a href="kijitsu.html" style="border-color:#db2777">信用期日</a>
    <a href="kijitsu_us.html" style="border-color:#db2777">信用期日(US)</a>
    <a href="fx_corr.html" style="border-color:#db2777">円安/円高相関</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>Japan Minervini Screening <span class="badge">{count} passed</span></h1>
  <p class="subtitle">{date} | TOPIXプライム | RS >= {rs_th}（TOPIXプライム全銘柄ベース）</p>
  <div class="legend">
    <span><span class="dot" style="background:#22c55e"></span>RS 90+</span>
    <span><span class="dot" style="background:#84cc16"></span>RS 80-89</span>
    <span><span class="dot" style="background:#f59e0b"></span>RS 70-79</span>
    <span>|</span>
    <span><span class="dot" style="background:#166534"></span>Ideal/High/Strong</span>
    <span><span class="dot" style="background:#1e3a5f"></span>Healthy/Medium/Recovering</span>
    <span><span class="dot" style="background:#78350f"></span>Caution</span>
    <span><span class="dot" style="background:#7f1d1d"></span>Danger/Low/Weak</span>
  </div>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th class="sticky-col sticky-ticker">コード</th>
        <th class="sticky-col sticky-name">銘柄名</th>
        <th class="sticky-col sticky-rs">RS</th>
        <th>営業利益率</th>
        <th>株価</th><th>制度信用倍率</th><th>MA50</th><th>MA150</th><th>MA200</th>
        <th>52W Low</th><th>52W High</th><th>vs Low</th><th>vs High</th>
        <th>Cond 1-7</th><th>First Seen</th>
        <th class="new-col col-divider">Pullback%</th>
        <th class="new-col">Pullback Health</th>
        <th class="new-col">Short Momentum</th>
        <th class="new-col">Reversal Potential</th>
        <th class="new-col">Comments</th>
        <th class="vcp-col col-divider">Stage</th>
        <th class="vcp-col">Pivot BO</th>
        <th class="vcp-col">T1 Start</th>
        <th class="vcp-col">T1 Trough</th>
        <th class="vcp-col">T2 Start</th>
        <th class="vcp-col">T2 Trough</th>
        <th class="vcp-col">T3 Start</th>
        <th class="vcp-col">T3 Trough</th>
        <th class="vcp-col">Contractions</th>
        <th class="vcp-col">Pivot Price</th>
        <th class="vcp-col">VCP High</th>
        <th class="vcp-col">Vol Cont</th>
        <th class="vcp-col">O'Neil BO</th>
        <th class="vcp-col">Minervini BO</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
  <div class="cond-legend">
    Cond: 1=Price&gt;MA150&amp;MA200 2=MA150&gt;MA200 3=MA200 uptrend 4=MA50&gt;MA150&amp;MA200 5=Price&gt;MA50 6=+30% from 52W Low 7=within 25% of 52W High<br>
    Pullback% = 52週高値からの下落率 | Pullback Health: Ideal(≤8%) Healthy(≤25%) Caution(≤30%) Danger(30%超)<br>
    Short Momentum: Strong(価格&gt;MA25&gt;MA5) Recovering(価格&gt;MA5) Weak(価格&lt;MA5)<br>
    VCP: Stage=収縮回数(T1-T6) | Vol Cont: T1=収縮期出来高&lt;50日平均, T2+=各収縮期が単調減少<br>
    O'Neil BO: 当日出来高&gt;50日平均×1.5 | Minervini BO: 当日出来高&gt;収縮期間平均×1.5
  </div>
  <p class="updated">Generated: {ts}</p>
  {removed}
</body>
</html>""".format(
        date=date_str,
        count=count,
        rs_th=RS_THRESHOLD,
        rows=rows,
        ts=datetime.now().strftime("%Y-%m-%d %H:%M"),
        removed=removed_html,
    )

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
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)  # 他スクリプトとのgit競合・放置ロック対策
    print("[5/5] GitHub Pagesに公開中...")
    today = datetime.today().strftime("%Y-%m-%d")
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", "jpminervini.html"], check=True)
    result = subprocess.run(["git", "-C", SCRIPT_DIR, "commit", "-m", "update jpminervini report " + today], capture_output=True)
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

    topix_tickers = get_universe()
    if not topix_tickers:
        print("ERROR: ユニバース取得失敗")
        return

    all_data   = download_data(topix_tickers)
    rs_ratings = calc_rs_rating(all_data)
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

    margin, _margin_date = fetch_margin_ratios()

    output_path = os.path.join(SCRIPT_DIR, "jpminervini.html")
    generate_html(results, output_path, removed_tickers=removed_tickers, margin=margin)

    tv_path = os.path.join(SCRIPT_DIR, "txt", "Japan Minervini.txt")
    generate_tradingview_list(results, tv_path)

    push_to_github()

    elapsed = time.time() - start_time
    print("=" * 50)
    print("  完了: " + str(round(elapsed, 1)) + "秒")
    print("  URL: https://ichikon77.github.io/minervini/jpminervini.html")
    print("=" * 50)

if __name__ == "__main__":
    main()
