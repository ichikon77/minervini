# -*- coding: utf-8 -*-
"""
需給×並び — 制度信用倍率が1倍未満で、形が「押し目候補」か「B 形が揃わない」、かつ注意3条件に当たらない銘柄（日本株プライム）

  押し目候補 = 並び完成のあと、5MAが25MAを下抜けた（例: 2026-11-20 の HONDA）
  B         = 並び転換の B（5MAは200MAを上抜けたが、25/50/75/100MAのどれかが右下がり）
  注意3条件  = 売買代金（20日平均）1億円未満 / 売残が20日平均出来高の1日分超 / 規模区分 TOPIX Small 2
              → どれか1つでも当たる銘柄は載せない（ページ末尾の「外した銘柄」に理由つきで残す）

・並びの判定は tenkan_screen.py と同じ定義（states() が全日付でベクトル計算。最終日の A/B/C 件数は tenkan と一致する）
・信用残は margin_all_history.json（MarginScreener 16:40 が更新）。公表の遅れを入れて「その日の夜に見られた数字」だけを使う
・仮説検証は毎晩、手元の全期間（信用残の蓄積開始 2026-06-12 以降）で計算し直す。履歴ファイルは持たない
・出力: jukyu.html / txt/Jukyu_oshime.txt, Jukyu_B.txt。push はしない（23:00 の nightly に任せる。tenkan と同じ）

2026-11-25 作成。同日、転換初期（並び転換A）のリストは外し、注意3条件で絞る形にした（本人の指示）。
注意3条件は 6/16〜8/25 掲載分の60営業日後の結果を見てから決めたもの。RULE_DATE より後の掲載分が本当の答え合わせ。
しきい値はすべて暫定（仮説）。
"""
import os
import json
import bisect
import datetime as dt
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

import tenkan_screen as ts

BASE = Path(__file__).resolve().parent
OUT_HTML = Path(os.environ.get("JUKYU_OUT", str(BASE / "jukyu.html")))
MARGIN_JSON = BASE / "margin_all_history.json"
TXT_DIR = BASE / "txt"

RATIO_MAX = 1.0        # 制度信用倍率（買残÷売残）がこれ未満
THIN_SELL = 10000      # 売残がこの株数未満は倍率が暴れるので対象外（margin.html の「売り残僅少」と同じ基準）
DC_LOOKBACK = 20       # 押し目候補: 5MAが25MAを下抜けてから何営業日以内か
HIGH_DAYS = 60         # 直近高値を測る窓（営業日）
HZ = [5, 10, 20, 40, 60]   # 検証する保有期間（営業日）
DAILY_START = "2026-09-25"  # JPX の信用残が週次→日次になった最初の申込日
NEW_DAYS = 10          # 「最近の新規掲載」に出す営業日数
TREND_BACK = 10        # 倍率の向き: 何営業日前の倍率と比べるか
# 注意3条件（どれかに当たれば載せない）
THIN_TO = 1.0          # 売買代金（20日平均）がこの億円未満
DTC_MAX = 1.0          # 売残 ÷ 20日平均出来高 がこの日数を超える
SMALL_SIZE = "TOPIX Small 2"
LOSS_TH = -0.10        # 「大きく負けた」とみなす騰落率
RULE_DATE = "2026-11-25"   # 注意3条件を決めた日。これより後の掲載分が「答え合わせ」
FOL = ts.FOLLOWERS
KEYS = ("p", "b")
NAME = {"p": "押し目候補", "b": "B 形が揃わない"}
SHORT = {"p": "押し目", "b": "B"}


def log(msg):
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


warnings.filterwarnings("ignore", message="All-NaN slice encountered")
ts.log = log   # tenkan_log.txt には書かない（bat が jukyu_log.txt に流す）


# ---------------- 並びの状態（全日付） ----------------
def states(px):
    """tenkan_screen.analyze/classify と同じ定義を、最終日だけでなく全日付について計算する"""
    close = px["Close"]
    adj = px["Adj"].ffill() if "Adj" in px.columns else close
    ma = {n: adj.rolling(n).mean() for n in ts.MAS}
    m200 = ma[200]
    slope = m200 / m200.shift(ts.SLOPE_DAYS) - 1
    above5 = ma[5] > m200
    up = above5 & ~above5.shift(1, fill_value=False)
    up.iloc[0] = False
    gc = up.rolling(ts.GC_LOOKBACK, min_periods=1).max().astype(bool)   # 直近60営業日以内に5MAが200MAを上抜け
    rising = {n: (ma[n] / ma[n].shift(ts.APPROACH_DAYS) - 1) > 0 for n in FOL}
    abv = {n: (ma[n] / m200 - 1) > 0 for n in FOL}
    all_r = rising[25] & rising[50] & rising[75] & rising[100]
    all_a = abv[25] & abv[50] & abv[75] & abv[100]
    ok = (slope >= ts.SLOPE_MIN) & above5 & gc
    grp = np.zeros(len(px), dtype=np.int8)          # 0=なし 1=A 2=B 3=C
    grp[(ok & all_r & ~all_a).values] = 1
    grp[(ok & ~all_r).values] = 2
    grp[(ok & all_r & all_a).values] = 3

    # 押し目候補: 並び完成（GCの期限は問わない）の翌日に5MAが25MAを下抜け、その後ずっと5MA<25MA
    kansei = (slope >= ts.SLOPE_MIN) & all_r & all_a
    b525 = ma[5] > ma[25]
    dn = (~b525) & b525.shift(1, fill_value=False) & ma[25].notna()
    idx = np.arange(len(px))
    last_dn = pd.Series(np.where(dn, idx, np.nan), index=px.index).ffill()
    dc_ago = idx - last_dn
    k_at_dc = pd.Series(np.where(dn, kansei.shift(1, fill_value=False), np.nan), index=px.index).ffill()
    oshime = (~b525) & (dc_ago <= DC_LOOKBACK) & (k_at_dc == 1) & (slope >= ts.SLOPE_MIN) & above5
    out = pd.DataFrame({"close": close, "adj": adj, "grp": grp, "oshime": oshime.values, "dc_ago": dc_ago.values,
                        "to20": ((close * px["Volume"]).rolling(20).mean() / 1e8).values,   # 売買代金20日平均（億円）
                        "vol20": px["Volume"].rolling(20).mean().values},                   # 出来高20日平均（株）
                       index=px.index)
    for n in ts.MAS:
        out[f"m{n}"] = ma[n]
    return out


