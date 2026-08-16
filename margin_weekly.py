# -*- coding: utf-8 -*-
"""
銘柄別ファンダメンタルズ 週次取得 → margin_fundamentals.json → GitHub Pages公開

margin.html（銘柄別信用倍率の検索ページ）の検索結果に、業績・ベータ・レーティングを
追加表示するためのデータを週次（土曜朝）で取得する。3段構えの設計:
  - 制度信用倍率: 全銘柄・毎日更新（既存の margin_screen.py、本スクリプトは触らない）
  - 四半期業績 + ベータ: 時価総額500億円以上（約1,500銘柄）・週次
  - レーティング: 時価総額3,000億円以上（約500銘柄）・週次
    （小型株はアナリストカバレッジがほぼ無く、平均の意味が薄いため絞る）

データ源: yfinance
  - 時価総額: fast_info（軽い）
  - 四半期売上・営業利益: かぶたん(kabutan.jp)の3ヵ月実績テーブル（8四半期・百万円。YoYは自前計算で4期分出る）
    ※yfinanceのquarterly_income_stmtは日本株の欠落が多く（日立・JAL等でEPSのみ）不採用
  - ベータ: 2年日次リターンから対日経(^N225)・対TOPIX(1306.T)を自前計算（一括取得）
  - レーティング: info の recommendationMean / targetMeanPrice 等

実行: 毎週土曜 7:00（margin_weekly_run.bat）。--test で先頭20銘柄のみ。
"""

import os
import re
import sys
import json
import time
import subprocess
import datetime

import yfinance as yf
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_JSON = os.path.join(SCRIPT_DIR, "margin_all_history.json")   # 銘柄コード一覧の元
OUT_JSON = os.path.join(SCRIPT_DIR, "margin_fundamentals.json")
MCAP_CACHE = os.path.join(SCRIPT_DIR, "margin_mcap_cache.json")      # 時価総額キャッシュ（毎回更新）

FUND_MIN_OKU = 500     # 業績・ベータの対象: 時価総額500億円以上
RATING_MIN_OKU = 3000  # レーティングの対象: 3,000億円以上
BETA_CHUNK = 200       # ベータ用株価の一括取得チャンク
SLEEP = 0.4            # API呼び出し間隔（レート制限対策）


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_codes():
    """margin_all_history.json の names から銘柄コード一覧（4桁数字のみ=ETF等を除外しない。
    ETFはfast_infoで時価総額が取れないか業績が無いので自然に落ちる）"""
    with open(HISTORY_JSON, encoding="utf-8") as f:
        names = json.load(f)["names"]
    return sorted(names.keys()), names


def progress(label, i, total, t0):
    """進捗ログ: 件数・%・経過・残り見積もり"""
    if i == 0:
        return
    elapsed = time.time() - t0
    eta = elapsed / i * (total - i)
    log(f"  {label} {i}/{total} ({i/total*100:.0f}%) 経過{elapsed/60:.0f}分 残り約{eta/60:.0f}分")


def fetch_market_caps(codes, full_save=True):
    """fast_infoで時価総額（億円）を取得。
    2026-08-16の教訓（yfinanceレート制限で大半が取得失敗→Noneでキャッシュ全体を上書き→
    時価総額フィルタを使う下流スクリプト(kijitsu/fx_corr等)のユニバースが56銘柄まで崩壊）を受けて:
      1. 既存キャッシュを読み込んでマージ（--test等の部分実行でも全体を消さない）
      2. 取得失敗(None)のときは既存の値を保持（レート制限時に正常値を潰さない）
      3. 成功率が50%未満なら保存しない（レート制限中と判断して既存キャッシュを守る）
    full_save=Falseの場合はファイル保存自体を行わない（テスト実行用）。"""
    caps = {}
    if os.path.exists(MCAP_CACHE):
        try:
            caps = json.load(open(MCAP_CACHE, encoding="utf-8"))
        except Exception:
            caps = {}
    ok = 0
    t0 = time.time()
    for i, code in enumerate(codes):
        mc = None
        try:
            fi = yf.Ticker(code + ".T").fast_info
            mc = fi["marketCap"] if "marketCap" in fi else None
        except Exception:
            mc = None
        if mc:
            caps[code] = round(mc / 1e8)
            ok += 1
        else:
            # 取得失敗: 既存値があれば保持、無ければNone
            caps.setdefault(code, None)
        if i % 100 == 0:
            progress("時価総額", i, len(codes), t0)
        time.sleep(0.15)

    success_rate = ok / len(codes) if codes else 0
    log(f"  時価総額取得: 成功{ok}/{len(codes)}銘柄 ({success_rate*100:.0f}%)")
    if full_save:
        if success_rate >= 0.5:
            json.dump(caps, open(MCAP_CACHE, "w", encoding="utf-8"))
        else:
            log("  警告: 取得成功率が50%未満（yfinanceレート制限の可能性）。"
                "キャッシュ保存をスキップし既存データを保護します")
    return caps


