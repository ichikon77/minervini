# -*- coding: utf-8 -*-
"""
航海士の風見表（日経平均が上がった日に、いっしょに上がる船・逆らう船） → kazami.html → GitHub Pages公開

考え方（2026-10-11 ユーザー発案）:
  「今年は日経平均が上がっているのに自分の銘柄は上がっていない」人が多い。
  直近3か月の日経平均の上昇日を取り出し、各銘柄がその日に上がった回数を数える。
    順風の船 = 日経上昇日に いっしょに上がった率が高いTop
    逆風の船 = 日経上昇日に 逆に下がった率が高いTop
  率だけだと僅差で順位が決まるので、分母（上昇日数）・上昇日の平均騰落・3か月リターン（対日経）を併記する。

窓の長さ（2026-10-11 検証、92時点・944銘柄・対照群=全銘柄中央値）:
  先行き21営業日の持続性は 1M +6.9pt → 2M +9.3 → 3M +9.8 → 6M +10.5 → 1Y +11.8 と3か月でほぼ頭打ち。
  翌週の顔ぶれ維持率は 1M 36% / 3M 64% / 6M 74% / 1Y 84%。
  → 3か月をメイン（今年の局面を映す最短の窓）、6か月でも同じ側のTop30に入る船に★（半年通してその性格）。

対象銘柄: 時価総額1,000億円以上（fx_corr と同じ閾値）。fx_corr/kijitsu の「直近四半期 営業黒字」フィルタは掛けない
  （2026-10-11 ユーザー判断: 風見は観測が目的で、赤字転落銘柄こそ逆風の典型。外すと逆風側から本来の船が消える）。
注意: 統計的な観測であり、来週も同じ関係が続く保証はない。売買判断ではない。
実行: 毎週日曜 9:50（kazami_run.bat → 続けて weekend_post.py kazami が投稿）。--test で100銘柄・push無し。--nopush。
"""
import os
import re
import sys
import json
import time
import datetime
import subprocess

import numpy as np
import pandas as pd
import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import fx_corr_screen as fx  # noqa: E402  時価総額キャッシュの所在・閾値を共用

REPORT_HTML = "kazami.html"
POST_JSON = os.path.join(SCRIPT_DIR, "kazami_post.json")        # 週末投稿（weekend_post.py kazami）に渡す
HISTORY_JSON = os.path.join(SCRIPT_DIR, "kazami_history.json")  # 週ごとのTop10を蓄積（後日の答え合わせ用）
LOG_TXT = os.path.join(SCRIPT_DIR, "kazami_log.txt")
MAIN_W = 63     # 3か月（営業日）
STAR_W = 126    # 6か月
STAR_TOP = 30   # 6か月側でこの順位以内なら★
TOP_HTML = 30
TOP_POST = 10
CHUNK = 100
MIN_VALID = 0.9  # 窓の9割以上の日に株価があること
# JPX信用残PDFの行交錯で崩れた社名の手当て（「ホールディングス」に市場区分「スタンダード/プライム」や「貸借」の文字が挟まる）。根本は jpx_margin.py 側
NAME_FIX = {
    "3222": "ユナイテッド・スーパーマーケット・ホールディングス",
    "7532": "パン・パシフィック・インターナショナルホールディングス",
}


def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)


fx.log = log


# -----------------------------------------
# データ取得
# -----------------------------------------
def fetch_closes(codes):
    closes = {}
    t0 = time.time()
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        tickers = [c + ".T" for c in chunk]
        try:
            data = yf.download(tickers, period="1y", auto_adjust=True, progress=False, group_by="ticker", threads=True)
        except Exception as e:
            log(f"  chunk dl error: {e}")
            continue
        for c in chunk:
            try:
                s = data[c + ".T"]["Close"].dropna()
                if len(s) >= STAR_W * MIN_VALID:
                    closes[c] = s
            except Exception:
                pass
        log(f"  株価取得 {min(i + CHUNK, len(codes))}/{len(codes)}（{time.time() - t0:.0f}秒）")
    nk = yf.download("^N225", period="1y", auto_adjust=True, progress=False)["Close"]
    if isinstance(nk, pd.DataFrame):
        nk = nk.iloc[:, 0]
    return pd.DataFrame(closes), nk.dropna()