# ---------------- 信用残 ----------------
def margin_calendar(H, days):
    """各営業日 t の夜に見られた最新の申込日。日次(9/25〜)は翌営業日16時公表、週次は翌週の第2営業日公表"""
    mdates = sorted(H["weeks"])
    avail = []
    for m in mdates:
        i = bisect.bisect_left(days, m)
        avail.append((i + (1 if m >= DAILY_START else 2), m))
    use, j = {}, -1
    for t in range(len(days)):
        while j + 1 < len(avail) and avail[j + 1][0] <= t:
            j += 1
        if j >= 0:
            use[t] = avail[j][1]
    return use, mdates


# ---------------- 集計 ----------------
def fmt3(x, loss=False):
    """中央値 / 平均 / 勝率。loss=True なら下段に −10%以下で終わった割合も出す"""
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return "<td class='num'>-</td>"
    sub = f"N={len(x):,}"
    if loss:
        sub = f"{LOSS_TH*100:.0f}%以下 {(x <= LOSS_TH).mean()*100:.0f}%・{sub}"
    return (f"<td class='num'>{np.median(x)*100:+.1f} / {x.mean()*100:+.1f} / {(x > 0).mean()*100:.0f}%"
            f"<span class='sub'>{sub}</span></td>")


def man(v):
    """株数 → 万株"""
    if v is None or not np.isfinite(v):
        return "-"
    return f"{v/1e4:,.1f}万" if v >= 1e4 else f"{v:,.0f}"


def md(d):
    return d[5:].replace("-", "/")