KABUTAN_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}


def fetch_quarters(code):
    """かぶたんの四半期業績（3ヵ月実績）テーブルから
    [期間, 売上(百万円), 営利(百万円), 営利率%, 売上YoY%, 営利YoY%, 営利率YoY bps] 最新順を返す。
    8四半期取れるため、最新4四半期でYoYを自前計算できる。
    銀行等は営業益がNone（銀行決算に営業利益の概念がないため）"""
    import urllib.request
    url = f"https://kabutan.jp/stock/finance?code={code}"
    req = urllib.request.Request(url, headers=KABUTAN_UA)
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", errors="replace")

    best = None
    for m in re.finditer(r"<table[^>]*>(.*?)</table>", html, re.S):
        tbl = m.group(1)
        flat = re.sub(r"<[^>]+>", "", tbl)
        if "売上営業" not in flat or "損益率" not in flat:
            continue
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S)
        parsed = []
        for r in rows:
            cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                     for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S)]
            if len(cells) < 7:
                continue
            m2 = re.search(r"(\d{2})\.(\d{2})-(\d{2})", cells[0])
            if not m2 or "予" in cells[0]:
                continue
            yy, ms, me = m2.groups()

            def num(x):
                x = x.replace(",", "").replace("－", "").strip()
                try:
                    return float(x)
                except ValueError:
                    return None
            parsed.append([f"20{yy}.{ms}-{me}", num(cells[1]), num(cells[2]), num(cells[6])])
        if parsed and (best is None or len(parsed) > len(best)):
            best = parsed
    if not best:
        return []

    # 古い順に並んでいる → 新しい順に。YoYは4四半期前と比較
    best.sort(key=lambda x: x[0], reverse=True)
    out = []
    for i, (period, rev, op, margin) in enumerate(best):
        yoy_r = yoy_o = yoy_m = None
        if i + 4 < len(best):
            _, pr, po, pm = best[i + 4]
            if rev and pr:
                yoy_r = round((rev / pr - 1) * 100, 1)
            if op is not None and po not in (None, 0):
                yoy_o = round((op / po - 1) * 100, 1)
            if margin is not None and pm is not None:
                yoy_m = round((margin - pm) * 100)  # bps
        out.append([period, rev, op, margin, yoy_r, yoy_o, yoy_m])
    return out


def fetch_rating(code):
    """レーティング: {mean, n, target, high, low, dist:[strongBuy,buy,hold,sell,strongSell], price}"""
    t = yf.Ticker(code + ".T")
    info = t.info
    mean = info.get("recommendationMean")
    n = info.get("numberOfAnalystOpinions")
    if not mean or not n:
        return None
    rec = {"mean": round(mean, 2), "n": n,
           "target": info.get("targetMeanPrice"),
           "high": info.get("targetHighPrice"),
           "low": info.get("targetLowPrice"),
           "price": info.get("currentPrice")}
    try:
        r = t.recommendations
        if r is not None and len(r):
            row = r.iloc[0]
            rec["dist"] = [int(row.get(k, 0)) for k in
                           ["strongBuy", "buy", "hold", "sell", "strongSell"]]
    except Exception:
        pass
    return rec


def _clean_ret(close):
    """日次リターン。±20%超は併合・分割の調整漏れとみなして除外"""
    r = close.pct_change()
    return r[abs(r) <= 0.2]