# -----------------------------------------
# 計算
# -----------------------------------------
def window_stats(px, nk, w):
    """直近 w 営業日の {code: {...}} と 日経の要約を返す"""
    r = px.pct_change(fill_method=None)
    n = nk.pct_change(fill_method=None).reindex(r.index)
    r = r.iloc[-w:]
    n = n.iloc[-w:]
    up = (n > 0).values
    dn = (n < 0).values
    n_up = int(up.sum())
    n_dn = int(dn.sum())
    valid = r.notna().sum() >= MIN_VALID * w
    same = ((r > 0).values & up[:, None]).sum(0)
    opp = ((r < 0).values & up[:, None]).sum(0)
    up_dn = ((r > 0).values & dn[:, None]).sum(0)   # 日経下落日に逆に上がった（嵐に強い）
    dn_dn = ((r < 0).values & dn[:, None]).sum(0)   # 日経下落日にいっしょに下がった（嵐に弱い）
    avg_up = r[up].mean(0) * 100
    avg_dn = r[dn].mean(0) * 100
    ret_w = (px.iloc[-1] / px.iloc[-w - 1] - 1) * 100 if len(px) > w else (px.iloc[-1] / px.iloc[0] - 1) * 100
    nk_ret = float((nk.iloc[-1] / nk.iloc[-w - 1] - 1) * 100) if len(nk) > w else float((nk.iloc[-1] / nk.iloc[0] - 1) * 100)
    out = {}
    for i, c in enumerate(r.columns):
        if not valid[c]:
            continue
        out[c] = {"same": int(same[i]), "opp": int(opp[i]), "n_up": n_up,
                  "hit": same[i] / n_up * 100 if n_up else None, "opp_rate": opp[i] / n_up * 100 if n_up else None,
                  "up_dn": int(up_dn[i]), "dn_dn": int(dn_dn[i]), "n_dn": n_dn,
                  "shield": up_dn[i] / n_dn * 100 if n_dn else None, "storm": dn_dn[i] / n_dn * 100 if n_dn else None,
                  "avg_up": float(avg_up[c]) if pd.notna(avg_up[c]) else None,
                  "avg_dn": float(avg_dn[c]) if pd.notna(avg_dn[c]) else None,
                  "ret": float(ret_w[c]) if pd.notna(ret_w[c]) else None}
        out[c]["excess"] = out[c]["ret"] - nk_ret if out[c]["ret"] is not None else None
    meta = {"n_up": n_up, "n_dn": n_dn, "days": int(len(r)), "nk_ret": nk_ret,
            "start": r.index[0].date().isoformat(), "end": r.index[-1].date().isoformat()}
    return out, meta


def rank(stats, side):
    """side: fair=順風（日経上昇日に上がった率↓）/ head=逆風（上昇日に下がった率↓）/
             shield=嵐に強い（日経下落日に上がった率↓）/ storm=嵐に弱い（下落日に下がった率↓）。同率は平均騰落で並べる"""
    items = [(c, v) for c, v in stats.items() if v["hit"] is not None and v["shield"] is not None]
    key = {"fair": lambda v: (-v["hit"], -(v["avg_up"] or 0)),
           "head": lambda v: (-v["opp_rate"], (v["avg_up"] or 0)),
           "shield": lambda v: (-v["shield"], -(v["avg_dn"] or 0)),
           "storm": lambda v: (-v["storm"], (v["avg_dn"] or 0))}[side]
    items.sort(key=lambda cv: key(cv[1]))
    return [c for c, _ in items]


