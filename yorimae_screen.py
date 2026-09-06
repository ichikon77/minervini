# -*- coding: utf-8 -*-
"""
寄り前チェック（夜間先物ギャップ×ADR） → yorimae.html → GitHub Pages公開

ディーラーの朝のミーティング資料の再現。毎朝7:15（CME引け後・東京寄り前）に実行し、
「今日の寄り付きがどこから始まり、その動きは米株・為替で説明がつくのか」を1ページで出す。

① 夜間先物ギャップ
  CME日経平均先物（円建て NIY=F、なければドル建て NKD=F）の夜間終値と
  前日の日経平均終値のギャップ。現物はほぼ夜間先物の水準に揃って寄り付くため、
  これが「今朝の寄り付き目安」になる。

② 理論値との答え合わせ（kinri流）
  夜間の日経先物の変動は、理屈上「米株(S&P500)の変動×ベータ + ドル円の変動×為替感応度」で
  だいたい説明できる。過去1年の日次データで回帰係数を自前推定し、
    理論値% = b1 × S&P500前日騰落% + b2 × ドル円変化%
  を計算。実測ギャップとの乖離 = 日本固有の夜間要因（海外勢の日本株への強弱など）。
  乖離が小さければ青（理論通り）、大きければ赤（日本固有要因あり）で表示。
  ※ドル円の「前日東京引け時点」は取得できないためNY終値で近似。厳密な分解ではなく目安。

③ ADRギャップ
  主要ADR銘柄（NYSE/OTC）の米国終値を円換算し、東京の前日終値と比較。
  ADR倍率（1ADR=何株）はハードコードせず、直近20日の (ADR×ドル円)÷東京終値 の中央値を
  「平常時の換算比率」として使う（株式分割や倍率変更に自動追随。平均ギャップは定義上ほぼ0になり、
  今朝の値=夜間に付いた固有のプレミアム/ディスカウントを表す）。

データ: yfinance のみ。実行: 毎朝7:15（yorimae_run.bat）。--nopush でpush省略。
"""

import os
import sys
import json
import time
import subprocess
import datetime

import numpy as np
import pandas as pd
import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_HTML = "yorimae.html"
HISTORY_JSON = os.path.join(SCRIPT_DIR, "yorimae_history.json")  # 乖離の履歴と答え合わせ用
POST_JSON = os.path.join(SCRIPT_DIR, "yorimae_post.json")        # X自動投稿(kabuchiwa_post.py)に渡す今朝の数値

# 主要ADR銘柄（ADRティッカー, 東証コード, 表示名, 市場区分）
# NYSE上場は板が厚くADR価格の信頼性が高い。OTCは流動性が薄く値が古い/飛ぶことがある
ADR_LIST = [
    ("TM", "7203.T", "トヨタ", "NYSE"),
    ("SONY", "6758.T", "ソニーG", "NYSE"),
    ("MUFG", "8306.T", "三菱UFJ", "NYSE"),
    ("SMFG", "8316.T", "三井住友FG", "NYSE"),
    ("MFG", "8411.T", "みずほFG", "NYSE"),
    ("HMC", "7267.T", "ホンダ", "NYSE"),
    ("TAK", "4502.T", "武田薬品", "NYSE"),
    ("NMR", "8604.T", "野村HD", "NYSE"),
    ("IX", "8591.T", "オリックス", "NYSE"),
    ("NTDOY", "7974.T", "任天堂", "OTC"),
    ("SFTBY", "9984.T", "ソフトバンクG", "OTC"),
    ("HTHIY", "6501.T", "日立", "OTC"),
    ("TOELY", "8035.T", "東京エレクトロン", "OTC"),
    ("FRCOY", "9983.T", "ファーストリテ", "OTC"),
    ("KMTUY", "6301.T", "コマツ", "OTC"),
]

BETA_LOOKBACK = 250   # 理論値の回帰に使う日数（約1年）
RATIO_WINDOW = 20     # ADR換算比率の中央値を取る日数
DEVIATION_TH = 0.5    # 実測-理論値の乖離がこの%を超えたら「日本固有要因あり」(赤)


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# データ取得
# -----------------------------------------
def last_price(ticker, prefer_intraday=True):
    """直近の取引値。夜間・時間外を含む最新値が欲しいので5分足の最後を優先"""
    t = yf.Ticker(ticker)
    if prefer_intraday:
        try:
            h = t.history(period="2d", interval="5m")["Close"].dropna()
            if len(h):
                return float(h.iloc[-1])
        except Exception:
            pass
    try:
        h = t.history(period="5d")["Close"].dropna()
        if len(h):
            return float(h.iloc[-1])
    except Exception:
        pass
    return None


def daily_closes(ticker, period="2y"):
    try:
        h = yf.Ticker(ticker).history(period=period)["Close"].dropna()
        h.index = h.index.tz_localize(None).normalize()
        return h
    except Exception:
        return pd.Series(dtype=float)


# -----------------------------------------
# 理論値の回帰係数（過去1年: 日経リターン ~ SPX前日リターン + ドル円変化）
# -----------------------------------------
def estimate_betas(n225, spx, fx):
    """b0 + b1*SPX前日リターン + b2*ドル円日次変化 で日経日次リターンを回帰。
    (b1, b2, 決定係数R2) を返す。データ不足時はNone"""
    n_ret = n225.pct_change().dropna()
    s_ret = spx.pct_change().dropna()
    f_ret = fx.pct_change().dropna()

    rows = []
    for t, y in n_ret.iloc[-BETA_LOOKBACK:].items():
        # 東京のt日に対して「その時点で判明している直近の米国終値リターン」= t-1日以前の最新
        s = s_ret.asof(t - pd.Timedelta(days=1))
        f = f_ret.asof(t)
        if pd.notna(s) and pd.notna(f):
            rows.append((y, s, f))
    if len(rows) < 100:
        return None
    arr = np.array(rows)
    y, X = arr[:, 0], np.column_stack([np.ones(len(arr)), arr[:, 1], arr[:, 2]])
    coef, res, _, _ = np.linalg.lstsq(X, y, rcond=None)
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - res[0] / ss_tot if len(res) and ss_tot > 0 else None
    return coef[1], coef[2], r2


# -----------------------------------------
# 乖離の履歴と答え合わせ
# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        try:
            return json.load(open(HISTORY_JSON, encoding="utf-8"))
        except Exception:
            pass
    return {"days": {}}


def save_history(hist):
    json.dump(hist, open(HISTORY_JSON, "w", encoding="utf-8"), ensure_ascii=False)


def update_history(hist, today, rec, adr_rows=None):
    """今朝の観測（ギャップ・理論・乖離 + ADRギャップ）を営業日キーで記録。
    週末や既存日には上書きしない（7:15の初回観測を保存する趣旨）"""
    if today.weekday() >= 5:
        return
    key = today.isoformat()
    if key not in hist["days"]:
        hist["days"][key] = rec
    # ADRギャップ（銘柄別）。同日再実行でも初回分を保持
    if adr_rows and "adr" not in hist["days"][key]:
        hist["days"][key]["adr"] = {
            r["tyo"]: {"gap": round(r["gap"], 2), "prev": r["tyo_prev"], "mkt": r["mkt"]}
            for r in adr_rows
        }


def finalize_days(hist):
    """過去日のOHLCを日足の確定値で上書きする（15:45の5分足ベースの終値は暫定。Yahooは翌日以降に値を改定することもある）"""
    today = pd.Timestamp(datetime.date.today())
    targets = [k for k, v in hist["days"].items() if "close" in v and not v.get("final") and pd.Timestamp(k) < today]
    if not targets:
        return
    try:
        d = yf.Ticker("^N225").history(period="3mo")
        d = d.set_axis(d.index.tz_localize(None).normalize())
    except Exception:
        return
    for k in targets:
        ts = pd.Timestamp(k)
        if ts not in d.index:
            continue
        row = d.loc[ts]
        newc = round(float(row["Close"]), 1)
        oldc = hist["days"][k]["close"]
        if abs(newc / oldc - 1) > 0.015:
            # 日中足ベースの終値と1.5%以上違う日足は疑わしい（Yahooの異常行）→ 採用せず次回に再確認
            log(f"  {k}: 日足の終値 {newc:,.0f} が記録 {oldc:,.0f} と大きく違うため保留（Yahooの異常値の可能性）")
            continue
        if abs(newc - oldc) >= 0.5:
            log(f"  {k}: 終値を日足の確定値に更新 {oldc:,.0f} → {newc:,.0f}")
        hist["days"][k].update({"open": round(float(row["Open"]), 1), "high": round(float(row["High"]), 1),
                                "low": round(float(row["Low"]), 1), "close": newc, "final": True})


def reanchor_futures(hist):
    """過去の②を「6:00直前の5分足バー」に揃える（旧方式は実行時刻の最新値を取っていて、遅延データで数百円ズレる日があった）。
    5分足は約1ヶ月分しか取れないので、取れる範囲だけ直し fut_anchored=True を付ける"""
    targets = [k for k, v in hist["days"].items() if v.get("fut") and not v.get("fut_anchored")]
    if not targets:
        return
    try:
        h = yf.Ticker("NIY=F").history(period="1mo", interval="5m")
        if h.empty:
            return
        h = h.set_axis(h.index.tz_convert("Asia/Tokyo"))["Close"].dropna()
    except Exception:
        return
    jst = datetime.timezone(datetime.timedelta(hours=9))
    for k in targets:
        d = datetime.date.fromisoformat(k)
        anchor = datetime.datetime.combine(d, ANCHOR, tzinfo=jst)
        x = h[(h.index < anchor) & (h.index > anchor - datetime.timedelta(days=3))]   # 月曜は土曜6:00のバー（金曜夜間の終値）
        if x.empty:
            continue
        newf = float(x.iloc[-1])
        r = hist["days"][k]
        if abs(newf - r["fut"]) >= 5:
            log(f"  {k}: ②夜間先物を6:00のバーに再固定 {r['fut']:,.0f} → {newf:,.0f}（{x.index[-1]:%m/%d %H:%M}）")
        r["fut"] = newf
        r["obs_time"] = "6:00"
        if r.get("prev_close"):
            r["gap"] = round((newf / r["prev_close"] - 1) * 100, 3)
            if r.get("theo") is not None:
                r["dev"] = round(r["gap"] - r["theo"], 3)
        r["fut_anchored"] = True


