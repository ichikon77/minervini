"""
Minervini Trend Template Screener v2
=====================================
v1からの変更点：
  - 5MA / 25MA を追加計算
  - 新カラム追加: Pullback_Depth_%, Pullback_Health, ShortTerm_Momentum,
                  Reversal_Potential, Comments
  - HTMLレポートに新カラムを色付きで表示
  - Ticker / RS 列を左固定（横スクロール時も常に表示）
  - 既存ロジックはそのまま維持
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

# -----------------------------------------
# 設定
# -----------------------------------------
RS_THRESHOLD       = 70
PRICE_HISTORY_DAYS = 400
BATCH_SIZE         = 50
MA200_TREND_DAYS   = 20

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE  = os.path.join(SCRIPT_DIR, "minervini_history.json")
PREV_FILE     = os.path.join(SCRIPT_DIR, "minervini_prev_v2.json")   # v2専用（v1と分離）
VCP_SCORE_FILE= os.path.join(SCRIPT_DIR, "minervini_vcp_scores.json") # VCPスコア前日比較用

# -----------------------------------------
# ユニバース取得
# -----------------------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

def get_sp500_tickers():
    try:
        url  = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        html = requests.get(url, headers=HEADERS, timeout=15).text
        df   = pd.read_html(io.StringIO(html))[0]
        return [t.replace(".", "-") for t in df["Symbol"].tolist()]
    except Exception as e:
        print("S&P500取得エラー: " + str(e))
        return []

def get_nasdaq100_tickers():
    try:
        url    = "https://en.wikipedia.org/wiki/Nasdaq-100"
        html   = requests.get(url, headers=HEADERS, timeout=15).text
        tables = pd.read_html(io.StringIO(html))
        for table in tables:
            cols = [str(c).lower() for c in table.columns]
            if "ticker" in cols:
                col = table.columns[cols.index("ticker")]
                return [str(t).replace(".", "-") for t in table[col].dropna().tolist()]
        return []
    except Exception as e:
        print("NASDAQ100取得エラー: " + str(e))
        return []

def get_universe():
    print("[1/5] ユニバース取得中...")
    sp500  = get_sp500_tickers()
    ndq100 = get_nasdaq100_tickers()
    universe = list(set(sp500 + ndq100))
    print("  S&P500: " + str(len(sp500)) + " / NASDAQ100: " + str(len(ndq100)) + " / 合計: " + str(len(universe)))
    return universe

# -----------------------------------------
# データ取得
# -----------------------------------------
def download_data(tickers):
    print("[2/5] 価格データ取得中（" + str(len(tickers)) + "銘柄）...")
    end_date   = datetime.today()
    start_date = end_date - timedelta(days=PRICE_HISTORY_DAYS)
    all_data   = {}
    batches    = [tickers[i:i+BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

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
                if not raw.empty:
                    all_data[batch[0]] = raw
            else:
                for ticker in batch:
                    try:
                        df = raw[ticker].dropna()
                        if len(df) >= 210:
                            all_data[ticker] = df
                    except Exception:
                        pass
        except Exception as e:
            print("\n  バッチエラー: " + str(e))
        time.sleep(0.3)

    print("\n  データ取得完了: " + str(len(all_data)) + "銘柄")
    return all_data

# -----------------------------------------
# RS レーティング計算
# -----------------------------------------
def calc_rs_rating(all_data):
    print("[3/5] RSレーティング計算中...")
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

    if not scores:
        return {}

    score_series = pd.Series(scores)
    rs_ratings   = {}
    for ticker, score in scores.items():
        percentile         = (score_series < score).sum() / len(score_series) * 98 + 1
        rs_ratings[ticker] = round(percentile, 1)

    print("  RS計算完了: " + str(len(rs_ratings)) + "銘柄")
    return rs_ratings

# -----------------------------------------
# トレンドテンプレート判定
# -----------------------------------------
def check_trend_template(ticker, df):
    close = df["Close"]
    if len(close) < 252:
        return False, {}

    price         = close.iloc[-1]
    ma5           = close.rolling(5).mean().iloc[-1]    # v2追加
    ma25          = close.rolling(25).mean().iloc[-1]   # v2追加
    ma50          = close.rolling(50).mean().iloc[-1]
    ma150         = close.rolling(150).mean().iloc[-1]
    ma200         = close.rolling(200).mean().iloc[-1]
    ma200_20d_ago = close.rolling(200).mean().iloc[-MA200_TREND_DAYS - 1]
    high_52w      = close.iloc[-252:].max()
    low_52w       = close.iloc[-252:].min()

    cond1 = bool(price > ma150 and price > ma200)
    cond2 = bool(ma150 > ma200)
    cond3 = bool(ma200 > ma200_20d_ago)
    cond4 = bool(ma50  > ma150 and ma50 > ma200)
    cond5 = bool(price > ma50)
    cond6 = bool(price >= low_52w  * 1.30)
    cond7 = bool(price >= high_52w * 0.75)

    passed = all([cond1, cond2, cond3, cond4, cond5, cond6, cond7])

    details = {
        "price":              round(float(price),    2),
        "ma5":                round(float(ma5),      2),  # v2追加
        "ma25":               round(float(ma25),     2),  # v2追加
        "ma50":               round(float(ma50),     2),
        "ma150":              round(float(ma150),    2),
        "ma200":              round(float(ma200),    2),
        "high_52w":           round(float(high_52w), 2),
        "low_52w":            round(float(low_52w),  2),
        "cond1": cond1, "cond2": cond2, "cond3": cond3,
        "cond4": cond4, "cond5": cond5, "cond6": cond6, "cond7": cond7,
        "from_52w_low_pct":  round((float(price) / float(low_52w)  - 1) * 100, 1),
        "from_52w_high_pct": round((float(price) / float(high_52w) - 1) * 100, 1),
    }
    return passed, details

# -----------------------------------------
# ★ 新機能: プルバック指標の計算
# -----------------------------------------
def calculate_pullback_metrics(r):
    """
    1件分の結果辞書 r から押し目・モメンタム関連の指標を計算して返す。

    Returns dict with keys:
      pullback_depth_pct  : 52週高値からの下落率（%）
      pullback_health     : "Ideal" / "Healthy" / "Caution" / "Danger"
      short_term_momentum : "Strong" / "Recovering" / "Weak"
      reversal_potential  : "High" / "Medium" / "Low"
      comments            : 簡易コメント文字列
    """
    price    = r["price"]
    high_52w = r["high_52w"]
    ma5      = r["ma5"]
    ma25     = r["ma25"]
    rs       = r["rs_rating"]

    # 1. Pullback_Depth_%  (52週高値からの下落率、プラス方向)
    pullback_depth = round((high_52w - price) / high_52w * 100, 1)

    # 2. Pullback_Health
    if pullback_depth <= 8:
        pullback_health = "Ideal"
    elif pullback_depth <= 25:
        pullback_health = "Healthy"
    elif pullback_depth <= 30:
        pullback_health = "Caution"
    else:
        pullback_health = "Danger"

    # 3. ShortTerm_Momentum  (MA5/MA25との関係)
    #   Strong    : 価格 > MA25 > MA5  (上昇加速)
    #   Recovering: 価格 > MA5         (MA5超えているが MA25 との順列は中間)
    #   Weak      : 価格 < MA5
    if price > ma25 and ma25 > ma5:
        short_term_momentum = "Strong"
    elif price > ma5:
        short_term_momentum = "Recovering"
    else:
        short_term_momentum = "Weak"

    # 4. Reversal_Potential
    #   High  : 健全な押し目(Ideal/Healthy) + RS>=75 + momentum Recovering以上
    #   Low   : Danger ゾーン
    #   Medium: それ以外
    is_healthy = pullback_health in ("Ideal", "Healthy")
    is_rs_ok   = rs >= 75
    is_mom_ok  = short_term_momentum in ("Strong", "Recovering")

    if is_healthy and is_rs_ok and is_mom_ok:
        reversal_potential = "High"
    elif pullback_health == "Danger":
        reversal_potential = "Low"
    else:
        reversal_potential = "Medium"

    # 5. Comments
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

# -----------------------------------------
# ★ 新機能: VCPパターン検出
# -----------------------------------------
# VCP検出パラメータ
VCP_MIN_CONTRACTION_PCT = 5.0   # 収縮として認識する最小下落率(%)
VCP_LOOKBACK_DAYS       = 252   # VCP探索対象期間（約1年）
VCP_MAX_STAGES          = 6     # T1〜T6まで対応

def detect_vcp(df):
    """
    過去VCP_LOOKBACK_DAYS日のClose/Volumeデータから VCP パターンを検出する。

    アルゴリズム概要:
      1. 直近の高値から「ピーク→トラフ→ピーク→トラフ...」という波を検出
      2. 各トラフへの下落率（収縮率）を計算
      3. 収縮率が厳密に単調減少している連続区間を「有効VCP」とみなす
         （崩れた時点でリスタート）
      4. 最大T6まで数える

    Returns dict with keys:
      vcp_pattern         : True / False
      vcp_stage           : "T1"〜"T6" or ""
      vcp_contractions    : "18.0%→11.0%→6.0%" など
      vcp_highest_price   : VCP期間全体の最高値（終値ベース）
      pivot_price         : 最後の収縮内の高値（ブレイクアウト基準）
      vol_contraction     : True / False
      oneil_breakout      : True / False  当日出来高 > 50日平均出来高 × 1.5
      minervini_breakout  : True / False  当日出来高 > 収縮期間中平均出来高 × 1.5
      above_pivot         : True / False  当日終値 > pivot_price
      above_vcp_high      : True / False  当日終値 > vcp_highest_price
      breakout_alert      : "STRONG" / "WATCH" / "-"
    """
    close  = df["Close"].values
    high   = df["High"].values   if "High"   in df.columns else df["Close"].values
    low    = df["Low"].values    if "Low"    in df.columns else df["Close"].values
    volume = df["Volume"].values
    n      = len(close)

    # デフォルト（VCPなし）の返り値
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

    # 探索対象は直近 VCP_LOOKBACK_DAYS 日
    lookback = min(VCP_LOOKBACK_DAYS, n)
    c   = close[-lookback:]
    h   = high[-lookback:]    # ピーク検出用（日中高値）
    lo  = low[-lookback:]     # トラフ検出用（日中安値）
    vol = volume[-lookback:]
    m   = len(c)

    # lookback配列インデックス → 実際の日付文字列 (MM/DD)
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

    # ── ステップ1: ローカルピーク・トラフの検出 ──────────────────
    # 検出はCloseベース（同日にピーク・トラフ両方が生じる矛盾を防ぐ）
    # ただしPivot Price = ピーク日のHigh、収縮depth = ピーク日High→トラフ日Lowで計算
    WIN = 5
    peaks  = []   # (index, close_price, high_price)
    troughs= []   # (index, close_price, low_price)
    for i in range(WIN, m - WIN):
        if c[i] == max(c[i-WIN:i+WIN+1]):
            peaks.append((i, c[i], h[i]))
        if c[i] == min(c[i-WIN:i+WIN+1]):
            troughs.append((i, c[i], lo[i]))

    if len(peaks) < 1:
        return no_vcp

    # ── ステップ1b: 末尾ジグザグ補完 ─────────────────────────────
    # WIN=5 の検出はデータ末尾のWIN日をカバーできないため、
    # 最後に検出されたピーク/トラフ以降をCloseベースのジグザグ走査で補完する。
    ZIGZAG_SWING = 2.0  # ジグザグ末尾補完のスイング閾値（%）

    last_peak_idx   = peaks[-1][0]   if peaks   else -1
    last_trough_idx = troughs[-1][0] if troughs else -1

    if last_peak_idx >= last_trough_idx:
        tail_scan_start = last_peak_idx
        direction       = "trough"
        extreme_price   = float(c[last_peak_idx])  # Close起点
        extreme_idx     = last_peak_idx
    else:
        tail_scan_start = last_trough_idx
        direction       = "peak"
        extreme_price   = float(c[last_trough_idx])  # Close起点
        extreme_idx     = last_trough_idx

    for j in range(tail_scan_start + 1, m):
        c_j = float(c[j])
        if direction == "trough":
            if c_j < extreme_price:
                extreme_price = c_j
                extreme_idx   = j
            elif extreme_price > 0 and c_j > extreme_price * (1 + ZIGZAG_SWING / 100):
                # Close から ZIGZAG_SWING % 以上バウンス → トラフ確定
                if not (troughs and troughs[-1][0] == extreme_idx):
                    troughs.append((extreme_idx, float(c[extreme_idx]), float(lo[extreme_idx])))
                direction     = "peak"
                extreme_price = c_j
                extreme_idx   = j
        else:  # direction == "peak"
            if c_j > extreme_price:
                extreme_price = c_j
                extreme_idx   = j
            elif extreme_price > 0 and c_j < extreme_price * (1 - ZIGZAG_SWING / 100):
                # Close から ZIGZAG_SWING % 以上下落 → ピーク確定
                if not (peaks and peaks[-1][0] == extreme_idx):
                    peaks.append((extreme_idx, float(c[extreme_idx]), float(h[extreme_idx])))
                direction     = "trough"
                extreme_price = c_j
                extreme_idx   = j

    # 末尾時点でトラフ探索中なら進行中トラフを仮登録（形成中の収縮も対象に含める）
    if direction == "trough":
        if not (troughs and troughs[-1][0] == extreme_idx):
            troughs.append((extreme_idx, float(c[extreme_idx]), float(lo[extreme_idx])))

    if not peaks:
        return no_vcp

    # ── ステップ2: 収縮（ピーク→トラフ）を順番に抽出 ─────────
    # Closeベース検出なので同日にピーク・トラフ両方は発生しない
    # depth計算: ピーク日のHigh → トラフ日のLow
    events = sorted(peaks + troughs, key=lambda x: x[0])

    contractions = []  # [(peak_idx, peak_high, trough_idx, trough_low, depth_pct)]
    last_peak = None
    for item in events:
        idx = item[0]
        is_peak   = any(p[0] == idx for p in peaks)
        is_trough = any(t[0] == idx for t in troughs)
        if is_peak:
            peak_high = item[2]  # High価格
            # より高いピークが来たらlast_peakを更新（低いピークは無視）
            if last_peak is None or peak_high > last_peak[1]:
                last_peak = (idx, peak_high)
        elif is_trough and last_peak is not None:
            trough_low = item[2]  # Low価格
            depth_pct = (last_peak[1] - trough_low) / last_peak[1] * 100
            if depth_pct >= VCP_MIN_CONTRACTION_PCT:
                contractions.append((last_peak[0], last_peak[1], idx, trough_low, round(depth_pct, 1)))
            last_peak = None  # ペアにしたらリセット

    # 未マッチのピークが残っていて、当日安値がそのピークより下なら「形成中の収縮」として追加
    if last_peak is not None:
        current_as_trough_depth = (last_peak[1] - lo[-1]) / last_peak[1] * 100
        if current_as_trough_depth >= VCP_MIN_CONTRACTION_PCT:
            contractions.append((last_peak[0], last_peak[1], m - 1, lo[-1], round(current_as_trough_depth, 1)))

    if not contractions:
        return no_vcp

    # ── ステップ3: 単調減少の連続区間を探す（崩れたらリスタート）─
    # 最新から遡って有効VCP区間を探す
    valid_contractions = [contractions[-1]]  # 最新の収縮から開始
    for i in range(len(contractions) - 2, -1, -1):
        prev_depth = contractions[i][4]
        curr_depth = valid_contractions[0][4]

        if prev_depth > curr_depth:
            # 収縮率が単調減少 → 連結
            valid_contractions.insert(0, contractions[i])
        else:
            # 単調減少が崩れた → リスタート
            break

    stage = min(len(valid_contractions), VCP_MAX_STAGES)
    if stage == 0:
        return no_vcp

    # ── ステップ4: 各種価格・出来高指標を計算 ───────────────
    # VCP期間全体の最高値 = 有効な収縮内の全ピークの最高値
    # （ピークが切り上がるケースに対応）
    vcp_highest_price   = float(max(ct[1] for ct in valid_contractions))

    # Pivot Price: 最後の収縮内の高値（最後の収縮のピーク価格）
    pivot_price         = float(valid_contractions[-1][1])

    # 収縮期間（有効区間全体）のインデックス範囲
    contraction_start   = valid_contractions[0][0]
    contraction_end     = valid_contractions[-1][2]  # 最後のトラフのインデックス
    contraction_vol     = vol[contraction_start:contraction_end + 1]

    # 当日（最終日）の終値・出来高
    current_price       = float(c[-1])
    current_vol         = float(vol[-1])

    # 50日平均出来高
    vol_ma50            = float(np.mean(vol[-50:])) if n >= 50 else float(np.mean(vol))

    # 収縮期間中の平均出来高
    avg_contraction_vol = float(np.mean(contraction_vol)) if len(contraction_vol) > 0 else vol_ma50

    # ── Vol_Contraction 判定 ──────────────────────────────
    if stage == 1:
        # T1のみ: 収縮期間の平均出来高 < 50日平均出来高
        vol_contraction = avg_contraction_vol < vol_ma50
    else:
        # T2以上: 各収縮区間の平均出来高が単調減少しているか
        stage_vols = []
        for pc_idx, pc_price, tr_idx, tr_price, depth in valid_contractions:
            seg_vol = vol[pc_idx:tr_idx + 1]
            stage_vols.append(float(np.mean(seg_vol)) if len(seg_vol) > 0 else 0)
        vol_contraction = all(stage_vols[i] > stage_vols[i+1] for i in range(len(stage_vols)-1))

    # ── Stage / Start / Trough Dates ─────────────────────────
    vcp_stage_str  = "T" + str(stage)
    contraction_str = "→".join(str(ct[4]) + "%" for ct in valid_contractions)
    t1_start_date   = idx_to_date(valid_contractions[0][0])
    t1_trough_date  = idx_to_date(valid_contractions[0][2])
    t2_start_date   = idx_to_date(valid_contractions[1][0]) if len(valid_contractions) >= 2 else ""
    t2_trough_date  = idx_to_date(valid_contractions[1][2]) if len(valid_contractions) >= 2 else ""
    t3_start_date   = idx_to_date(valid_contractions[2][0]) if len(valid_contractions) >= 3 else ""
    t3_trough_date  = idx_to_date(valid_contractions[2][2]) if len(valid_contractions) >= 3 else ""

    # ── ブレイクアウト判定（高値ベース）────────────────────────
    current_high        = float(h[-1])
    oneil_breakout      = current_vol > vol_ma50 * 1.5
    minervini_breakout  = current_vol > avg_contraction_vol * 1.5
    above_pivot         = current_high > pivot_price          # 当日高値がPivotを超えたか
    above_vcp_high      = current_high > vcp_highest_price    # 当日高値がVCP Highを超えたか
    pivot_breakout      = current_high > pivot_price          # Pivot Breakout: 当日高値がPivot超え

    # ── Breakout_Alert ────────────────────────────────────
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
        "vcp_highest_price":  round(vcp_highest_price, 2),
        "pivot_price":        round(pivot_price, 2),
        "vol_contraction":    vol_contraction,
        "oneil_breakout":     oneil_breakout,
        "minervini_breakout": minervini_breakout,
        "pivot_breakout":      pivot_breakout,
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

# -----------------------------------------
# VCP スコア管理（前日比較用）
# -----------------------------------------
def calc_vcp_score(r):
    """Vol_Contraction / ONeil_BO / Minervini_BO の3項目のYes数"""
    return sum([
        bool(r.get("vol_contraction",    False)),
        bool(r.get("oneil_breakout",     False)),
        bool(r.get("minervini_breakout", False)),
    ])

def load_vcp_scores():
    """前日のVCPスコアを読み込む {ticker: score}"""
    if os.path.exists(VCP_SCORE_FILE):
        with open(VCP_SCORE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_vcp_scores(results):
    """今日のVCPスコアを保存する"""
    scores = {r["ticker"]: calc_vcp_score(r) for r in results}
    with open(VCP_SCORE_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)

def attach_vcp_score_info(results, prev_scores):
    """各結果にVCPスコアとスコアアップフラグを付与する"""
    for r in results:
        score      = calc_vcp_score(r)
        prev_score = prev_scores.get(r["ticker"], 0)
        r["vcp_score"]    = score
        r["vcp_score_up"] = (score > prev_score)  # 前日よりYの数が増えた
    return results

# -----------------------------------------
# 履歴管理
# -----------------------------------------
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
            r["is_new"]     = True
        else:
            r["first_seen"] = history[ticker]
            r["is_new"]     = (history[ticker] == today)
    save_history(history)
    return results

# -----------------------------------------
# スクリーニング実行
# -----------------------------------------
def run_screen(all_data, rs_ratings):
    print("[4/5] スクリーニング実行中...")
    results = []

    for ticker, df in all_data.items():
        rs = rs_ratings.get(ticker, 0)
        if rs < RS_THRESHOLD:
            continue
        passed, details = check_trend_template(ticker, df)
        if passed:
            record = {"ticker": ticker, "rs_rating": rs, **details}
            record.update(calculate_pullback_metrics(record))  # 押し目分析
            record.update(detect_vcp(df))                      # ★ VCP検出
            results.append(record)

    results.sort(key=lambda x: x["rs_rating"], reverse=True)
    print("  通過銘柄: " + str(len(results)) + "銘柄")
    return results

# -----------------------------------------
# HTMLレポート生成（v2: 新カラム + 左固定列）
# -----------------------------------------

BREAKOUT_COLORS = {
    "STRONG": {"bg": "#166534", "fg": "#bbf7d0"},
    "WATCH":  {"bg": "#78350f", "fg": "#fde68a"},
    "-":      {"bg": "#1e293b", "fg": "#64748b"},
}
VCP_STAGE_COLORS = {
    "T0": {"bg": "#4c1d95", "fg": "#ddd6fe"},  # 紫: ブレイクアウト後リセット待機中
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
    """色付きバッジ HTML を生成するヘルパー"""
    c = cmap.get(label, {"bg": "#1e293b", "fg": "#94a3b8"})
    return (
        '<span style="background:{bg};color:{fg};padding:2px 8px;'
        'border-radius:4px;font-size:0.78rem;font-weight:600;">{lbl}</span>'
    ).format(bg=c["bg"], fg=c["fg"], lbl=label)

def generate_html(results, output_path, removed_tickers=None):
    date_str = "最終更新: " + datetime.today().strftime("%Y-%m-%d %H:%M")
    count    = len(results)

    rows = ""
    for r in results:
        rs = r["rs_rating"]
        rs_color = "#22c55e" if rs >= 90 else ("#84cc16" if rs >= 80 else "#f59e0b")

        def c(v): return "OK" if v else "NG"

        is_new     = r.get("is_new", False)
        first_seen = r.get("first_seen", "-")
        new_badge  = ' <span class="new-badge">NEW</span>' if is_new else ""
        row_cls    = ' class="new-row"' if is_new else ""

        pb_badge  = badge(r["pullback_health"],     PULLBACK_COLORS)
        rev_badge = badge(r["reversal_potential"],  REVERSAL_COLORS)
        mom_badge = badge(r["short_term_momentum"], MOMENTUM_COLORS)

        # VCP関連バッジ
        vcp_stage_badge = badge(r["vcp_stage"], VCP_STAGE_COLORS) if r["vcp_pattern"] else '<span style="color:#475569">-</span>'
        pivot_str   = ("$" + "{:,.2f}".format(r["pivot_price"])) if r["pivot_price"] else "-"
        vcphigh_str = ("$" + "{:,.2f}".format(r["vcp_highest_price"])) if r["vcp_highest_price"] else "-"

        def yn(v):
            return '<span style="color:#22c55e;font-weight:bold;">Y</span>' if v else '<span style="color:#475569">N</span>'
        def bo_badge(v):
            if v:
                return '<span style="background:#b45309;color:#fef3c7;padding:2px 8px;border-radius:4px;font-size:0.78rem;font-weight:700;">BO</span>'
            return '<span style="color:#475569">-</span>'
        vol_cont_yn    = yn(r.get("vol_contraction",    False))
        oneil_yn       = yn(r.get("oneil_breakout",     False))
        minervini_yn   = yn(r.get("minervini_breakout", False))
        pivot_bo       = bo_badge(r.get("pivot_breakout", False))

        rows += (
            "\n        <tr" + row_cls + ">"
            + '<td class="ticker sticky-col sticky-ticker">' + r["ticker"] + new_badge + "</td>"
            + '<td class="sticky-col sticky-rs" style="color:' + rs_color + ';font-weight:bold;">' + str(rs) + "</td>"
            + "<td>$" + "{:,.2f}".format(r["price"])  + "</td>"
            + "<td>$" + "{:,.2f}".format(r["ma50"])   + "</td>"
            + "<td>$" + "{:,.2f}".format(r["ma150"])  + "</td>"
            + "<td>$" + "{:,.2f}".format(r["ma200"])  + "</td>"
            + "<td>$" + "{:,.2f}".format(r["low_52w"])  + "</td>"
            + "<td>$" + "{:,.2f}".format(r["high_52w"]) + "</td>"
            + '<td class="pct">+' + "{:.1f}".format(r["from_52w_low_pct"])  + "%</td>"
            + '<td class="pct">'  + "{:.1f}".format(r["from_52w_high_pct"]) + "%</td>"
            + "<td>" + c(r["cond1"]) + c(r["cond2"]) + c(r["cond3"]) + c(r["cond4"]) + c(r["cond5"]) + c(r["cond6"]) + c(r["cond7"]) + "</td>"
            + '<td class="first-seen">' + first_seen + "</td>"
            + '<td class="pct col-divider">' + "{:.1f}".format(r["pullback_depth_pct"]) + "%</td>"
            + "<td>" + pb_badge  + "</td>"
            + "<td>" + mom_badge + "</td>"
            + "<td>" + rev_badge + "</td>"
            + '<td style="color:#94a3b8;font-size:0.78rem;">' + r["comments"] + "</td>"
            # ── VCPカラム（Stage / Pivot BO / T1-T3 Start/Trough / Contractions / Pivot / VCP High / Vol Cont / O'Neil / Minervini）──
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
        chips = "".join(['<span class="removed-ticker">' + t + "</span>" for t in sorted(removed_tickers)])
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
<title>Minervini Screening v2 - {date}</title>
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
  .version-badge {{
    display: inline-block; background: #5b21b6; color: #ddd6fe;
    border-radius: 12px; padding: 2px 10px; font-size: 0.78rem; margin-left: 6px;
  }}
  .legend {{ display: flex; gap: 16px; margin-bottom: 16px; font-size: 0.8rem; color: #94a3b8; flex-wrap: wrap; }}
  .legend span {{ display: flex; align-items: center; gap: 4px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  /* ── テーブル全体ラッパー ──
       縦・横ともにこのコンテナ内でスクロールさせることで
       sticky top（表頭）と sticky left（表側）を両立させる */
  .table-wrap {{
    overflow: auto;               /* 縦横ともにこのdivがスクロール */
    max-height: calc(100vh - 180px); /* 画面に収まる高さ */
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  /* ── ヘッダー行: .table-wrap 内での縦スクロールで固定 ── */
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 10px 12px;
    text-align: left; font-weight: 600;
    position: sticky; top: 0; white-space: nowrap; z-index: 2;
  }}
  thead th.new-col {{ color: #a78bfa; }}
  thead th.vcp-col {{ color: #34d399; }}
  /* ── 左固定列の基本スタイル ── */
  th.sticky-col {{ z-index: 3; }}                        /* ヘッダー左固定は最前面 */
  td.sticky-col {{
    position: sticky; background: #0f172a; z-index: 1;
  }}
  /* ホバー時・NEW行でも背景色を合わせる */
  tr:hover               td.sticky-col {{ background: #1e293b; }}
  .new-row               td.sticky-col {{ background: #130e0e; }}
  .new-row:hover         td.sticky-col {{ background: #1e293b; }}
  /* Ticker 列: left=0 */
  th.sticky-ticker, td.sticky-ticker {{ left: 0; min-width: 110px; }}
  /* RS 列: Ticker 幅ぶん右にずらす + 右境界線で区切り */
  th.sticky-rs, td.sticky-rs         {{ left: 110px; min-width: 52px; border-right: 2px solid #334155; }}
  tbody tr:hover {{ background: #1e293b; }}
  tbody td {{ padding: 9px 12px; border-bottom: 1px solid #1e293b; white-space: nowrap; }}
  .ticker {{ font-weight: bold; color: #60a5fa; font-size: 0.95rem; }}
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
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; }}
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
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="minervini_report_v2.html" class="active">米国株 (Minervini)</a>
    <a href="haitou.html">日本株 (配当)</a>
    <a href="jpminervini.html">日本株 (Minervini)</a>
    <a href="saitei.html">裁定取引</a>
    <a href="totan.html">日銀利上げ確率</a>
    <a href="daikin.html">売買代金</a>
    <a href="shinyou.html">信用評価率</a>
    <a href="shutai.html">投資主体別</a>
  </nav>
  <h1>Minervini Trend Template Screening
    <span class="badge">{count} passed</span>
    <span class="version-badge">v2 押し目分析</span>
  </h1>
  <p class="subtitle">{date} | S&amp;P500 + NASDAQ100 | RS &gt;= {rs_th}</p>
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
        <th class="sticky-col sticky-ticker">Ticker</th>
        <th class="sticky-col sticky-rs">RS</th>
        <th>Price</th><th>MA50</th><th>MA150</th><th>MA200</th>
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
    ★ v2: Pullback% = 52週高値からの下落率 | Pullback Health: Ideal(≤8%) Healthy(≤25%) Caution(≤30%) Danger(30%超)<br>
    Short Momentum: Strong(価格&gt;MA25&gt;MA5) Recovering(価格&gt;MA5) Weak(価格&lt;MA5) |
    Reversal Potential: High = Healthy押し目 + RS≥75 + Recovering以上<br>
    ★ VCP: Stage=収縮回数(T1-T6) | Vol Cont: T1=収縮期出来高&lt;50日平均, T2+=各収縮期が単調減少<br>
    O'Neil BO: 当日出来高&gt;50日平均×1.5 | Minervini BO: 当日出来高&gt;収縮期間平均×1.5
  </div>
  <p class="updated">Generated: {ts} (v2)</p>
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

# -----------------------------------------
# TradingView用リスト生成
# -----------------------------------------
def generate_tradingview_list(results, output_path):
    """ティッカーをTradingView用テキストファイルに出力（コードのみ、NEWバッジ除去）"""
    lines = []
    for r in results:
        ticker = r["ticker"].strip()
        # .T を除去（日本株用、米国株には不要だが念のため）
        ticker = ticker.replace(".T", "")
        lines.append(ticker + "\t,")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("  TradingViewリスト出力: " + output_path)

# -----------------------------------------
# GitHub Pages 自動 push
# -----------------------------------------
def push_to_github(report_filename):
    print("[5/5] GitHub Pages に公開中...")
    today = datetime.today().strftime("%Y-%m-%d")
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", report_filename], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update v2 report " + today],
        capture_output=True,
    )
    if result.returncode != 0:
        msg = result.stdout.decode(errors="ignore") + result.stderr.decode(errors="ignore")
        if "nothing to commit" in msg:
            print("  commit skip (already committed)")
        else:
            print("  commit failed: " + msg)
            return

    for attempt in range(1, 6):
        try:
            subprocess.run(["git", "-C", SCRIPT_DIR, "pull", "--rebase", "--autostash"], check=True)
            subprocess.run(["git", "-C", SCRIPT_DIR, "push"],            check=True)
            print("  Done: https://ichikon77.github.io/minervini/minervini_report_v2.html")
            return
        except subprocess.CalledProcessError as e:
            print("  push failed (attempt " + str(attempt) + "/5): " + str(e))
            time.sleep(10)
    print("  push failed finally")

# -----------------------------------------
# main
# -----------------------------------------
def main():
    start_time = time.time()
    print("=" * 55)
    print("  Minervini Trend Template Screener v2")
    print("=" * 55)

    tickers = get_universe()
    if not tickers:
        print("ERROR: universe fetch failed")
        return

    all_data   = download_data(tickers)
    rs_ratings = calc_rs_rating(all_data)
    results    = run_screen(all_data, rs_ratings)

    history = load_history()
    results = update_history(results, history)
    new_count = sum(1 for r in results if r.get("is_new"))
    if new_count:
        print("  NEW: " + str(new_count) + " new entries")

    prev_scores = load_vcp_scores()
    results     = attach_vcp_score_info(results, prev_scores)
    score_up_count = sum(1 for r in results if r.get("vcp_score_up"))
    if score_up_count:
        print("  VCP SCORE UP: " + str(score_up_count) + " tickers increased")
    save_vcp_scores(results)

    prev_tickers    = load_prev_tickers()
    current_tickers = set(r["ticker"] for r in results)
    removed_tickers = prev_tickers - current_tickers
    if removed_tickers:
        print("  REMOVED: " + str(len(removed_tickers)) + " (" + ", ".join(sorted(removed_tickers)) + ")")
    save_prev_tickers(current_tickers)

    report_filename = "minervini_report_v2.html"
    output_path     = os.path.join(SCRIPT_DIR, report_filename)
    generate_html(results, output_path, removed_tickers=removed_tickers)

    tv_path = os.path.join(SCRIPT_DIR, "txt", "Minervini.txt")
    generate_tradingview_list(results, tv_path)

    push_to_github(report_filename)

    elapsed = time.time() - start_time
    print("=" * 55)
    print("  Done: " + str(round(elapsed, 1)) + "sec")
    print("  URL: https://ichikon77.github.io/minervini/minervini_report_v2.html")
    print("=" * 55)

if __name__ == "__main__":
    main()