def main():
    uni = ts.load_universe()
    if ts.LIMIT:
        uni = uni[:ts.LIMIT]
    meta = {u["code"]: u for u in uni}
    prices = ts.fetch_prices([u["code"] + ".T" for u in uni])
    log(f"価格データ十分: {len(prices)}/{len(uni)}")
    H = json.loads(MARGIN_JSON.read_text(encoding="utf-8"))

    # ---- 状態を全銘柄・全日付で ----
    S = {}
    for t, px in prices.items():
        try:
            S[t.replace(".T", "")] = states(px)
        except Exception as e:
            log(f"states失敗 {t}: {e}")
    codes = sorted(S)
    cnt = pd.Series(np.concatenate([s.index.values for s in S.values()])).value_counts()
    cal = pd.DatetimeIndex(sorted(cnt[cnt >= len(S) * 0.5].index))   # 半数以上の銘柄に足がある日だけ
    days = [d.strftime("%Y-%m-%d") for d in cal]
    T, N = len(cal), len(codes)
    ADJ = np.full((T, N), np.nan)
    CLS = np.full((T, N), np.nan)
    TO = np.full((T, N), np.nan)
    VOL = np.full((T, N), np.nan)
    G = np.zeros((T, N), dtype=np.int8)
    O = np.zeros((T, N), dtype=bool)
    for j, c in enumerate(codes):
        s = S[c].reindex(cal)
        ADJ[:, j] = s["adj"].values
        CLS[:, j] = s["close"].values
        TO[:, j] = s["to20"].values
        VOL[:, j] = s["vol20"].values
        G[:, j] = s["grp"].fillna(0).values.astype(np.int8)
        O[:, j] = (s["oshime"] == True).values   # reindexで欠けた日は False
    nA, nB, nC = [(G[-1] == k).sum() for k in (1, 2, 3)]
    log(f"並び（{days[-1]}）: A{nA} B{nB} C{nC}（tenkan と同じ件数になるはず）／押し目の形 {O[-1].sum()}")

    use, mdates = margin_calendar(H, days)
    t0 = min(use)
    SELL = np.full((T, N), np.nan)
    BUY = np.full((T, N), np.nan)
    cache = {}
    for t in range(t0, T):
        m = use[t]
        if m not in cache:
            w = H["weeks"][m]
            se = np.array([(w.get(c) or [np.nan, np.nan])[0] for c in codes], dtype=float)
            bu = np.array([(w.get(c) or [np.nan, np.nan])[1] for c in codes], dtype=float)
            cache[m] = (se, bu)
        SELL[t], BUY[t] = cache[m]
    with np.errstate(divide="ignore", invalid="ignore"):
        R = np.where(SELL > 0, BUY / SELL, np.nan)
        DTC = np.where(VOL > 0, SELL / VOL, np.nan)       # 売残が出来高の何日分か
    valid = np.isfinite(ADJ) & (ADJ > 0)
    valid[:t0] = False
    LT1 = valid & (R < RATIO_MAX) & (SELL >= THIN_SELL)
    THIN = valid & (R < RATIO_MAX) & (SELL < THIN_SELL)
    GE1 = valid & (R >= RATIO_MAX)
    small = np.array([meta[c].get("size", "") == SMALL_SIZE for c in codes])
    F_THIN = TO < THIN_TO
    F_HEAVY = DTC > DTC_MAX
    F_SMALL = np.broadcast_to(small[None, :], (T, N))
    BAD = F_THIN | F_HEAVY | F_SMALL
    SHAPE = {"p": O, "b": G == 2}
    M = {k: LT1 & SHAPE[k] & ~BAD for k in KEYS}          # このページに載せる
    EXC = {k: LT1 & SHAPE[k] & BAD for k in KEYS}         # 注意に当たって外した
    CTRL = {k: GE1 & SHAPE[k] for k in KEYS}
    m_last = use[T - 1]
    last = T - 1
    log(f"信用残: 申込日 {m_last} を使用／倍率1倍未満 {LT1[-1].sum()}銘柄（うち注意なし {(LT1 & ~BAD)[-1].sum()}、ほかに売残僅少 {THIN[-1].sum()}）"
        f"／押し目候補 {M['p'][-1].sum()}（外した {EXC['p'][-1].sum()}）／B {M['b'][-1].sum()}（外した {EXC['b'][-1].sum()}）")

    # ---- 先の騰落率（配当調整済み終値→終値） ----
    F, X = {}, {}
    for h in HZ:
        f = np.full((T, N), np.nan)
        f[:T - h] = ADJ[h:] / ADJ[:T - h] - 1
        f[~valid] = np.nan
        f[np.abs(f) > 2] = np.nan            # 異常値（調整済み価格の崩れ）を除く
        F[h] = f
        X[h] = f - np.nanmedian(f, axis=1)[:, None]   # 同じ日のプライム全銘柄の中央値との差

    def raw_row(label, mask, dim=False, hz=HZ, loss=False):
        st = " style='color:#94a3b8'" if dim else ""
        cells = "".join(fmt3(F[h][mask], loss) for h in hz)
        return (f"<tr><td class='name'{st}>{label}</td><td class='num'>{int(mask.sum()):,}</td>"
                f"<td class='num'>{int(mask.any(axis=0).sum()):,}</td>{cells}</tr>")

    def loss_row(label, mask, dim=False):
        """N営業日後に LOSS_TH 以下で終わった割合"""
        st = " style='color:#94a3b8'" if dim else ""
        cells = ""
        for h in HZ:
            x = F[h][mask]
            x = x[np.isfinite(x)]
            cells += ("<td class='num'>-</td>" if len(x) == 0 else
                      f"<td class='num big'>{(x <= LOSS_TH).mean()*100:.0f}%<span class='sub'>"
                      f"{LOSS_TH*200:.0f}%以下 {(x <= LOSS_TH*2).mean()*100:.0f}%・N={len(x):,}</span></td>")
        return f"<tr><td class='name'{st}>{label}</td>{cells}</tr>"

    def beat(mask, h):
        x = X[h][mask]
        x = x[np.isfinite(x)]
        if len(x) == 0:
            return None
        rows = mask.any(axis=1)
        dm = np.nanmedian(np.where(mask, X[h], np.nan)[rows], axis=1)
        dm = dm[np.isfinite(dm)]
        return ((x > 0).mean() * 100, np.median(x) * 100, (dm > 0).mean() * 100 if len(dm) else float("nan"), len(dm))

    def beat_row(label, mask, dim=False):
        st = " style='color:#94a3b8'" if dim else ""
        cells = ""
        for h in HZ:
            b = beat(mask, h)
            cells += ("<td class='num'>-</td>" if not b else
                      f"<td class='num big'>{b[0]:.0f}%<span class='sub'>差の中央値 {b[1]:+.1f}%・日数ベース {b[2]:.0f}%（{b[3]}日）</span></td>")
        return f"<tr><td class='name'{st}>{label}</td>{cells}</tr>"

    ALL = valid
    LAB = {k: f"倍率1倍未満 × {NAME[k]} × 注意なし（このページのリスト）" for k in KEYS}
    spec = ([(LAB[k], M[k], False) for k in KEYS] +
            [(f"（外した分）倍率1倍未満 × {NAME[k]} × 注意あり", EXC[k], True) for k in KEYS] +
            [("（対照）倍率1倍未満 × 注意なし（形を問わない）", LT1 & ~BAD, True),
             ("（対照）倍率1倍未満 × 注意あり（形を問わない）", LT1 & BAD, True)] +
            [(f"（対照）倍率1倍以上 × {NAME[k]}", CTRL[k], True) for k in KEYS])
    raw_rows = [raw_row(l, m, d) for l, m, d in spec] + [raw_row("（対照）プライム全銘柄（同じ期間・毎日）", ALL, True)]
    beat_rows = [beat_row(l, m, d) for l, m, d in spec]
    loss_rows = [loss_row(l, m, d) for l, m, d in spec] + [loss_row("（対照）プライム全銘柄（同じ期間・毎日）", ALL, True)]

    # 注意3条件の内訳（形は押し目候補とBを合わせる）
    both = LT1 & (SHAPE["p"] | SHAPE["b"])
    hz3 = [20, 40, 60]
    cond_rows = [
        raw_row(f"売買代金（20日平均）{THIN_TO:.0f}億円未満", both & F_THIN, hz=hz3, loss=True),
        raw_row(f"売残が20日平均出来高の{DTC_MAX:.0f}日分超", both & F_HEAVY, hz=hz3, loss=True),
        raw_row(f"規模区分 {SMALL_SIZE}", both & F_SMALL, hz=hz3, loss=True),
        raw_row("3つのどれかに当たる", both & BAD, hz=hz3, loss=True),
        raw_row("どれにも当たらない（＝このページのリスト）", both & ~BAD, hz=hz3, loss=True),
    ]

    # 掲載月ごと
    months = sorted({d[:7] for d in days[t0:]})
    mon_idx = {mo: np.array([d[:7] == mo for d in days]) for mo in months}

    def mon_row(label, mask, h, dim=False):
        st = " style='color:#94a3b8'" if dim else ""
        cells = []
        for mo in months:
            x = F[h][mask & mon_idx[mo][:, None]]
            x = x[np.isfinite(x)]
            cells.append(f"<td class='num'>{np.median(x)*100:+.1f} / {(x > 0).mean()*100:.0f}%<span class='sub'>{LOSS_TH*100:.0f}%以下 {(x <= LOSS_TH).mean()*100:.0f}%・N={len(x):,}</span></td>"
                         if len(x) else "<td class='num'>-</td>")
        return f"<tr><td class='name'{st}>{label}</td>{''.join(cells)}</tr>"

    def mon_rows(h):
        return ([mon_row(f"{NAME[k]} × 注意なし", M[k], h) for k in KEYS] +
                [mon_row(f"（外した分）{NAME[k]} × 注意あり", EXC[k], h, True) for k in KEYS] +
                [mon_row("（対照）倍率1倍未満 × 注意なし（形を問わない）", LT1 & ~BAD, h, True),
                 mon_row("（対照）プライム全銘柄", ALL, h, True)])

    # 答え合わせ: 注意3条件を決めた日より後に載った分だけ
    after = np.array([d > RULE_DATE for d in days])[:, None]
    oos_rows = ([raw_row(f"{NAME[k]} × 注意なし", M[k] & after, loss=True) for k in KEYS] +
                [raw_row(f"（外した分）{NAME[k]} × 注意あり", EXC[k] & after, True, loss=True) for k in KEYS] +
                [raw_row("（対照）プライム全銘柄", ALL & after, True, loss=True)])
    n_after = int(after.sum())

    # ---- 今日のリスト ----
    back = None
    mi = mdates.index(m_last)
    if mi - TREND_BACK >= 0 and mdates[mi - TREND_BACK] >= DAILY_START:
        back = mdates[mi - TREND_BACK]

    def run_start(mask, j):
        t = last
        while t - 1 >= t0 and mask[t - 1, j]:
            t -= 1
        return t

    def detail(k, j, mask):
        c = codes[j]
        r = ts.analyze(c, prices[c + ".T"])
        if not r:
            return None
        s = S[c]
        r["ratio"] = float(R[last, j]); r["sell"] = float(SELL[last, j]); r["buy"] = float(BUY[last, j])
        r["dtc"] = float(DTC[last, j]) if np.isfinite(DTC[last, j]) else None
        r["size"] = meta[c].get("size", "")
        r["ratio_back"] = r["sell_back"] = None
        if back:
            w = H["weeks"][back].get(c)
            if w and w[0] and w[0] > 0:
                r["ratio_back"] = w[1] / w[0]
                r["sell_back"] = w[0]
        st = run_start(mask, j)
        r["since"] = days[st]; r["run"] = last - st + 1
        other = "b" if k == "p" else "p"
        r["also"] = SHORT[other] if (LT1 & SHAPE[other])[last, j] else ""
        dc = s["dc_ago"].iloc[-1]
        if np.isfinite(dc) and int(dc) < len(s):
            r["dc_ago"] = int(dc)
            r["dc_date"] = s.index[-1 - int(dc)].strftime("%m/%d")
        hi = s["adj"].tail(HIGH_DAYS).max()
        a = float(s["adj"].iloc[-1])
        r["dd"] = float(a / hi - 1)
        mv = {n: float(s[f"m{n}"].iloc[-1]) for n in (25, 50, 75, 100, 200)}
        below = [(n, v) for n, v in mv.items() if v <= a]
        if below:
            n, v = max(below, key=lambda x: x[1])
            r["pos"] = f"{n}MAの上"; r["pos_d"] = a / v - 1
        else:
            r["pos"] = "200MAの下"; r["pos_d"] = a / mv[200] - 1
        r["order"] = mv[25] > mv[50] > mv[75] > mv[100] > mv[200]
        why = []
        if F_THIN[last, j]:
            why.append(f"売買代金 {TO[last, j]:.1f}億円")
        if F_HEAVY[last, j]:
            why.append(f"売残が出来高の {DTC[last, j]:.1f}日分")
        if F_SMALL[last, j]:
            why.append("Small 2")
        r["why"] = "・".join(why)
        return r

    picks = {k: [] for k in KEYS}
    excl = {k: [] for k in KEYS}
    for k in KEYS:
        for j in np.where(M[k][last])[0]:
            r = detail(k, j, M[k])
            if r:
                picks[k].append(r)
        for j in np.where(EXC[k][last])[0]:
            r = detail(k, j, EXC[k])
            if r:
                excl[k].append(r)
    caps = ts.load_mcaps(sorted({r["code"] for k in KEYS for r in picks[k] + excl[k]}))
    for k in KEYS:
        for r in picks[k] + excl[k]:
            r["mcap"] = caps.get(r["code"])
    picks["p"].sort(key=lambda r: (r.get("dc_ago", 99), r["ratio"]))
    picks["b"].sort(key=lambda r: r["ratio"])
    for k in KEYS:
        excl[k].sort(key=lambda r: r["ratio"])

    # ---- 最近の新規掲載とその後 ----
    new_rows = []
    for t in range(last - 1, max(t0, last - NEW_DAYS), -1):
        um = np.nanmedian(ADJ[last] / ADJ[t] - 1)
        for k in KEYS:
            for j in np.where(M[k][t] & ~M[k][t - 1])[0]:
                ret = ADJ[last, j] / ADJ[t, j] - 1
                if not np.isfinite(ret):
                    continue
                c = codes[j]
                new_rows.append((days[t], k, c, meta[c]["name"], CLS[t, j], CLS[last, j], ret, um, last - t,
                                 bool(M[k][last, j])))
    new_rows.sort(key=lambda x: (x[0], x[1] == "p", x[2]), reverse=True)

    def card_stat(k):
        out = []
        for h in (60, 20):
            x = F[h][M[k]]; x = x[np.isfinite(x)]
            u = F[h][ALL]; u = u[np.isfinite(u)]
            if len(x) < 30:
                continue
            b = beat(M[k], h)
            out.append(f"<b>{h}営業日後</b> 中央値 {np.median(x)*100:+.1f}%・勝率 {(x > 0).mean()*100:.0f}%"
                       f"（プライム全銘柄 {np.median(u)*100:+.1f}%・{(u > 0).mean()*100:.0f}%）"
                       f"・同じ日の全銘柄の真ん中を上回った割合 {b[0]:.0f}%"
                       f"・{LOSS_TH*100:.0f}%以下で終わった割合 {(x <= LOSS_TH).mean()*100:.0f}%（全銘柄 {(u <= LOSS_TH).mean()*100:.0f}%）")
        return (f"過去の同条件（{int(days[t0][5:7])}/{int(days[t0][8:])}〜）<br>" + "<br>".join(out)) if out else "過去の同条件: まだ件数不足"

    ctx = dict(days=days, t0=t0, last=last, m_last=m_last, back=back, n_universe=len(uni), n_valid=len(prices),
               n_lt1=int(LT1[last].sum()), n_clean=int((LT1 & ~BAD)[last].sum()), n_thin=int(THIN[last].sum()),
               picks=picks, excl=excl, meta=meta, raw_rows=raw_rows, beat_rows=beat_rows, loss_rows=loss_rows, cond_rows=cond_rows,
               mon20=mon_rows(20), mon60=mon_rows(60), months=months, oos_rows=oos_rows, n_after=n_after,
               new_rows=new_rows, card={k: card_stat(k) for k in KEYS})
    OUT_HTML.write_text(build_html(ctx), encoding="utf-8")
    try:
        TXT_DIR.mkdir(exist_ok=True)
        (TXT_DIR / "Jukyu_oshime.txt").write_text("".join(f"{r['code']}\t,\n" for r in picks["p"]), encoding="utf-8")
        (TXT_DIR / "Jukyu_B.txt").write_text("".join(f"{r['code']}\t,\n" for r in picks["b"]), encoding="utf-8")
    except Exception as e:
        log(f"txt出力失敗: {e}")
    log(f"完了: 押し目候補 {len(picks['p'])} / B {len(picks['b'])} → {OUT_HTML.name}")