def recompute_theo(hist, betas):
    """過去の⑧理論値を、その日の材料（前日までの米株日足・6:00のドル円）から作り直す。
    旧方式は実行時刻の最新値を使っていて、米株日足が未更新の朝に前日と同じ理論値になる事故があった（8/28と8/31が同値）"""
    if not betas:
        return
    targets = [k for k, v in hist["days"].items() if v.get("gap") is not None and not v.get("theo_recomputed")]
    if not targets:
        return
    try:
        spx = daily_closes("^GSPC", period="3mo")
        ndx = daily_closes("^IXIC", period="3mo")
        fxd = daily_closes("JPY=X", period="3mo")
        fx5 = yf.Ticker("JPY=X").history(period="1mo", interval="5m")
        fx5 = fx5.set_axis(fx5.index.tz_convert("Asia/Tokyo"))["Close"].dropna() if not fx5.empty else fx5
    except Exception:
        return
    jst = datetime.timezone(datetime.timedelta(hours=9))
    b1, b2 = betas[0], betas[1]
    for k in targets:
        d = datetime.date.fromisoformat(k)
        r = hist["days"][k]
        s_ = spx[spx.index.date < d]
        n_ = ndx[ndx.index.date < d]
        f_ = fxd[fxd.index.date < d]
        if len(s_) < 2 or len(f_) < 1:
            continue
        anchor = datetime.datetime.combine(d, ANCHOR, tzinfo=jst)
        x = fx5[(fx5.index < anchor) & (fx5.index > anchor - datetime.timedelta(days=3))] if len(fx5) else fx5
        if x.empty:
            continue
        spx_ret = float((s_.iloc[-1] / s_.iloc[-2] - 1) * 100)
        ndx_ret = float((n_.iloc[-1] / n_.iloc[-2] - 1) * 100) if len(n_) >= 2 else None
        fx_now = float(x.iloc[-1])
        fx_chg = (fx_now / float(f_.iloc[-1]) - 1) * 100
        theo = round(b1 * spx_ret + b2 * fx_chg, 3)
        if r.get("theo") is None or abs(theo - r["theo"]) >= 0.05:
            log(f"  {k}: ⑧理論値を材料から再計算 {r.get('theo')} → {theo}（S&P500 {spx_ret:+.2f}% / ドル円 {fx_chg:+.2f}%）")
        r.update({"theo": theo, "spx_ret": spx_ret, "ndx_ret": ndx_ret, "fx_now": fx_now, "fx_chg": fx_chg,
                  "dev": round(r["gap"] - theo, 3), "theo_recomputed": True})


def repair_stale_prev(hist):
    """各日の①前日終値を「前の記録の④終値」と突き合わせ、ズレていれば作り直す。
    ケース1: 日足欠損で一つ前の終値を掴んだ（2026-08-31, 09-01）。ケース2: Yahooが終値を後日改定した。
    どちらも ①→⑤⑨ が連鎖して変わるので、前日終値・ギャップ・乖離を再計算し repaired=True を付ける"""
    keys = sorted(hist["days"])
    for prev_k, k in zip(keys, keys[1:]):
        p, r = hist["days"][prev_k], hist["days"][k]
        if "close" not in p or not r.get("prev_close"):
            continue
        # 前の記録が「直前の営業日」でなければ比較しない（祝日を挟む等）
        gap_days = (datetime.date.fromisoformat(k) - datetime.date.fromisoformat(prev_k)).days
        if gap_days > 4:
            continue
        if abs(p["close"] - r["prev_close"]) >= 0.5:
            old = r["prev_close"]
            r["prev_close"] = p["close"]
            if r.get("fut"):
                r["gap"] = round((r["fut"] / r["prev_close"] - 1) * 100, 3)
                if r.get("theo") is not None:
                    r["dev"] = round(r["gap"] - r["theo"], 3)
            r["repaired"] = True
            r.pop("adr_repaired", None)     # ADR側の前日終値も作り直す
            log(f"  {k}: ①前日終値を前日の確定終値に合わせて修正 {old:,.0f} → {p['close']:,.0f}（⑤ {r.get('gap')}% / ⑨ {r.get('dev')}%）")


def backfill_answers(hist):
    """過去の記録に「その日の実際の寄り・高安・引け」を埋めて答え合わせを可能にする。
    当日は場中の未確定値を固定してしまわないようスキップ（翌朝の実行で埋まる）"""
    today = pd.Timestamp(datetime.date.today())
    missing = [k for k, v in hist["days"].items()
               if "close" not in v and pd.Timestamp(k) < today]
    if not missing:
        return
    try:
        ohlc = yf.Ticker("^N225").history(period="3mo")
        ohlc.index = ohlc.index.tz_localize(None).normalize()
    except Exception:
        return
    for k in missing:
        d = pd.Timestamp(k)
        if d in ohlc.index:
            row = ohlc.loc[d]
            hist["days"][k].update({
                "open": round(float(row["Open"]), 1),
                "high": round(float(row["High"]), 1),
                "low": round(float(row["Low"]), 1),
                "close": round(float(row["Close"]), 1),
            })
        else:
            # Yahooの^N225は日足だけ欠損する日がある（例: 2026-08-28）。
            # 60分足には残っていることが多いので、そこからOHLCを復元する
            try:
                intr = yf.Ticker("^N225").history(
                    start=d, end=d + pd.Timedelta(days=1), interval="60m")
                if len(intr):
                    hist["days"][k].update({
                        "open": round(float(intr["Open"].iloc[0]), 1),
                        "high": round(float(intr["High"].max()), 1),
                        "low": round(float(intr["Low"].min()), 1),
                        "close": round(float(intr["Close"].iloc[-1]), 1),
                    })
                    log(f"  {k}: 日足欠損→60分足からOHLC復元")
            except Exception:
                pass


def backfill_adr_answers(hist):
    """ADRギャップ記録に「その日の東京の寄り・引け」を銘柄別に埋める。
    当日は場中の未確定値を固定してしまわないようスキップ"""
    today = pd.Timestamp(datetime.date.today())

    def unanswered(s):
        # 未回答、または NaN が保存されてしまった（yfinance が空行を返した日）ものは再取得対象
        return ("o" not in s) or (s["o"] != s["o"]) or (s.get("c") != s.get("c"))

    # 未回答の(日付, 銘柄)、または前日終値の修正(repaired)が未反映のものがあるか
    need = any("adr" in v and pd.Timestamp(k) < today
               and (any(unanswered(s) for s in v["adr"].values())
                    or (v.get("repaired") and not v.get("adr_repaired")))
               for k, v in hist["days"].items())
    if not need:
        return
    tickers = [tyo for _, tyo, _, _ in ADR_LIST]
    try:
        data = yf.download(tickers, period="3mo", auto_adjust=True,
                           progress=False, group_by="ticker", threads=True)
    except Exception:
        return
    for k, v in hist["days"].items():
        if "adr" not in v:
            continue
        d = pd.Timestamp(k)
        if d >= today:
            continue
        for code, s in v["adr"].items():
            try:
                df = data[code + ".T"].dropna(subset=["Open", "Close"])
                idx = list(df.index.tz_localize(None).normalize())
                if d not in idx:
                    continue
                pos = idx.index(d)
                # 前日終値が古い値だった日（日足欠損）は、ADR側の「東京前日終値」も古い。
                # 記録済みのギャップから円換算値を復元し、正しい前日終値で作り直す
                if v.get("repaired") and pos >= 1 and s.get("prev"):
                    true_prev = float(df["Close"].iloc[pos - 1])
                    if abs(true_prev - s["prev"]) >= 0.5:
                        implied = s["prev"] * (1 + s["gap"] / 100)
                        s["gap"] = round((implied / true_prev - 1) * 100, 2)
                        s["prev"] = round(true_prev, 1)
                    s["repaired"] = True
                if unanswered(s):
                    o, c = float(df["Open"].iloc[pos]), float(df["Close"].iloc[pos])
                    if o == o and c == c:      # NaN は保存しない
                        s["o"], s["c"] = round(o, 1), round(c, 1)
            except Exception:
                continue
        if v.get("repaired"):
            v["adr_repaired"] = True


def build_adr_stats(hist):
    """ADRギャップの答え合わせ集計。
    ①精度: |ADRギャップ - 実際の寄りギャップ| の平均誤差（NYSE/OTC別）
    ②平均回帰: 固有ギャップ（ADRギャップ-その日の指数ギャップ）の大きさ別に日中リターン"""
    prec = {"NYSE": [], "OTC": []}
    buckets = {"固有ギャップ≤-1%": [], "±1%以内": [], "固有ギャップ≥+1%": []}
    n_total = 0
    for v in hist["days"].values():
        idx_gap = v.get("gap")
        for code, s in v.get("adr", {}).items():
            if "o" not in s or not s.get("prev") or s["o"] != s["o"] or s.get("c") != s.get("c"):
                continue   # 未回答・NaN は集計から外す
            n_total += 1
            open_gap = (s["o"] / s["prev"] - 1) * 100
            intraday = (s["c"] / s["o"] - 1) * 100
            prec.setdefault(s.get("mkt", "OTC"), []).append(abs(s["gap"] - open_gap))
            if idx_gap is not None:
                own = s["gap"] - idx_gap
                if own <= -1:
                    buckets["固有ギャップ≤-1%"].append(intraday)
                elif own >= 1:
                    buckets["固有ギャップ≥+1%"].append(intraday)
                else:
                    buckets["±1%以内"].append(intraday)
    return n_total, prec, buckets