def calc_betas(codes):
    """対日経・対TOPIXのベータと相関係数（2年日次リターン、±20%超の異常値は除外）。
    TOPIXは1308.T（1306.Tは2026年の併合でyfinanceの調整が壊れているため）
    戻り値: {code: [β対N225, β対TOPIX, 相関対N225, 相関対TOPIX]}"""
    bench = yf.download(["^N225", "1308.T"], period="2y", auto_adjust=True,
                        progress=False, group_by="ticker", threads=True)
    n225 = _clean_ret(bench["^N225"]["Close"])
    topix = _clean_ret(bench["1308.T"]["Close"])
    betas = {}

    def beta_corr(ret, bench_ret):
        df = pd.concat([ret, bench_ret], axis=1).dropna()
        if len(df) <= 100:
            return None, None
        b = df.iloc[:, 0].cov(df.iloc[:, 1]) / df.iloc[:, 1].var()
        r = df.iloc[:, 0].corr(df.iloc[:, 1])
        return round(b, 2), round(r, 2)

    for i in range(0, len(codes), BETA_CHUNK):
        chunk = [c + ".T" for c in codes[i:i + BETA_CHUNK]]
        data = yf.download(chunk, period="2y", auto_adjust=True,
                           progress=False, group_by="ticker", threads=True)
        for c in codes[i:i + BETA_CHUNK]:
            try:
                ret = _clean_ret(data[c + ".T"]["Close"])
                b_n, r_n = beta_corr(ret, n225)
                b_t, r_t = beta_corr(ret, topix)
                betas[c] = [b_n, b_t, r_n, r_t]
            except Exception:
                betas[c] = [None, None, None, None]
        log(f"  ベータ {min(i + BETA_CHUNK, len(codes))}/{len(codes)}")
    return betas


def push_to_github():
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)  # 他スクリプトとのgit競合・放置ロック対策
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add",
                    os.path.basename(OUT_JSON), "margin_weekly.py", "margin_weekly_run.bat"],
                   check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update margin fundamentals " + today],
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
            log("  Done")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


def main():
    test = "--test" in sys.argv
    log("銘柄別ファンダメンタルズ 週次取得 開始" + ("（テスト: 20銘柄）" if test else ""))

    codes, names = load_codes()
    log(f"銘柄コード: {len(codes)}件")
    if test:
        codes = [c for c in codes if c in
                 ("1570", "2802", "4368", "6098", "6501", "7014", "7203", "7974",
                  "8306", "9101", "9984", "6146", "6857", "8035", "9020", "9201",
                  "9433", "9613", "4063", "6981")][:20]

    log("① 時価総額スクリーニング...")
    caps = fetch_market_caps(codes, full_save=not test)
    fund_codes = [c for c in codes if (caps.get(c) or 0) >= FUND_MIN_OKU]
    rating_codes = [c for c in fund_codes if (caps.get(c) or 0) >= RATING_MIN_OKU]
    log(f"  業績・ベータ対象: {len(fund_codes)}銘柄（{FUND_MIN_OKU}億以上） / "
        f"レーティング対象: {len(rating_codes)}銘柄（{RATING_MIN_OKU}億以上）")

    log("② ベータ計算（一括）...")
    betas = calc_betas(fund_codes)

    log("③ 四半期業績...")
    stocks = {}
    t0 = time.time()
    for i, c in enumerate(fund_codes):
        try:
            quarters = fetch_quarters(c)
        except Exception:
            quarters = []
        stocks[c] = {"name": names.get(c, ""), "mcap_oku": caps.get(c),
                     "beta": betas.get(c, [None, None, None, None]), "quarters": quarters}
        if i % 50 == 0:
            progress("業績", i, len(fund_codes), t0)
        time.sleep(SLEEP)

    log("④ レーティング...")
    t0 = time.time()
    for i, c in enumerate(rating_codes):
        try:
            r = fetch_rating(c)
        except Exception:
            r = None
        if r:
            stocks[c]["rating"] = r
        if i % 50 == 0:
            progress("レーティング", i, len(rating_codes), t0)
        time.sleep(SLEEP)

    out = {"updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "fund_min_oku": FUND_MIN_OKU, "rating_min_oku": RATING_MIN_OKU,
           "stocks": stocks}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"保存: {OUT_JSON}（{len(stocks)}銘柄）")

    if "--nopush" in sys.argv or test:
        log("push スキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
