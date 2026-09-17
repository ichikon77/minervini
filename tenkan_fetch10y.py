# -*- coding: utf-8 -*-
"""
tenkan_fetch10y.py — 並び転換バックテスト用に、プライム全銘柄の10年分日足（配当調整済み終値）を取得
中断再開可能（バッチごとにpkl保存）。完了すると 1つの DataFrame（日付×銘柄コード、Adj Close）を gzip pickle で保存。
使い方: python tenkan_fetch10y.py [cache_dir] [out_pkl_gz]
"""
import sys, os, re, io, time, glob
from pathlib import Path
import pandas as pd, requests, yfinance as yf

CACHE = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/tk10")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parent / "tenkan_prices10y.pkl.gz")
BATCH = 120
URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"

def universe():
    r = requests.get(URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"}); r.raise_for_status()
    df = pd.read_excel(io.BytesIO(r.content))
    df = df[df["市場・商品区分"].astype(str).str.contains("プライム")]
    return sorted(str(c).strip() for c in df["コード"] if re.match(r"^[0-9]{3}[0-9A-Z]$", str(c).strip()))

def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    codes = universe()
    tickers = [c + ".T" for c in codes]
    print("universe", len(tickers), flush=True)
    for i in range(0, len(tickers), BATCH):
        f = CACHE / f"b{i//BATCH:02d}.pkl"
        if f.exists():
            continue
        chunk = tickers[i:i+BATCH]
        t0 = time.time()
        d = yf.download(chunk, period="10y", interval="1d", group_by="ticker",
                        auto_adjust=False, progress=False, threads=True)
        got = {}; got_to = {}
        for t in chunk:
            try:
                sub = d[t] if isinstance(d.columns, pd.MultiIndex) else d
                sub = sub.dropna(subset=["Close"])
                if len(sub) >= 260:
                    code = t.replace(".T", "")
                    got[code] = sub["Adj Close"].astype("float32")
                    got_to[code] = (sub["Close"] * sub["Volume"] / 1e8).astype("float32")   # 売買代金（億円）
            except Exception:
                pass
        pd.to_pickle(got, f); pd.to_pickle(got_to, CACHE / f"t{i//BATCH:02d}.pkl")
        print(f"batch {i//BATCH} ok {len(got)}/{len(chunk)} {time.time()-t0:.0f}s", flush=True)
    # 結合
    parts = {}
    for f in sorted(CACHE.glob("b*.pkl")):
        parts.update(pd.read_pickle(f))
    df = pd.DataFrame(parts).sort_index()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.to_pickle(OUT, compression="gzip")
    print("saved", OUT, df.shape, df.index[0].date(), "→", df.index[-1].date(), flush=True)
    parts = {}
    for f in sorted(CACHE.glob("t*.pkl")):
        parts.update(pd.read_pickle(f))
    if parts:
        to = pd.DataFrame(parts).sort_index(); to.index = pd.to_datetime(to.index).tz_localize(None)
        out2 = OUT.with_name(OUT.name.replace("prices", "turnover"))
        to.to_pickle(out2, compression="gzip"); print("saved", out2, to.shape, flush=True)

if __name__ == "__main__":
    main()