def _answer_row(rec):
    """記録1件から答え合わせ指標を計算。(実寄りギャップ%, 日中リターン%, ギャップ埋めbool or None)"""
    if "close" not in rec or not rec.get("prev_close"):
        return None, None, None
    prev = rec["prev_close"]
    open_gap = (rec["open"] / prev - 1) * 100
    intraday = (rec["close"] / rec["open"] - 1) * 100
    # 窓埋め: 寄り付きで前日終値から離れて始まった後、日中に一度でも前日終値まで戻ったか（高値/安値で判定）。
    # 寄り付きギャップが±0.2%以内は「窓」と呼べないので判定しない（None）
    if abs(open_gap) < 0.2:
        filled = None
    elif rec["open"] < prev:
        filled = rec["high"] >= prev
    else:
        filled = rec["low"] <= prev
    return open_gap, intraday, filled


def classify_dev(dev):
    if dev is None:
        return None
    if dev >= DEVIATION_TH:
        return "buy"
    if dev <= -DEVIATION_TH:
        return "sell"
    return "neutral"


# 答え合わせ表の列番号（①〜⑬）。前提の4つの値から他の全列が式で決まる（MECE）
#   前提:   ①前日終値 ②夜間先物の終値(6:00) ③当日始値 ④当日終値
#   問い1:  ⑤夜間先物ギャップ=②/①-1  ⑥実際の寄り付きギャップ=③/①-1  ⑦寄りまでの変化=⑥-⑤
#   問い2:  ⑧理論値  ⑨乖離=⑤-⑧  ⑩判定（|⑨|≤0.5%で青、超で赤）
#   問い3:  ⑪寄り時点の乖離=⑥-⑧  ⑫引け時点の乖離=(④/①-1)-⑧  ⑬判定=⑫÷⑨（残り率）
def day_values(rec):
    """1日分の記録から①〜⑬を計算して辞書で返す。未確定（採点前）の値は None"""
    v = {"p1": rec.get("prev_close"), "p2": rec.get("fut"), "p3": rec.get("open"), "p4": rec.get("close"),
         "g5": rec.get("gap"), "g6": None, "g7": None, "t8": rec.get("theo"), "d9": rec.get("dev"),
         "cls": classify_dev(rec.get("dev")), "d11": None, "d12": None, "cls12": None, "k13": None}
    p1 = v["p1"]
    if p1 and v["p3"] is not None:
        v["g6"] = (v["p3"] / p1 - 1) * 100
        if v["g5"] is not None:
            v["g7"] = v["g6"] - v["g5"]
    if v["t8"] is not None and v["g6"] is not None:
        v["d11"] = v["g6"] - v["t8"]
    if v["t8"] is not None and p1 and v["p4"] is not None:
        v["d12"] = (v["p4"] / p1 - 1) * 100 - v["t8"]
    v["cls12"] = classify_dev(v["d12"])          # 引け時点の乖離⑫を⑩と同じしきい値で判定
    if v["cls"] in ("buy", "sell", "neutral") and v["cls12"] is not None:
        red9 = v["cls"] in ("buy", "sell")
        red12 = v["cls12"] in ("buy", "sell")
        if red9 and not red12:
            v["k13"] = "filled"        # 赤→青: 夜の日本固有要因は引けまでに消えた（ノイズ・需給）
        elif red9 and red12:
            v["k13"] = "remained" if v["cls12"] == v["cls"] else "reversed"   # 赤→赤: 残った / 逆方向に反転
        elif (not red9) and red12:
            v["k13"] = "emerged"       # 青→赤: 夜は理論通り、日本の場中で何か起きた
        else:
            v["k13"] = "theory"        # 青→青: 終日理論通り
    return v


K13_LABEL = {"filled": ("埋まった", "ok"), "remained": ("残った", "warn"), "reversed": ("反転", "warn"),
             "emerged": ("日中に発生", "warn"), "theory": ("理論通り", "ok")}


def build_qbox(hist):
    """3つの問いの成績を3枚のカードで返す"""
    errs, same_dir = [], []
    n_all, n_red, n_buy, n_sell = 0, 0, 0, 0
    pat = {"filled": 0, "remained": 0, "reversed": 0, "emerged": 0, "theory": 0}
    n_open_expand = 0
    for rec in hist["days"].values():
        v = day_values(rec)
        if v["g6"] is None or v["g5"] is None:
            continue
        n_all += 1
        errs.append(abs(v["g7"]))
        if abs(v["g5"]) >= 0.2:
            same_dir.append((v["g6"] > 0) == (v["g5"] > 0))
        if v["cls"] in ("buy", "sell"):
            n_red += 1
            n_buy += v["cls"] == "buy"
            n_sell += v["cls"] == "sell"
            if v["d11"] is not None and v["d9"] and v["d11"] / v["d9"] > 1.2:
                n_open_expand += 1
        if v["k13"]:
            pat[v["k13"]] += 1
    if n_all == 0:
        return '  <div class="qbox"><div class="q"><div class="qt">成績</div><div class="qs">蓄積中（翌朝から採点が始まります）</div></div></div>'
    caveat = "（サンプル少・傾向の目安）" if n_all < 20 else ""
    q1 = (f'<div class="q"><div class="qt">問い1 夜間先物ギャップ⑤（6:00時点）は、寄り付き⑥を当てたか</div>'
          f'<div class="qv">平均誤差 {sum(errs)/len(errs):.2f}%</div>'
          f'<div class="qs">N={n_all}。|⑦寄りまでの変化| の平均。'
          + (f'方向一致 {sum(same_dir)/len(same_dir)*100:.0f}%（|⑤|≥0.2%の{len(same_dir)}日）' if same_dir else "")
          + f'{caveat}</div></div>')
    q2 = (f'<div class="q"><div class="qt">問い2-a 6:00時点で日本固有要因（赤）があった頻度</div>'
          f'<div class="qv">{n_red}/{n_all}日</div>'
          f'<div class="qs">日本固有要因：買い {n_buy}日・売り {n_sell}日・理論通り {n_all - n_red}日。赤が多いほど「米株を見ているだけでは日本の寄りは読めない」相場</div></div>')
    n_dec = sum(pat.values())
    if n_dec:
        verdict = ""
        if pat["filled"] + pat["remained"] >= 3:
            if pat["filled"] > pat["remained"]:
                verdict = "→ いまのところ夜の乖離は<b>埋まる</b>ことが多い＝ノイズ寄り。赤の朝は寄りで飛びつかず戻りを待つ（逆張り）"
            elif pat["remained"] > pat["filled"]:
                verdict = "→ いまのところ夜の乖離は<b>残る</b>ことが多い＝本物の材料寄り。赤の方向に乗る（順張り）"
            else:
                verdict = "→ 五分五分"
        expand_s = f'　赤の朝のうち寄りで拡大 {n_open_expand}日。' if n_open_expand else ""
        q3 = (f'<div class="q"><div class="qt">問い2-b 15:30時点⑫で、理論値との差は残ったか</div>'
              f'<div class="qv">赤→青 埋まった {pat["filled"]} ／ 赤→赤 残った {pat["remained"] + pat["reversed"]} ／ 青→赤 日中に発生 {pat["emerged"]} ／ 青→青 {pat["theory"]}</div>'
              f'<div class="qs">⑩（6:00）→⑬（15:30）の組み合わせ。N={n_dec}。{expand_s}'
              f'<br>{verdict}{caveat}</div></div>')
    else:
        q3 = '<div class="q"><div class="qt">問い2-b 15:30時点⑫で、理論値との差は残ったか</div><div class="qs">採点がまだ無い</div></div>'
    return f'  <div class="qbox">{q1}{q2}{q3}</div>'


def build_history_stats(hist):
    """乖離の符号別に日中リターンとギャップ埋め率を集計"""
    groups = {"日本売り(乖離≤-0.5%)": [], "中立(±0.5%以内)": [], "日本買い(乖離≥+0.5%)": []}
    for rec in hist["days"].values():
        dev = rec.get("dev")
        og, intraday, filled = _answer_row(rec)
        if dev is None or intraday is None:
            continue
        if dev <= -DEVIATION_TH:
            g = "日本売り(乖離≤-0.5%)"
        elif dev >= DEVIATION_TH:
            g = "日本買い(乖離≥+0.5%)"
        else:
            g = "中立(±0.5%以内)"
        groups[g].append((intraday, filled))
    out = []
    for g, rows in groups.items():
        if not rows:
            out.append((g, 0, None, None))
            continue
        avg = sum(r[0] for r in rows) / len(rows)
        fills = [r[1] for r in rows if r[1] is not None]
        fill_rate = sum(fills) / len(fills) * 100 if fills else None
        out.append((g, len(rows), avg, fill_rate))
    return out


# -----------------------------------------
# ADRギャップ
# -----------------------------------------
def expected_prev_bday():
    """直近の平日（今日より前）。祝日は考慮しない（休日なら60分足も無く、日足の最終日がそのまま使われる）"""
    d = datetime.date.today() - datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d


def ensure_latest_close(ticker, series, expected):
    """日足の最終日が expected より古ければ、その日の60分足の最終値を末尾に足して返す（Yahoo日足欠損対策）"""
    if series.empty or series.index[-1].date() >= expected:
        return series
    try:
        intr = yf.Ticker(ticker).history(
            start=expected, end=expected + datetime.timedelta(days=1), interval="60m")
        if len(intr):
            series = series.copy()
            series.loc[pd.Timestamp(expected)] = float(intr["Close"].iloc[-1])
            log(f"  {ticker}: 日足に{expected}が無い → 60分足の終値で補完")
    except Exception:
        pass
    return series