def build(px, nk, names, caps):
    s3, m3 = window_stats(px, nk, MAIN_W)
    s6, m6 = window_stats(px, nk, STAR_W)
    SIDES = ("fair", "head", "shield", "storm")
    star = {side: set(rank(s6, side)[:STAR_TOP]) for side in SIDES}
    r1 = lambda x: round(x, 1) if x is not None else None
    r2 = lambda x: round(x, 2) if x is not None else None
    lists = {}
    for side in SIDES:
        rows = []
        for i, c in enumerate(rank(s3, side)[:TOP_HTML], 1):
            v = s3[c]
            v6 = s6.get(c, {})
            rows.append({"rank": i, "code": c, "name": names.get(c, c), "mcap": caps.get(c, 0),
                         "same": v["same"], "opp": v["opp"], "n_up": v["n_up"], "hit": r1(v["hit"]), "opp_rate": r1(v["opp_rate"]),
                         "up_dn": v["up_dn"], "dn_dn": v["dn_dn"], "n_dn": v["n_dn"], "shield": r1(v["shield"]), "storm": r1(v["storm"]),
                         "avg_up": r2(v["avg_up"]), "avg_dn": r2(v["avg_dn"]),
                         "ret": r1(v["ret"]), "excess": r1(v["excess"]),
                         "star": c in star[side],
                         "hit6": r1(v6.get("hit")), "opp6": r1(v6.get("opp_rate")), "shield6": r1(v6.get("shield")), "storm6": r1(v6.get("storm"))})
        lists[side] = rows
    med = lambda k: float(np.median([v[k] for v in s3.values() if v[k] is not None]))
    return {"generated": datetime.datetime.now().isoformat(timespec="seconds"), "asof": m3["end"],
            "main": m3, "star_w": m6, "universe": len(s3),
            "median": {"hit": med("hit"), "opp_rate": med("opp_rate"), "shield": med("shield"), "storm": med("storm"), "ret": med("ret")},
            "fair": lists["fair"], "head": lists["head"], "shield": lists["shield"], "storm": lists["storm"]}


