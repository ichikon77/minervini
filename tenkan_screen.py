# -*- coding: utf-8 -*-
"""
tenkan_screen.py — 移動平均線の並び転換スクリーナー（日本株プライム）

見つけたいもの:
  下降トレンドから上昇トレンドへ「並びが逆転していく途中」の銘柄。
  ①200MAが水平よりやや右上がり以上
  ②5MAが200MAを上抜け（ゴールデンクロス）済みで、25/50/75/100MAも順に上抜けしていきそうな形

出力: tenkan.html（同じフォルダ）, tenkan_history.json（初回検出日の記録）, txt/Tenkan.txt（TradingView用）
"""
import os, re, sys, json, io, time, datetime as dt
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf
import requests

BASE = Path(__file__).resolve().parent
OUT_HTML = BASE / "tenkan.html"
HIST_JSON = BASE / "tenkan_history.json"
TXT_DIR = BASE / "txt"
LOG = BASE / "tenkan_log.txt"
MCAP_CACHE = Path(os.environ.get("TENKAN_MCAP", str(BASE / "margin_mcap_cache.json")))  # 時価総額(億円)。margin_weekly.pyが毎週更新する共有キャッシュ

# ---------------- 暫定しきい値（仮説。分布が溜まったら見直す） ----------------
SLOPE_DAYS = 20          # ①傾きを測る窓（営業日）
SLOPE_MIN = 0.005        # ①200MAの傾き ≥ +0.5% / 20営業日（年率約+6%）＝「水平よりやや右上がり」
GC_LOOKBACK = 60         # ②5MAのゴールデンクロスが直近何営業日以内か
APPROACH_DAYS = 20       # ②接近判定: 200MAとの距離が何営業日前より縮んでいるか
MAS = [5, 25, 50, 75, 100, 200]
FOLLOWERS = [25, 50, 75, 100]   # 5MAの後に続くべき4本
BATCH = 120
PERIOD = "2y"
MIN_ROWS = 200 + SLOPE_DAYS + APPROACH_DAYS + 5

JPX_URLS = [
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx",
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls",
]
LIMIT = int(os.environ.get("TENKAN_LIMIT", "0") or 0)  # 動作確認用: 先頭N銘柄だけ

def log(msg):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ---------------- ユニバース ----------------
def load_universe():
    last_err = None
    for url in JPX_URLS:
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            df = pd.read_excel(io.BytesIO(r.content))
            df = df[df["市場・商品区分"].astype(str).str.contains("プライム")]
            rows = []
            for _, x in df.iterrows():
                c = str(x["コード"]).strip()
                if re.match(r"^[0-9]{3}[0-9A-Z]$", c):
                    rows.append({"code": c, "name": str(x["銘柄名"]), "sector": str(x["33業種区分"]),
                                 "size": str(x.get("規模区分", ""))})
            log(f"ユニバース取得OK: {url.rsplit('/',1)[-1]} プライム{len(rows)}銘柄")
            return rows
        except Exception as e:
            last_err = e
            log(f"ユニバース取得失敗 {url}: {e}")
    raise SystemExit(f"ユニバース取得失敗: {last_err}")

# ---------------- 価格取得 ----------------
CACHE_DIR = os.environ.get("TENKAN_CACHE", "")  # 動作確認用: バッチ結果を一時保存して中断再開できるようにする

def fetch_prices(tickers):
    frames = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i+BATCH]
        cache_f = Path(CACHE_DIR) / f"batch_{i//BATCH}.pkl" if CACHE_DIR else None
        if cache_f and cache_f.exists():
            frames.update(pd.read_pickle(cache_f))
            log(f"価格取得(キャッシュ) {min(i+BATCH, len(tickers))}/{len(tickers)} 有効{len(frames)}")
            continue
        got = {}
        for attempt in range(3):
            try:
                d = yf.download(chunk, period=PERIOD, interval="1d", group_by="ticker",
                                auto_adjust=False, progress=False, threads=True)
                break
            except Exception as e:
                log(f"download失敗 batch{i//BATCH} try{attempt}: {e}")
                time.sleep(5)
        else:
            continue
        for t in chunk:
            try:
                sub = d[t] if isinstance(d.columns, pd.MultiIndex) else d
                sub = sub.dropna(subset=["Close"])
                if len(sub) >= MIN_ROWS:
                    # MAは配当調整済み終値(Adj Close)で計算 = TradingViewの「配当落ち調整」ONと同じ。
                    # 配当落ちの段差がMAの位置関係を狂わせるのを防ぐ（2734で100/200MAの上下が逆転した実例）
                    keep = sub[["Close", "Volume"]].copy()
                    keep["Adj"] = sub["Adj Close"] if "Adj Close" in sub.columns else sub["Close"]
                    got[t] = keep
            except Exception:
                pass
        frames.update(got)
        if cache_f:
            Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
            pd.to_pickle(got, cache_f)
        log(f"価格取得 {min(i+BATCH, len(tickers))}/{len(tickers)} 有効{len(frames)}")
    return frames

# ---------------- 判定 ----------------
def last_cross_up(fast, slow, lookback):
    """fastがslowを下から上へ抜けた最後の日付とその経過営業日数（lookback以内）。無ければ(None, None)"""
    above = (fast > slow).values
    n = len(above)
    for k in range(1, lookback + 1):
        i = n - k
        if i - 1 < 0:
            break
        if above[i] and not above[i-1]:
            return fast.index[i], k - 1
    return None, None

def analyze(code, px):
    close = px["Close"]
    adj = px["Adj"].ffill() if "Adj" in px.columns else close
    ma = {n: adj.rolling(n).mean() for n in MAS}   # 配当調整済み終値ベース
    m200 = ma[200]
    if m200.dropna().shape[0] < SLOPE_DAYS + APPROACH_DAYS + 2:
        return None
    price = float(close.iloc[-1])   # 表示用の株価は実際の終値
    slope = float(m200.iloc[-1] / m200.iloc[-1 - SLOPE_DAYS] - 1)

    # ② 5MAのゴールデンクロス
    gc_date, gc_ago = last_cross_up(ma[5], m200, GC_LOOKBACK)
    ma5_above = bool(ma[5].iloc[-1] > m200.iloc[-1])
    # GC日の「MAの束の幅」= 25/50/75/100/200MAの最大−最小 ÷ 200MA（10年検証: 3%以下が最良、6〜10%が最悪）
    bundle = None
    if gc_ago is not None:
        gi = -1 - gc_ago
        vals = [float(ma[n].iloc[gi]) for n in (25, 50, 75, 100, 200)]
        if all(np.isfinite(vals)) and vals[-1] > 0:
            bundle = (max(vals) - min(vals)) / vals[-1]

    fol = {}
    for n in FOLLOWERS:
        dist_now = float(ma[n].iloc[-1] / m200.iloc[-1] - 1)
        dist_prev = float(ma[n].iloc[-1 - APPROACH_DAYS] / m200.iloc[-1 - APPROACH_DAYS] - 1)
        speed = (dist_now - dist_prev) / APPROACH_DAYS  # 1営業日あたりの距離変化（＋なら200MAに接近）
        own_slope = float(ma[n].iloc[-1] / ma[n].iloc[-1 - APPROACH_DAYS] - 1)  # そのMA自身の向き
        rising = own_slope > 0
        eta = None
        if dist_now > 0:
            st = "上抜け済み" if rising else "上だが右下がり"
        else:
            if rising:
                st = "接近中"           # 200MAの下にいるが、そのMA自身は右上がり
                if speed > 0:
                    eta = int(round(-dist_now / speed))
            else:
                st = "下離れ中"         # 200MAの下にいて、そのMA自身も右下がり
        cdate, _ = last_cross_up(ma[n], m200, GC_LOOKBACK)
        fol[n] = {"dist": dist_now, "prev": dist_prev, "slope": own_slope, "status": st, "eta": eta,
                  "cross": cdate.strftime("%m/%d") if cdate is not None else ""}

    n_above = sum(1 for n in FOLLOWERS if fol[n]["dist"] > 0)
    n_warn = sum(1 for n in FOLLOWERS if fol[n]["status"] == "上だが右下がり")
    n_appr = sum(1 for n in FOLLOWERS if fol[n]["status"] == "接近中")
    n_away = sum(1 for n in FOLLOWERS if fol[n]["status"] == "下離れ中")

    turnover = float((close * px["Volume"]).tail(20).mean()) / 1e8  # 億円

    return {"code": code, "price": price, "slope": slope,
            "ma5_above": ma5_above, "gc_date": gc_date, "gc_ago": gc_ago, "bundle": bundle,
            "fol": fol, "n_above": n_above, "n_appr": n_appr, "n_away": n_away, "n_warn": n_warn,
            "turnover": turnover,
            "ma": {n: float(ma[n].iloc[-1]) for n in MAS}}