def calc_adr_gaps():
    log("ADRギャップを計算中...")
    fx_now = last_price("JPY=X")
    expected = expected_prev_bday()
    out = []
    for adr, tyo, name, mkt in ADR_LIST:
        try:
            a = daily_closes(adr, period="3mo")
            j = ensure_latest_close(tyo, daily_closes(tyo, period="3mo"), expected)
            f = daily_closes("JPY=X", period="3mo")
            if len(a) < RATIO_WINDOW or len(j) < RATIO_WINDOW:
                continue
            # 平常時の換算比率: 直近20日の (ADR×為替)/東京終値 の中央値
            df = pd.DataFrame({"a": a, "f": f}).dropna()
            df["af"] = df["a"] * df["f"]
            merged = pd.concat([df["af"], j], axis=1, keys=["af", "j"], sort=True).dropna()
            if len(merged) < RATIO_WINDOW:
                continue
            ratio = float((merged["af"] / merged["j"]).iloc[-RATIO_WINDOW:].median())
            adr_last = float(a.iloc[-1])          # 今朝の米国終値
            tyo_prev = float(j.iloc[-1])          # 東京の前日終値
            implied = adr_last * (fx_now or float(f.iloc[-1])) / ratio
            gap = (implied / tyo_prev - 1) * 100
            out.append({"name": name, "adr": adr, "tyo": tyo.replace(".T", ""),
                        "mkt": mkt, "tyo_prev": tyo_prev, "implied": implied, "gap": gap})
        except Exception as e:
            log(f"  {adr}: skip ({e})")
    out.sort(key=lambda r: -r["gap"])
    log(f"  ADR: {len(out)}銘柄")
    return out


# -----------------------------------------
# ADR追随の銘柄別バックテスト（過去約1年）
# -----------------------------------------
ADR_BT_DAYS = 250   # 集計対象の営業日数


def backtest_adr_follow():
    """銘柄ごとに過去約1年分のADRギャップを再構成し、翌日の東京の
    寄りギャップ・日中リターンとの関係を集計する。

    ADRギャップの定義は本番の表示と同じ「ADR円換算 ÷ 東京前日終値 − 1」。
    ADRの日次騰落には「東京がその日すでに動いた分への追いつき」が混ざるため使わない。
    この定義なら東京の動きは分母に織り込み済みで、米国セッションで
    上乗せされた分（東京が未織り込みの情報）だけが残る。
    米国休場などでADR終値が東京の引けより古い日はシグナルが陳腐化しているので除外。
    """
    log("ADR追随バックテストを計算中...")
    fx = daily_closes("JPY=X", period="2y")
    results = []
    for adr, tyo, name, mkt in ADR_LIST:
        try:
            a = daily_closes(adr, period="2y")
            h = yf.Ticker(tyo).history(period="2y")[["Open", "Close"]].dropna()
            h.index = h.index.tz_localize(None).normalize()
            if len(a) < 60 or len(h) < 60:
                continue
            af = (a * fx).dropna()                    # ADRの円換算値（米国日付）
            ratio_all = (af / h["Close"]).dropna()    # 換算比率の系列（本番と同じ同日ラベル同士）
            rows = []
            dates = list(h.index)
            for i in range(RATIO_WINDOW + 1, len(dates)):
                t, tp = dates[i], dates[i - 1]
                # 「昨晩の米国終値」= tより前の最新のADR日付
                pos = af.index.searchsorted(t) - 1
                if pos < 0:
                    continue
                u = af.index[pos]
                if u < tp:      # 米国休場明け等: ADRが東京前日の引けより古い → 除外
                    continue
                r_hist = ratio_all[ratio_all.index <= tp]
                if len(r_hist) < RATIO_WINDOW:
                    continue
                ratio = float(r_hist.iloc[-RATIO_WINDOW:].median())
                gap = (float(af.loc[u]) / ratio / float(h["Close"].loc[tp]) - 1) * 100
                og = (float(h["Open"].loc[t]) / float(h["Close"].loc[tp]) - 1) * 100
                intra = (float(h["Close"].loc[t]) / float(h["Open"].loc[t]) - 1) * 100
                rows.append((gap, og, intra))
            rows = rows[-ADR_BT_DAYS:]
            if len(rows) < 100:
                continue
            arr = np.array(rows)
            gaps, ogs, intras = arr[:, 0], arr[:, 1], arr[:, 2]
            match = float((np.sign(gaps) == np.sign(ogs)).mean() * 100)
            big = np.abs(gaps) >= 1.0
            match_big = (float((np.sign(gaps[big]) == np.sign(ogs[big])).mean() * 100)
                         if int(big.sum()) >= 10 else None)
            beta = float(np.polyfit(gaps, ogs, 1)[0])
            corr = float(np.corrcoef(gaps, ogs)[0, 1])
            err = float(np.abs(gaps - ogs).mean())
            up = gaps >= 0.5
            dn = gaps <= -0.5
            results.append({
                "name": name, "tyo": tyo.replace(".T", ""), "mkt": mkt, "n": len(rows),
                "match": match, "match_big": match_big, "big_n": int(big.sum()),
                "beta": beta, "corr": corr, "err": err,
                "up_n": int(up.sum()),
                "up_intra": float(intras[up].mean()) if int(up.sum()) else None,
                "up_win": float((intras[up] > 0).mean() * 100) if int(up.sum()) else None,
                "dn_n": int(dn.sum()),
                "dn_intra": float(intras[dn].mean()) if int(dn.sum()) else None,
                "dn_win": float((intras[dn] < 0).mean() * 100) if int(dn.sum()) else None,
            })
        except Exception as e:
            log(f"  BT {adr}: skip ({e})")
    results.sort(key=lambda r: -r["corr"])
    log(f"  バックテスト: {len(results)}銘柄")
    return results