# ---------------- HTML ----------------
def nav_html():
    out = []
    has = any(h == "jukyu.html" for h, _, _ in ts.NAV)
    for href, label, color in ts.NAV:
        act = ' class="active"' if href == "jukyu.html" else ""
        out.append(f'<a href="{href}"{act} style="border-color:{color}">{label}</a>')
        if href == "tenkan.html" and not has:
            out.append('<a href="jukyu.html" class="active" style="border-color:#db2777">需給×並び</a>')
    return "\n".join(out)


def margin_cells(r):
    tr = "-"
    if r.get("ratio_back"):
        d = r["ratio"] - r["ratio_back"]
        arrow = "↓" if d < -0.02 else ("↑" if d > 0.02 else "→")
        sc = r["sell"] / r["sell_back"] - 1
        tr = f"{r['ratio_back']:.2f} {arrow} {r['ratio']:.2f}<span class='sub'>売残 {sc*100:+.0f}%</span>"
    dtc = f"{r['dtc']:.2f}日分" if r.get("dtc") is not None else "-"
    return (f"<td class='num lt1'>{r['ratio']:.2f}<span class='sub'>売残 {man(r['sell'])}・買残 {man(r['buy'])}</span></td>"
            f"<td class='num'>{tr}</td><td class='num'>{dtc}</td>")


