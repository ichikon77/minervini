# -*- coding: utf-8 -*-
"""
tenkan_backtest10y.py — 並び転換の10年バックテスト
 検証1: どの段階（1本=5MA GC日 / 2本=25MA / 3本=50MA / 4本=75MA / 5本=100MA=C）で買うのがベストか
         → 年別・対ユニバース超過リターン（同じ日にプライム全銘柄を買った中央値との差）
 検証2: 損切りルールの比較（ベストな段階で買った後、どこで切るのが期待値最大か）
入力: tenkan_prices10y.pkl.gz（日付×銘柄、配当調整済み終値）
"""
import sys, os
import numpy as np, pandas as np_pd
import pandas as pd
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRICES = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "tenkan_prices10y.pkl.gz"
SLOPE_DAYS, SLOPE_MIN, GC_LOOKBACK, DIR_DAYS = 20, 0.005, 60, 20
F = (25, 50, 75, 100)
H = (60, 120, 180)
COOLDOWN = 20        # 同じ段階のイベントを同じ転換内で二重に数えない
MAXH = max(H)

P = pd.read_pickle(PRICES, compression="gzip").astype(float)
P = P.ffill(limit=5)
dates = P.index
print("価格:", P.shape, dates[0].date(), "→", dates[-1].date(), flush=True)

# ---------- ユニバース基準: 日付ごとの「全銘柄を買った時の h 日後リターンの中央値」 ----------
uni_med = {}
for h in H:
    R = P.shift(-h) / P - 1
    uni_med[h] = R.median(axis=1).values   # 日付ごと
uni_med_all = {h: np.nanmedian(P.shift(-h).values / P.values - 1) for h in H}

def sma(a, n):
    s = pd.Series(a).rolling(n).mean().values
    return s

# ---------- 検証1: 段階イベント ----------
events = []   # dict(code, i, stage, r60, r120, r180, ex60, ex120, ex180)
per_stock = {}  # 検証2用に配列を保持
for code in P.columns:
    a = P[code].values
    valid = ~np.isnan(a)
    if valid.sum() < 300:
        continue
    ma = {n: sma(a, n) for n in (5,) + F + (200,)}
    m200 = ma[200]; m5 = ma[5]
    n = len(a)
    # 条件配列
    with np.errstate(invalid="ignore", divide="ignore"):
        slope_ok = np.zeros(n, bool); slope_ok[SLOPE_DAYS:] = (m200[SLOPE_DAYS:] / m200[:-SLOPE_DAYS] - 1) >= SLOPE_MIN
        above5 = m5 > m200
        gc_day = np.zeros(n, bool); gc_day[1:] = above5[1:] & ~above5[:-1]
        # 直近60日以内にGC
        gc_recent = pd.Series(gc_day.astype(int)).rolling(GC_LOOKBACK, min_periods=1).max().values.astype(bool)
        base_ok = slope_ok & above5 & gc_recent
        rising = np.ones(n, bool)
        n_above = np.zeros(n, int)
        for k in F:
            r = np.zeros(n, bool); r[DIR_DAYS:] = ma[k][DIR_DAYS:] > ma[k][:-DIR_DAYS]
            rising &= r
            n_above += (ma[k] > m200).astype(int)
    n5 = n_above + 1
    stageA = base_ok & rising          # A or C（4本とも右上がり）
    per_stock[code] = (a, ma)
    last_fire = {k: -10**9 for k in range(1, 6)}
    for i in range(221, n - MAXH):
        if np.isnan(a[i]):
            continue
        fired = None
        # 1本 = 5MA GC日（①を満たす。4本の向きは問わない＝入口）
        if gc_day[i] and slope_ok[i]:
            fired = 1
        # 2〜5本 = 4本とも右上がり かつ 上抜け本数がその段階に「なった」日（前日はそれ未満 or 条件外）
        if stageA[i] and n5[i] >= 2:
            k = n5[i]
            prev_ok = stageA[i-1] and n5[i-1] >= k
            if not prev_ok and i - last_fire[k] > COOLDOWN:
                fired = k
        if fired is None:
            continue
        last_fire[fired] = i
        rec = {"code": code, "i": i, "date": dates[i], "year": dates[i].year, "stage": fired}
        for h in H:
            r = a[i+h] / a[i] - 1
            rec[f"r{h}"] = r
            rec[f"ex{h}"] = r - uni_med[h][i]
        events.append(rec)

ev = pd.DataFrame(events)
ev.to_csv(HERE / "tenkan_bt10y_events.csv", index=False)
print("イベント数", len(ev), "期間", ev.date.min().date(), "→", ev.date.max().date(), flush=True)

def fmt(x): return f"{x*100:+.1f}%"
def summarize(d):
    o = {"N": len(d)}
    for h in H:
        o[f"{h}日 中央値"] = fmt(d[f"r{h}"].median()); o[f"{h}日 平均"] = fmt(d[f"r{h}"].mean())
        o[f"{h}日 勝率"] = f"{(d[f'r{h}']>0).mean()*100:.0f}%"
        o[f"{h}日 超過中央値"] = fmt(d[f"ex{h}"].median())
        o[f"{h}日 超過勝率"] = f"{(d[f'ex{h}']>0).mean()*100:.0f}%"
    return o

pd.set_option("display.width", 300); pd.set_option("display.max_columns", 40)
labels = {1: "1本 5MA GC日", 2: "2本 25MA抜け", 3: "3本 50MA抜け", 4: "4本 75MA抜け", 5: "5本 100MA抜け=C"}
print("\n================ 検証1: 段階別（10年・全期間） ================")
rows = []
for k in range(1, 6):
    d = ev[ev.stage == k]
    rows.append({"段階": labels[k], **summarize(d)})
