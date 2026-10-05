# -*- coding: utf-8 -*-
"""
情報漏洩銘柄検証 → roei.html → GitHub Pages公開

国内上場企業の「不正アクセス・情報漏洩・ランサムウェア」公表事案（roei_incidents.json、手動管理）について、
公表後 1・3・5・10・15・30営業日の株価騰落と、TOPIX（1306 ETF）に対する超過リターンを毎日埋めていく。
種別ごとの集計（中央値／平均／マイナス率）で「どの種類の事故が、何日後まで売られるか」を答え合わせする。

■ 起点と日数の定義
  - 「1営業日後」= 公表後、最初に市場が反応できた取引日の終値。公表が引け前（time=before）なら公表日当日、
    引け後（after）なら翌営業日。不明（?）は公表日当日として扱い、表に「?」を付ける
  - 基準値 = その「1営業日後」の前営業日の終値（＝公表前の最後の終値）
  - 騰落% = 終値 ÷ 基準値 − 1。超過 = 銘柄の騰落 − 同じ日付のTOPIX(1306)の騰落
■ Xの投稿数
  snsデッキと同じ Yahooリアルタイム検索の推移APIで「不正アクセス」「情報漏洩」等の日次投稿数を取り、
  直近7日の合計と、その前の3週間の平均（7日換算）を比べる

実行: 毎日16:30（roei_run.bat）。--nopush でpush省略。--card で速報カード画像（drafts/）を生成。
事案の追加: roei_incidents.json に1行足して実行するだけ。
"""

import os
import re
import sys
import json
import time
import datetime
import subprocess
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INCIDENTS_JSON = os.path.join(SCRIPT_DIR, "roei_incidents.json")
REPORT_HTML = "roei.html"
DRAFTS_DIR = os.path.join(SCRIPT_DIR, "drafts")
BENCH = "1306.T"          # TOPIX連動ETF（^TOPXはyfinanceで取れないため）
HORIZONS = [1, 3, 5, 10, 15, 30]
WORDS = [
    ("不正アクセス", "不正アクセス"),
    ("情報漏洩", "情報漏洩 OR 情報漏えい"),
    ("個人情報流出", "個人情報流出 OR 個人情報漏えい"),
    ("ランサムウェア", "ランサムウェア"),
    ("サイバー攻撃", "サイバー攻撃"),
    ("お詫び", "お詫びとお知らせ"),
]
YAHOO_API = ("https://search.yahoo.co.jp/realtime/api/v1/transition"
             "?p={word}&interval=86400&span=2592000&rkf=3")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# 事案・株価
# -----------------------------------------
def load_incidents():
    d = json.load(open(INCIDENTS_JSON, encoding="utf-8"))
    inc = d["incidents"]
    inc.sort(key=lambda r: (r["date"], r["code"]))
    return inc


def fetch_closes(codes, start):
    tickers = sorted({c + ".T" for c in codes} | {BENCH})
    data = yf.download(tickers, start=start, auto_adjust=True, progress=False, threads=True)["Close"]
    if isinstance(data, pd.Series):
        data = data.to_frame()
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    return data


def first_reaction_day(dates, announce, time_flag):
    """公表日と引け前/後から『1営業日後』の日付を返す（dates=その銘柄の取引日index）"""
    a = pd.Timestamp(announce)
    if time_flag == "after":
        cand = dates[dates > a]
    else:
        cand = dates[dates >= a]
    return cand[0] if len(cand) else None


