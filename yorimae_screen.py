# -*- coding: utf-8 -*-
"""
寄り前チェック（夜間先物ギャップ×ADR） → yorimae.html → GitHub Pages公開

ディーラーの朝のミーティング資料の再現。毎朝7:15（CME引け後・東京寄り前）に実行し、
「今日の寄り付きがどこから始まり、その動きは米株・為替で説明がつくのか」を1ページで出す。

① 夜間先物ギャップ
  CME日経平均先物（円建て NIY=F、なければドル建て NKD=F）の夜間終値と
  前日の日経平均終値のギャップ。現物はほぼ夜間先物の水準に揃って寄り付くため、
  これが「今朝の寄り付き目安」になる。

② 理論ギャップとの答え合わせ（kinri流）
  夜間の日経先物の変動は、理屈上「米株(S&P500)の変動×ベータ + ドル円の変動×為替感応度」で
  だいたい説明できる。過去1年の日次データで回帰係数を自前推定し、
    理論ギャップ% = b1 × S&P500前日騰落% + b2 × ドル円変化%
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

BETA_LOOKBACK = 250   # 理論ギャップの回帰に使う日数（約1年）
RATIO_WINDOW = 20     # ADR換算比率の中央値を取る日数
DEVIATION_TH = 0.5    # 実測-理論ギャップの乖離がこの%を超えたら「日本固有要因あり」(赤)


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
# 理論ギャップの回帰係数（過去1年: 日経リターン ~ SPX前日リターン + ドル円変化）
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
    # 未回答の(日付, 銘柄)があるか
    need = any("adr" in v and pd.Timestamp(k) < today
               and any("o" not in s for s in v["adr"].values())
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
            if "o" in s:
                continue
            try:
                df = data[code + ".T"]
                idx = df.index.tz_localize(None).normalize()
                pos = list(idx).index(d) if d in list(idx) else None
                if pos is None:
                    continue
                s["o"] = round(float(df["Open"].iloc[pos]), 1)
                s["c"] = round(float(df["Close"].iloc[pos]), 1)
            except Exception:
                continue


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
            if "o" not in s or not s.get("prev"):
                continue
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
    if rec["open"] < prev:
        filled = rec["high"] >= prev
    elif rec["open"] > prev:
        filled = rec["low"] <= prev
    else:
        filled = None
    return open_gap, intraday, filled


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
def calc_adr_gaps():
    log("ADRギャップを計算中...")
    fx_now = last_price("JPY=X")
    out = []
    for adr, tyo, name, mkt in ADR_LIST:
        try:
            a = daily_closes(adr, period="3mo")
            j = daily_closes(tyo, period="3mo")
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
                "dn_n": int(dn.sum()),
                "dn_intra": float(intras[dn].mean()) if int(dn.sum()) else None,
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
  .cards {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 14px; }}
  .card {{
    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    padding: 14px 18px; min-width: 215px;
  }}
  .card .label {{ font-size: 0.75rem; color: #94a3b8; margin-bottom: 4px; }}
  .card .value {{ font-size: 1.35rem; font-weight: 700; color: #f8fafc; }}
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
  <p class="subtitle">最終更新: {updated}（毎朝7:15・CME引け後） | データ: CME日経先物・S&amp;P500・ドル円・主要ADR（yfinance）</p>
  <div class="evidence">
    <b>使い方:</b> 日経平均の現物は夜間先物の水準にほぼ揃って寄り付くため、①のギャップが「今朝の寄り付き目安」。
    ②はその夜間変動が<b>米株とドル円で説明がつくか</b>の答え合わせ（過去1年の回帰で理論値を計算）。
    実測と理論の乖離が小さければ<span class="ok">理論通り（青）</span>=米国由来の動き、
    乖離が±{dev_th}%超なら<span class="warn">日本固有要因（赤）</span>=海外勢の日本株への強弱や日本関連ニュースが夜間に動いた可能性。
    ③のADRギャップは個別銘柄の「今朝の寄り付き目安」。プラス=米国市場で東京終値より高く買われた。
  </div>
{cards}
  <h2>ADRギャップ（米国終値の円換算 vs 東京前日終値）</h2>
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
    <b>寄りで完全に織り込むなら日中は0近辺</b>（=寄り後に乗っても取れない）。プラスに偏れば日中も続伸、マイナスなら寄りで行き過ぎ→平均回帰。<br>
    ※米国休場明けなどADR終値が東京前日の引けより古い日は除外。相関の高い順。
  </div>
  <div class="table-wrap" style="max-width:1100px">
  <table>
    <thead><tr><th>銘柄</th><th>コード/市場</th><th>N</th><th>寄り方向一致</th><th>同 |ADR|≥1%</th><th>感応度β</th><th>相関</th><th>平均誤差</th><th>ADR↑日の日中</th><th>ADR↓日の日中</th></tr></thead>
    <tbody>
{adr_bt_rows}
    </tbody>
  </table>
  </div>
  <h2>乖離の答え合わせ（履歴）</h2>
  <div class="evidence">
    <b>検証の趣旨:</b> 「日本固有要因（赤）」が出た朝は、その後どうなりやすいのか。
    毎朝の乖離を記録し、その日の実際の寄り付き・日中リターン（寄り→引け）・ギャップ埋め（前日終値まで戻したか）で答え合わせする。
    データが溜まれば「乖離が大きい日は逆張り/順張りどちらが効くか」をkasetsu（仮説検証）に昇格させる。<br>
{stats_line}
  </div>
  <div class="table-wrap">
  <table>
    <thead><tr><th>日付</th><th>夜間ギャップ</th><th>理論</th><th>乖離</th><th>実際の寄り</th><th>日中(寄→引)</th><th>ギャップ埋め</th></tr></thead>
    <tbody>
{history_rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・<b>夜間先物</b> = CME日経平均先物（円建てNIY=F、取得不可時はドル建てNKD=F）の直近値。大証ナイトセッションとほぼ同水準。<br>
    ・<b>理論ギャップ</b> = b1×S&amp;P500前日騰落% + b2×ドル円変化%（係数は過去1年の日次回帰で自前推定、ページ下部に係数表示）。
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
    cards = []

    n225_prev = data["n225_prev"]
    fut = data["fut_last"]
    gap_pct = data["gap_pct"]
    gap_yen = data["gap_yen"]

    cards.append(
        f'    <div class="cards">\n'
        f'    <div class="card"><div class="label">日経平均 前日終値</div>'
        f'<div class="value">{n225_prev:,.0f}円</div>'
        f'<div class="sub">{data["n225_date"]} 大引け</div></div>\n')

    if fut is not None:
        cards.append(
            f'    <div class="card"><div class="label">CME日経先物（夜間）→ 寄り付き目安</div>'
            f'<div class="value">{fut:,.0f}円</div>'
            f'<div class="sub">ギャップ {fmt_pct(gap_pct)}（{gap_yen:+,.0f}円）・{data["fut_ticker"]}</div></div>\n')
    else:
        cards.append(
            '    <div class="card"><div class="label">CME日経先物（夜間）</div>'
            '<div class="value">取得失敗</div><div class="sub">yfinance側の一時的な問題の可能性</div></div>\n')

    theo = data["theo_gap"]
    if theo is not None and gap_pct is not None:
        dev = gap_pct - theo
        if abs(dev) <= DEVIATION_TH:
            judge = '<span class="ok">理論通り（米国由来の動き）</span>'
        else:
            direction = "日本買い" if dev > 0 else "日本売り"
            judge = f'<span class="warn">日本固有要因あり（{direction}方向に{abs(dev):.2f}%）</span>'
        cards.append(
            f'    <div class="card"><div class="label">理論ギャップとの答え合わせ</div>'
            f'<div class="value">{fmt_pct(theo)}</div>'
            f'<div class="sub">実測 {fmt_pct(gap_pct)} − 理論 = 乖離 {dev:+.2f}%<br>{judge}</div></div>\n')

    cards.append(
        f'    <div class="card"><div class="label">S&amp;P500（前日）</div>'
        f'<div class="value">{fmt_pct(data["spx_ret"])}</div>'
        f'<div class="sub">NASDAQ {fmt_pct(data["ndx_ret"])}</div></div>\n'
        f'    <div class="card"><div class="label">ドル円</div>'
        f'<div class="value">{data["fx_now"]:.2f}円</div>'
        f'<div class="sub">NY前日終値比 {fmt_pct(data["fx_chg"])}</div></div>\n'
        '    </div>')

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
        beta_note = '回帰係数: データ不足のため理論ギャップは非表示'

    # 乖離履歴（新しい順に最大30日）
    history_rows = []
    for k in sorted(hist["days"], reverse=True)[:30]:
        rec = hist["days"][k]
        og, intraday, filled = _answer_row(rec)
        dev = rec.get("dev")
        dev_s = fmt_pct(dev) if dev is not None else "-"
        fill_s = "-" if filled is None else ("○" if filled else "×")
        history_rows.append(
            f'      <tr><td>{k[5:]}</td>'
            f'<td>{fmt_pct(rec.get("gap"))}</td>'
            f'<td>{fmt_pct(rec.get("theo"))}</td>'
            f'<td>{dev_s}</td>'
            f'<td>{fmt_pct(og)}</td>'
            f'<td>{fmt_pct(intraday)}</td>'
            f'<td>{fill_s}</td></tr>')
    if not history_rows:
        history_rows.append('      <tr><td colspan="7" style="text-align:center; color:#64748b">蓄積中（毎朝の実行で1行ずつ増えます）</td></tr>')

    stats = build_history_stats(hist)
    total_n = sum(s[1] for s in stats)
    if total_n >= 5:
        parts = []
        for g, n, avg, fill in stats:
            if n:
                fill_s = f'・ギャップ埋め率{fill:.0f}%' if fill is not None else ""
                parts.append(f'<b>{g}</b>: N={n} 日中平均{avg:+.2f}%{fill_s}')
            else:
                parts.append(f'{g}: N=0')
        stats_line = "    " + " ／ ".join(parts)
    else:
        stats_line = f'    集計はデータが溜まってから表示（現在{total_n}日分）。'

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
        match_cls = "ok" if r["match"] >= 65 else ""
        adr_bt_rows.append(
            f'      <tr><td>{r["name"]}</td><td>{r["tyo"]} / {r["mkt"]}</td>'
            f'<td>{r["n"]}</td>'
            f'<td><span class="{match_cls}">{r["match"]:.0f}%</span></td>'
            f'<td>{mb}</td>'
            f'<td>{r["beta"]:.2f}</td>'
            f'<td>{r["corr"]:.2f}</td>'
            f'<td>{r["err"]:.2f}%</td>'
            f'<td>{up_s}</td><td>{dn_s}</td></tr>')
    if not adr_bt_rows:
        adr_bt_rows.append('      <tr><td colspan="10" style="text-align:center; color:#64748b">計算失敗（yfinance側の一時的な問題の可能性）</td></tr>')

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        dev_th=DEVIATION_TH,
        ratio_window=RATIO_WINDOW,
        cards="".join(cards),
        adr_rows="\n".join(adr_rows),
        beta_note=beta_note,
        history_rows="\n".join(history_rows),
        stats_line=stats_line,
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
                    "yorimae_history.json", ".gitignore"], check=True)
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
def main():
    log("寄り前チェック 開始")

    # 日経平均の前日終値
    n225 = daily_closes("^N225")
    if n225.empty:
        log("エラー: 日経平均を取得できません")
        sys.exit(1)
    n225_prev = float(n225.iloc[-1])
    n225_date = n225.index[-1].strftime("%m/%d")

    # 夜間先物（円建て優先）
    fut_last, fut_ticker = None, None
    for tk in ("NIY=F", "NKD=F"):
        v = last_price(tk)
        if v and v > 10000:  # 日経水準のサニティチェック
            fut_last, fut_ticker = v, tk
            break
    gap_pct = (fut_last / n225_prev - 1) * 100 if fut_last else None
    gap_yen = fut_last - n225_prev if fut_last else None
    log(f"日経前日終値 {n225_prev:,.0f} / 夜間先物 {fut_last} ({fut_ticker}) / ギャップ {gap_pct}")

    # 米株・ドル円
    spx = daily_closes("^GSPC")
    ndx = daily_closes("^IXIC")
    fx = daily_closes("JPY=X")
    spx_ret = float((spx.iloc[-1] / spx.iloc[-2] - 1) * 100) if len(spx) >= 2 else None
    ndx_ret = float((ndx.iloc[-1] / ndx.iloc[-2] - 1) * 100) if len(ndx) >= 2 else None
    fx_now = last_price("JPY=X") or (float(fx.iloc[-1]) if len(fx) else None)
    fx_chg = (fx_now / float(fx.iloc[-1]) - 1) * 100 if fx_now and len(fx) else None

    # 理論ギャップ
    betas = estimate_betas(n225, spx, fx)
    theo_gap = None
    if betas and spx_ret is not None and fx_chg is not None:
        b1, b2, _ = betas
        theo_gap = b1 * spx_ret + b2 * fx_chg
    log(f"S&P500前日 {spx_ret} / ドル円変化 {fx_chg} / 理論ギャップ {theo_gap}")

    # ADR
    adr = calc_adr_gaps()
    adr_bt = backtest_adr_follow()

    # 乖離の履歴を更新（今朝の観測を記録 + 過去分の答え合わせを埋める）
    hist = load_history()
    dev = (gap_pct - theo_gap) if (gap_pct is not None and theo_gap is not None) else None
    update_history(hist, datetime.date.today(), {
        "prev_close": n225_prev, "fut": fut_last,
        "gap": None if gap_pct is None else round(gap_pct, 3),
        "theo": None if theo_gap is None else round(theo_gap, 3),
        "dev": None if dev is None else round(dev, 3),
    }, adr_rows=adr)
    backfill_answers(hist)
    backfill_adr_answers(hist)
    save_history(hist)

    generate_html({
        "n225_prev": n225_prev, "n225_date": n225_date,
        "fut_last": fut_last, "fut_ticker": fut_ticker,
        "gap_pct": gap_pct, "gap_yen": gap_yen,
        "spx_ret": spx_ret, "ndx_ret": ndx_ret,
        "fx_now": fx_now, "fx_chg": fx_chg,
        "theo_gap": theo_gap, "betas": betas,
        "adr": adr, "adr_bt": adr_bt,
    }, hist)

    if "--nopush" in sys.argv:
        log("push スキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