print(pd.DataFrame(rows).to_string(index=False))
print("ユニバース中央値（全期間）:", {h: fmt(v) for h, v in uni_med_all.items()})

print("\n---- 年別: 120日の 中央値 / 超過中央値 / 超過勝率（段階ごと） ----")
yr = []
for y, dy in ev.groupby("year"):
    row = {"年": y}
    for k in range(1, 6):
        d = dy[dy.stage == k]
        row[labels[k][:2]] = f"N{len(d)} {fmt(d.r120.median())} / 超過{fmt(d.ex120.median())} / {(d.ex120>0).mean()*100:.0f}%" if len(d) >= 10 else f"N{len(d)}"
    yr.append(row)
print(pd.DataFrame(yr).to_string(index=False))

# 年別のユニバース120日中央値（相場局面の確認）
print("\n---- 年別ユニバース120日中央値（局面の確認） ----")
u = pd.Series(uni_med[120], index=dates)
print(u.groupby(u.index.year).median().map(fmt).to_string())

# ---------- 検証2: 損切りルール ----------
print("\n================ 検証2: 損切りルール（買った後の出口） ================")
def simulate(a, ma, i, rule, horizon=180):
    """i で買い、rule に該当した日の終値で売る。該当なしなら horizon 日後。戻り値 (ret, days, stopped)"""
    entry = a[i]
    peak = entry
    for j in range(i + 1, i + horizon + 1):
        c = a[j]
        if np.isnan(c):
            continue
        peak = max(peak, c)
        stop = False
        if rule == "none":
            stop = False
        elif rule == "5MA<25MA":
            stop = ma[5][j] < ma[25][j]
        elif rule == "5MA<50MA":
            stop = ma[5][j] < ma[50][j]
        elif rule == "終値<50MA":
            stop = c < ma[50][j]
        elif rule == "終値<75MA":
            stop = c < ma[75][j]
        elif rule == "5MA<200MA":
            stop = ma[5][j] < ma[200][j]
        elif rule == "終値<200MA":
            stop = c < ma[200][j]
        elif rule == "固定-8%":
            stop = c / entry - 1 <= -0.08
        elif rule == "固定-12%":
            stop = c / entry - 1 <= -0.12
        elif rule == "高値から-15%":
            stop = c / peak - 1 <= -0.15
        elif rule == "高値から-20%":
            stop = c / peak - 1 <= -0.20
        if stop:
            return c / entry - 1, j - i, True
    return a[i + horizon] / entry - 1, horizon, False

RULES = ["none", "5MA<25MA", "5MA<50MA", "終値<50MA", "終値<75MA", "5MA<200MA", "終値<200MA", "固定-8%", "固定-12%", "高値から-15%", "高値から-20%"]

def stop_table(stage_list, title):
    d = ev[ev.stage.isin(stage_list)]
    print(f"\n---- {title}  N={len(d)} （180日持ち切りを上限、途中で該当したら翌日ではなくその日の終値で売却） ----")
    out = []
    for rule in RULES:
        res = np.array([simulate(*per_stock[r.code], r.i, rule) for r in d.itertuples()])
        ret, days, stopped = res[:, 0], res[:, 1], res[:, 2].astype(bool)
        hold180 = d.r180.values
        wins = ret > 0
        whipsaw = stopped & (hold180 > 0)          # 切ったのに持っていれば勝っていた
        saved = stopped & (hold180 <= ret)          # 切って正解（持っていたらもっと悪かった）
        out.append({
            "出口ルール": rule, "平均(期待値)": fmt(ret.mean()), "中央値": fmt(np.median(ret)),
            "勝率": f"{wins.mean()*100:.0f}%",
            "勝ち平均": fmt(ret[wins].mean()) if wins.any() else "-",
            "負け平均": fmt(ret[~wins].mean()) if (~wins).any() else "-",
            "最悪": fmt(ret.min()),
            "損切り発動": f"{stopped.mean()*100:.0f}%",
            "だまし(切って損)": f"{whipsaw.mean()*100:.0f}%",
            "切って正解": f"{saved.mean()*100:.0f}%",
            "平均保有日": f"{days.mean():.0f}",
        })
    print(pd.DataFrame(out).to_string(index=False))

stop_table([4], "4本（75MA抜け）で買った場合")
stop_table([3], "3本（50MA抜け）で買った場合")
stop_table([5], "5本（100MA抜け=C）で買った場合")

# 局面別（ユニバース120日中央値がマイナスの年＝逆風年）での損切り効果: 4本
bad_years = [int(y) for y, v in u.groupby(u.index.year).median().items() if v < 0]
print("\n逆風年（ユニバース120日中央値がマイナス）:", bad_years)
if bad_years:
    d = ev[(ev.stage == 4) & (ev.year.isin(bad_years))]
    print(f"---- 逆風年だけ・4本で買った場合 N={len(d)} ----")
    out = []
    for rule in ["none", "5MA<25MA", "5MA<50MA", "終値<75MA", "5MA<200MA", "固定-8%", "高値から-15%"]:
        res = np.array([simulate(*per_stock[r.code], r.i, rule) for r in d.itertuples()])
        ret, stopped = res[:, 0], res[:, 2].astype(bool)
        out.append({"出口ルール": rule, "平均": fmt(ret.mean()), "中央値": fmt(np.median(ret)), "勝率": f"{(ret>0).mean()*100:.0f}%", "負け平均": fmt(ret[ret<=0].mean()) if (ret<=0).any() else "-", "発動": f"{stopped.mean()*100:.0f}%"})
    print(pd.DataFrame(out).to_string(index=False))