# -----------------------------------------
# HTML
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>寄り前チェック - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 22px 0 10px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }}
  .rowcap {{ font-size: 0.85rem; color: #fbbf24; margin: 14px 0 6px; font-weight: 600; }}
  .card {{
    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    padding: 12px 16px; min-width: 200px; max-width: 330px; flex: 1;
  }}
  .card .label {{ font-size: 0.75rem; color: #94a3b8; margin-bottom: 4px; }}
  .card .value {{ font-size: 1.15rem; font-weight: 700; color: #f8fafc; }}
  .card .label.big + .value {{ font-size: 1.6rem; }}
  .card .sub {{ font-size: 0.78rem; color: #94a3b8; margin-top: 4px; line-height: 1.5; }}
  .pos {{ color: #4ade80; font-weight: 700; }}
  .neg {{ color: #f87171; font-weight: 700; }}
  .ok {{ color: #93c5fd; font-weight: 700; }}
  .warn {{ color: #fca5a5; font-weight: 700; }}
  .table-wrap {{ overflow-x: auto; max-width: 900px; }}
  table {{ border-collapse: collapse; font-size: 0.85rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 8px 12px;
    text-align: right; font-weight: 600; white-space: nowrap;
  }}
  thead th:nth-child(-n+2) {{ text-align: left; }}
  td {{
    padding: 7px 12px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:nth-child(-n+2) {{ text-align: left; }}
  tr:hover td {{ background: #16213a; }}
  .evidence {{
    background: rgba(59,130,246,0.08); border: 1px solid rgba(59,130,246,0.3);
    border-radius: 8px; padding: 10px 14px; font-size: 0.8rem; line-height: 1.8;
    max-width: 1100px; margin-bottom: 16px;
  }}
  .evidence b {{ color: #93c5fd; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.9; max-width: 1100px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .num {{ color: #fbbf24; font-weight: 700; }}
  .card .label.big {{ font-size: 1.05rem; color: #cbd5e1; margin-bottom: 6px; }}
  .qbox {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; max-width: 1100px; }}
  .q {{ flex: 1; min-width: 300px; background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 12px 16px; font-size: 0.82rem; line-height: 1.7; }}
  .q .qt {{ color: #fbbf24; font-weight: 700; margin-bottom: 4px; }}
  .q .qv {{ font-size: 1.1rem; color: #f8fafc; font-weight: 700; }}
  .q .qs {{ color: #94a3b8; font-size: 0.78rem; }}
  thead th.grp {{ text-align: center; color: #fbbf24; border-bottom: 1px solid #334155; font-size: 0.8rem; }}
  thead th.grp.sep, td.sep {{ border-left: 1px solid #334155; }}
  td.read {{ text-align: left; color: #cbd5e1; white-space: normal; min-width: 220px; }}
  td.small {{ color: #64748b; font-size: 0.78rem; }}
  table.compact {{ font-size: 0.78rem; }}
  table.compact th, table.compact td {{ padding: 5px 7px; }}
  table.compact thead th {{ white-space: normal; line-height: 1.35; vertical-align: bottom; }}
  table.compact td.read {{ min-width: 130px; }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="map.html" style="border-color:#94a3b8">デッキの見方</a>
    <a href="calendar.html" style="border-color:#94a3b8">イベント予定</a>
    <a href="yorimae.html" class="active" style="border-color:#94a3b8">寄り前</a>
    <a href="cpi.html" style="border-color:#7c3aed">米インフレと雇用</a>
    <a href="fedwatch.html" style="border-color:#7c3aed">FRB利上げ確率</a>
    <a href="totan.html" style="border-color:#7c3aed">日銀利上げ確率</a>
    <a href="kinri.html" style="border-color:#7c3aed">金利と為替</a>
    <a href="kanryu.html" style="border-color:#7c3aed">還流ウォッチ</a>
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
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
    <a href="insider.html" style="border-color:#db2777">インサイダー売買</a>
    <a href="margin.html" style="border-color:#db2777">銘柄チェッカー</a>
    <a href="buffett.html" style="border-color:#db2777">バフェット</a>
    <a href="cramer.html" style="border-color:#db2777">クレイマー</a>
    <a href="kijitsu.html" style="border-color:#db2777">信用期日</a>
    <a href="kijitsu_us.html" style="border-color:#db2777">下落日数(US)</a>
    <a href="fx_corr.html" style="border-color:#db2777">円安/円高相関</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>寄り前チェック — 夜間先物ギャップ × ADR</h1>
  <p class="subtitle">最終更新: {updated} | 1日3回更新: 7:15（予告①②⑤⑧⑨⑩＝6:00時点の値）→ 9:30（③⑥⑦⑪）→ 15:45（④⑫⑬） | データ: CME日経先物・S&amp;P500・ドル円・主要ADR（yfinance）{holiday_note}</p>
  <div class="evidence">
    <b>見方（番号①〜⑬は下の答え合わせ表の列と共通。「ギャップ」は全部「①前日終値から何%離れているか」）:</b><br>
    <b class="num">⑤ 夜間先物ギャップ</b>（②夜間先物の終値 − ①前日終値）＝ 今朝の寄り付き目安。日経平均の現物は夜間先物の水準にほぼ揃って寄り付く。<br>
    <b class="num">⑨ 乖離</b>（⑤ − ⑧理論値）＝ ⑤のうち<b>米株とドル円で説明できなかった分</b>。
    ⑧理論値は「米株とドル円だけ見たら本来こう動くはず」という計算値。
    ⑩判定: |⑨|が{dev_th}%以内なら<span class="ok">理論通り（青）</span>、超えたら<span class="warn">日本固有要因：買い／売り（赤）</span>＝夜のうちに日本株に固有の買い（上振れ）/売り（下振れ）が入った。<br>
    <b class="num">ADRギャップ</b> ＝ 個別銘柄の寄り付き目安。プラス＝米国市場で東京終値より高く買われた。<br>
    <span style="color:#94a3b8">9:30の更新で③当日始値（⑥⑦⑪）、15:45の更新で④当日終値（⑫⑬）が埋まり、その日のうちに採点が終わる → 下の「答え合わせ（履歴）」表。今朝のカードの数字は、その表の一番上の行。</span>
  </div>
{cards}
  <h2>ADRギャップ（米国終値の円換算 vs 東京前日終値）— 個別銘柄の寄り付き目安</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>銘柄</th><th>コード</th><th>東京前日終値</th><th>ADR円換算</th><th>ギャップ</th></tr></thead>
    <tbody>
{adr_rows}
    </tbody>
  </table>
  </div>
  <div class="evidence" style="margin-top:12px">
    <b>ADRギャップの答え合わせ（蓄積中）:</b> 毎朝のADRギャップを記録し、その日の実際の寄り・日中リターンで検証する。
    ①<b>予告の精度</b>=|ADRギャップ−実際の寄りギャップ|の平均誤差（NYSE上場は精度が高く、OTCは値が飛びやすい想定）。
    ②<b>平均回帰</b>=固有ギャップ（ADRギャップ−指数ギャップ）が大きい銘柄はその日中に戻すのか。<br>
{adr_stats_line}
  </div>
  <h2>ADR追随の銘柄別検証（過去約1年バックテスト）</h2>
  <div class="evidence">
    <b>検証の趣旨:</b> 「東京で動いた分<b>以上に</b>ADRが動いた分（=ADRギャップ）」は、翌日の東京にどれだけ反映されるのか。
    ADRの単純な日次騰落には「東京がその日すでに動いた分への追いつき」が混ざるため、
    ここでは上と同じ<b>ADR円換算÷東京前日終値</b>の定義で過去約1年分を再構成して検証する（毎朝再計算）。<br>
    ①<b>寄りの追随</b>: 方向一致率=翌朝の寄りがADRと同方向に開いた率／感応度β=ADRギャップ1%につき寄りが何%動くか（1.0で完全追随）／相関。
    ②<b>日中の続き</b>: ADR↑の日（ギャップ≥+0.5%）とADR↓の日（≤−0.5%）の寄り→引け平均。
    <b>勝率</b>=ADR↑の日に寄りで買って大引けまで持ったら日中プラスだった率、<b>続落率</b>=ADR↓の日に日中もマイナスだった率。
    <b>寄りで完全に織り込むなら日中は0近辺</b>（=寄り後に乗っても取れない）。プラスに偏れば日中も続伸、マイナスなら寄りで行き過ぎ→平均回帰。<br>
    ※米国休場明けなどADR終値が東京前日の引けより古い日は除外。相関の高い順。
  </div>
  <div class="table-wrap" style="max-width:1100px">
  <table>
    <thead><tr><th>銘柄</th><th>コード/市場</th><th>N</th><th>寄り方向一致</th><th>同 |ADR|≥1%</th><th>感応度β</th><th>相関</th><th>平均誤差</th><th>ADR↑日の日中</th><th>同勝率</th><th>ADR↓日の日中</th><th>同続落率</th></tr></thead>
    <tbody>
{adr_bt_rows}
    </tbody>
  </table>
  </div>
  <h2>答え合わせ（履歴）— 前提の4つの値①〜④から、2つの問いを採点する</h2>
  <div class="evidence">
    毎朝7:15に出した数字を、9:30（始値）と15:45（終値）の更新でその日のうちに採点する。列番号①〜⑬は上のカードと共通。<br>
    <b class="num">前提</b> ①前日終値 ②夜間先物の終値（6:00） ③当日始値 ④当日終値 — この4つの値から下の全列が式で決まる。<br>
    <b class="num">問い1</b> 夜間先物ギャップ⑤は、実際の寄り付き⑥をどれだけ当てたか（⑦＝6:00〜9:00の間に動いた分）。<br>
    <b class="num">問い2</b> ⑤のうち米株とドル円で説明できない分＝乖離は、引けまでに埋まったか。
    まず6:00時点の乖離⑨（=⑤−⑧理論値）が±{dev_th}%以内なら<span class="ok">理論通り（青）</span>、超えたら<span class="warn">日本固有要因（赤）</span>（⑩）。
    赤の朝は、その乖離を9:00時点⑪→15:30時点⑫まで追い、引けまでに埋まればノイズ（需給）、残れば本物の材料（⑬）。
    埋まる日が多ければ「赤の朝は寄りで飛びつかず戻りを待つ」、残る日が多ければ「赤は順張り」という実務ルールに昇格できる。
  </div>
{qbox}
  <div class="table-wrap" style="max-width:none">
  <table class="compact">
    <thead>
      <tr><th></th><th class="grp sep" colspan="4">前提（円）</th><th class="grp sep" colspan="3">問い1 夜間先物ギャップ（6:00）は、寄り付きを当てたか</th><th class="grp sep" colspan="6">問い2 理論値との乖離は、引けまでに埋まったか（⑨6:00 → ⑪9:00 → ⑫15:30）</th></tr>
      <tr><th>日付</th>
        <th class="sep">①前日終値</th><th>②夜間先物終値<br><span style="font-weight:normal">（6:00）</span></th><th>③当日始値</th><th>④当日終値</th>
        <th class="sep">⑤夜間先物ギャップ<br><span style="font-weight:normal">（②−①）</span></th>
        <th>⑥実際の寄り付きギャップ<br><span style="font-weight:normal">（③−①）</span></th>
        <th>⑦寄りまでの変化<br><span style="font-weight:normal">（⑥−⑤）</span></th>
        <th class="sep">⑧理論値<br><span style="font-weight:normal">（米株・ドル円から）</span></th>
        <th>⑨乖離（6:00時点）<br><span style="font-weight:normal">（⑤−⑧）</span></th>
        <th>⑩判定（6:00時点）<br><span style="font-weight:normal">（|⑨|と{dev_th}%・暫定）</span></th>
        <th class="sep">⑪乖離（9:00時点）<br><span style="font-weight:normal">（⑥−⑧）</span></th>
        <th>⑫乖離（15:30時点）<br><span style="font-weight:normal">（④−①−⑧）</span></th>
        <th>⑬判定（15:30時点）<br><span style="font-weight:normal">（|⑫|と{dev_th}%・⑩→⑬）</span></th></tr>
    </thead>
    <tbody>
{history_rows}
    </tbody>
  </table>
  </div>
  <p class="note" style="margin-top:8px">
    ・%の列はすべて「①前日終値に対する%」。②〜④は円。<br>
    ・<b>②夜間先物の終値</b>＝CME日経先物の6:00（日本時間）のバーの終値。CME先物は6:00〜7:00に休憩、大証ナイトセッションも6:00終了なので「夜間の終値」に相当。7:15の実行時刻ではなく6:00の値を取るため、PCの起動が遅れて8:50や10:00に走っても同じ値になる。<br>
    ・<b>⑦寄りまでの変化</b>が大きい日は、6:00以降のニュース（7:00〜の先物再開・8:50の国内指標など）か寄り付きの板の偏り。<br>
    ・<b>⑧理論値</b>＝「米株とドル円だけを見たら本来こう動くはず」の値。b1×S&amp;P500前日騰落% + b2×ドル円変化%（係数は過去1年の日次回帰、ページ最下部に表示）。<br>
    ・<b>問い2の⑨⑪⑫</b>は同じ「乖離＝理論値との差」を3つの時点で追う: ⑨6:00時点（夜間終値） → ⑪9:00時点（寄り） → ⑫15:30時点（引け）。寄りで埋めた分＝⑨−⑪、日中で埋めた分＝⑪−⑫。<br>
    ・<b>⑩と⑬</b>は同じ物差し: 乖離の絶対値が{dev_th}%以内なら<span class="ok">理論通り</span>、超えたら<span class="warn">日本固有要因：買い/売り</span>。⑩は⑨（6:00時点）に、⑬は⑫（15:30時点）に当てる。
    組み合わせで <span class="ok">赤→青 埋まった</span>（夜の日本固有要因は引けまでに消えた＝ノイズ・需給）／<span class="warn">赤→赤 残った</span>（引けまで残った＝本物の材料。逆符号なら反転）／
    <span class="warn">青→赤 日中に発生</span>（夜は理論通りだったが日本の場中で何か起きた）／<span class="ok">青→青 理論通り</span>。⑪が⑨の1.2倍超なら「寄りで拡大」。<br>
    ・しきい値{dev_th}%は<b>暫定（仮説）</b>。⑫の分布が溜まったら見直す（例: 過去1年の|⑫|の中央値や標準偏差から決める）。<br>
    ・前日終値の日足がYahooに無かった朝（8/31・9/1）は翌朝に正しい終値で再計算済み（<span class="small">※</span>印）。<br>
    ・土日・祝日の実行は記録しない（表に行が増えるのは平日の朝だけ）。
  </p>
  <p class="note">
    ・<b>②夜間先物の終値</b> = CME日経平均先物（円建てNIY=F、取得不可時はドル建てNKD=F）の6:00の値。<b>⑤夜間先物ギャップ</b>はこれと①前日終値の差。<br>
    ・<b>⑧理論値</b> = b1×S&amp;P500前日騰落% + b2×ドル円変化%（係数は過去1年の日次回帰で自前推定、下の行に係数表示）。
    ドル円の起点はNY終値で近似しているため厳密な分解ではなく目安。<br>
    ・<b>ADR換算比率</b> = 直近{ratio_window}日の（ADR価格×ドル円）÷東京終値の中央値。倍率をハードコードしないため株式分割にも自動追随する。
    定義上、平常時のギャップは0近辺になり、表示される値は「昨晩ついた固有のプレミアム/ディスカウント」。<br>
    ・ADRは米国での流動性が薄い銘柄（OTC系）ほどノイズが大きい。大型のTM/SONY/MUFG等を優先的に信頼する。<br>
    ・{beta_note}
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def fmt_pct(v, digits=2):
    if v is None:
        return "-"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+.{digits}f}%</span>'


def generate_html(data, hist=None):
    hist = hist or {"days": {}}
    n225_prev = data["n225_prev"]
    fut = data["fut_last"]
    gap_pct = data["gap_pct"]
    gap_yen = data["gap_yen"]
    theo = data["theo_gap"]
    dev = (gap_pct - theo) if (theo is not None and gap_pct is not None) else None

    # 採点対象の日付（今日が休場日なら次の平日）。③④以降はその日の場が終わるまで「（−）」
    target = datetime.date.today()
    while target.weekday() >= 5:
        target += datetime.timedelta(days=1)
    tlabel = f"{target.month}/{target.day}"
    pending_big = f'<div class="value" style="color:#64748b">{tlabel}（−）</div>'

    def card(num, title, value_html, sub_html="", big=False, style=""):
        return (f'    <div class="card"{" style=" + chr(34) + style + chr(34) if style else ""}>'
                f'<div class="label{" big" if big else ""}"><b class="num">{num}</b> {title}</div>'
                f'{value_html}<div class="sub">{sub_html}</div></div>\n')

    def val(html, cls=""):
        return f'<div class="value"><span class="{cls}">{html}</span></div>' if cls else f'<div class="value">{html}</div>'

    tv = day_values(data["today_rec"]) if data.get("today_rec") else None   # 当日レコードの①〜⑬（未確定はNone）

    def pv(key, fmt="pct"):
        """当日レコードに値があれば表示、無ければ「9/7（−）」"""
        x = tv.get(key) if tv else None
        if x is None:
            return pending_big
        if fmt == "yen":
            return val(f"{x:,.0f}円")
        if fmt == "plain":
            return val(f"{x:+.2f}%")
        return val(fmt_pct(x))

    # ---- 前提（円）----
    row1 = [card("①", "前日終値", val(f"{n225_prev:,.0f}円"), f'{data["n225_date"]} 大引け')]
    if fut is not None:
        obs = data.get("obs_time") or "6:00"
        row1.append(card("②", f"夜間先物の終値（CME日経先物・{obs}）", val(f"{fut:,.0f}円"),
                         f'{data["fut_ticker"]}。何時に実行しても6:00時点の値を取る（大証ナイト終値と同水準）', big=True))
    else:
        row1.append(card("②", "夜間先物の終値（CME日経先物）", val("取得失敗"), "yfinance側の一時的な問題の可能性", big=True))
    row1.append(card("③", "当日始値", pv("p3", "yen"), "9:00に確定 → 9:30の更新で入る"))
    row1.append(card("④", "当日終値", pv("p4", "yen"), "15:30に確定 → 15:45の更新で入る"))
    # 参考（⑧理論値の材料）
    row1.append(card("参考", "S&amp;P500（前日）", val(fmt_pct(data["spx_ret"])), f'NASDAQ {fmt_pct(data["ndx_ret"])}'))
    row1.append(card("参考", "ドル円", val(f'{data["fx_now"]:.2f}円' if data.get("fx_now") else "-"),
                     f'NY前日終値比 {fmt_pct(data["fx_chg"])}'))

    # ---- 問い1 ----
    row2 = [card("⑤", "夜間先物ギャップ（②−①）＝ 今朝の寄り付き目安",
                 val(f'{fmt_pct(gap_pct)}') if gap_pct is not None else val("-"),
                 (f'{gap_yen:+,.0f}円。現物はこの水準に揃って寄り付きやすい' if gap_yen is not None else ""), big=True),
            card("⑥", "実際の寄り付きギャップ（③−①）", pv("g6"), "9:30の更新で入る"),
            card("⑦", "寄りまでの変化（⑥−⑤）", pv("g7", "plain"), "6:00〜9:00の間に動いた分")]

    # ---- 問い2 ----
    if data.get("betas") and theo is not None:
        b1, b2, _ = data["betas"]
        theo_sub = (f'S&amp;P500 {data["spx_ret"]:+.2f}%×{b1:.2f} + ドル円 {data["fx_chg"]:+.2f}%×{b2:.2f}<br>'
                    f'「米株とドル円だけ見たら本来こう動くはず」の値')
    else:
        theo_sub = "データ不足で計算できず"
    row3 = [card("⑧", "理論値（米株・ドル円から）", val(fmt_pct(theo)) if theo is not None else val("-"), theo_sub)]
    if dev is not None:
        cls = classify_dev(dev)
        dev_cls = "ok" if cls == "neutral" else "warn"
        row3.append(card("⑨", "乖離（6:00時点）＝ ⑤ − ⑧", val(f"{dev:+.2f}%", dev_cls),
                         f'⑤ {fmt_pct(gap_pct)} − ⑧ {fmt_pct(theo)}。米株・ドル円で説明できない分', big=True))
        if cls == "neutral":
            j_html = '<div class="value" style="font-size:1.3rem"><span class="ok">理論通り</span></div>'
            j_sub = f"|⑨|が{DEVIATION_TH}%以内。米株・ドル円で説明がつく動き"
        else:
            direction = "買い" if dev > 0 else "売り"
            j_html = f'<div class="value" style="font-size:1.3rem; white-space:nowrap"><span class="warn">日本固有要因：{direction}</span></div>'
            j_sub = f"|⑨|が{DEVIATION_TH}%超（暫定しきい値）。夜のうちに日本株固有の{direction}が入った"
        row3.append(card("⑩", "判定（6:00時点）", j_html, j_sub, big=True))
    else:
        row3.append(card("⑨", "乖離（6:00時点）＝ ⑤ − ⑧", val("-"), "理論値が無いため計算できず", big=True))
        row3.append(card("⑩", "判定（6:00時点）", val("-"), "", big=True))
    row3.append(card("⑪", "乖離（9:00時点）＝ ⑥ − ⑧", pv("d11"), "寄り付きで乖離がどう変わったか（9:30の更新で入る）"))
    row3.append(card("⑫", "乖離（15:30時点）＝（④−①）− ⑧", pv("d12"), "引けで理論値との差がいくら残ったか（15:45の更新で入る）"))
    if tv and tv.get("k13"):
        label13, lcls13 = K13_LABEL[tv["k13"]]
        if tv["k13"] in ("remained", "reversed", "emerged"):
            label13 += "（日本固有要因：" + ("買い" if tv["cls12"] == "buy" else "売り") + "）"
        pat = ("赤" if tv["cls"] in ("buy", "sell") else "青") + "→" + ("赤" if tv["cls12"] in ("buy", "sell") else "青")
        row3.append(card("⑬", "判定（15:30時点）",
                         f'<div class="value" style="font-size:1.3rem"><span class="{lcls13}">{label13}</span></div>',
                         f"⑩→⑬ ＝ {pat}。|⑫|と{DEVIATION_TH}%（暫定）で判定", big=True))
    else:
        row3.append(card("⑬", "判定（15:30時点）", pending_big, "⑩→⑬で 埋まった／残った／日中に発生／理論通り（15:45の更新で入る）"))

    cards = [
        f'    <div class="rowcap">前提（円）— この4つの値から下の全列が決まる（＋⑧の材料）</div>\n    <div class="cards">\n{"".join(row1)}    </div>\n',
        f'    <div class="rowcap">問い1 夜間先物ギャップは、寄り付きを当てたか</div>\n    <div class="cards">\n{"".join(row2)}    </div>\n',
        f'    <div class="rowcap">問い2 理論値との乖離は、引けまでに埋まったか（⑨6:00 → ⑪9:00 → ⑫15:30）</div>\n    <div class="cards">\n{"".join(row3)}    </div>\n',
    ]

    adr_rows = []
    for r in data["adr"]:
        adr_rows.append(
            f'      <tr><td>{r["name"]}</td><td>{r["tyo"]} / {r["adr"]}</td>'
            f'<td>{r["tyo_prev"]:,.0f}円</td><td>{r["implied"]:,.0f}円</td>'
            f'<td>{fmt_pct(r["gap"])}</td></tr>')

    if data["betas"]:
        b1, b2, r2 = data["betas"]
        beta_note = (f'<b>回帰係数（過去{BETA_LOOKBACK}日）</b>: 日経リターン ≈ {b1:.2f}×S&P500前日 + {b2:.2f}×ドル円変化'
                     f'（決定係数R²={r2:.2f}）' if r2 is not None else
                     f'<b>回帰係数（過去{BETA_LOOKBACK}日）</b>: b1={b1:.2f}, b2={b2:.2f}')
    else:
        beta_note = '回帰係数: データ不足のため理論値は非表示'

    # 乖離履歴（新しい順に最大30日）
    history_rows = []
    today_key = datetime.date.today().isoformat()

    def yen(v):
        return f"{v:,.0f}" if v is not None else "-"

    def judge10(cls):
        return ('<span class="warn">日本固有要因：買い</span>' if cls == "buy" else
                '<span class="warn">日本固有要因：売り</span>' if cls == "sell" else
                '<span class="ok">理論通り</span>' if cls == "neutral" else "-")

    def plain(v):
        return f"{v:+.2f}%" if v is not None else "-"

    if today_key not in hist["days"] and gap_pct is not None:
        # 休場日（土日など）は履歴に記録しないが、カードと表の対応が見えるように「今朝」の行だけ表示する
        dev_now = (gap_pct - theo) if theo is not None else None
        history_rows.append(
            f'      <tr style="opacity:0.75"><td style="white-space:nowrap">{today_key[5:]} '
            f'<span class="num" style="font-size:0.75rem">◀ 今朝</span> <span class="small">休場日・記録なし</span></td>'
            f'<td class="sep">{yen(n225_prev)}</td><td>{yen(fut)}</td><td>-</td><td>-</td>'
            f'<td class="sep">{fmt_pct(gap_pct)}</td><td>-</td><td>-</td>'
            f'<td class="sep">{fmt_pct(theo)}</td><td>{fmt_pct(dev_now)}</td><td>{judge10(classify_dev(dev_now))}</td>'
            f'<td class="sep">-</td><td>-</td><td class="read small">休場日のため採点なし（次の営業日の朝から記録）</td></tr>')
    for k in sorted(hist["days"], reverse=True)[:30]:
        rec = hist["days"][k]
        v = day_values(rec)
        mark = '<span class="small">※</span>' if rec.get("repaired") else ""
        is_today = (k == today_key)
        if is_today:
            mark += ' <span class="num" style="font-size:0.75rem" title="上のカードと同じ数字。③は9:30、④は15:45の更新で埋まる">◀ 今日</span>'
        if v["k13"]:
            label, lcls = K13_LABEL[v["k13"]]
            if v["k13"] in ("remained", "reversed", "emerged"):
                label += "（日本固有要因：" + ("買い" if v["cls12"] == "buy" else "売り") + "）"
            expand = "・寄りで拡大" if (v["cls"] in ("buy", "sell") and v["d11"] is not None and v["d9"] and v["d11"] / v["d9"] > 1.2) else ""
            j13 = f'<span class="{lcls}">{label}</span>{expand}'
        elif v["g6"] is None:
            j13 = '<span class="small">' + ("9:30/15:45の更新で採点" if is_today else "採点待ち") + '</span>'
        else:
            j13 = "-"
        history_rows.append(
            f'      <tr><td style="white-space:nowrap">{k[5:]}{mark}</td>'
            f'<td class="sep">{yen(v["p1"])}</td><td>{yen(v["p2"])}</td><td>{yen(v["p3"])}</td><td>{yen(v["p4"])}</td>'
            f'<td class="sep">{fmt_pct(v["g5"])}</td><td>{fmt_pct(v["g6"])}</td><td>{plain(v["g7"])}</td>'
            f'<td class="sep">{fmt_pct(v["t8"])}</td><td>{fmt_pct(v["d9"])}</td><td>{judge10(v["cls"])}</td>'
            f'<td class="sep">{fmt_pct(v["d11"])}</td><td>{fmt_pct(v["d12"])}</td><td class="read">{j13}</td></tr>')
    if not history_rows:
        history_rows.append('      <tr><td colspan="14" style="text-align:center; color:#64748b">蓄積中（毎朝の実行で1行ずつ増えます）</td></tr>')

    qbox = build_qbox(hist)

    # ADR答え合わせの集計
    adr_n, prec, buckets = build_adr_stats(hist)
    if adr_n >= 30:
        parts = []
        for mkt in ("NYSE", "OTC"):
            errs = prec.get(mkt, [])
            if errs:
                parts.append(f'<b>{mkt}の予告精度</b>: 平均誤差{sum(errs)/len(errs):.2f}%（N={len(errs)}）')
        for g, rows in buckets.items():
            if rows:
                parts.append(f'<b>{g}</b>: N={len(rows)} 日中平均{sum(rows)/len(rows):+.2f}%')
        adr_stats_line = "    " + " ／ ".join(parts)
    else:
        adr_stats_line = f'    集計は30サンプルから表示（現在{adr_n}件・15銘柄×営業日で蓄積）。'

    # ADR追随バックテスト（銘柄別）
    adr_bt_rows = []
    for r in data.get("adr_bt") or []:
        mb = f'{r["match_big"]:.0f}% (N={r["big_n"]})' if r["match_big"] is not None else "-"
        up_s = f'{fmt_pct(r["up_intra"])} (N={r["up_n"]})' if r["up_intra"] is not None else "-"
        dn_s = f'{fmt_pct(r["dn_intra"])} (N={r["dn_n"]})' if r["dn_intra"] is not None else "-"
        up_w = (f'<span class="{"ok" if r["up_win"] >= 55 else ""}">{r["up_win"]:.0f}%</span>'
                if r.get("up_win") is not None else "-")
        dn_w = (f'<span class="{"warn" if r["dn_win"] >= 55 else ""}">{r["dn_win"]:.0f}%</span>'
                if r.get("dn_win") is not None else "-")
        match_cls = "ok" if r["match"] >= 65 else ""
        adr_bt_rows.append(
            f'      <tr><td>{r["name"]}</td><td>{r["tyo"]} / {r["mkt"]}</td>'
            f'<td>{r["n"]}</td>'
            f'<td><span class="{match_cls}">{r["match"]:.0f}%</span></td>'
            f'<td>{mb}</td>'
            f'<td>{r["beta"]:.2f}</td>'
            f'<td>{r["corr"]:.2f}</td>'
            f'<td>{r["err"]:.2f}%</td>'
            f'<td>{up_s}</td><td>{up_w}</td><td>{dn_s}</td><td>{dn_w}</td></tr>')
    if not adr_bt_rows:
        adr_bt_rows.append('      <tr><td colspan="12" style="text-align:center; color:#64748b">計算失敗（yfinance側の一時的な問題の可能性）</td></tr>')

    is_holiday = datetime.date.today().weekday() >= 5
    holiday_note = (' | <span class="warn">休場日</span>: カードの数字は直前の取引日の夜間の値。記録・採点は次の営業日から'
                    if is_holiday else "")
    html = HTML_TEMPLATE.format(
        holiday_note=holiday_note,
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        dev_th=DEVIATION_TH,
        ratio_window=RATIO_WINDOW,
        cards="".join(cards),
        adr_rows="\n".join(adr_rows),
        beta_note=beta_note,
        history_rows="\n".join(history_rows),
        qbox=qbox,
        adr_stats_line=adr_stats_line,
        adr_bt_rows="\n".join(adr_bt_rows),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")


# -----------------------------------------
# push
# -----------------------------------------
def push_to_github():
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)  # 他スクリプトとのgit競合・放置ロック・中断rebase対策
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "yorimae_screen.py", "yorimae_run.bat",
                    "yorimae_history.json", ".gitignore",
                    "kabuchiwa_post.py", "x_config.example.json"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update yorimae report " + today],
        capture_output=True)
    if result.returncode != 0:
        msg = result.stdout.decode(errors="ignore") + result.stderr.decode(errors="ignore")
        if "nothing to commit" in msg:
            log("  commit skip (already committed)")
        else:
            log("  commit failed: " + msg)
            return
    for attempt in range(1, 6):
        try:
            subprocess.run(["git", "-C", SCRIPT_DIR, "pull", "--rebase", "--autostash"], check=True)
            subprocess.run(["git", "-C", SCRIPT_DIR, "push"], check=True)
            log("  Done: https://ichikon77.github.io/minervini/yorimae.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
# -----------------------------------------
# 時刻を指定したデータ取得（実行時刻に依存しない「6:00の値」を取るため）
# -----------------------------------------
ANCHOR = datetime.time(6, 0)       # 観測の基準時刻＝夜間セッションの終値（CME先物は6:00〜7:00休憩。大証ナイトも6:00終了）
OPEN_READY = datetime.time(9, 20)  # この時刻以降の実行で③当日始値を取り込む（Yahooの反映遅れ考慮）
CLOSE_READY = datetime.time(15, 40)  # この時刻以降の実行で④当日終値を取り込む


def bar_close_at(ticker, when):
    """5分足で when（JSTのaware datetime）以前の最後のバーの終値と、その時刻を返す。無ければ (None, None)"""
    try:
        h = yf.Ticker(ticker).history(period="5d", interval="5m")
        if h.empty:
            return None, None
        h = h.set_axis(h.index.tz_convert("Asia/Tokyo"))
        h = h[h.index < when]["Close"].dropna()      # when より前に始まったバー（6:00なら 05:55 のバー＝6:00の終値）
        if h.empty:
            return None, None
        return float(h.iloc[-1]), h.index[-1].to_pydatetime()
    except Exception:
        return None, None


def today_ohlc(day):
    """当日の日中足から O/H/L/C を組む（日足がまだ無い時間帯用）。無ければ None"""
    try:
        h = yf.Ticker("^N225").history(period="5d", interval="5m")
        if h.empty:
            return None
        h = h.set_axis(h.index.tz_convert("Asia/Tokyo"))
        h = h[h.index.date == day].dropna(subset=["Open", "Close"])
        if h.empty:
            return None
        return {"open": round(float(h["Open"].iloc[0]), 1), "high": round(float(h["High"].max()), 1),
                "low": round(float(h["Low"].min()), 1), "close": round(float(h["Close"].iloc[-1]), 1)}
    except Exception:
        return None


def observe_morning(today):
    """6:00時点（夜間セッション終値）の観測（①②⑤⑧⑨と参考値）を作る。何時に実行しても同じ値になるように、
    先物・ドル円は「6:00直前のバー」、日経・米株の前日終値は「今日より前の日足」から取る"""
    jst = datetime.timezone(datetime.timedelta(hours=9))
    anchor = datetime.datetime.combine(today, ANCHOR, tzinfo=jst)

    # ①前日終値: 今日より前の日足のみ（寄り付き後に実行しても当日の値が混ざらない）
    n225 = daily_closes("^N225")
    if not n225.empty:
        n225 = n225[n225.index.date < today]
    if n225.empty:
        log("エラー: 日経平均を取得できません")
        sys.exit(1)
    n225_prev = float(n225.iloc[-1])
    n225_date = n225.index[-1].strftime("%m/%d")
    expected = today - datetime.timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= datetime.timedelta(days=1)
    if n225.index[-1].date() < expected:
        # Yahooの^N225は前日の日足が朝まだ無いことがある → 60分足の最終値で復元
        try:
            intr = yf.Ticker("^N225").history(start=expected, end=expected + datetime.timedelta(days=1), interval="60m")
            if len(intr):
                n225_prev = float(intr["Close"].iloc[-1])
                n225_date = expected.strftime("%m/%d")
                n225.loc[pd.Timestamp(expected)] = n225_prev
                log(f"  ^N225日足に{expected}が無い → 60分足から前日終値 {n225_prev:,.0f} を復元")
            else:
                log(f"  ^N225: {expected}は休場か日足未着 → 日足の最終日 {n225_date} を前日として使用")
        except Exception as e:
            log(f"  前日終値の復元失敗（日足の最終日を使用）: {e}")

    # ②夜間先物の終値（円建て優先）: 5分足の6:00直前のバー。取れなければ最新値で代用（時刻を記録）
    fut_last, fut_ticker, fut_time, anchored = None, None, None, False
    for tk in ("NIY=F", "NKD=F"):
        v, t = bar_close_at(tk, anchor)
        if v and v > 10000:
            fut_last, fut_ticker, fut_time, anchored = v, tk, t, True
            break
    if fut_last is None:
        for tk in ("NIY=F", "NKD=F"):
            v = last_price(tk)
            if v and v > 10000:
                fut_last, fut_ticker, fut_time = v, tk, datetime.datetime.now(jst)
                log(f"  {tk}: 6:00のバーが取れず最新値で代用")
                break
    gap_pct = (fut_last / n225_prev - 1) * 100 if fut_last else None
    gap_yen = fut_last - n225_prev if fut_last else None
    log(f"①前日終値 {n225_prev:,.0f}（{n225_date}） / ②先物 {fut_last} ({fut_ticker}, {fut_time:%H:%M} 時点) / ⑤ギャップ {gap_pct}"
        if fut_time else f"①前日終値 {n225_prev:,.0f} / ②先物 取得失敗")

    # 参考: 米株・ドル円（前日終値は今日より前の日足、ドル円は6:00のバー）
    def before_today(sr):
        return sr[sr.index.date < today] if not sr.empty else sr
    spx = before_today(daily_closes("^GSPC"))
    ndx = before_today(daily_closes("^IXIC"))
    fx = before_today(daily_closes("JPY=X"))
    spx_ret = float((spx.iloc[-1] / spx.iloc[-2] - 1) * 100) if len(spx) >= 2 else None
    ndx_ret = float((ndx.iloc[-1] / ndx.iloc[-2] - 1) * 100) if len(ndx) >= 2 else None
    fx_now, _ = bar_close_at("JPY=X", anchor)
    if fx_now is None:
        fx_now = last_price("JPY=X") or (float(fx.iloc[-1]) if len(fx) else None)
    fx_chg = (fx_now / float(fx.iloc[-1]) - 1) * 100 if fx_now and len(fx) else None

    # ⑧理論値
    betas = estimate_betas(n225, spx, fx)
    theo = None
    if betas and spx_ret is not None and fx_chg is not None:
        b1, b2, _ = betas
        theo = b1 * spx_ret + b2 * fx_chg
    dev = (gap_pct - theo) if (gap_pct is not None and theo is not None) else None
    log(f"S&P500前日 {spx_ret} / ドル円変化 {fx_chg} / ⑧理論値 {theo} / ⑨乖離 {dev}")

    return {
        "prev_close": n225_prev, "n225_date": n225_date,
        "fut": fut_last, "fut_ticker": fut_ticker,
        "obs_time": (lambda t: f"{t.hour}:{t.minute:02d}")(fut_time + datetime.timedelta(minutes=5)) if fut_time else None,
        "gap": None if gap_pct is None else round(gap_pct, 3),
        "theo": None if theo is None else round(theo, 3),
        "dev": None if dev is None else round(dev, 3),
        "spx_ret": spx_ret, "ndx_ret": ndx_ret,
        "fx_now": fx_now, "fx_chg": fx_chg,
        "betas": list(betas) if betas else None,
        "fut_anchored": anchored, "theo_recomputed": True,
    }


def fill_intraday(rec, today, now):
    """当日レコードに③始値（9:20以降）と④終値ほか（15:40以降）を追記する。既にあれば触らない"""
    if now.time() < OPEN_READY:
        return "morning"
    o = today_ohlc(today)
    if not o:
        log("  当日の日中足が取れず、③④は未記入のまま")
        return "intraday"
    if "open" not in rec:
        rec["open"] = o["open"]
        log(f"  ③当日始値 {o['open']:,.0f} を記録")
    if now.time() >= CLOSE_READY:
        if "close" not in rec:
            # 日足が既にあればそちらを優先（確定値）
            try:
                d = yf.Ticker("^N225").history(period="5d")
                d = d.set_axis(d.index.tz_localize(None).normalize())
                if pd.Timestamp(today) in d.index:
                    row = d.loc[pd.Timestamp(today)]
                    o = {"open": round(float(row["Open"]), 1), "high": round(float(row["High"]), 1),
                         "low": round(float(row["Low"]), 1), "close": round(float(row["Close"]), 1)}
            except Exception:
                pass
            rec.update(o)
            log(f"  ④当日終値 {o['close']:,.0f} を記録（高値 {o['high']:,.0f} / 安値 {o['low']:,.0f}）")
        return "close"
    return "intraday"


def main():
    now = datetime.datetime.now()
    today = now.date()
    log(f"寄り前チェック 開始（{now:%H:%M} 実行）")

    hist = load_history()
    key = today.isoformat()
    rec = hist["days"].get(key)
    created = False
    if rec is None:
        # 今日の観測がまだ無い → 6:00時点の値を作る（何時に実行しても同じ値になる）
        obs = observe_morning(today)
        if today.weekday() < 5:
            hist["days"][key] = obs
            rec = obs
            created = True
        else:
            rec = obs      # 休場日は記録しないが表示には使う
    else:
        log(f"  本日の6:00観測は記録済み（②先物 {rec.get('fut')} / ⑤ {rec.get('gap')}%）→ 追記モード")
        # 旧形式（参考値なし）のレコードは参考値だけ補う
        if "spx_ret" not in rec:
            obs = observe_morning(today)
            for k2 in ("spx_ret", "ndx_ret", "fx_now", "fx_chg", "betas", "n225_date", "fut_ticker", "obs_time"):
                rec.setdefault(k2, obs.get(k2))

    # ADR（米国終値ベースなので実行時刻に依存しない）
    adr = calc_adr_gaps()
    adr_bt = backtest_adr_follow()
    if today.weekday() < 5 and adr and "adr" not in hist["days"].get(key, {}):
        hist["days"][key]["adr"] = {
            r["tyo"]: {"gap": round(r["gap"], 2), "prev": r["tyo_prev"], "mkt": r["mkt"]} for r in adr}

    # 当日の③④を時刻に応じて追記
    phase = "morning"
    if today.weekday() < 5:
        phase = fill_intraday(hist["days"][key], today, now)

    # 過去分の答え合わせ・修復
    backfill_answers(hist)
    finalize_days(hist)
    reanchor_futures(hist)
    repair_stale_prev(hist)
    recompute_theo(hist, rec.get("betas"))
    backfill_adr_answers(hist)
    save_history(hist)

    gap_pct, theo = rec.get("gap"), rec.get("theo")
    data = {
        "n225_prev": rec["prev_close"], "n225_date": rec.get("n225_date", ""),
        "fut_last": rec.get("fut"), "fut_ticker": rec.get("fut_ticker"), "obs_time": rec.get("obs_time"),
        "gap_pct": gap_pct, "gap_yen": (rec["fut"] - rec["prev_close"]) if rec.get("fut") else None,
        "spx_ret": rec.get("spx_ret"), "ndx_ret": rec.get("ndx_ret"),
        "fx_now": rec.get("fx_now"), "fx_chg": rec.get("fx_chg"),
        "theo_gap": theo, "betas": rec.get("betas"),
        "adr": adr, "adr_bt": adr_bt,
        "today_rec": hist["days"].get(key), "phase": phase,
    }
    generate_html(data, hist)

    # X自動投稿用の数値（kabuchiwa_post.py が読む。朝フェーズ以外は投稿しない）
    try:
        json.dump({
            "generated": now.isoformat(timespec="seconds"), "phase": phase, "created_today": created,
            "n225_prev": rec["prev_close"], "n225_date": rec.get("n225_date", ""),
            "fut_last": rec.get("fut"), "fut_ticker": rec.get("fut_ticker"), "obs_time": rec.get("obs_time"),
            "gap_pct": gap_pct, "gap_yen": data["gap_yen"],
            "spx_ret": rec.get("spx_ret"), "ndx_ret": rec.get("ndx_ret"),
            "fx_now": rec.get("fx_now"), "fx_chg": rec.get("fx_chg"),
            "theo_gap": theo, "dev": rec.get("dev"), "dev_th": DEVIATION_TH,
            "adr": [{"name": r["name"], "tyo": r["tyo"], "mkt": r["mkt"], "gap": round(r["gap"], 2)} for r in adr],
        }, open(POST_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log(f"投稿用JSON出力: {POST_JSON}（phase={phase}）")
    except Exception as e:
        log(f"投稿用JSONの出力失敗（投稿はスキップされます）: {e}")

    if "--nopush" in sys.argv:
        log("push スキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