def classify(r):
    """①②を満たすか、満たすならどの段階か（定義1: 1本でも右下がりならB。2026-09-16バックテストで採用）"""
    if r["slope"] < SLOPE_MIN:
        return None
    if not (r["ma5_above"] and r["gc_date"] is not None):
        return None
    if r["n_away"] + r["n_warn"] > 0:
        return "B"   # 25/50/75/100のどれかが右下がり（200MAの上下は問わない）＝形が揃わない
    if r["n_above"] == 4:
        return "C"   # 4本すべて右上がりで200MAの上＝並び完成（Minervini側の領域）
    return "A"       # 4本すべて右上がり、まだ200MAの下の線が残る＝転換初期（本命）

# ---------------- 時価総額 ----------------
def load_mcaps(codes):
    """共有キャッシュ margin_mcap_cache.json（{code: 億円}）を読み、無い銘柄だけyfinanceで補う"""
    caps = {}
    if MCAP_CACHE.exists():
        try:
            raw = json.loads(MCAP_CACHE.read_text(encoding="utf-8"))
            caps = {k: v for k, v in raw.items() if isinstance(v, (int, float))}
            log(f"時価総額キャッシュ読込: {len(caps)}銘柄 ({MCAP_CACHE.name})")
        except Exception as e:
            log(f"時価総額キャッシュ読込失敗: {e}")
    missing = [c for c in codes if c not in caps]
    for c in missing:
        try:
            mc = yf.Ticker(c + ".T").fast_info.get("marketCap")
            if mc:
                caps[c] = round(mc / 1e8)
        except Exception:
            pass
    if missing:
        log(f"時価総額をyfinanceで補完: {len(missing)}銘柄中 {sum(1 for c in missing if c in caps)}件取得")
    return caps

# ---------------- HTML ----------------
NAV = [
    ("map.html", "デッキの見方", "#94a3b8"),
    ("calendar.html", "イベント予定", "#94a3b8"),
    ("yorimae.html", "寄り前", "#94a3b8"),
    ("cpi.html", "米インフレと雇用", "#7c3aed"),
    ("fedwatch.html", "FRB利上げ確率", "#7c3aed"),
    ("totan.html", "日銀利上げ確率", "#7c3aed"),
    ("kinri.html", "金利と為替", "#7c3aed"),
    ("kanryu.html", "還流ウォッチ", "#7c3aed"),
    ("spriron.html", "SP500理論株価", "#7c3aed"),
    ("riron.html", "日経理論株価", "#7c3aed"),
    ("gaikoku.html", "海外投資家", "#2563eb"),
    ("saitei.html", "裁定取引", "#2563eb"),
    ("shutai.html", "投資主体別", "#2563eb"),
    ("shinyou.html", "信用評価率", "#d97706"),
    ("touraku.html", "騰落レシオ", "#d97706"),
    ("karauri.html", "空売り比率", "#d97706"),
    ("vix.html", "VIX温度計", "#d97706"),
    ("sns.html", "SNS恐怖温度計", "#d97706"),
    ("flow.html", "資金フロー", "#059669"),
    ("daikin.html", "売買代金", "#059669"),
    ("minervini_report_v2.html", "米国株 (Minervini)", "#db2777"),
    ("jpminervini.html", "日本株 (Minervini)", "#db2777"),
    ("haitou.html", "日本株 (配当)", "#db2777"),
    ("tenkan.html", "並び転換", "#db2777"),
    ("insider.html", "インサイダー売買", "#db2777"),
    ("margin.html", "銘柄チェッカー", "#db2777"),
    ("buffett.html", "バフェット", "#db2777"),
    ("cramer.html", "クレイマー", "#db2777"),
    ("kijitsu.html", "信用期日", "#db2777"),
    ("kijitsu_us.html", "下落日数(US)", "#db2777"),
    ("fx_corr.html", "円安/円高相関", "#db2777"),
    ("kasetsu.html", "仮説検証", "#94a3b8"),
]

def nav_html():
    out = []
    for href, label, color in NAV:
        act = ' class="active"' if href == "tenkan.html" else ""
        out.append(f'<a href="{href}"{act} style="border-color:{color}">{label}</a>')
    return "\n".join(out)

def pct(x, plus=True, nd=1):
    s = f"{x*100:+.{nd}f}%" if plus else f"{x*100:.{nd}f}%"
    return s

def dist_cell(f):
    d = f["dist"]
    st = f["status"]
    cls = {"上抜け済み": "st-above", "上だが右下がり": "st-warn", "接近中": "st-appr", "下離れ中": "st-away"}[st]
    arrow = {"上抜け済み": "✓", "上だが右下がり": "⚠", "接近中": "↗", "下離れ中": "↘"}[st]
    slope_txt = f"向き{pct(f['slope'], nd=1)}"
    sub = ""
    if st == "接近中":
        if f["eta"] is None:
            sub = f"<span class='sub'>{slope_txt}・距離は広がり気味</span>"
        elif f["eta"] <= 1:
            sub = f"<span class='sub'>{slope_txt}・目前（1日以内）</span>"
        elif f["eta"] <= 120:
            sub = f"<span class='sub'>{slope_txt}・あと約{f['eta']}日</span>"
        else:
            sub = f"<span class='sub'>{slope_txt}・あと120日超</span>"
    elif st == "上抜け済み":
        sub = f"<span class='sub'>{slope_txt}" + (f"・{f['cross']}上抜け" if f["cross"] else "") + "</span>"
    elif st == "上だが右下がり":
        sub = f"<span class='sub'>{slope_txt}・デッドクロス注意</span>"
    else:
        sub = f"<span class='sub'>{slope_txt}</span>"
    return f"<td class='{cls}'>{arrow} {pct(d, nd=2)}{sub}</td>"

def hist_cell(h):
    if not h:
        return "-"
    parts = [h.get("first", "-")[5:]]
    for k, lab in (("n3", "3本"), ("n4", "4本"), ("n5", "5本C")):
        if h.get(k):
            parts.append(f"{lab}{h[k][5:]}")
    return "<span class='sub'>" + "・".join(parts[1:]) + "</span>" if len(parts) > 1 else ""

def turnover_cell(t):
    if t < 1:
        return f"<td class='num thin'>{t:,.1f}<span class='sub'>薄い</span></td>"
    return f"<td class='num'>{t:,.1f}</td>"

def bundle_cell(b):
    if b is None:
        return "<td class='num'>-</td>"
    pc = b * 100
    if pc <= 3:
        cls, lab = "st-above", "束"
    elif pc <= 6:
        cls, lab = "", ""
    elif pc <= 10:
        cls, lab = "st-away", "散"
    else:
        cls, lab = "st-warn", "バラ"
    sub = f"<span class='sub'>{lab}</span>" if lab else ""
    return f"<td class='num {cls}'>{pc:.1f}%{sub}</td>"

def row_html(r, meta, hist_entry):
    first_seen = (hist_entry or {}).get("first", "-")
    stages = hist_cell(hist_entry)
    gc = r["gc_date"].strftime("%m/%d") if r["gc_date"] is not None else "-"
    cells = [
        f"<td class='code'><a href='https://kabutan.jp/stock/chart?code={r['code']}' target='_blank'>{r['code']}</a></td>",
        f"<td class='name'>{meta['name']}</td>",
        f"<td class='sector'>{meta['sector']}</td>",
        f"<td class='num'>{r['price']:,.0f}</td>",
        f"<td class='num'>{format(r['mcap'], ',.0f') if r.get('mcap') else '-'}</td>",
        turnover_cell(r['turnover']),
        f"<td class='num slope'>{pct(r['slope'], nd=2)}</td>",
        f"<td class='num'>{gc}<span class='sub'>{r['gc_ago']}日前</span></td>",
        bundle_cell(r.get("bundle")),
        f"<td class='num big'>{r['n_above']+1}/5<span class='sub'>接近{r['n_appr']}・下離れ{r['n_away']}{'・⚠'+str(r['n_warn']) if r['n_warn'] else ''}</span></td>",
    ]
    for n in FOLLOWERS:
        cells.append(dist_cell(r["fol"][n]))
    cells.append(f"<td class='num'>{first_seen}{stages}</td>")
    return "<tr>" + "".join(cells) + "</tr>"