# -----------------------------------------
# HTML
# -----------------------------------------
def nav_html():
    """map.html のナビを読んで同じ並び・色で生成（active=kazami）。kazami が未挿入なら fx_corr の後に入れる"""
    try:
        src = open(os.path.join(SCRIPT_DIR, "map.html"), encoding="utf-8", errors="ignore").read()
        links = re.findall(r'<a href="([a-z_0-9]+\.html)"[^>]*border-color:(#[0-9a-fA-F]{6})[^>]*>([^<]+)</a>', src)
    except Exception:
        links = []
    if not links:
        links = [("map.html", "#94a3b8", "デッキの見方"), ("kazami.html", "#db2777", "風見表")]
    if not any(h == "kazami.html" for h, _, _ in links):
        out = []
        for h, col, lab in links:
            out.append((h, col, lab))
            if h == "fx_corr.html":
                out.append(("kazami.html", "#db2777", "風見表"))
        links = out
    out = []
    for h, col, lab in links:
        active = ' class="active"' if h == "kazami.html" else ""
        out.append(f'    <a href="{h}"{active} style="border-color:{col}">{lab}</a>')
    return "\n".join(out)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>航海士の風見表 - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.1rem; margin: 24px 0 10px; color: #f1f5f9; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{ color: #60a5fa; text-decoration: none; background: #1e293b; padding: 5px 14px; border-radius: 6px; border: 1px solid #334155; }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .evidence {{ background: rgba(59,130,246,0.08); border: 1px solid rgba(59,130,246,0.3); border-radius: 8px; padding: 10px 14px; font-size: 0.8rem; line-height: 1.8; max-width: 1150px; margin-bottom: 16px; }}
  .evidence b {{ color: #93c5fd; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }}
  .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 10px 16px; min-width: 190px; }}
  .card .label {{ font-size: 0.75rem; color: #94a3b8; }}
  .card .v {{ font-size: 1.5rem; margin-top: 2px; font-variant-numeric: tabular-nums; }}
  .card .v small {{ font-size: 0.85rem; color: #94a3b8; margin-left: 6px; }}
  .tables {{ display: flex; gap: 24px; flex-wrap: wrap; }}
  .table-col {{ flex: 1; min-width: 520px; max-width: 700px; }}
  .table-col h2 {{ margin-top: 8px; }}
  .table-col h2.fair {{ color: #86efac; }} .table-col h2.head {{ color: #fca5a5; }}
  table {{ border-collapse: collapse; font-size: 0.82rem; width: 100%; }}
  thead th {{ background: #1e293b; color: #94a3b8; padding: 7px 8px; text-align: right; font-weight: 600; white-space: nowrap; position: sticky; top: 0; }}
  thead th:nth-child(-n+2) {{ text-align: left; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #1e293b; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
  td:nth-child(-n+2) {{ text-align: left; }}
  tr:hover td {{ background: #16213a; }}
  .rank {{ color: #64748b; font-size: 0.76rem; }}
  .big {{ font-weight: 600; }}
  .fair .big {{ color: #86efac; }} .head .big {{ color: #fca5a5; }}
  .pos {{ color: #4ade80; }} .neg {{ color: #f87171; }}
  .star {{ color: #fde68a; }}
  .dim {{ color: #64748b; font-size: 0.74rem; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.9; max-width: 1150px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 20px; }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;">かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
{nav}
  </nav>
  <h1>航海士の風見表 — 日経平均が上がった日に、いっしょに上がる船・逆らう船</h1>
  <p class="subtitle">最終更新: {updated} | 観測期間: {start}〜{end}（{days}営業日、日経上昇日 {n_up}日・下落日 {n_dn}日） | 対象: 時価総額1,000億円以上 {universe}銘柄（営業赤字も含む） | 毎週日曜 9:50 更新</p>

  <div class="evidence">
    <b>見方:</b> 直近3か月の日経平均<b>上昇日</b>だけを取り出し、その日に各銘柄が上がった回数を数える。
    <b>順風の船</b>＝いっしょに上がった率が高い船、<b>逆風の船</b>＝逆に下がった率が高い船。全銘柄の中央値は 同方向 {med_hit:.0f}% ／ 逆行 {med_opp:.0f}%。<br>
    下段は鏡像で、日経平均<b>下落日</b>に <b>嵐に強い船</b>＝逆に上がった率が高い船、<b>嵐に弱い船</b>＝いっしょに下がった率が高い船（中央値 {med_shield:.0f}% ／ {med_storm:.0f}%）。日経が大きく下げた日の夕方に航海士がXで届ける。<br>
    <b>★</b>＝6か月の窓でも同じ側のTop{star_top}に入る船（今年の局面だけでなく、半年通してその性格）。
    「上昇日平均」はその銘柄の日経上昇日の平均騰落率、「3か月」は期間リターンと日経（{nk_ret:+.1f}%）との差。<br>
    <b>窓の長さの根拠（2026-10-11 検証、92時点・944銘柄）:</b> 選んだTop10のその後21営業日の同方向率は全銘柄中央値より 1か月窓 +6.9pt → 3か月 +9.8 → 6か月 +10.5 → 1年 +11.8 で3か月から頭打ち。翌週の顔ぶれ維持率は 1か月 36%・3か月 64%・6か月 74%・1年 84%。
    3か月は「今年の局面を映す最短の窓」、6か月★で安定側を補う。下落日側も同じ検証で、嵐に強い船Top10のその後21営業日の下落日に上がった率 48.8% vs 中央値 40.1%（86%の時点で上回り）、嵐に弱い船 68.5% vs 57.8%（89%）。
  </div>

  <div class="cards">
    <div class="card"><div class="label">日経平均 3か月</div><div class="v {nk_cls}">{nk_ret:+.1f}%</div></div>
    <div class="card"><div class="label">日経上昇日 / 下落日</div><div class="v">{n_up}<small>/ {n_dn}日</small></div></div>
    <div class="card"><div class="label">全銘柄中央値 同方向率</div><div class="v">{med_hit:.0f}%</div></div>
    <div class="card"><div class="label">全銘柄中央値 3か月リターン</div><div class="v {med_ret_cls}">{med_ret:+.1f}%</div></div>
  </div>

  <div class="tables">
    <div class="table-col fair">
      <h2 class="fair">順風の船 — 日経上昇日に いっしょに上がった率 Top{top}</h2>
      <table><thead><tr><th>#</th><th>銘柄</th><th>同方向</th><th>率</th><th>上昇日平均</th><th>3か月</th><th>対日経</th><th>6か月</th><th>時価総額</th></tr></thead>
      <tbody>{fair_rows}</tbody></table>
    </div>
    <div class="table-col head">
      <h2 class="head">逆風の船 — 日経上昇日に 逆に下がった率 Top{top}</h2>
      <table><thead><tr><th>#</th><th>銘柄</th><th>逆行</th><th>率</th><th>上昇日平均</th><th>3か月</th><th>対日経</th><th>6か月</th><th>時価総額</th></tr></thead>
      <tbody>{head_rows}</tbody></table>
    </div>
  </div>

  <h2 style="margin-top:28px">日経平均が下がった日（{n_dn}日）— 嵐に強い船・弱い船</h2>
  <div class="tables">
    <div class="table-col fair">
      <h2 class="fair">嵐に強い船 — 日経下落日に 逆に上がった率 Top{top}</h2>
      <table><thead><tr><th>#</th><th>銘柄</th><th>逆行</th><th>率</th><th>下落日平均</th><th>3か月</th><th>対日経</th><th>6か月</th><th>時価総額</th></tr></thead>
      <tbody>{shield_rows}</tbody></table>
    </div>
    <div class="table-col head">
      <h2 class="head">嵐に弱い船 — 日経下落日に いっしょに下がった率 Top{top}</h2>
      <table><thead><tr><th>#</th><th>銘柄</th><th>同方向</th><th>率</th><th>下落日平均</th><th>3か月</th><th>対日経</th><th>6か月</th><th>時価総額</th></tr></thead>
      <tbody>{storm_rows}</tbody></table>
    </div>
  </div>

  <p class="note">
    ・これは過去3か月の<b>観測</b>であり、来週も同じ関係が続く保証はない。売買の判断材料ではなく、「自分の船がどの風に乗っているか」を知るための風見。<br>
    ・率は同率が多く順位の差は小さい。分母（上昇日数）と上昇日平均・3か月リターンを一緒に読む。<br>
    ・逆風の船のその後21営業日の対日経超過リターンは、検証では中央値より約1.5pt低い傾向があった（2〜6か月窓）。独立した期間が20程度しかないため<b>蓄積中の観察</b>として置く。順風側には同様の差はなかった。<br>
    ・対象は時価総額1,000億円以上の全銘柄。<b>fx_corr（円安/円高相関）や信用期日スクリーナーと違い、直近四半期が営業赤字の銘柄も含む</b>（風見は観測が目的で、赤字転落した船こそ逆風の典型。業績は銘柄チェッカーで別に確認する）。株価は配当調整済み終値（yfinance）。毎週日曜のTop10は kazami_history.json に蓄積し、後日答え合わせに使う。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def fmt_oku(v):
    if not v:
        return "-"
    return f"{v / 10000:.1f}兆円" if v >= 10000 else f"{v:,.0f}億円"


def cls(v):
    return "pos" if (v or 0) > 0 else ("neg" if (v or 0) < 0 else "")


def rows_html(rows, side):
    spec = {"fair": ("same", "n_up", "hit", "hit6", "avg_up"), "head": ("opp", "n_up", "opp_rate", "opp6", "avg_up"),
            "shield": ("up_dn", "n_dn", "shield", "shield6", "avg_dn"), "storm": ("dn_dn", "n_dn", "storm", "storm6", "avg_dn")}[side]
    out = []
    for r in rows:
        cnt, den, rate, six, avg = r[spec[0]], r[spec[1]], r[spec[2]], r[spec[3]], r[spec[4]]
        star = ' <span class="star" title="6か月でも同じ側のTop30">★</span>' if r["star"] else ""
        out.append(
            f'<tr><td class="rank">{r["rank"]}</td><td>{r["name"]}{star} <span class="dim">{r["code"]}</span></td>'
            f'<td>{cnt}/{den}</td><td class="big">{rate:.0f}%</td>'
            f'<td class="{cls(avg)}">{avg:+.2f}%</td>'
            f'<td class="{cls(r["ret"])}">{r["ret"]:+.1f}%</td><td class="{cls(r["excess"])}">{r["excess"]:+.1f}</td>'
            f'<td class="dim">{six:.0f}%</td><td class="dim">{fmt_oku(r["mcap"])}</td></tr>' if six is not None else
            f'<td class="dim">-</td><td class="dim">{fmt_oku(r["mcap"])}</td></tr>')
    return "".join(out)


def generate_html(d):
    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(), updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        nav=nav_html(), start=d["main"]["start"], end=d["main"]["end"], days=d["main"]["days"], n_up=d["main"]["n_up"], n_dn=d["main"]["n_dn"],
        universe=d["universe"], med_hit=d["median"]["hit"], med_opp=d["median"]["opp_rate"], med_ret=d["median"]["ret"], med_ret_cls=cls(d["median"]["ret"]),
        star_top=STAR_TOP, nk_ret=d["main"]["nk_ret"], nk_cls=cls(d["main"]["nk_ret"]), top=TOP_HTML,
        med_shield=d["median"]["shield"], med_storm=d["median"]["storm"],
        fair_rows=rows_html(d["fair"], "fair"), head_rows=rows_html(d["head"], "head"),
        shield_rows=rows_html(d["shield"], "shield"), storm_rows=rows_html(d["storm"], "storm"))
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")


def save_history(d):
    hist = {}
    if os.path.exists(HISTORY_JSON):
        try:
            hist = json.load(open(HISTORY_JSON, encoding="utf-8"))
        except Exception:
            hist = {}
    hist[d["asof"]] = {"nk_ret": round(d["main"]["nk_ret"], 2), "n_up": d["main"]["n_up"],
                       "fair": [(r["code"], r["hit"], r["star"]) for r in d["fair"][:TOP_POST]],
                       "head": [(r["code"], r["opp_rate"], r["star"]) for r in d["head"][:TOP_POST]],
                       "shield": [(r["code"], r["shield"], r["star"]) for r in d["shield"][:TOP_POST]],
                       "storm": [(r["code"], r["storm"], r["star"]) for r in d["storm"][:TOP_POST]]}
    json.dump(hist, open(HISTORY_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"履歴保存: {len(hist)}週分")


def push_to_github():
    try:
        from git_lock_helper import wait_for_git_lock
        wait_for_git_lock(SCRIPT_DIR)
    except Exception:
        pass
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML, "kazami_screen.py", "kazami_history.json"], check=True)
    result = subprocess.run(["git", "-C", SCRIPT_DIR, "commit", "-m", "update kazami report " + today], capture_output=True)
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
            log("  Done: https://ichikon77.github.io/minervini/kazami.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


def load_universe():
    """時価総額1,000億円以上の全銘柄（営業赤字フィルタ無し）。名前・時価総額は fx_corr と同じキャッシュから"""
    h = json.load(open(fx.MARGIN_HISTORY, encoding="utf-8"))
    names = dict(h["names"])
    # JPX PDF 由来の社名は長い行で文字が交錯することがある（例: 3222・7532）→ margin_fundamentals.json の社名があれば優先
    if os.path.exists(fx.FUNDAMENTALS):
        try:
            fund = json.load(open(fx.FUNDAMENTALS, encoding="utf-8")).get("stocks", {})
            for c, v in fund.items():
                if v.get("name"):
                    names[c] = v["name"]
        except Exception:
            pass
    names.update(NAME_FIX)
    caps = json.load(open(fx.MCAP_CACHE, encoding="utf-8")) if os.path.exists(fx.MCAP_CACHE) else {}
    codes = sorted(c for c in names if (caps.get(c) or 0) >= fx.MIN_MCAP_OKU)
    return codes, names, caps


def main():
    log("航海士の風見表 開始")
    codes, names, caps = load_universe()
    if "--test" in sys.argv:
        codes = codes[:100]
    log(f"対象ユニバース: {len(codes)}銘柄")
    px, nk = fetch_closes(codes)
    if px.shape[1] < 50 or len(nk) < STAR_W * MIN_VALID:
        log("エラー: 株価が十分に取れませんでした → 更新しません")
        sys.exit(1)
    d = build(px, nk, names, caps)
    log(f"  日経 3か月 {d['main']['nk_ret']:+.1f}%（上昇{d['main']['n_up']}日/下落{d['main']['n_dn']}日） 中央値 同方向{d['median']['hit']:.0f}%")
    log("  順風Top5: " + "、".join(f"{r['name']} {r['same']}/{r['n_up']}{'★' if r['star'] else ''}" for r in d["fair"][:5]))
    log("  逆風Top5: " + "、".join(f"{r['name']} {r['opp']}/{r['n_up']}{'★' if r['star'] else ''}" for r in d["head"][:5]))
    log("  嵐に強いTop5: " + "、".join(f"{r['name']} {r['up_dn']}/{r['n_dn']}{'★' if r['star'] else ''}" for r in d["shield"][:5]))
    log("  嵐に弱いTop5: " + "、".join(f"{r['name']} {r['dn_dn']}/{r['n_dn']}{'★' if r['star'] else ''}" for r in d["storm"][:5]))
    json.dump(d, open(POST_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    generate_html(d)
    if "--test" not in sys.argv:
        save_history(d)
    if "--nopush" in sys.argv or "--test" in sys.argv:
        log("push スキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