def compute(inc, closes):
    """各事案に base / day0 / 各horizonの(株騰落, TOPIX騰落, 超過) を付ける"""
    bench = closes[BENCH].dropna()
    for r in inc:
        t = r["code"] + ".T"
        s = closes[t].dropna() if t in closes else pd.Series(dtype=float)
        r["ok"] = len(s) > 5
        r["ret"] = {}
        if not r["ok"]:
            r["err"] = "株価取得不可"
            continue
        d0 = first_reaction_day(s.index, r["date"], r.get("time", "?"))
        if d0 is None:
            r["err"] = "反応日がまだ来ていない"
            continue
        pos = s.index.get_loc(d0)
        if pos == 0:
            r["err"] = "基準日の株価なし"
            continue
        base = float(s.iloc[pos - 1])
        base_b = float(bench[bench.index < d0].iloc[-1]) if len(bench[bench.index < d0]) else None
        r["day0"] = d0.date().isoformat()
        r["base"] = base
        for n in HORIZONS:
            i = pos + n - 1
            if i < len(s):
                dt = s.index[i]
                rs = (float(s.iloc[i]) / base - 1) * 100
                rb = (float(bench.asof(dt)) / base_b - 1) * 100 if base_b else None
                r["ret"][n] = {"date": dt.date().isoformat(), "stock": rs, "bench": rb,
                               "excess": (rs - rb) if rb is not None else None}
            else:
                r["ret"][n] = None
        # 直近の終値（今日時点の騰落）と、直近1日の騰落（前日終値比）
        r["last"] = {"date": s.index[-1].date().isoformat(), "stock": (float(s.iloc[-1]) / base - 1) * 100,
                     "days": len(s) - pos,
                     "chg1d": (float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100 if len(s) >= 2 else None}
    return inc


def summarize(inc):
    """種別別に horizon ごとの超過リターン（中央値／平均／マイナス率）"""
    groups = {}
    for r in inc:
        if not r.get("ok") or not r.get("ret"):
            continue
        groups.setdefault(r["type"], []).append(r)
    groups["全事案"] = [r for r in inc if r.get("ok") and r.get("ret")]
    out = {}
    for g, rows in groups.items():
        out[g] = {}
        for n in HORIZONS:
            vals = [r["ret"][n]["excess"] for r in rows if r["ret"].get(n) and r["ret"][n]["excess"] is not None]
            raw = [r["ret"][n]["stock"] for r in rows if r["ret"].get(n)]
            if vals:
                out[g][n] = {"n": len(vals), "med": float(np.median(vals)), "mean": float(np.mean(vals)),
                             "neg": sum(1 for v in vals if v < 0) / len(vals) * 100,
                             "raw_med": float(np.median(raw))}
            else:
                out[g][n] = None
    return out


# -----------------------------------------
# Xの投稿数（Yahooリアルタイム検索）
# -----------------------------------------
def fetch_word_counts(word):
    url = YAHOO_API.format(word=urllib.parse.quote(word))
    req = urllib.request.Request(url, headers={**UA, "Referer": f"https://search.yahoo.co.jp/realtime/search?p={urllib.parse.quote(word)}"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    data = json.loads(raw)
    out = {}
    today = datetime.date.today().isoformat()
    for e in data.get("tweetTransition", {}).get("entry", []):
        d = datetime.datetime.fromtimestamp(e["from"]).date().isoformat()
        if d < today:
            out[d] = e["count"]
    return out


def word_stats():
    rows = []
    for label, q in WORDS:
        try:
            c = fetch_word_counts(q)
        except Exception as e:
            log(f"  Yahooリアルタイム {label}: 取得失敗 {e}")
            continue
        days = sorted(c)
        if len(days) < 10:
            continue
        last7 = sum(c[d] for d in days[-7:])
        prev = [c[d] for d in days[:-7]]
        prev7 = (sum(prev) / len(prev) * 7) if prev else None
        ratio = (last7 / prev7) if prev7 else None
        peak_day = max(days[-7:], key=lambda d: c[d])
        rows.append({"label": label, "q": q, "last7": last7, "prev7": prev7, "ratio": ratio,
                     "peak_day": peak_day, "peak": c[peak_day], "series": [c[d] for d in days[-14:]], "dates": days[-14:]})
        time.sleep(1)
    rows.sort(key=lambda r: -(r["ratio"] or 0))
    return rows


# -----------------------------------------
# HTML
# -----------------------------------------
def nav_html():
    """map.html のナビを読んでそのまま使う（並び順・色の正本）。roei を active に"""
    try:
        s = open(os.path.join(SCRIPT_DIR, "map.html"), encoding="utf-8").read()
        m = re.search(r'<nav class="nav">.*?</nav>', s, re.S)
        nav = m.group(0)
        nav = re.sub(r' class="active"', "", nav)
        nav = nav.replace('<a href="roei.html"', '<a href="roei.html" class="active"')
        if 'href="roei.html"' not in nav:
            nav = nav.replace('<a href="kasetsu.html"', '<a href="roei.html" class="active" style="border-color:#db2777">情報漏洩銘柄検証</a>\n    <a href="kasetsu.html"')
        return nav
    except Exception:
        return '<nav class="nav"><a href="map.html" style="border-color:#94a3b8">デッキの見方</a></nav>'


CHIWAWA = ('<div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;">'
           '<svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>'
           'かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>')

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>情報漏洩銘柄検証 - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 22px 0 10px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{ color: #60a5fa; text-decoration: none; background: #1e293b; padding: 5px 14px; border-radius: 6px; border: 1px solid #334155; }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .evidence {{ background: rgba(59,130,246,0.08); border: 1px solid rgba(59,130,246,0.3); border-radius: 8px; padding: 10px 14px; font-size: 0.8rem; line-height: 1.8; max-width: 1200px; margin-bottom: 16px; }}
  .evidence b {{ color: #93c5fd; }}
  .num {{ color: #fbbf24; font-weight: 700; }}
  .table-wrap {{ overflow-x: auto; max-width: 1400px; }}
  table {{ border-collapse: collapse; font-size: 0.8rem; }}
  thead th {{ background: #1e293b; color: #94a3b8; padding: 6px 8px; text-align: right; font-weight: 600; white-space: nowrap; vertical-align: bottom; }}
  thead th.l {{ text-align: left; }}
  thead th.grp {{ text-align: center; color: #fbbf24; border-bottom: 1px solid #334155; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #1e293b; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; vertical-align: top; }}
  td.l {{ text-align: left; }}
  td.wrap {{ white-space: normal; max-width: 260px; color: #cbd5e1; }}
  tr:hover td {{ background: #16213a; }}
  .pos {{ color: #4ade80; }} .neg {{ color: #f87171; }}
  .ex {{ font-size: 0.72rem; color: #94a3b8; }}
  .tag {{ display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 0.72rem; border: 1px solid #475569; color: #cbd5e1; }}
  .tag.t1 {{ border-color: #f87171; color: #fca5a5; }} .tag.t2 {{ border-color: #fbbf24; color: #fde68a; }} .tag.t3 {{ border-color: #a78bfa; color: #c4b5fd; }} .tag.t4 {{ border-color: #60a5fa; color: #93c5fd; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.9; max-width: 1200px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .bar {{ display: inline-block; height: 8px; background: #3b82f6; vertical-align: middle; }}
  .total td {{ border-top: 1px solid #475569; font-weight: 700; }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  {chiwawa}
  {nav}
  <h1>情報漏洩銘柄検証 — 不正アクセス・情報漏洩を公表した企業の株価は、その後どう動いたか</h1>
  <p class="subtitle">最終更新: {updated}（毎日16:30） | 対象: 国内上場企業、2026年9月以降の公表事案 {n_inc}件 | 株価: yfinance（配当調整済み終値） | 対照: TOPIX連動ETF 1306</p>
  <div class="evidence">
    <b>見方:</b>
    <b class="num">①</b> 事案ごとに、公表後 <b>1・3・5・10・15・30営業日</b> の終値を「公表前の最後の終値」と比べる（上段＝株価の騰落、下段の小文字＝同じ日のTOPIX(1306)を引いた<b>超過</b>）。
    <b class="num">②</b> 「1営業日後」＝公表後に市場が最初に反応できた取引日。公表が引け前なら当日、引け後なら翌営業日。公表時刻が不明の事案は当日として扱い <span class="num">?</span> を付ける。
    <b class="num">③</b> 上の集計表は<b>超過リターン</b>の中央値／平均／マイナス率（TOPIXより弱かった事案の割合）。「全事案」行が土台、種別行は内訳。N が小さいうちは目安。
    <b class="num">④</b> 下のXの投稿数は Yahooリアルタイム検索の日次件数。直近7日の合計を、その前3週間の平均（7日換算）と比べた倍率。ニュースの熱量の目安で、株価との因果は主張しない。
  </div>

  <h2>③ 種別ごとの超過リターン（銘柄 − TOPIX、%）</h2>
  <div class="table-wrap">
  <table>
    <thead>
      <tr><th class="l">種別</th><th>N</th>{sum_head}</tr>
      <tr><th class="l"></th><th></th>{sum_sub}</tr>
    </thead>
    <tbody>
{sum_rows}
    </tbody>
  </table>
  </div>

  <h2>①② 事案別マトリクス（上段: 株価騰落%、下段: TOPIX比の超過%）</h2>
  <div class="table-wrap">
  <table>
    <thead>
      <tr><th class="l">公表日</th><th class="l">企業（コード）</th><th class="l">種別</th><th class="l">対象・規模</th><th>1営業日後</th><th>3日後</th><th>5日後</th><th>10日後</th><th>15日後</th><th>30日後</th><th>直近</th></tr>
    </thead>
    <tbody>
{inc_rows}
    </tbody>
  </table>
  </div>

  <h2>④ Xの投稿数（Yahooリアルタイム検索、直近7日 vs その前3週間の平均）</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th class="l">語</th><th>直近7日 合計</th><th>前3週の平均（7日換算）</th><th>倍率</th><th>直近7日のピーク</th><th class="l">直近14日の推移</th></tr></thead>
    <tbody>
{word_rows}
    </tbody>
  </table>
  </div>

  <p class="note">
    ・事案は手動で追加（roei_incidents.json）。TDnetの適時開示に出ないプレスリリースのみの公表も多いため、見落としがあれば随時追加する。<br>
    ・「規模」は公表時点の数字。続報で増えることが多い（例: タイムズカーは第1報「可能性」→第2報660万件確定）。表の規模欄は最新の公表値に手で更新する。<br>
    ・騰落は配当調整済み終値。基準は公表前の最後の終値なので、公表が引け後なら「1営業日後」の値に翌日のギャップが含まれる。<br>
    ・同じ日に複数の事案が公表されると、物流3社（9/29〜9/30）のように業界全体の連想売りが混ざる。種別だけでなく時期の重なりも見る。<br>
    ・このページは観測と答え合わせの記録で、売買の推奨ではない。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""

TYPE_CLS = {"ランサム": "t1", "不正アクセス": "t2", "委託先": "t3", "内部不正": "t4", "設定不備": "t4"}


def fmt(v, digits=1, plain=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    if plain:
        return f"{v:+.{digits}f}"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+.{digits}f}</span>'


def generate_html(inc, summ, words):
    sum_head = "".join(f"<th>{n}営業日後</th>" for n in HORIZONS)
    sum_sub = "".join('<th class="ex">中央値 / 平均 / マイナス率</th>' for _ in HORIZONS)
    sum_rows = []
    order = [g for g in summ if g != "全事案"] + (["全事案"] if "全事案" in summ else [])
    for g in order:
        cells = []
        nmax = 0
        for n in HORIZONS:
            c = summ[g][n]
            if c:
                nmax = max(nmax, c["n"])
                cells.append(f'<td>{fmt(c["med"])} / {fmt(c["mean"])} / <span class="ex">{c["neg"]:.0f}%</span>'
                             f'<br><span class="ex">N={c["n"]}</span></td>')
            else:
                cells.append('<td class="ex">-</td>')
        cls = ' class="total"' if g == "全事案" else ""
        sum_rows.append(f'      <tr{cls}><td class="l">{g}</td><td>{nmax}</td>{"".join(cells)}</tr>')
    if not sum_rows:
        sum_rows.append('      <tr><td colspan="8" style="text-align:center;color:#64748b">事案なし</td></tr>')

    inc_rows = []
    for r in sorted(inc, key=lambda x: (x["date"], x["code"]), reverse=True):
        tag = f'<span class="tag {TYPE_CLS.get(r["type"], "")}">{r["type"]}</span>'
        q = ' <span class="num" title="公表時刻が不明のため公表日当日を1営業日後として扱う">?</span>' if r.get("time", "?") == "?" else ""
        name = f'{r["name"]}（{r["code"]}）'
        scale = f'{r.get("service", "")}<br><span class="ex">{r.get("scale", "")}</span>'
        if r.get("note"):
            scale += f'<br><span class="ex">{r["note"]}</span>'
        cells = []
        if not r.get("ok") or not r.get("ret"):
            cells = [f'<td colspan="7" class="ex">{r.get("err", "-")}</td>']
        else:
            for n in HORIZONS:
                c = r["ret"].get(n)
                if c:
                    cells.append(f'<td>{fmt(c["stock"])}<br><span class="ex">{fmt(c["excess"], plain=True)}</span></td>')
                else:
                    cells.append('<td class="ex">（未到達）</td>')
            L = r.get("last")
            cells.append(f'<td>{fmt(L["stock"])}<br><span class="ex">{L["days"]}日目 {L["date"][5:]}</span></td>' if L else "<td>-</td>")
        inc_rows.append(f'      <tr><td class="l">{r["date"][5:].replace("-", "/")}{q}</td><td class="l">{name}</td>'
                        f'<td class="l">{tag}</td><td class="l wrap">{scale}</td>{"".join(cells)}</tr>')
    if not inc_rows:
        inc_rows.append('      <tr><td colspan="11" style="text-align:center;color:#64748b">事案なし</td></tr>')

    word_rows = []
    for w in words:
        mx = max(w["series"]) or 1
        bars = "".join(f'<span class="bar" style="width:{max(2, int(v / mx * 40))}px; margin-right:2px; opacity:{0.5 if i < 7 else 1}" title="{d}: {v:,}"></span>'
                       for i, (d, v) in enumerate(zip(w["dates"], w["series"])))
        ratio = f'{w["ratio"]:.1f}倍' if w["ratio"] else "-"
        hot = ' class="num"' if (w["ratio"] or 0) >= 2 else ""
        word_rows.append(f'      <tr><td class="l">{w["label"]}<br><span class="ex">{w["q"]}</span></td><td>{w["last7"]:,}</td>'
                         f'<td>{w["prev7"]:,.0f}</td><td{hot}>{ratio}</td><td>{w["peak_day"][5:]} {w["peak"]:,}</td><td class="l">{bars}</td></tr>')
    if not word_rows:
        word_rows.append('      <tr><td colspan="6" style="text-align:center;color:#64748b">取得できず</td></tr>')

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_inc=len(inc), chiwawa=CHIWAWA, nav=nav_html(),
        sum_head=sum_head, sum_sub=sum_sub, sum_rows="\n".join(sum_rows),
        inc_rows="\n".join(inc_rows), word_rows="\n".join(word_rows),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")


# -----------------------------------------
# 速報カード（兜町の電光掲示板風）
# -----------------------------------------
def make_card(inc, summ, words, only_latest=True):
    """速報カード: 東証アローズの電光ボード風（濃紺の地・パネル枡・白箱の現在値・赤緑の騰落）"""
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(DRAFTS_DIR, exist_ok=True)
    rows = [r for r in inc if r.get("ok") and r.get("ret")]
    rows.sort(key=lambda r: (r["date"], r["code"]), reverse=True)
    W, H = 1200, 675
    BG = (8, 12, 22); PANEL = (14, 22, 40); BAR = (22, 34, 60); LINE = (40, 58, 92)
    WH = (240, 240, 235); GR = (150, 165, 190); AMB = (255, 196, 60); GN = (80, 220, 110); RD = (255, 85, 80); BOXBG = (235, 238, 242); BOXTX = (10, 14, 30)
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    fp = next((p for p in [r"C:\Windows\Fonts\meiryob.ttc", r"C:\Windows\Fonts\YuGothB.ttc", r"C:\Windows\Fonts\msgothic.ttc",
                          "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"] if os.path.exists(p)), None)
    F = lambda sz: ImageFont.truetype(fp, sz) if fp else ImageFont.load_default()
    f28, f22, f19, f16, f14, f12 = F(28), F(22), F(19), F(16), F(14), F(12)
    today = datetime.date.today()
    mday = max([r["last"]["date"] for r in rows if r.get("last")], default=today.isoformat())

    def panel(x0, y0, x1, y1, title, en):
        d.rectangle([x0, y0, x1, y1], fill=PANEL, outline=LINE)
        d.rectangle([x0, y0, x1, y0 + 26], fill=BAR)
        d.text((x0 + 10, y0 + 4), title, font=f16, fill=WH)
        d.text((x0 + 12 + d.textlength(title, font=f16), y0 + 8), en, font=f12, fill=GR)

    def box(x, y, w, text, col=BOXTX, fnt=None):
        fnt = fnt or f22
        d.rectangle([x, y, x + w, y + 32], fill=BOXBG)
        tw = d.textlength(text, font=fnt)
        d.text((x + w - tw - 8, y + 3), text, font=fnt, fill=col)

    def c(v):
        return GN if v is None or v >= 0 else RD

    # ---- ヘッダー
    d.text((20, 10), "情報漏洩銘柄検証", font=f28, fill=AMB)
    d.text((262, 20), "Data Breach Stock Watch", font=f14, fill=GR)
    d.text((W - 330, 14), f"{mday} 大引け時点  公表前終値比 %", font=f14, fill=GR)
    d.text((W - 330, 32), "下段の小数字＝TOPIX(1306)比の超過", font=f12, fill=GR)

    # ---- 上段3パネル
    top_y, top_h = 56, 170
    pw = (W - 20 * 2 - 12 * 2) // 3
    px = [20, 20 + pw + 12, 20 + 2 * (pw + 12)]
    # P1 本日が初日
    panel(px[0], top_y, px[0] + pw, top_y + top_h, "本日 公表後初日", "First Session")
    first = [r for r in rows if r.get("day0") == mday and r["ret"].get(1)]
    yy = top_y + 34
    if first:
        for r in first[:3]:
            cc = r["ret"][1]
            d.text((px[0] + 10, yy), f'{r["name"]} {r["code"]}', font=f16, fill=WH)
            box(px[0] + pw - 120, yy - 4, 110, f'{cc["stock"]:+.1f}', col=(170, 20, 20) if cc["stock"] < 0 else (0, 110, 50))
            d.text((px[0] + 10, yy + 22), f'TOPIX比 {cc["excess"]:+.1f}  {r.get("type", "")}', font=f12, fill=GR)
            yy += 46
    else:
        d.text((px[0] + 10, yy + 10), "本日の該当なし", font=f16, fill=GR)
    # P2 本日の最大下落
    panel(px[1], top_y, px[1] + pw, top_y + top_h, "本日 最大下落", "Biggest Drop (vs prev. close)")
    tracked = [r for r in rows if r.get("last") and r["last"].get("chg1d") is not None and r["last"]["days"] <= 30 and r["last"]["date"] == mday]
    if tracked:
        w_ = min(tracked, key=lambda r: r["last"]["chg1d"])
        d.text((px[1] + 10, top_y + 34), f'{w_["name"]} {w_["code"]}', font=f19, fill=WH)
        d.text((px[1] + 10, top_y + 58), f'{w_["date"][5:].replace("-", "/")}公表 {w_.get("type", "")}', font=f12, fill=GR)
        box(px[1] + 10, top_y + 78, 150, f'{w_["last"]["chg1d"]:+.1f}', col=(170, 20, 20) if w_["last"]["chg1d"] < 0 else (0, 110, 50), fnt=f28)
        d.text((px[1] + 170, top_y + 80), "前日比", font=f12, fill=GR)
        d.text((px[1] + 170, top_y + 96), f'公表前比 {w_["last"]["stock"]:+.1f}', font=f14, fill=c(w_["last"]["stock"]))
        d.text((px[1] + 10, top_y + 120), f'追跡中 {len(tracked)}銘柄（公表30営業日以内）', font=f12, fill=GR)
    # P3 全事案 中央値
    panel(px[2], top_y, px[2] + pw, top_y + top_h, "全事案 中央値", "Median excess vs TOPIX")
    g = summ.get("全事案", {})
    yy = top_y + 36
    for n in HORIZONS[:5]:
        cc = g.get(n)
        d.text((px[2] + 10, yy), f"{n}営業日後", font=f14, fill=GR)
        if cc:
            d.text((px[2] + 110, yy), f'{cc["med"]:+.1f}', font=f16, fill=c(cc["med"]))
            d.text((px[2] + 175, yy + 2), f'N={cc["n"]}  下落率{cc["neg"]:.0f}%', font=f12, fill=GR)
        else:
            d.text((px[2] + 110, yy), "—", font=f16, fill=GR)
        yy += 24

    # ---- 下段: 事案ボード
    by0 = top_y + top_h + 12
    panel(20, by0, W - 20, H - 60, "事案一覧（公表日順）", "Incidents since Sep 2026  —  1 / 5 / 10 days, latest")
    cols = [("公表", 32), ("銘柄", 100), ("コード", 330), ("種別", 400), ("1日", 520), ("5日", 620), ("10日", 720), ("直近", 820), ("日目", 905), ("対象", 960)]
    yy = by0 + 32
    for lab, x in cols:
        d.text((x, yy), lab, font=f12, fill=GR)
    yy += 18
    maxrows = max(1, (H - 60 - yy - 6) // 30)
    for i, r in enumerate(rows[:maxrows]):
        hi = (r.get("day0") == mday)
        col_name = AMB if hi else WH
        d.text((32, yy), r["date"][5:].replace("-", "/"), font=f14, fill=col_name)
        d.text((100, yy), r["name"][:14], font=f14, fill=col_name)
        d.text((330, yy), r["code"], font=f14, fill=GR)
        d.text((400, yy), r.get("type", "")[:6], font=f12, fill=GR)
        for n, x in ((1, 520), (5, 620), (10, 720)):
            cc = r["ret"].get(n)
            if cc:
                d.text((x, yy), f'{cc["stock"]:+.1f}', font=f16, fill=c(cc["stock"]))
                if cc.get("excess") is not None:
                    d.text((x + 52, yy + 3), f'{cc["excess"]:+.1f}', font=f12, fill=GR)
            else:
                d.text((x, yy), "—", font=f16, fill=(70, 85, 115))
        L = r.get("last")
        if L:
            d.text((820, yy), f'{L["stock"]:+.1f}', font=f16, fill=c(L["stock"]))
            d.text((905, yy + 2), f'{L["days"]}', font=f14, fill=GR)
        d.text((960, yy + 2), (r.get("scale") or "")[:18], font=f12, fill=GR)
        yy += 30
        d.line([(28, yy - 5), (W - 28, yy - 5)], fill=(24, 36, 60))

    # ---- フッター: Xの投稿数（為替パネル風）
    fy = H - 50
    d.rectangle([20, fy, W - 20, H - 12], fill=PANEL, outline=LINE)
    d.text((32, fy + 6), "X 投稿数（直近7日 / 前3週平均）", font=f12, fill=GR)
    x = 230
    for w in (words or [])[:4]:
        if not w.get("ratio"):
            continue
        d.text((x, fy + 4), w["label"], font=f14, fill=WH)
        d.text((x, fy + 22), f'{w["last7"]:,}件', font=f12, fill=GR)
        d.text((x + 120, fy + 8), f'×{w["ratio"]:.1f}', font=f19, fill=RD if w["ratio"] >= 2 else GN)
        x += 215
    d.text((W - 300, fy + 22), "ichikon77.github.io/minervini/roei.html", font=f12, fill=GR)
    out = os.path.join(DRAFTS_DIR, f"roei_card_{today:%Y%m%d}.png")
    im.save(out)
    log(f"速報カード: {out}")
    return out


# -----------------------------------------
# push
# -----------------------------------------
def push_to_github():
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML, "roei_screen.py", "roei_run.bat", "roei_incidents.json"], check=True)
    result = subprocess.run(["git", "-C", SCRIPT_DIR, "commit", "-m", "update roei report " + today], capture_output=True)
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
            log("  Done: https://ichikon77.github.io/minervini/roei.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


def main():
    log("情報漏洩銘柄検証 開始")
    inc = load_incidents()
    start = (datetime.date.fromisoformat(min(r["date"] for r in inc)) - datetime.timedelta(days=20)).isoformat() if inc else "2026-08-01"
    closes = fetch_closes([r["code"] for r in inc], start)
    compute(inc, closes)
    summ = summarize(inc)
    for r in inc:
        if r.get("ok") and r.get("ret"):
            r1 = r["ret"].get(1)
            log(f"  {r['date']} {r['name']}({r['code']}) 1日後 {r1['stock']:+.1f}% (超過 {r1['excess']:+.1f}%)" if r1 else f"  {r['date']} {r['name']}: 未到達")
        else:
            log(f"  {r['date']} {r['name']}({r['code']}): {r.get('err')}")
    words = word_stats()
    generate_html(inc, summ, words)
    # 夕方の自動投稿（roei_post.py）に渡す数値
    try:
        last_dates = [r["last"]["date"] for r in inc if r.get("last")]
        market_day = max(last_dates) if last_dates else None
        json.dump({
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "market_day": market_day,
            "incidents": [{k: r.get(k) for k in ("date", "time", "code", "name", "service", "type", "scale", "day0", "ret", "last", "ok", "err")}
                          for r in inc],
            "summary": {g: {str(n): v for n, v in d.items()} for g, d in summ.items()},
            "words": [{k: w.get(k) for k in ("label", "last7", "prev7", "ratio", "peak_day", "peak")} for w in words],
        }, open(os.path.join(SCRIPT_DIR, "roei_post.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    except Exception as e:
        log(f"投稿用JSONの出力失敗: {e}")
    if "--card" in sys.argv:
        try:
            make_card(inc, summ, words)
        except Exception as e:
            log(f"速報カード生成失敗: {e}")
    if "--nopush" in sys.argv:
        log("push スキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