def base_cells(r, meta):
    also = f"<span class='also'>{r['also']}の形にも該当</span>" if r.get("also") else ""
    return [
        f"<td class='code'><a href='https://kabutan.jp/stock/chart?code={r['code']}' target='_blank'>{r['code']}</a></td>",
        f"<td class='name'>{meta['name']}{also}</td>",
        f"<td class='sector'>{meta['sector']}</td>",
        f"<td class='num'>{r['price']:,.0f}</td>",
        f"<td class='num'>{format(r['mcap'], ',.0f') if r.get('mcap') else '-'}</td>",
        f"<td class='num'>{r['turnover']:,.1f}</td>",
        margin_cells(r),
    ]


def since_cell(r):
    return f"<td class='num'>{md(r['since'])}<span class='sub'>{r['run']}営業日連続</span></td>"


def row_b(r, meta):
    gc = r["gc_date"].strftime("%m/%d") if r["gc_date"] is not None else "-"
    cells = base_cells(r, meta) + [
        f"<td class='num slope'>{ts.pct(r['slope'], nd=2)}</td>",
        f"<td class='num'>{gc}<span class='sub'>{r['gc_ago']}日前</span></td>",
        f"<td class='num big'>{r['n_above']+1}/5<span class='sub'>接近{r['n_appr']}・下離れ{r['n_away']}{'・⚠'+str(r['n_warn']) if r['n_warn'] else ''}</span></td>",
    ] + [ts.dist_cell(r["fol"][n]) for n in FOL] + [since_cell(r)]
    return "<tr>" + "".join(cells) + "</tr>"