def build_html(results, meta_by_code, hist, today, n_universe, n_valid, n_slope_ok, n_gc_ok):
    groups = {"A": [], "B": [], "C": []}
    for r in results:
        g = classify(r)
        if g:
            groups[g].append(r)

    def next_eta(r):
        etas = [f["eta"] for f in r["fol"].values() if f["status"] == "接近中" and f["eta"] is not None]
        return min(etas) if etas else 999
    # 並び: 上抜け本数が多い順 → 次のクロスが近い順 → 傾きが強い順
    for g in groups.values():
        g.sort(key=lambda r: (-r["n_above"], next_eta(r), -r["slope"]))

    head = ("<thead><tr>"
            "<th class='code'>コード</th><th class='name'>銘柄名</th><th>33業種</th><th>株価</th><th>時価総額<br><span class='fx'>（億円）</span></th><th>売買代金<br><span class='fx'>（20日平均・億円）</span></th>"
            f"<th>①200MA傾き<br><span class='fx'>（今日÷{SLOPE_DAYS}日前−1）</span></th>"
            "<th class='gc'>②5MA<br>GC日<br><span class='fx'>最後の<br>上抜け日</span></th>"
            "<th class='gc'>GC日の<br>束の幅<br><span class='fx'>5MAがGCした日の<br>25〜200MA 5本の<br>最大−最小÷200MA</span></th>"
            "<th>②上抜け本数<br><span class='fx'>（5/25/50/75/100の5本中。5MAは①②で必ず1本目）</span></th>"
            + "".join(f"<th>{n}MA<br><span class='fx'>（{n}MA÷200MA−1）</span></th>" for n in FOLLOWERS) +
            "<th>初回検出<br><span class='fx'>（3本/4本/5本到達日）</span></th>"
            "</tr></thead>")

    def table(g, key):
        if not g:
            return "<p class='empty'>該当なし（今日は0銘柄）</p>"
        body = "\n".join(row_html(r, meta_by_code[r["code"]], hist.get(r["code"], {"first": today})) for r in g)
        return f"<div class='tw'><table class='t'>{head}<tbody>{body}</tbody></table></div>"

    nA, nB, nC = len(groups["A"]), len(groups["B"]), len(groups["C"])
    # 業種の顔ぶれ（Aグループ）
    sec = {}
    for r in groups["A"]:
        s = meta_by_code[r["code"]]["sector"]
        sec[s] = sec.get(s, 0) + 1
    sec_txt = "、".join(f"{k}{v}" for k, v in sorted(sec.items(), key=lambda x: -x[1])[:8]) or "-"

    html = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>並び転換 — 移動平均線の並びが逆転していく途中の銘柄</title>
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
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin-bottom:16px}}
.card{{background:#1e293b;border-radius:10px;padding:12px 14px;border-left:5px solid #db2777}}
.card .lbl{{font-size:12px;color:#94a3b8}} .card .val{{font-size:30px;font-weight:700;margin:2px 0}} .card .sm{{font-size:12px;color:#cbd5e1;line-height:1.5}}
.card.a{{border-left-color:#f472b6}} .card.b{{border-left-color:#64748b}} .card.c{{border-left-color:#a78bfa}} .card.f{{border-left-color:#334155}}
h2{{font-size:16px;margin:22px 0 6px;padding-left:10px;border-left:4px solid #db2777}} h2 .n{{color:#f9a8d4}}
h2 small{{color:#94a3b8;font-weight:normal;font-size:12px;margin-left:8px}}
.tw{{overflow-x:auto}} table.t{{border-collapse:collapse;width:100%;font-size:12.5px}}
.t th{{background:#334155;padding:6px 6px;text-align:center;white-space:nowrap;position:sticky;top:0}} .t th .fx{{font-weight:normal;color:#cbd5e1;font-size:10.5px}}
.t td{{padding:5px 6px;border-bottom:1px solid #1e293b;text-align:right;white-space:nowrap}}
.t td.code,.t td.name,.t td.sector{{text-align:left}} .t td.code a{{color:#93c5fd;text-decoration:none}}
.t td.sector{{color:#94a3b8;font-size:11.5px}}
.t td .sub{{display:block;font-size:10.5px;color:#94a3b8;font-weight:normal}}
.t td.big{{font-weight:700;font-size:14px}}
.t td.st-above{{background:#14532d;color:#bbf7d0}} .t td.st-appr{{background:#1e3a8a;color:#bfdbfe}} .t td.st-away{{background:#3f1d2e;color:#fda4af}}
.t td.slope{{color:#fcd34d}} .t td.thin{{color:#64748b}} .t td.thin .sub{{color:#64748b}}
.empty{{color:#94a3b8;padding:10px}}
.note{{color:#94a3b8;font-size:12px;line-height:1.7;margin-top:18px}}
tr:hover td{{filter:brightness(1.15)}}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head><body><div class="wrap">
<nav>
{nav_html()}
</nav>
<h1>並び転換 — 移動平均線の並びが逆転していく途中の銘柄（日本株プライム）</h1>
<div class="upd">更新: {today} 引け後データ ／ ユニバース: 東証プライム {n_universe}銘柄（価格データ十分 {n_valid}銘柄）</div>

<div class="def">
<b>見つけたいもの</b>: 下降トレンドが終わり、短い移動平均線から順に200MAを上抜けしていく「並びの逆転の途中」にある銘柄。
Minervini（並びが完成した銘柄）より<b>一段手前</b>を拾う。<br>
<b>① 200MAの傾き</b> ＝ <code>200MA(今日) ÷ 200MA({SLOPE_DAYS}営業日前) − 1</code> が <code>+{SLOPE_MIN*100:.1f}%</code> 以上（水平よりやや右上がり。年率約+6%相当）<br>
<b>② 5MAのゴールデンクロス</b> ＝ 5MAが200MAを下から上に抜けた日が直近{GC_LOOKBACK}営業日以内で、今も5MA＞200MA。
続く<b>25/50/75/100MA</b>は「200MAの上か下か」×「そのMA自身が右上がりか右下がりか（今日÷{APPROACH_DAYS}営業日前−1）」で4分類:
<span style="color:#bbf7d0">✓上抜け済み</span>（200MAの上・右上がり）／
<span style="color:#fde68a">⚠上だが右下がり</span>（200MAの上にいるが下向き＝デッドクロスの恐れ）／
<span style="color:#bfdbfe">↗接近中</span>（200MAの下・右上がり。距離が縮んでいれば「あと約N日」を目安表示）／
<span style="color:#fda4af">↘下離れ中</span>（200MAの下・右下がり）<br>
<b>計算の土台</b>: 移動平均はすべて<b>配当調整済み終値</b>で計算（TradingViewの「配当落ち調整」ONと同じ値になる）。配当落ちの段差が200MAだけに残って100MAとの上下が逆転するのを防ぐため。株価・売買代金の列は実際の終値。<br>
<b>A/B/Cの分け方</b>: 4本とも右上がりなら A（下の線が残る）か C（4本とも上）、1本でも右下がりなら B。<span class="prov">根拠: 2025/8〜2026/3のバックテスト（5営業日ごと判定）で、A は120日後中央値+14.6%・勝率73%、B は+1.3%・53%。「200MAの上にいるが右下がり」の線だけが理由の群も+0.6%とBと同じ成績だったため、上下を問わず「向き」で分ける定義を採用。上昇相場期のデータで重複サンプルあり＝暫定。</span><br>
<span class="prov">※しきい値（+{SLOPE_MIN*100:.1f}%・{GC_LOOKBACK}日・{APPROACH_DAYS}日）はすべて暫定（仮説段階）。分布が溜まったら見直す。「あと約N日」は直近{APPROACH_DAYS}日の速さが続いた場合の単純換算で、目安に過ぎない。</span>
</div>

<div class="cards">
<div class="card f"><div class="lbl">①を満たす（200MAが右上がり）</div><div class="val">{n_slope_ok}</div><div class="sm">{n_valid}銘柄中 → うち②5MA GC済み {n_gc_ok}</div></div>
<div class="card a"><div class="lbl">A 転換初期</div><div class="val">{nA}</div><div class="sm">①② + 25/50/75/100MAが<b>4本とも右上がり</b>で、まだ200MAの下の線が残る（上抜け2〜4本）<br>上位業種: {sec_txt}</div></div>
<div class="card b"><div class="lbl">B 形が揃わない（参考）</div><div class="val">{nB}</div><div class="sm">①② + 4本のうち<b>1本でも右下がり</b>（↘下離れ中 または ⚠上だが右下がり）</div></div>
<div class="card c"><div class="lbl">C 並び完成（Minervini側）</div><div class="val">{nC}</div><div class="sm">①② + 4本とも右上がりで200MAの上（上抜け5本）。転換は完了、次はトレンド継続を見る</div></div>
</div>


<div class="def" style="margin-top:-4px">
<b>読み方（10年バックテストから。2017/8〜2025/12、プライム1,541銘柄、22,575イベント）</b><br>
・上抜け本数（5MAを含む5本カウント）<b>3本＝候補入り、4本＝確認</b>。1本（5MA GC直後）は明確に劣る。2〜4本の差は小さい。<br>
・<b>典型的な1銘柄は市場並み</b>（中央値は全銘柄の行とほぼ同じ）。平均は市場をやや上回り、その差は一部の大化けから来る。→ 1銘柄に賭けず複数持ち、勝ち銘柄は半年持ち切る。負けは4割ある。<br>
・<b>損切りは近いほど悪い</b>。5MAが25/50MAを割る等は180日内にほぼ100%発動し6割がだまし、期待値を+11.8%→+1〜3%に削る。入れるなら固定−12% か 5MA&lt;200MA（転換の前提が崩れた時）の遠い保険だけ。<br>
・<b>GC日に25〜200MAが束になっていた（幅3%以下）銘柄が最良</b>: 4本買いで勝率63%・180日+6.8%（全銘柄56%・+4.8%）。長い横ばいから立ち上がった形。幅6〜10%（中途半端に散らばる）は+0.1%・勝率50%で最悪。売買代金で分けても段ごとの順位は安定せず（下表）、売買代金は成績ではなく売買のしやすさの目安。列「GC日の束の幅」（②の5MA GC日における25/50/75/100/200MAの最大−最小÷200MA。5MAは含めない）で <span style='color:#bbf7d0'>束</span>＝3%以下、<span style='color:#fda4af'>散</span>＝6〜10%。<br>
・<b>利確ルールは効かない</b>。+10/15/20/30%で利確すると勝率は上がる（+10%で75%）が、同じルールをランダムな銘柄に当てても同じ勝率・同じ平均になる（差ゼロ）。この形のわずかな取り分（持ち切りで+1.7pt、束で+1.4〜2.4pt）は分布の右端にあり、利確はそこを切り落とす。<br>
・200MAの傾きは緩い方（+0.5〜1%/20日）が良く、急な傾き（3%超）は逆に劣る。<br>
・<b>この形だけでは市場を安定して上回らない</b>。買う前の120日は+12%上がっている銘柄群だが、買った後は全銘柄と同じ（右肩下がりの銘柄群も先120日は+3.5%で同水準）。過熱度・過去の上げ幅で分けても安定した差は出ない。→ 「転換の途中にいる銘柄の一覧」として使い、資金フロー（④）・需給や信用倍率（②⑤）と重ねて絞る。
<table class="t" style="width:auto;margin-top:6px;font-size:12px">
<thead><tr><th>入り方<br><span class='fx'>（その段階に進んだ日に買う）</span></th><th>N</th><th>30日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>60日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>90日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>120日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>180日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th></tr></thead>
<tbody>
<tr><td class='name'>1本（5MAが抜けた日＝入口）</td><td class='num'>7,886</td><td class='num'>+0.9 / +1.6 / 54%</td><td class='num'>+1.2 / +2.9 / 54%</td><td class='num'>+1.5 / +4.0 / 54%</td><td class='num'>+1.8 / +5.0 / 55%</td><td class='num'>+3.3 / +8.1 / 56%</td></tr>
<tr><td class='name'>2本（25MAが抜けた日）</td><td class='num'>1,446</td><td class='num'>+1.4 / +2.3 / 58%</td><td class='num'>+1.9 / +3.6 / 56%</td><td class='num'>+1.7 / +3.6 / 54%</td><td class='num'>+2.0 / +6.2 / 54%</td><td class='num'>+4.3 / +11.5 / 57%</td></tr>
<tr><td class='name'>3本（50MAが抜けた日）</td><td class='num'>2,431</td><td class='num'>+1.2 / +2.0 / 56%</td><td class='num'>+1.8 / +3.2 / 56%</td><td class='num'>+1.3 / +3.4 / 53%</td><td class='num'>+2.2 / +6.4 / 55%</td><td class='num'>+5.1 / +11.8 / 58%</td></tr>
<tr><td class='name'>4本（75MAが抜けた日）</td><td class='num'>3,079</td><td class='num'>+1.0 / +1.8 / 54%</td><td class='num'>+1.4 / +2.6 / 54%</td><td class='num'>+1.6 / +3.8 / 54%</td><td class='num'>+2.9 / +7.1 / 57%</td><td class='num'>+5.2 / +11.8 / 59%</td></tr>
<tr><td class='name'>5本（100MAが抜けた日＝C 並び完成）</td><td class='num'>7,726</td><td class='num'>+1.4 / +2.2 / 56%</td><td class='num'>+2.4 / +4.0 / 57%</td><td class='num'>+2.8 / +5.4 / 57%</td><td class='num'>+3.5 / +7.2 / 58%</td><td class='num'>+5.6 / +11.7 / 60%</td></tr>
<tr><td class='name' style="color:#94a3b8">プライム全銘柄（同じ期間に毎日買った全サンプル）</td><td class='num'>-</td><td class='num'>+1.1 / +1.7 / 55%</td><td class='num'>+1.8 / +3.3 / 56%</td><td class='num'>+2.5 / +4.9 / 57%</td><td class='num'>+3.2 / +6.4 / 57%</td><td class='num'>+4.8 / +10.0 / 59%</td></tr>
</tbody></table>
<table class="t" style="width:auto;margin-top:8px;font-size:12px">
<thead><tr><th>出口ルール<br><span class='fx'>（4本で買い、180日持ち切りが上限）</span></th><th>期待値<br><span class='fx'>平均</span></th><th>中央値</th><th>勝率</th><th>勝ち平均</th><th>負け平均</th><th>最悪</th><th>発動率</th><th>だまし<br><span class='fx'>切ったが持てば勝ち</span></th></tr></thead>
<tbody>
<tr><td class='name'>損切りなし</td><td class='num'>+11.8%</td><td class='num'>+5.2%</td><td class='num'>59%</td><td class='num'>+31.1%</td><td class='num'>-16.0%</td><td class='num'>-71.7%</td><td class='num'>0%</td><td class='num'>-</td></tr>
<tr><td class='name'>5MA&lt;25MA</td><td class='num'>+1.0%</td><td class='num'>-1.0%</td><td class='num'>38%</td><td class='num'>+8.7%</td><td class='num'>-3.8%</td><td class='num'>-29.9%</td><td class='num'>100%</td><td class='num'>59%</td></tr>
<tr><td class='name'>5MA&lt;50MA</td><td class='num'>+2.5%</td><td class='num'>-1.8%</td><td class='num'>38%</td><td class='num'>+15.9%</td><td class='num'>-5.6%</td><td class='num'>-42.6%</td><td class='num'>100%</td><td class='num'>59%</td></tr>
<tr><td class='name'>終値&lt;75MA</td><td class='num'>+3.0%</td><td class='num'>-2.5%</td><td class='num'>36%</td><td class='num'>+19.2%</td><td class='num'>-6.2%</td><td class='num'>-36.2%</td><td class='num'>99%</td><td class='num'>58%</td></tr>
<tr><td class='name'>5MA&lt;200MA（転換の前提が崩れる）</td><td class='num'>+7.0%</td><td class='num'>-4.6%</td><td class='num'>38%</td><td class='num'>+35.8%</td><td class='num'>-10.6%</td><td class='num'>-47.1%</td><td class='num'>72%</td><td class='num'>31%</td></tr>
<tr><td class='name'>固定 -8%</td><td class='num'>+6.5%</td><td class='num'>-8.3%</td><td class='num'>35%</td><td class='num'>+36.1%</td><td class='num'>-9.5%</td><td class='num'>-27.0%</td><td class='num'>63%</td><td class='num'>24%</td></tr>
<tr><td class='name'>固定 -12%</td><td class='num'>+7.4%</td><td class='num'>-12.0%</td><td class='num'>43%</td><td class='num'>+34.0%</td><td class='num'>-12.9%</td><td class='num'>-29.3%</td><td class='num'>51%</td><td class='num'>16%</td></tr>
<tr><td class='name'>高値から -20%</td><td class='num'>+5.9%</td><td class='num'>-2.0%</td><td class='num'>47%</td><td class='num'>+27.5%</td><td class='num'>-13.4%</td><td class='num'>-33.4%</td><td class='num'>60%</td><td class='num'>26%</td></tr>
</tbody></table>

<div style="margin-top:10px"><b>同じ表を、GC日の束の幅3%以下（<span style='color:#bbf7d0'>束</span>）の銘柄だけに絞ったもの</b></div>
<table class="t" style="width:auto;margin-top:4px;font-size:12px"><thead><tr><th>入り方<br><span class='fx'>（束の銘柄が、その段階に進んだ日に買う）</span></th><th>N</th><th>30日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>60日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>90日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>120日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>180日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th></tr></thead><tbody>
<tr><td class='name'>1本（5MAが抜けた日＝入口）</td><td class='num'>1,619</td><td class='num'>+1.2 / +1.7 / 57%</td><td class='num'>+1.8 / +3.1 / 58%</td><td class='num'>+3.0 / +4.8 / 59%</td><td class='num'>+2.9 / +5.8 / 59%</td><td class='num'>+6.0 / +10.1 / 63%</td></tr>
<tr><td class='name'>2本（25MAが抜けた日）</td><td class='num'>280</td><td class='num'>+1.3 / +2.3 / 60%</td><td class='num'>+3.8 / +5.3 / 61%</td><td class='num'>+7.3 / +7.3 / 63%</td><td class='num'>+6.2 / +9.3 / 65%</td><td class='num'>+5.9 / +12.3 / 61%</td></tr>
<tr><td class='name'>3本（50MAが抜けた日）</td><td class='num'>538</td><td class='num'>+1.6 / +2.7 / 62%</td><td class='num'>+3.5 / +4.8 / 64%</td><td class='num'>+3.6 / +6.2 / 61%</td><td class='num'>+3.9 / +7.5 / 59%</td><td class='num'>+5.3 / +10.9 / 59%</td></tr>
<tr><td class='name'>4本（75MAが抜けた日）</td><td class='num'>762</td><td class='num'>+1.4 / +1.9 / 59%</td><td class='num'>+2.2 / +3.8 / 59%</td><td class='num'>+3.4 / +5.1 / 60%</td><td class='num'>+3.9 / +7.1 / 63%</td><td class='num'>+6.8 / +11.6 / 63%</td></tr>
<tr><td class='name'>5本（100MAが抜けた日＝C 並び完成）</td><td class='num'>2,261</td><td class='num'>+1.0 / +1.7 / 55%</td><td class='num'>+2.2 / +3.6 / 57%</td><td class='num'>+2.8 / +4.6 / 58%</td><td class='num'>+4.0 / +6.9 / 61%</td><td class='num'>+6.7 / +10.9 / 63%</td></tr>
<tr style='color:#94a3b8'><td class='name'>プライム全銘柄（同じ期間に毎日買った全サンプル）</td><td class='num'>-</td><td class='num'>+1.1 / +1.7 / 55%</td><td class='num'>+1.8 / +3.3 / 56%</td><td class='num'>+2.5 / +4.9 / 57%</td><td class='num'>+3.2 / +6.4 / 57%</td><td class='num'>+4.8 / +10.0 / 59%</td></tr>
</tbody></table>
<span class="prov">束に絞ると全段階で勝率が3〜6ポイント上がり、180日は全銘柄+4.8%に対し+5.3〜6.8%。1本（GC直後）でも束なら全銘柄並みに戻る（束でない1本は劣る）。2本（N=280）は最も良く見えるがNが少ない。</span>

<div style="margin-top:10px"><b>束（GC日の幅3%以下）→ 2〜4本目が抜けた日に買う: 33業種別</b>（180日後の平均が高い順）</div>
<table class="t" style="width:auto;margin-top:4px;font-size:12px"><thead><tr><th>33業種<br><span class='fx'>180日後の平均が高い順</span></th><th>N</th><th>30日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>60日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>90日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>120日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>180日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th></tr></thead><tbody>
<tr style='color:#64748b'><td class='name'>石油・石炭製品<span class='sub'>N少・参考</span></td><td class='num'>9</td><td class='num'>+9.2 / +7.4 / 78%</td><td class='num'>+13.5 / +13.8 / 89%</td><td class='num'>+17.1 / +19.3 / 78%</td><td class='num'>+38.7 / +30.1 / 78%</td><td class='num'>+46.6 / +42.3 / 100%</td></tr>
<tr style='color:#64748b'><td class='name'>証券、商品先物取引業<span class='sub'>N少・参考</span></td><td class='num'>6</td><td class='num'>+8.0 / +6.1 / 83%</td><td class='num'>+17.2 / +21.7 / 83%</td><td class='num'>+21.9 / +24.5 / 83%</td><td class='num'>+19.4 / +22.9 / 83%</td><td class='num'>+36.1 / +35.9 / 83%</td></tr>
<tr style='color:#64748b'><td class='name'>非鉄金属<span class='sub'>N少・参考</span></td><td class='num'>17</td><td class='num'>-0.1 / +2.6 / 47%</td><td class='num'>+7.3 / +12.1 / 82%</td><td class='num'>+7.1 / +11.9 / 71%</td><td class='num'>+11.4 / +22.3 / 71%</td><td class='num'>+23.5 / +33.7 / 76%</td></tr>
<tr style='color:#64748b'><td class='name'>保険業<span class='sub'>N少・参考</span></td><td class='num'>5</td><td class='num'>+9.7 / +5.6 / 80%</td><td class='num'>+5.4 / +6.6 / 80%</td><td class='num'>+13.6 / +15.9 / 100%</td><td class='num'>+13.0 / +10.2 / 80%</td><td class='num'>+33.8 / +29.2 / 80%</td></tr>
<tr><td class='name'>銀行業</td><td class='num'>71</td><td class='num'>+1.5 / +3.3 / 66%</td><td class='num'>+6.5 / +7.1 / 63%</td><td class='num'>+8.0 / +12.5 / 73%</td><td class='num'>+10.1 / +15.6 / 79%</td><td class='num'>+17.7 / +26.2 / 79%</td></tr>
<tr style='color:#64748b'><td class='name'>海運業<span class='sub'>N少・参考</span></td><td class='num'>7</td><td class='num'>+1.4 / +8.4 / 71%</td><td class='num'>+1.4 / +14.7 / 57%</td><td class='num'>+2.2 / +13.6 / 57%</td><td class='num'>-11.4 / +17.6 / 29%</td><td class='num'>-16.0 / +24.7 / 29%</td></tr>
<tr style='color:#64748b'><td class='name'>倉庫・運輸関連業<span class='sub'>N少・参考</span></td><td class='num'>10</td><td class='num'>+3.5 / +7.9 / 80%</td><td class='num'>+4.0 / +12.3 / 70%</td><td class='num'>+7.5 / +12.5 / 100%</td><td class='num'>+8.8 / +13.5 / 100%</td><td class='num'>+21.0 / +23.5 / 90%</td></tr>
<tr style='color:#64748b'><td class='name'>鉱業<span class='sub'>N少・参考</span></td><td class='num'>5</td><td class='num'>+6.8 / +6.4 / 80%</td><td class='num'>+24.9 / +19.1 / 80%</td><td class='num'>+18.6 / +21.9 / 60%</td><td class='num'>+43.2 / +23.3 / 60%</td><td class='num'>+23.4 / +21.1 / 60%</td></tr>
<tr style='color:#64748b'><td class='name'>水産・農林業<span class='sub'>N少・参考</span></td><td class='num'>8</td><td class='num'>-2.2 / -1.5 / 50%</td><td class='num'>+6.2 / +4.9 / 62%</td><td class='num'>-1.9 / +5.5 / 38%</td><td class='num'>-0.3 / +4.1 / 50%</td><td class='num'>+24.4 / +21.0 / 100%</td></tr>
<tr><td class='name'>卸売業</td><td class='num'>148</td><td class='num'>+2.7 / +3.3 / 65%</td><td class='num'>+5.1 / +5.7 / 70%</td><td class='num'>+8.1 / +8.5 / 71%</td><td class='num'>+8.9 / +11.7 / 72%</td><td class='num'>+13.3 / +17.4 / 76%</td></tr>
<tr><td class='name'>建設業</td><td class='num'>111</td><td class='num'>+1.2 / +1.3 / 59%</td><td class='num'>+4.2 / +5.3 / 63%</td><td class='num'>+3.9 / +8.4 / 60%</td><td class='num'>+4.5 / +10.6 / 67%</td><td class='num'>+10.3 / +17.2 / 67%</td></tr>
<tr style='color:#64748b'><td class='name'>ゴム製品<span class='sub'>N少・参考</span></td><td class='num'>9</td><td class='num'>-3.7 / -0.2 / 44%</td><td class='num'>+1.5 / +0.1 / 56%</td><td class='num'>+7.1 / +4.4 / 56%</td><td class='num'>+12.8 / +9.1 / 78%</td><td class='num'>+13.9 / +15.9 / 78%</td></tr>
<tr><td class='name'>金属製品</td><td class='num'>35</td><td class='num'>+0.5 / -0.1 / 57%</td><td class='num'>+5.0 / +4.8 / 63%</td><td class='num'>+8.8 / +7.8 / 69%</td><td class='num'>+10.3 / +11.4 / 71%</td><td class='num'>+6.7 / +15.6 / 74%</td></tr>
<tr><td class='name'>輸送用機器</td><td class='num'>34</td><td class='num'>+3.9 / +5.8 / 68%</td><td class='num'>+13.7 / +11.8 / 76%</td><td class='num'>+7.8 / +14.6 / 62%</td><td class='num'>+8.0 / +16.5 / 65%</td><td class='num'>+4.4 / +15.6 / 65%</td></tr>
<tr><td class='name'>その他製品</td><td class='num'>34</td><td class='num'>+3.6 / +2.7 / 65%</td><td class='num'>+2.3 / +4.4 / 56%</td><td class='num'>-2.5 / +5.0 / 41%</td><td class='num'>-2.0 / +8.7 / 47%</td><td class='num'>+0.9 / +15.3 / 53%</td></tr>
<tr><td class='name'>機械</td><td class='num'>121</td><td class='num'>+3.7 / +3.5 / 63%</td><td class='num'>+6.8 / +6.9 / 69%</td><td class='num'>+8.1 / +8.2 / 66%</td><td class='num'>+10.3 / +10.0 / 64%</td><td class='num'>+10.7 / +13.0 / 62%</td></tr>
<tr><td class='name'>電気機器</td><td class='num'>67</td><td class='num'>+2.5 / +1.9 / 63%</td><td class='num'>+0.8 / +2.8 / 58%</td><td class='num'>+4.1 / +3.3 / 64%</td><td class='num'>+3.8 / +5.0 / 58%</td><td class='num'>+5.9 / +12.8 / 69%</td></tr>
<tr><td class='name'>精密機器</td><td class='num'>21</td><td class='num'>+0.5 / +1.0 / 52%</td><td class='num'>+7.3 / +5.5 / 67%</td><td class='num'>+8.6 / +7.2 / 67%</td><td class='num'>+5.8 / +7.1 / 67%</td><td class='num'>+8.3 / +12.0 / 62%</td></tr>
<tr><td class='name'>繊維製品</td><td class='num'>32</td><td class='num'>+1.2 / +0.7 / 69%</td><td class='num'>+7.8 / +4.6 / 75%</td><td class='num'>+9.5 / +5.2 / 69%</td><td class='num'>+5.9 / +7.8 / 69%</td><td class='num'>+9.0 / +9.6 / 62%</td></tr>
<tr><td class='name'>不動産業</td><td class='num'>42</td><td class='num'>+0.3 / +1.1 / 55%</td><td class='num'>+0.6 / +2.2 / 55%</td><td class='num'>+0.9 / +0.9 / 52%</td><td class='num'>+1.9 / +2.6 / 52%</td><td class='num'>+4.0 / +9.4 / 57%</td></tr>
<tr style='color:#64748b'><td class='name'>パルプ・紙<span class='sub'>N少・参考</span></td><td class='num'>15</td><td class='num'>+0.9 / +0.2 / 53%</td><td class='num'>+3.5 / +9.2 / 53%</td><td class='num'>+4.1 / +11.9 / 60%</td><td class='num'>+6.3 / +8.8 / 60%</td><td class='num'>+5.7 / +9.3 / 60%</td></tr>
<tr><td class='name'>サービス業</td><td class='num'>120</td><td class='num'>+1.4 / +3.5 / 64%</td><td class='num'>+3.4 / +4.6 / 62%</td><td class='num'>+4.3 / +5.6 / 64%</td><td class='num'>+5.5 / +8.3 / 67%</td><td class='num'>+3.4 / +9.2 / 57%</td></tr>
<tr><td class='name'>化学</td><td class='num'>96</td><td class='num'>+2.8 / +3.3 / 66%</td><td class='num'>+2.0 / +4.2 / 56%</td><td class='num'>+3.1 / +5.4 / 64%</td><td class='num'>-0.1 / +5.7 / 49%</td><td class='num'>+5.1 / +9.2 / 57%</td></tr>
<tr><td class='name'>情報・通信業</td><td class='num'>133</td><td class='num'>+0.7 / +0.6 / 55%</td><td class='num'>+0.6 / +1.0 / 51%</td><td class='num'>+0.1 / +1.9 / 51%</td><td class='num'>+2.6 / +3.9 / 59%</td><td class='num'>+4.2 / +7.3 / 57%</td></tr>
<tr><td class='name'>ガラス・土石製品</td><td class='num'>23</td><td class='num'>+0.5 / +4.1 / 57%</td><td class='num'>+1.6 / +3.4 / 70%</td><td class='num'>-0.8 / +3.4 / 48%</td><td class='num'>+2.0 / +4.5 / 65%</td><td class='num'>+7.0 / +7.1 / 61%</td></tr>
<tr><td class='name'>その他金融業</td><td class='num'>20</td><td class='num'>-1.2 / +3.4 / 45%</td><td class='num'>-0.9 / +2.8 / 45%</td><td class='num'>+1.5 / +3.2 / 60%</td><td class='num'>+4.5 / +2.6 / 70%</td><td class='num'>+3.2 / +5.8 / 65%</td></tr>
<tr><td class='name'>小売業</td><td class='num'>173</td><td class='num'>+0.9 / +1.0 / 60%</td><td class='num'>+1.8 / +2.7 / 58%</td><td class='num'>+2.7 / +3.7 / 60%</td><td class='num'>+3.0 / +5.2 / 62%</td><td class='num'>+1.3 / +5.0 / 52%</td></tr>
<tr><td class='name'>電気・ガス業</td><td class='num'>26</td><td class='num'>+0.9 / +6.3 / 58%</td><td class='num'>-0.0 / +7.1 / 46%</td><td class='num'>-1.1 / +4.2 / 46%</td><td class='num'>-4.3 / +2.2 / 38%</td><td class='num'>+1.1 / +4.3 / 50%</td></tr>
<tr><td class='name'>陸運業</td><td class='num'>59</td><td class='num'>+0.6 / +0.0 / 51%</td><td class='num'>-1.1 / -0.5 / 49%</td><td class='num'>+2.1 / +1.5 / 54%</td><td class='num'>-0.7 / +0.7 / 46%</td><td class='num'>+0.7 / +3.4 / 51%</td></tr>
<tr><td class='name'>食料品</td><td class='num'>92</td><td class='num'>-0.3 / +0.4 / 48%</td><td class='num'>+1.6 / +1.3 / 58%</td><td class='num'>-0.6 / +1.4 / 49%</td><td class='num'>-0.5 / +0.8 / 49%</td><td class='num'>-1.8 / +2.6 / 47%</td></tr>
<tr style='color:#64748b'><td class='name'>空運業<span class='sub'>N少・参考</span></td><td class='num'>4</td><td class='num'>+4.4 / +4.5 / 50%</td><td class='num'>+3.2 / +2.8 / 50%</td><td class='num'>-1.8 / -2.5 / 25%</td><td class='num'>-1.7 / -0.9 / 50%</td><td class='num'>-2.4 / -2.9 / 50%</td></tr>
<tr style='color:#64748b'><td class='name'>医薬品<span class='sub'>N少・参考</span></td><td class='num'>15</td><td class='num'>+4.3 / +4.0 / 87%</td><td class='num'>+1.3 / +0.3 / 60%</td><td class='num'>-6.5 / -1.7 / 40%</td><td class='num'>-1.2 / -0.4 / 47%</td><td class='num'>-5.0 / -3.9 / 13%</td></tr>
<tr style='color:#64748b'><td class='name'>鉄鋼<span class='sub'>N少・参考</span></td><td class='num'>12</td><td class='num'>-1.4 / -1.0 / 42%</td><td class='num'>-1.1 / -2.8 / 42%</td><td class='num'>-5.0 / -2.5 / 42%</td><td class='num'>-1.8 / -2.5 / 50%</td><td class='num'>-7.8 / -5.1 / 42%</td></tr>
<tr style='color:#f9a8d4;font-weight:700'><td class='name'>全業種 合計</td><td class='num'>1,580</td><td class='num'>+1.4 / +2.2 / 60%</td><td class='num'>+2.8 / +4.4 / 61%</td><td class='num'>+3.9 / +5.9 / 61%</td><td class='num'>+4.2 / +7.6 / 62%</td><td class='num'>+6.0 / +11.5 / 61%</td></tr>
</tbody></table>
<span class="prov">業種は現在のJPX分類（過去の業種変更は無視）。N&lt;20の業種は灰色（1〜2銘柄の大化けで順位が決まるので参考）。N≥50で見ると、銀行・卸売・建設・機械が180日+10〜18%で上位、小売・陸運・食料品・情報通信が+0〜4%で下位。ただし2017〜2025年は金融・商社・建設が構造的に強かった時期で、業種の順位は相場局面の反映を含む。2〜4本の各段階を1銘柄につき最大3回数える重複あり。</span>

<div style="margin-top:10px"><b>束（GC日の幅3%以下）→ 2本目（25MA）が抜けた日に買う: 売買代金別</b></div>
<table class="t" style="width:auto;margin-top:4px;font-size:12px"><thead><tr><th>売買代金<br><span class='fx'>買った日の20日平均</span></th><th>N</th><th>30日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>60日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>90日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>120日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>180日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th></tr></thead><tbody>
<tr><td class='name'>1億円未満</td><td class='num'>97</td><td class='num'>+2.1 / +3.1 / 65%</td><td class='num'>+3.4 / +7.1 / 62%</td><td class='num'>+7.2 / +9.1 / 63%</td><td class='num'>+7.3 / +13.2 / 71%</td><td class='num'>+6.6 / +14.8 / 64%</td></tr>
<tr><td class='name'>1〜3億円</td><td class='num'>71</td><td class='num'>+0.0 / +0.1 / 51%</td><td class='num'>+1.2 / +2.3 / 54%</td><td class='num'>+2.9 / +3.8 / 58%</td><td class='num'>+3.1 / +4.9 / 59%</td><td class='num'>+3.6 / +7.7 / 55%</td></tr>
<tr><td class='name'>3〜10億円</td><td class='num'>54</td><td class='num'>+0.4 / +2.6 / 54%</td><td class='num'>+3.3 / +5.4 / 56%</td><td class='num'>+7.2 / +7.7 / 63%</td><td class='num'>+6.5 / +9.8 / 59%</td><td class='num'>+7.7 / +14.5 / 63%</td></tr>
<tr><td class='name'>10〜30億円</td><td class='num'>32</td><td class='num'>+1.3 / +1.8 / 59%</td><td class='num'>+5.9 / +4.0 / 66%</td><td class='num'>+9.5 / +7.1 / 66%</td><td class='num'>+6.9 / +8.3 / 69%</td><td class='num'>+4.9 / +10.8 / 59%</td></tr>
<tr><td class='name'>30億円以上</td><td class='num'>26</td><td class='num'>+4.6 / +4.9 / 85%</td><td class='num'>+7.7 / +8.1 / 81%</td><td class='num'>+10.4 / +9.2 / 77%</td><td class='num'>+5.0 / +7.2 / 65%</td><td class='num'>+9.2 / +12.8 / 65%</td></tr>
<tr><td class='name' style='color:#f9a8d4;font-weight:700'>合計</td><td class='num'>280</td><td class='num'>+1.3 / +2.3 / 60%</td><td class='num'>+3.8 / +5.3 / 61%</td><td class='num'>+7.3 / +7.3 / 63%</td><td class='num'>+6.2 / +9.3 / 65%</td><td class='num'>+5.9 / +12.3 / 61%</td></tr>
</tbody></table>
<div style="margin-top:10px"><b>束（GC日の幅3%以下）→ 3本目（50MA）が抜けた日に買う: 売買代金別</b></div>
<table class="t" style="width:auto;margin-top:4px;font-size:12px"><thead><tr><th>売買代金<br><span class='fx'>買った日の20日平均</span></th><th>N</th><th>30日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>60日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>90日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>120日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>180日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th></tr></thead><tbody>
<tr><td class='name'>1億円未満</td><td class='num'>164</td><td class='num'>+1.9 / +3.3 / 63%</td><td class='num'>+3.3 / +7.2 / 65%</td><td class='num'>+2.9 / +8.0 / 56%</td><td class='num'>+2.8 / +9.7 / 56%</td><td class='num'>+4.0 / +13.4 / 57%</td></tr>
<tr><td class='name'>1〜3億円</td><td class='num'>139</td><td class='num'>+1.1 / +2.3 / 58%</td><td class='num'>+3.6 / +4.2 / 63%</td><td class='num'>+3.1 / +5.4 / 61%</td><td class='num'>+4.2 / +6.6 / 59%</td><td class='num'>+5.3 / +8.7 / 59%</td></tr>
<tr><td class='name'>3〜10億円</td><td class='num'>108</td><td class='num'>+1.8 / +2.6 / 69%</td><td class='num'>+4.1 / +4.0 / 63%</td><td class='num'>+4.3 / +5.7 / 67%</td><td class='num'>+6.6 / +7.4 / 60%</td><td class='num'>+6.6 / +10.1 / 62%</td></tr>
<tr><td class='name'>10〜30億円</td><td class='num'>71</td><td class='num'>+0.6 / +1.2 / 54%</td><td class='num'>+2.5 / +2.0 / 55%</td><td class='num'>+3.0 / +5.4 / 59%</td><td class='num'>+3.6 / +6.1 / 56%</td><td class='num'>+4.3 / +11.5 / 59%</td></tr>
<tr><td class='name'>30億円以上</td><td class='num'>56</td><td class='num'>+3.2 / +4.1 / 66%</td><td class='num'>+3.1 / +4.7 / 75%</td><td class='num'>+4.1 / +4.8 / 66%</td><td class='num'>+2.6 / +5.7 / 64%</td><td class='num'>+4.6 / +10.4 / 59%</td></tr>
<tr><td class='name' style='color:#f9a8d4;font-weight:700'>合計</td><td class='num'>538</td><td class='num'>+1.6 / +2.7 / 62%</td><td class='num'>+3.5 / +4.8 / 64%</td><td class='num'>+3.6 / +6.2 / 61%</td><td class='num'>+3.9 / +7.5 / 59%</td><td class='num'>+5.3 / +10.9 / 59%</td></tr>
</tbody></table>
<div style="margin-top:10px"><b>束（GC日の幅3%以下）→ 4本目（75MA）が抜けた日に買う: 売買代金別</b></div>
<table class="t" style="width:auto;margin-top:4px;font-size:12px"><thead><tr><th>売買代金<br><span class='fx'>買った日の20日平均</span></th><th>N</th><th>30日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>60日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>90日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>120日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>180日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th></tr></thead><tbody>
<tr><td class='name'>1億円未満</td><td class='num'>237</td><td class='num'>+1.2 / +1.9 / 59%</td><td class='num'>+1.2 / +4.2 / 56%</td><td class='num'>+2.0 / +5.6 / 57%</td><td class='num'>+3.2 / +7.6 / 59%</td><td class='num'>+5.1 / +12.0 / 58%</td></tr>
<tr><td class='name'>1〜3億円</td><td class='num'>161</td><td class='num'>+1.8 / +2.1 / 62%</td><td class='num'>+3.8 / +4.5 / 58%</td><td class='num'>+5.0 / +6.8 / 63%</td><td class='num'>+5.8 / +9.7 / 66%</td><td class='num'>+8.7 / +13.6 / 69%</td></tr>
<tr><td class='name'>3〜10億円</td><td class='num'>171</td><td class='num'>+1.4 / +1.9 / 60%</td><td class='num'>+3.5 / +2.8 / 61%</td><td class='num'>+4.2 / +4.4 / 62%</td><td class='num'>+3.6 / +5.3 / 61%</td><td class='num'>+5.6 / +9.0 / 58%</td></tr>
<tr><td class='name'>10〜30億円</td><td class='num'>110</td><td class='num'>+0.5 / +1.1 / 54%</td><td class='num'>+2.3 / +3.0 / 64%</td><td class='num'>+2.8 / +3.6 / 58%</td><td class='num'>+4.5 / +5.6 / 64%</td><td class='num'>+9.6 / +11.5 / 65%</td></tr>
<tr><td class='name'>30億円以上</td><td class='num'>83</td><td class='num'>+1.9 / +2.6 / 59%</td><td class='num'>+2.9 / +4.3 / 63%</td><td class='num'>+2.5 / +4.1 / 61%</td><td class='num'>+3.9 / +6.2 / 69%</td><td class='num'>+10.2 / +11.7 / 69%</td></tr>
<tr><td class='name' style='color:#f9a8d4;font-weight:700'>合計</td><td class='num'>762</td><td class='num'>+1.4 / +1.9 / 59%</td><td class='num'>+2.2 / +3.8 / 59%</td><td class='num'>+3.4 / +5.1 / 60%</td><td class='num'>+3.9 / +7.1 / 63%</td><td class='num'>+6.8 / +11.6 / 63%</td></tr>
</tbody></table>
<div style="margin-top:10px;color:#94a3b8"><b>参考: プライム全銘柄（同じ期間に毎日買った全サンプル）を同じ売買代金で分けたもの</b></div>
<table class="t" style="width:auto;margin-top:4px;font-size:12px"><thead><tr><th>売買代金<br><span class='fx'>買った日の20日平均</span></th><th>N</th><th>30日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>60日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>90日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>120日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th><th>180日後<br><span class='fx'>中央値 / 平均 / 勝率</span></th></tr></thead><tbody>
<tr style='color:#94a3b8'><td class='name'>1億円未満</td><td class='num'>-</td><td class='num'>+1.0 / +1.8 / 56%</td><td class='num'>+1.9 / +3.5 / 57%</td><td class='num'>+2.8 / +5.3 / 59%</td><td class='num'>+3.5 / +7.0 / 60%</td><td class='num'>+5.7 / +10.9 / 62%</td></tr>
<tr style='color:#94a3b8'><td class='name'>1〜3億円</td><td class='num'>-</td><td class='num'>+1.1 / +1.7 / 55%</td><td class='num'>+1.8 / +3.4 / 56%</td><td class='num'>+2.4 / +5.0 / 56%</td><td class='num'>+3.1 / +6.5 / 57%</td><td class='num'>+4.7 / +10.1 / 59%</td></tr>
<tr style='color:#94a3b8'><td class='name'>3〜10億円</td><td class='num'>-</td><td class='num'>+1.0 / +1.5 / 54%</td><td class='num'>+1.6 / +3.0 / 55%</td><td class='num'>+2.1 / +4.3 / 55%</td><td class='num'>+2.6 / +5.7 / 55%</td><td class='num'>+3.9 / +8.8 / 57%</td></tr>
<tr style='color:#94a3b8'><td class='name'>10〜30億円</td><td class='num'>-</td><td class='num'>+1.0 / +1.3 / 54%</td><td class='num'>+1.5 / +2.7 / 54%</td><td class='num'>+1.8 / +3.9 / 54%</td><td class='num'>+2.3 / +5.1 / 55%</td><td class='num'>+3.2 / +7.8 / 56%</td></tr>
<tr style='color:#94a3b8'><td class='name'>30億円以上</td><td class='num'>-</td><td class='num'>+1.3 / +1.9 / 56%</td><td class='num'>+2.2 / +3.8 / 56%</td><td class='num'>+3.0 / +5.6 / 57%</td><td class='num'>+4.0 / +7.6 / 58%</td><td class='num'>+6.0 / +11.9 / 60%</td></tr>
</tbody></table>
<span class="prov">3つの表とも合計行は全銘柄を上回る。一方、売買代金の段ごとの順位は表によって入れ替わる（1億円未満は2本では最良級、4本では最下位。1〜3億円はその逆）。各段N=26〜237の誤差の範囲＝<b>売買代金は成績の予測には使えない</b>。売買のしやすさ（滑り）の目安としてだけ見る。</span>
<span class="prov">％＝買った日の終値からN営業日後（または売った日）の終値までの騰落率（配当調整済み）。全銘柄の行は同じ期間（2017/8〜2025/12）に毎日・全銘柄を買った場合。なお「買った日と同じ日に市場を買った場合」との比較では4本買いの平均が120日で+4.4%上回る（この形が完成するのは相場の一段上げの後で、その先の市場が弱い時期に当たりやすいため）。中央値＝典型的な結果、平均＝大化け込み。同日比較の平均は2017〜2025の9年すべてプラス（最低2024年+1.3%、最高2025年+11.6%）。今のプライム銘柄だけで検証しているため上場廃止銘柄が抜けており、平均（大化け側）は実際より良く出ている可能性あり。同じ銘柄の転換を複数回数える重複サンプル。検証コード: tenkan_backtest10y.py。</span>
</div>

<h2><span class="n">A</span> 転換初期<small>並び: 上抜け本数（5本カウント）が多い順 → 次のクロスが近い順 → ①傾きが強い順</small></h2>
{table(groups["A"], "A")}

<h2><span class="n">C</span> 並び完成（Minervini側）<small>4本すべて上抜け済み。jpminervini.html と重なる銘柄はそちらのテンプレート判定も見る</small></h2>
{table(groups["C"], "C")}

<h2><span class="n">B</span> 形が揃わない（参考）<small>25/50/75/100MAのうち1本でも右下がりがある（200MAの上下は問わない）</small></h2>
{table(groups["B"], "B")}

<div class="note">
・列の「初回検出」はこのデッキに初めて載った日（tenkan_history.json）。A→C に移った日数がわかると「転換にかかる期間」の分布が取れる（今後の答え合わせ用）。<br>
・時価総額（margin_mcap_cache.json、毎週土曜更新の共有キャッシュ。無い銘柄はyfinanceで補完）と売買代金は絞り込みに使っていない（テクニカル条件のみ）。売買代金1億円未満は「薄い」と灰色表示（自分の注文が1日の出来高の数%になり、成行では値が動く水準）。<br>
・コードのリンクは株探のチャート。個別銘柄のテクニカル分析はTrading Viewで。TradingViewリスト: txt/Tenkan.txt（Aグループ）。
</div>
</div></body></html>"""
    return html, groups

# ---------------- main ----------------
def main():
    today = dt.date.today().strftime("%Y-%m-%d")
    uni = load_universe()
    if LIMIT:
        uni = uni[:LIMIT]
    meta = {u["code"]: u for u in uni}
    tickers = [u["code"] + ".T" for u in uni]
    prices = fetch_prices(tickers)
    log(f"価格データ十分: {len(prices)}/{len(tickers)}")

    results = []
    for t, px in prices.items():
        try:
            r = analyze(t.replace(".T", ""), px)
            if r:
                results.append(r)
        except Exception as e:
            log(f"analyze失敗 {t}: {e}")
    n_slope_ok = sum(1 for r in results if r["slope"] >= SLOPE_MIN)
    n_gc_ok = sum(1 for r in results if r["slope"] >= SLOPE_MIN and r["ma5_above"] and r["gc_date"] is not None)

    hist = {}
    if HIST_JSON.exists():
        try:
            hist = json.loads(HIST_JSON.read_text(encoding="utf-8"))
        except Exception:
            hist = {}
    # 旧形式 {code: "YYYY-MM-DD"} → 新形式 {code: {"first": d, "A2": d, "A3": d, "C": d}}
    for k, v in list(hist.items()):
        if isinstance(v, str):
            hist[k] = {"first": v}
    listed = [r["code"] for r in results if classify(r)]
    caps = load_mcaps(listed)
    for r in results:
        r["mcap"] = caps.get(r["code"])

    # 履歴更新（HTML生成の前に行う）: 初回検出日と、上抜け本数3/4/5本に初めて到達した日
    for r in results:
        g = classify(r)
        if not g:
            continue
        h = hist.setdefault(r["code"], {})
        h.setdefault("first", today)
        n5 = r["n_above"] + 1          # 5MAを含めた上抜け本数（5本カウント）
        if n5 >= 3:
            h.setdefault("n3", today)   # 3本（50MAまで）
        if n5 >= 4:
            h.setdefault("n4", today)   # 4本（75MAまで）
        if g == "C":
            h.setdefault("n5", today)   # 5本（100MAまで＝C 並び完成）
    html, groups = build_html(results, meta, hist, today, len(uni), len(prices), n_slope_ok, n_gc_ok)
    HIST_JSON.write_text(json.dumps(hist, ensure_ascii=False, indent=0), encoding="utf-8")
    OUT_HTML.write_text(html, encoding="utf-8")
    TXT_DIR.mkdir(exist_ok=True)
    (TXT_DIR / "Tenkan.txt").write_text("".join(f"{r['code']}\t,\n" for r in groups["A"]), encoding="utf-8")
    log(f"完了: A{len(groups['A'])} B{len(groups['B'])} C{len(groups['C'])} → {OUT_HTML.name}")

if __name__ == "__main__":
    main()