def row_oshime(r, meta):
    ago = r.get("dc_ago")
    ago_txt = "今日" if ago == 0 else (f"{ago}営業日前" if ago is not None else "-")
    cells = base_cells(r, meta) + [
        f"<td class='num'>{r.get('dc_date', '-')}<span class='sub'>{ago_txt}</span></td>",
        f"<td class='num'>{ts.pct(r['dd'])}</td>",
        f"<td class='num'>{r['pos']}<span class='sub'>その線から {ts.pct(r['pos_d'])}</span></td>",
        f"<td class='num'>{'◎ 順番どおり' if r['order'] else '—'}</td>",
        f"<td class='num'>{ts.pct(r['fol'][25]['slope'])}</td>",
        f"<td class='num slope'>{ts.pct(r['slope'], nd=2)}</td>",
        since_cell(r),
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def build_html(c):
    days, t0, last = c["days"], c["t0"], c["last"]
    picks, excl, meta = c["picks"], c["excl"], c["meta"]
    today = days[last]
    mshort = md(c["m_last"])
    head_base = ("<th class='code'>コード</th><th class='name'>銘柄名</th><th>33業種</th><th>株価</th>"
                 "<th>時価総額<br><span class='fx'>（億円）</span></th><th>売買代金<br><span class='fx'>（20日平均・億円）</span></th>"
                 f"<th>制度信用倍率<br><span class='fx'>買残÷売残（{mshort}申込分）</span></th>"
                 f"<th>倍率の向き<br><span class='fx'>{TREND_BACK}営業日前 → 今</span></th>"
                 "<th>売残÷出来高<br><span class='fx'>（20日平均出来高の何日分か）</span></th>")
    head_b = ("<thead><tr>" + head_base +
              f"<th>200MA傾き<br><span class='fx'>（今日÷{ts.SLOPE_DAYS}日前−1）</span></th>"
              "<th class='gc'>5MA<br>GC日</th>"
              "<th>上抜け本数<br><span class='fx'>（5/25/50/75/100の5本中）</span></th>"
              + "".join(f"<th>{n}MA<br><span class='fx'>（{n}MA÷200MA−1）</span></th>" for n in FOL) +
              "<th>掲載開始<br><span class='fx'>（載り続けている日数）</span></th></tr></thead>")
    head_p = ("<thead><tr>" + head_base +
              "<th>5MAが25MAを<br>下抜けた日</th>"
              f"<th>高値から<br><span class='fx'>（直近{HIGH_DAYS}営業日の高値比）</span></th>"
              "<th>株価の位置<br><span class='fx'>（すぐ下にある線）</span></th>"
              "<th>線の順番<br><span class='fx'>25＞50＞75＞100＞200</span></th>"
              f"<th>25MAの向き<br><span class='fx'>（今日÷{ts.APPROACH_DAYS}日前−1）</span></th>"
              f"<th>200MA傾き<br><span class='fx'>（今日÷{ts.SLOPE_DAYS}日前−1）</span></th>"
              "<th>掲載開始<br><span class='fx'>（載り続けている日数）</span></th></tr></thead>")

    def table(k):
        g = picks[k]
        if not g:
            return "<p class='empty'>該当なし（今日は0銘柄）</p>"
        if k == "p":
            body = "\n".join(row_oshime(r, meta[r["code"]]) for r in g)
            return f"<div class='tw'><table class='t'>{head_p}<tbody>{body}</tbody></table></div>"
        body = "\n".join(row_b(r, meta[r["code"]]) for r in g)
        return f"<div class='tw'><table class='t'>{head_b}<tbody>{body}</tbody></table></div>"

    ex_rows = []
    for k in KEYS:
        for r in excl[k]:
            m = meta[r["code"]]
            ex_rows.append(
                f"<tr><td class='num'>{SHORT[k]}</td>"
                f"<td class='code'><a href='https://kabutan.jp/stock/chart?code={r['code']}' target='_blank'>{r['code']}</a></td>"
                f"<td class='name'>{m['name']}</td><td class='sector'>{m['sector']}</td><td class='num'>{r['price']:,.0f}</td>"
                f"<td class='num'>{r['ratio']:.2f}</td><td class='name' style='color:#fda4af'>{r['why']}</td></tr>")
    n_ex = len(ex_rows)
    ex_body = "\n".join(ex_rows) or "<tr><td colspan='7' class='empty'>該当なし</td></tr>"

    hz_head = "".join(f"<th>{h}営業日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th>" for h in HZ)
    hz_head2 = "".join(f"<th>{h}営業日後</th>" for h in HZ)
    hz_head3 = "".join(f"<th>{h}営業日後<br><span class='fx'>中央値 / 平均 / 勝率（下段 {LOSS_TH*100:.0f}%以下の割合）</span></th>" for h in (20, 40, 60))
    hz_head_l = "".join(f"<th>{h}営業日後<br><span class='fx'>中央値 / 平均 / 勝率（下段 {LOSS_TH*100:.0f}%以下の割合）</span></th>" for h in HZ)
    mon_head = "".join(f"<th>{int(mo[5:])}月に掲載<br><span class='fx'>中央値 / 勝率（下段 {LOSS_TH*100:.0f}%以下の割合）</span></th>" for mo in c["months"])
    new_body = "\n".join(
        f"<tr><td class='num'>{md(d)}<span class='sub'>{n}営業日前</span></td><td class='num'>{SHORT[k]}</td>"
        f"<td class='code'><a href='https://kabutan.jp/stock/chart?code={cd}' target='_blank'>{cd}</a></td><td class='name'>{nm}</td>"
        f"<td class='num'>{p0:,.0f}</td><td class='num'>{p1:,.0f}</td><td class='num big'>{ret*100:+.1f}%</td>"
        f"<td class='num'>{um*100:+.1f}%</td><td class='num'>{(ret-um)*100:+.1f}%</td><td class='num'>{'掲載中' if still else '外れた'}</td></tr>"
        for d, k, cd, nm, p0, p1, ret, um, n, still in c["new_rows"]) or "<tr><td colspan='10' class='empty'>該当なし</td></tr>"
    back_txt = f"{md(c['back'])}申込分" if c["back"] else "-"
    rule_md = md(RULE_DATE)
    oos_note = (f"{rule_md} より後の掲載はまだ {c['n_after']}営業日分。" if c["n_after"] else f"{rule_md} より後の掲載分はまだ無い。") + \
               "結果が出はじめるのは掲載の5営業日後から。"

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>需給×並び — 信用倍率1倍未満の押し目候補</title>
<style>
body{{font-family:-apple-system,"Segoe UI","Hiragino Sans","Noto Sans JP",sans-serif;margin:0;background:#0f172a;color:#e2e8f0;font-size:14px}}
.wrap{{max-width:1500px;margin:0 auto;padding:16px}}
nav{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}}
nav a{{color:#cbd5e1;text-decoration:none;font-size:12px;padding:4px 9px;border:2px solid;border-radius:6px;background:#1e293b}}
nav a.active{{background:#db2777;color:#fff}}
h1{{font-size:20px;margin:6px 0 2px}} .upd{{color:#94a3b8;font-size:12px;margin-bottom:12px}}
.def{{background:#1e293b;border-radius:10px;padding:12px 16px;margin-bottom:14px;line-height:1.7}}
.def b{{color:#f9a8d4}} .def code{{background:#0f172a;padding:1px 6px;border-radius:4px;color:#fcd34d}}
.prov{{color:#fcd34d;font-size:12px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px;margin-bottom:16px}}
.card{{background:#1e293b;border-radius:10px;padding:12px 14px;border-left:5px solid #db2777}}
.card .lbl{{font-size:12px;color:#94a3b8}} .card .val{{font-size:30px;font-weight:700;margin:2px 0}} .card .sm{{font-size:12px;color:#cbd5e1;line-height:1.6}}
.card .sm b{{color:#f9a8d4}}
.card.b{{border-left-color:#a78bfa}} .card.c{{border-left-color:#38bdf8}} .card.f{{border-left-color:#334155}}
h2{{font-size:16px;margin:22px 0 6px;padding-left:10px;border-left:4px solid #db2777}} h2 .n{{color:#f9a8d4}}
h2 small{{color:#94a3b8;font-weight:normal;font-size:12px;margin-left:8px}}
.tw{{overflow-x:auto}} table.t{{border-collapse:collapse;width:100%;font-size:12.5px}}
.t th{{background:#334155;padding:6px 6px;text-align:center;white-space:nowrap}} .t th .fx{{font-weight:normal;color:#cbd5e1;font-size:10.5px}}
.t td{{padding:5px 6px;border-bottom:1px solid #1e293b;text-align:right;white-space:nowrap}}
.t td.code,.t td.name,.t td.sector{{text-align:left}} .t td.code a{{color:#93c5fd;text-decoration:none}}
.t td.sector{{color:#94a3b8;font-size:11.5px}}
.t td .sub{{display:block;font-size:10.5px;color:#94a3b8;font-weight:normal}}
.t td.big{{font-weight:700;font-size:14px}}
.t td.st-above{{background:#14532d;color:#bbf7d0}} .t td.st-appr{{background:#1e3a8a;color:#bfdbfe}} .t td.st-away{{background:#3f1d2e;color:#fda4af}} .t td.st-warn{{background:#422006;color:#fde68a}}
.t td.slope{{color:#fcd34d}}
.t td.lt1{{color:#7dd3fc;font-weight:700}}
.also{{font-size:10.5px;color:#fcd34d;margin-left:6px}}
.empty{{color:#94a3b8;padding:10px}}
.note{{color:#94a3b8;font-size:12px;line-height:1.7;margin-top:10px}}
table.v{{width:auto;margin-top:6px;font-size:12px}}
details{{margin-top:18px}} summary{{cursor:pointer;color:#cbd5e1;font-size:13px}}
tr:hover td{{filter:brightness(1.15)}}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head><body><div class="wrap">
<nav>
{nav_html()}
</nav>
<h1>需給×並び — 信用倍率1倍未満の押し目候補（日本株プライム）</h1>
<div class="upd">更新: {today} 引け後データ ／ 信用残: {mshort} 申込分（JPX） ／ ユニバース: 東証プライム {c['n_universe']}銘柄（価格データ十分 {c['n_valid']}銘柄）</div>

<div class="def">
<b>見つけたいもの</b>: 売り方のほうが多い（＝将来の買い戻しが控えている）銘柄のうち、上げたあとの押し目にあり、売り買いが十分にあるもの。<br>
<b>信用倍率</b> ＝ <code>制度信用の買残 ÷ 売残</code>。<b>1倍未満</b>（売残のほうが多い）を対象にする。売残が{THIN_SELL:,}株未満の銘柄は倍率が暴れるので対象外（今日は{c['n_thin']}銘柄）。数字は「その日の夜に見られた最新の申込分」。<br>
<b>押し目候補</b> ＝ 25/50/75/100MAが4本とも200MAの上で右上がり（並び完成）だった銘柄で、<b>5MAが25MAを下抜けてから{DC_LOOKBACK}営業日以内</b>・今も5MA＜25MA。200MAは右上がりのまま、5MAは200MAの上。<br>
<b>B 形が揃わない</b> ＝ <a href="tenkan.html" style="color:#93c5fd">並び転換</a>の B と同じ。200MAが右上がり・5MAが直近{ts.GC_LOOKBACK}営業日以内に200MAを上抜けたが、25/50/75/100MAのどれかが右下がり。<br>
<b>注意3条件</b>（どれか1つでも当たる銘柄は<b>載せない</b>）: ① 売買代金（20日平均）が{THIN_TO:.0f}億円未満　② 売残が20日平均出来高の{DTC_MAX:.0f}日分を超える　③ 規模区分が {SMALL_SIZE}。
この3つに当たる銘柄は、倍率が1倍未満でもそのあとの成績が全銘柄並みかそれ以下だった（下の表4）。<br>
<b>計算の土台</b>: 移動平均は配当調整済み終値。株価・売買代金の列は実際の終値。
<span class="prov">※しきい値はすべて暫定（仮説段階）。注意3条件は 6/16〜8/25 に載った銘柄の60営業日後を見て {rule_md} に決めた。結果を見てから決めた条件なので、過去の成績が良く見えるのは当然。本当の答え合わせは {rule_md} より後に載る銘柄（下の表7）。</span>
</div>

<div class="cards">
<div class="card f"><div class="lbl">対象: プライムで信用倍率1倍未満</div><div class="val">{c['n_lt1']}</div><div class="sm">{c['n_valid']}銘柄中。うち注意3条件に当たらない {c['n_clean']}銘柄 → ここから形で拾う</div></div>
<div class="card c"><div class="lbl">押し目候補（倍率1倍未満・注意なし）</div><div class="val">{len(picks['p'])}</div><div class="sm">{c['card']['p']}</div></div>
<div class="card b"><div class="lbl">B 形が揃わない（倍率1倍未満・注意なし）</div><div class="val">{len(picks['b'])}</div><div class="sm">{c['card']['b']}</div></div>
</div>

<h2>押し目候補 <span class="n">{len(picks['p'])}銘柄</span><small>倍率1倍未満・注意なし。5MAが25MAを下抜けた日が新しい順 → 倍率が低い順</small></h2>
{table('p')}
<h2>B 形が揃わない <span class="n">{len(picks['b'])}銘柄</span><small>倍率1倍未満・注意なし。倍率が低い順</small></h2>
{table('b')}

<h2>仮説検証 — 載った銘柄は、そのあと上がったか<small>毎晩、手元の全期間で計算し直す</small></h2>
<div class="def">
<b>数え方</b>: {days[t0]}〜{today} の毎営業日について、その日の夜に載っていた全銘柄を「その日の終値で買って N営業日後の終値で売った」とみなす（配当調整済み）。
同じ銘柄が何日も続けて載ると何回も数える（延べ）。<b>勝率</b>＝プラスで終わった割合。<br>
<b>読むときは必ず対照の行と比べる</b>。この期間の相場の追い風・向かい風は全部の行に同じように入っているので、意味があるのは行と行の差だけ。<br>
<span class="prov">※信用残の蓄積が {days[t0][:4]}年{int(days[t0][5:7])}月からなので検証できる期間はまだ短く、相場の局面も1〜2種類しか含まない。{HZ[-1]}営業日後は古い掲載分しか結果が出ていない。数字は日々変わる。</span>
</div>
<table class="t v">
<thead><tr><th>表1 そのあとの騰落率</th><th>延べ<br><span class='fx'>銘柄×日</span></th><th>銘柄数</th>{hz_head}</tr></thead>
<tbody>
{chr(10).join(c['raw_rows'])}
</tbody></table>
<table class="t v" style="margin-top:14px">
<thead><tr><th>表2 大きく負けた割合<br><span class='fx'>N営業日後に {LOSS_TH*100:.0f}%以下で終わった割合。負けるリスクを見る表</span></th>{hz_head2}</tr></thead>
<tbody>
{chr(10).join(c['loss_rows'])}
</tbody></table>
<div class="note">表2の下段は {LOSS_TH*200:.0f}%以下で終わった割合。外した分（注意あり）は、相場全体が崩れた時期に全銘柄並みに大きく負けた。注意3条件は勝率を上げるためというより、この割合を抑えるための条件。</div>
<table class="t v" style="margin-top:14px">
<thead><tr><th>表3 同じ日のプライム全銘柄の真ん中を上回った割合<br><span class='fx'>50%なら差なし。相場全体の上げ下げを取り除いた比較</span></th>{hz_head2}</tr></thead>
<tbody>
{chr(10).join(c['beat_rows'])}
</tbody></table>
<div class="note">表3の<b>上回った割合</b>＝載っていた銘柄（延べ）のうち、N営業日後の騰落率が「同じ日に買ったプライム全銘柄の中央値」より上だった割合。
<b>差の中央値</b>＝その差の真ん中。<b>日数ベース</b>＝その日のリスト全体の中央値が全銘柄の中央値を上回った日の割合（同じ銘柄の数え過ぎに左右されにくい）。</div>
<table class="t v" style="margin-top:14px">
<thead><tr><th>表4 注意3条件の内訳<br><span class='fx'>倍率1倍未満 ×（押し目候補 または B）を、条件ごとに</span></th><th>延べ<br><span class='fx'>銘柄×日</span></th><th>銘柄数</th>{hz_head3}</tr></thead>
<tbody>
{chr(10).join(c['cond_rows'])}
</tbody></table>
<table class="t v" style="margin-top:14px">
<thead><tr><th>表5 掲載した月ごとの 60営業日後<br><span class='fx'>月によって順位が入れ替わるなら、まだ当てにならない</span></th>{mon_head}</tr></thead>
<tbody>
{chr(10).join(c['mon60'])}
</tbody></table>
<table class="t v" style="margin-top:14px">
<thead><tr><th>表6 掲載した月ごとの 20営業日後</th>{mon_head}</tr></thead>
<tbody>
{chr(10).join(c['mon20'])}
</tbody></table>
<table class="t v" style="margin-top:14px">
<thead><tr><th>表7 答え合わせ — {rule_md} より後に載った分だけ<br><span class='fx'>注意3条件を決めたあとのデータ。ここが良ければ本物</span></th><th>延べ<br><span class='fx'>銘柄×日</span></th><th>銘柄数</th>{hz_head_l}</tr></thead>
<tbody>
{chr(10).join(c['oos_rows'])}
</tbody></table>
<div class="note">{oos_note}</div>

<h2>最近の新規掲載とその後<small>直近{NEW_DAYS}営業日に新しく載った銘柄（前日は載っていなかった）</small></h2>
<div class="tw"><table class="t v">
<thead><tr><th>掲載日</th><th>表</th><th class='code'>コード</th><th class='name'>銘柄名</th><th>掲載日の終値</th><th>今日の終値</th><th>掲載日→今日</th><th>同じ期間の<br>プライム中央値</th><th>差</th><th>今</th></tr></thead>
<tbody>
{new_body}
</tbody></table></div>

<details>
<summary>注意3条件に当たって外した銘柄（今日 {n_ex}銘柄）— 形と倍率は条件どおりだが、載せていない</summary>
<div class="tw"><table class="t v">
<thead><tr><th>形</th><th class='code'>コード</th><th class='name'>銘柄名</th><th>33業種</th><th>株価</th><th>制度信用倍率</th><th class='name'>外した理由</th></tr></thead>
<tbody>
{ex_body}
</tbody></table></div>
</details>

<div class="note">
・出所: 株価 Yahoo Finance（配当調整済み終値で移動平均と騰落率を計算）、信用残 JPX「銘柄別信用取引残高」。倍率は制度信用のみ（一般信用は含まない）。<br>
・信用残の公表は申込日の翌営業日16時ごろ（{DAILY_START[:4]}年{int(DAILY_START[5:7])}月{int(DAILY_START[8:])}日より前は週1回・翌週の第2営業日）。検証でも「その日の夜に見られた数字」だけを使っている。倍率の向きの比較先は {back_txt}。<br>
・騰落率は終値→終値で、売買コスト・翌朝の寄り付きとの差は入れていない。調整済み価格が崩れた銘柄（N営業日で±200%超）は除いている。<br>
・この表は条件に当てはまる銘柄の一覧と、過去の同じ条件の結果を並べたもので、売買を勧めるものではない。
</div>
</div></body></html>"""


if __name__ == "__main__":
    main()
