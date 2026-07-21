# -*- coding: utf-8 -*-
"""
米インフレと雇用（FRBの入力と出力） → HTML出力 → GitHub Pages公開

FRBのデュアルマンデート（物価の安定・雇用の最大化）の入力2つと出力を並べる。
  入力1: CPI（前年比）      … BLS公式API (CUSR0000SA0)
  入力2: 非農業部門雇用者数  … BLS公式API (CES0000000001)
  出力:  政策金利（実効FF金利EFFR） … NY連銀公式API
  結果:  S&P500の月間騰落   … yfinance

構成:
  ① カード3枚（CPI直近3回 / 雇用直近3回 / 政策金利と次回FOMC）
     発表への市場の審判は fedwatch_history.json の確率変化で表示
  ② 月次長期表（2020年〜、最新上）: CPI前年比 / NFP / 政策金利 / S&P500月間
     CPI色帯: >=5% 赤（利下げ不能ゾーン） / 3-5% 黄 / 2%台 青（目標圏） / <2% グレー
  ③ 仮説検証枠: 「CPIが5%を超えると金利を下げられなくなり株価はピークをとる」
     （2022年の実例: CPI 5%超え→S&P500ピーク→-24%）
"""

import os
import sys
import json
import time
import subprocess
import datetime
import urllib.request

import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_HTML = "cpi.html"

BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
EFFR_URL = ("https://markets.newyorkfed.org/api/rates/unsecured/effr/"
            "search.json?startDate={start}&endDate={end}")

# 次回FOMC（calendar_screen.pyのFOMC_DATESと同期して手動更新）
FOMC_DATES = [
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    "2027-01-27", "2027-03-17", "2027-04-28",
]

START_YEAR = 2019   # CPI前年比計算のため表示開始(2020)の1年前から取得

# 発表日（BLS公式スケジュール。対象月 -> 発表日。年1回、BLSサイトから翌年分を追記）
# https://www.bls.gov/schedule/news_release/cpi.htm
CPI_RELEASE = {
    "2025-11": "2025-12-18", "2025-12": "2026-01-13",
    "2026-01": "2026-02-13", "2026-02": "2026-03-11", "2026-03": "2026-04-10",
    "2026-04": "2026-05-12", "2026-05": "2026-06-10", "2026-06": "2026-07-14",
    "2026-07": "2026-08-12", "2026-08": "2026-09-11", "2026-09": "2026-10-14",
    "2026-10": "2026-11-10", "2026-11": "2026-12-10",
}
# https://www.bls.gov/schedule/news_release/empsit.htm
NFP_RELEASE = {
    "2025-11": "2025-12-16", "2025-12": "2026-01-09",
    "2026-01": "2026-02-11", "2026-02": "2026-03-06", "2026-03": "2026-04-03",
    "2026-04": "2026-05-08", "2026-05": "2026-06-05", "2026-06": "2026-07-02",
    "2026-07": "2026-08-07", "2026-08": "2026-09-04", "2026-09": "2026-10-02",
    "2026-10": "2026-11-06", "2026-11": "2026-12-04",
}

WEEKDAYS_JP = ["月", "火", "水", "木", "金", "土", "日"]


def release_label(month_key, table):
    """'2026-06' -> '7/14(火)発表' （リストにない月は空文字）"""
    d = table.get(month_key)
    if not d:
        return ""
    dt = datetime.date.fromisoformat(d)
    return f"{dt.month}/{dt.day}({WEEKDAYS_JP[dt.weekday()]})発表"


def next_release(table, today):
    """今日以降で最初の発表日 -> '8/12(水)' （なければ空文字）"""
    future = sorted(d for d in table.values()
                    if datetime.date.fromisoformat(d) >= today)
    if not future:
        return ""
    dt = datetime.date.fromisoformat(future[0])
    return f"{dt.month}/{dt.day}({WEEKDAYS_JP[dt.weekday()]})"


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# データ取得
# -----------------------------------------
def fetch_bls():
    """CPI指数・NFP水準・失業率の月次 {(\'YYYY-MM\'): value} を3系列返す"""
    payload = json.dumps({
        "seriesid": ["CUSR0000SA0", "CES0000000001", "LNS14000000"],
        "startyear": str(START_YEAR),
        "endyear": str(datetime.date.today().year),
    }).encode()
    req = urllib.request.Request(BLS_URL, data=payload,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=40).read())
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API: {data.get('message')}")
    out = {}
    for s in data["Results"]["series"]:
        d = {}
        for r in s["data"]:
            if not r["period"].startswith("M"):
                continue
            try:
                d[f"{r['year']}-{r['period'][1:]}"] = float(r["value"])
            except ValueError:
                continue  # 未発表月は '-' が入ることがある
        out[s["seriesID"]] = d
    cpi, nfp, unemp = (out["CUSR0000SA0"], out["CES0000000001"],
                       out["LNS14000000"])
    log(f"  BLS: CPI {len(cpi)}ヶ月 / 雇用 {len(nfp)}ヶ月 / 失業率 {len(unemp)}ヶ月（最新 {max(cpi)}）")
    return cpi, nfp, unemp


def fetch_effr():
    """実効FF金利の日次 [(date, rate)] 新しい順"""
    url = EFFR_URL.format(start=f"{START_YEAR}-01-01",
                          end=datetime.date.today().isoformat())
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=40).read())
    rates = [(r["effectiveDate"], float(r["percentRate"]))
             for r in data.get("refRates", [])]
    rates.sort(reverse=True)
    if len(rates) < 100:
        raise RuntimeError(f"EFFRデータ不足（{len(rates)}件）")
    log(f"  NY連銀EFFR: {len(rates)}日分（最新 {rates[0][0]} {rates[0][1]}%）")
    return rates


def fetch_spx_monthly():
    """S&P500の月間騰落% {\'YYYY-MM\': pct}"""
    close = yf.Ticker("^GSPC").history(period="8y")["Close"].dropna()
    close.index = close.index.tz_localize(None)
    monthly = close.resample("ME").last()
    chg = monthly.pct_change() * 100
    out = {d.strftime("%Y-%m"): round(float(v), 2)
           for d, v in chg.items() if v == v}
    log(f"  S&P500: {len(out)}ヶ月分")
    return out


def load_fedwatch():
    try:
        return json.load(open(os.path.join(SCRIPT_DIR, "fedwatch_history.json"),
                              encoding="utf-8"))
    except Exception:
        return {}


# -----------------------------------------
# 加工
# -----------------------------------------
def cpi_yoy(cpi):
    """前年比% {\'YYYY-MM\': yoy}"""
    out = {}
    for k, v in cpi.items():
        y, m = int(k[:4]), int(k[5:7])
        prev = cpi.get(f"{y-1}-{m:02d}")
        if prev:
            out[k] = round((v / prev - 1) * 100, 1)
    return out


def nfp_change(nfp):
    """前月比増減(千人) {\'YYYY-MM\': chg}"""
    out = {}
    for k, v in nfp.items():
        y, m = int(k[:4]), int(k[5:7])
        pm, py = (12, y - 1) if m == 1 else (m - 1, y)
        prev = nfp.get(f"{py}-{pm:02d}")
        if prev is not None:
            out[k] = round(v - prev)
    return out


def effr_monthly(rates):
    """月末値 {\'YYYY-MM\': rate}"""
    out = {}
    for d, r in sorted(rates):  # 古い順→月内最後の値で上書き
        out[d[:7]] = r
    return out


def cpi_color(v):
    if v is None:
        return ""
    if v >= 5:
        return ' class="cpi-danger"'
    if v >= 3:
        return ' class="cpi-warn"'
    if v >= 2:
        return ' class="cpi-ok"'
    return ' class="cpi-low"'


# -----------------------------------------
# HTML
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>米インフレと雇用 - {updated_date}</title>
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
  .cards {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }}
  .card {{
    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    padding: 14px 18px; min-width: 300px;
  }}
  .card .label {{ font-size: 0.75rem; color: #94a3b8; margin-bottom: 6px; font-weight: 700; }}
  .card table {{ border-collapse: collapse; font-size: 0.85rem; width: 100%; }}
  .card td {{ padding: 3px 8px; white-space: nowrap; font-variant-numeric: tabular-nums; }}
  .card td:first-child {{ color: #94a3b8; }}
  .card .verdict {{ font-size: 0.8rem; margin-top: 8px; color: #fbbf24; font-weight: 700; }}
  .card .big {{ font-size: 1.3rem; font-weight: 700; color: #f8fafc; }}
  .arrow {{ align-self: center; color: #64748b; font-size: 1.6rem; font-weight: 700; }}
  .hypo {{
    max-width: 1010px; margin: 14px 0; background: rgba(236,72,153,0.08);
    border: 1px solid #db2777; border-radius: 10px; padding: 13px 18px;
    font-size: 0.85rem; line-height: 1.8; color: #e2e8f0;
  }}
  .hypo b {{ color: #f9a8d4; }}
  .table-wrap {{ overflow: auto; max-height: 70vh; max-width: 780px; }}
  table.main {{ border-collapse: collapse; font-size: 0.87rem; }}
  table.main thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 14px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  table.main thead th:first-child {{ text-align: left; }}
  table.main td {{
    padding: 7px 14px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  table.main td:first-child {{ text-align: left; color: #94a3b8; }}
  table.main tr:hover td {{ background: #16213a; }}
  td.cpi-danger {{ background: rgba(220,38,38,0.35); color: #fca5a5; font-weight: 700; }}
  td.cpi-warn {{ background: rgba(251,191,36,0.22); color: #fde68a; }}
  td.cpi-ok {{ background: rgba(59,130,246,0.2); color: #93c5fd; }}
  td.cpi-low {{ color: #94a3b8; }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
  .rate-up {{ color: #f87171; font-weight: 700; }}
  .rate-down {{ color: #4ade80; font-weight: 700; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.8; max-width: 1010px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="map.html" style="border-color:#94a3b8">デッキの見方</a>
    <a href="calendar.html" style="border-color:#94a3b8">イベント予定</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
    <a href="cpi.html" class="active" style="border-color:#7c3aed">米インフレと雇用</a>
    <a href="fedwatch.html" style="border-color:#7c3aed">FRB利上げ確率</a>
    <a href="totan.html" style="border-color:#7c3aed">日銀利上げ確率</a>
    <a href="kinri.html" style="border-color:#7c3aed">金利と為替</a>
    <a href="spriron.html" style="border-color:#7c3aed">SP500理論株価</a>
    <a href="riron.html" style="border-color:#7c3aed">日経理論株価</a>
    <a href="gaikoku.html" style="border-color:#2563eb">海外投資家</a>
    <a href="saitei.html" style="border-color:#2563eb">裁定取引</a>
    <a href="shutai.html" style="border-color:#2563eb">投資主体別</a>
    <a href="margin.html" style="border-color:#2563eb">銘柄別信用倍率</a>
    <a href="insider.html" style="border-color:#2563eb">インサイダー売買</a>
    <a href="buffett.html" style="border-color:#2563eb">バフェット</a>
    <a href="shinyou.html" style="border-color:#d97706">信用評価率</a>
    <a href="touraku.html" style="border-color:#d97706">騰落レシオ</a>
    <a href="karauri.html" style="border-color:#d97706">空売り比率</a>
    <a href="vix.html" style="border-color:#d97706">VIX温度計</a>
    <a href="flow.html" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
  </nav>
  <h1>米インフレと雇用（FRBの入力と出力）</h1>
  <p class="subtitle">最終更新: {updated} | 出所: BLS・NY連銀・Yahoo Finance | 入力（CPI・雇用）→ 出力（政策金利）→ 結果（S&amp;P500）</p>
  <div class="cards">
{cards}
  </div>
{hypo}
  <h2>月次の長期推移（2020年〜 / 最新上）</h2>
  <div class="table-wrap">
  <table class="main">
    <thead><tr><th>月</th><th>CPI前年比</th><th>雇用者数増減</th><th>失業率</th><th>政策金利(EFFR)</th><th>S&amp;P500月間</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・<b>FRBのデュアルマンデート</b>: 「物価の安定」(CPI)と「雇用の最大化」(雇用者数・失業率)の2つが入力、政策金利が出力。
    CPIが高い→利上げ圧力、雇用が弱い→利下げ圧力。両者が逆を向くとFRBはジレンマに陥る。<br>
    ・<b>雇用の判定は3軸</b>: ①水準=雇用増の3ヶ月平均（+100K=人口増を吸収する損益分岐点）②方向=加速/減速の並び
    ③失業率=直近12ヶ月最低からの上昇幅（<b>+0.5%以上はサーム・ルール警告圏</b>=過去の景気後退入りシグナル）。<br>
    ・CPI色帯: <span style="color:#fca5a5">5%以上=利下げ不能ゾーン（赤）</span> /
    <span style="color:#fde68a">3〜5%=警戒（黄）</span> /
    <span style="color:#93c5fd">2%台=目標圏（青）</span> / 2%未満=低インフレ（グレー）。<br>
    ・政策金利は実効FF金利（EFFR）の月末値。矢印は前月からの変化（<span class="rate-up">↑利上げ</span>/<span class="rate-down">↓利下げ</span>）。<br>
    ・カードの「市場の審判」は発表前後のFRB利上げ確率（自前のfedwatchデッキ）の変化。<br>
    ・CPI・雇用者数はBLS公式API（季調済）。公表スケジュールは<a href="calendar.html" style="color:#60a5fa">イベント予定</a>参照。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def trend_comment(vals, higher_word, lower_word):
    """直近3個の値の傾向コメント"""
    if len(vals) < 3:
        return "-"
    a, b, c = vals[0], vals[1], vals[2]  # 新→古
    if a > b > c:
        return f"3連続で{higher_word}"
    if a < b < c:
        return f"3連続で{lower_word}"
    if a > b:
        return f"直近は{higher_word}に転換"
    if a < b:
        return f"直近は{lower_word}に転換"
    return "横ばい"


def make_cards(yoy, nfp_chg, unemp, rates, fed):
    months = sorted(yoy, reverse=True)
    nfp_months = sorted(nfp_chg, reverse=True)
    today = datetime.date.today()

    # CPIカード
    rows = []
    last3 = months[:3]
    for m in last3:
        arrow = ""
        idx = sorted(yoy, reverse=True).index(m)
        if idx + 1 < len(months):
            p = yoy[months[idx + 1]]
            arrow = ' <span class="neg">↑</span>' if yoy[m] > p else (' <span class="pos">↓</span>' if yoy[m] < p else "")
        rel = release_label(m, CPI_RELEASE)
        rel_s = f'<td style="color:#64748b; font-size:0.78rem">{rel}</td>' if rel else "<td></td>"
        rows.append(f"<tr><td>{m.replace('-', '/')}分</td><td><b>{yoy[m]:.1f}%</b>{arrow}</td>{rel_s}</tr>")
    vals = [yoy[m] for m in last3]
    nx = next_release(CPI_RELEASE, today)
    nx_s = f'<br>→ 次回発表: {nx}' if nx else ""
    cpi_card = (
        '    <div class="card"><div class="label">入力1: CPI 前年比（物価の使命）</div>'
        f'<table>{"".join(rows)}</table>'
        f'<div class="verdict">→ {trend_comment(vals, "上振れ", "鈍化")}'
        f'（5%の利下げ不能ラインまで {5 - vals[0]:+.1f}%）{nx_s}</div></div>')

    # 雇用カード（水準×方向の2軸 + 失業率）
    rows = []
    last3n = nfp_months[:3]
    for m in last3n:
        v = nfp_chg[m]
        u = unemp.get(m)
        cls = "pos" if v >= 100 else ("neg" if v < 0 else "")
        u_s = f"{u:.1f}%" if u is not None else "-"
        rel = release_label(m, NFP_RELEASE)
        rel_s = f'<td style="color:#64748b; font-size:0.78rem">{rel}</td>' if rel else "<td></td>"
        rows.append(f"<tr><td>{m.replace('-', '/')}分</td>"
                    f"<td><b class=\"{cls}\">{v:+,}K</b></td>"
                    f'<td style="color:#94a3b8">失業率 {u_s}</td>{rel_s}</tr>')
    nvals = [nfp_chg[m] for m in last3n]
    avg3 = sum(nvals) / 3
    # 軸1: 水準（3ヶ月平均、損益分岐点+100K基準）
    level = "堅調" if avg3 >= 100 else ("減速気味" if avg3 >= 0 else "悪化")
    # 軸2: 方向（3ヶ月の並び）
    if nvals[0] < nvals[1] < nvals[2]:
        direction = "3ヶ月連続減速中（要監視）"
    elif nvals[0] > nvals[1] > nvals[2]:
        direction = "3ヶ月連続加速中"
    elif nvals[0] < nvals[1]:
        direction = "直近は減速"
    elif nvals[0] > nvals[1]:
        direction = "直近は加速"
    else:
        direction = "横ばい"
    # 軸3: 失業率のトレンド（直近12ヶ月最低からの上昇幅、サーム・ルールの簡易版）
    u_months = sorted(unemp, reverse=True)[:12]
    u_now = unemp.get(u_months[0]) if u_months else None
    u_alert = ""
    if u_now is not None and len(u_months) >= 6:
        u_min12 = min(unemp[m] for m in u_months)
        rise = u_now - u_min12
        if rise >= 0.5:
            u_alert = f' / <span style="color:#f87171">失業率が12ヶ月最低から+{rise:.1f}%（サーム・ルール警告圏）</span>'
        elif rise >= 0.3:
            u_alert = f" / 失業率が12ヶ月最低から+{rise:.1f}%（上昇の芽）"
    # 圧力の総合判定
    if avg3 < 0 or (u_alert and "+0.5" in u_alert) or (u_alert and "警告圏" in u_alert):
        pressure = "利下げ圧力あり"
    elif avg3 < 100 or "連続減速" in direction:
        pressure = "利下げ圧力が芽生え中"
    else:
        pressure = "利下げ圧力なし"
    nxn = next_release(NFP_RELEASE, today)
    nxn_s = f'<br>→ 次回発表: {nxn}' if nxn else ""
    nfp_card = (
        '    <div class="card"><div class="label">入力2: 非農業部門雇用者数（雇用の使命）</div>'
        f'<table>{"".join(rows)}</table>'
        f'<div class="verdict">→ 3ヶ月平均 {avg3:+,.0f}K = {level}だが{direction}{u_alert}<br>'
        f'→ 総合: {pressure}{nxn_s}</div></div>')

    # 政策金利カード
    cur_rate = rates[0][1]
    next_fomc = next((d for d in FOMC_DATES if datetime.date.fromisoformat(d) >= today), None)
    fed_txt = ""
    if fed and next_fomc:
        latest = fed[sorted(fed)[-1]]
        key = next_fomc[:7].replace("-", "/")
        if key in latest:
            p = latest[key]
            fed_txt = (f'次回会合の<b>{"利上げ" if p > 0 else "利下げ"}確率 {abs(p):.0f}%</b>'
                       f'（0.25%幅、fedwatchより）')
    fomc_s = next_fomc
    if next_fomc:
        fd = datetime.date.fromisoformat(next_fomc)
        fomc_s = f"{fd.month}/{fd.day}({WEEKDAYS_JP[fd.weekday()]})"
    rate_card = (
        '    <div class="card"><div class="label">出力: FRB政策金利（実効FF金利）</div>'
        f'<div class="big">{cur_rate:.2f}%</div>'
        f'<table><tr><td>次回FOMC</td><td><b>{fomc_s}</b></td></tr></table>'
        f'<div class="verdict">→ {fed_txt or "織り込みデータなし"}</div></div>')

    return (cpi_card + '\n    <div class="arrow">→</div>\n' + nfp_card +
            '\n    <div class="arrow">→</div>\n' + rate_card), vals[0]


def make_hypo(cur_yoy):
    dist = 5.0 - cur_yoy
    if cur_yoy >= 5:
        status = f'<b>現在 {cur_yoy:.1f}% = ゾーン内！利下げ不能圏。株価ピークに最大警戒</b>'
    elif cur_yoy >= 4:
        status = f'現在 {cur_yoy:.1f}%。ラインまであと{dist:.1f}% — <b>接近中、要警戒</b>'
    else:
        status = f'現在 {cur_yoy:.1f}%。ラインまであと{dist:.1f}%の余裕'
    return (
        '  <div class="hypo"><b>仮説（2022年の教訓）:</b> '
        'CPIが5%を超えてくると金利を下げられなくなり（むしろ上げるしかなくなり）、株価はピークをとるのではないか？<br>'
        '実例: 2021年6月にCPI 5%超え → 2022年1月3日にS&amp;P500ピーク → 2022年3月から利上げ開始 → 6月までに-24%。'
        'CPIが赤帯に入ったら「金利の下支えが消える」サイン。<br>'
        f'📍 {status}</div>')


def generate_html(yoy, nfp_chg, unemp, rates, spx, fed):
    cards, cur_yoy = make_cards(yoy, nfp_chg, unemp, rates, fed)
    eff_m = effr_monthly(rates)

    months = [m for m in sorted(yoy, reverse=True) if m >= "2020-01"]
    rows = []
    prev_rate = None
    # 金利矢印は古い順で計算
    rate_arrow = {}
    for m in sorted(eff_m):
        r = eff_m[m]
        if prev_rate is not None:
            if r > prev_rate + 0.05:
                rate_arrow[m] = ' <span class="rate-up">↑</span>'
            elif r < prev_rate - 0.05:
                rate_arrow[m] = ' <span class="rate-down">↓</span>'
        prev_rate = r

    for m in months:
        cy = yoy.get(m)
        nf = nfp_chg.get(m)
        un = unemp.get(m)
        er = eff_m.get(m)
        sp = spx.get(m)
        nf_s = f'{nf:+,}K' if nf is not None else "-"
        nf_cls = ' class="neg"' if (nf is not None and nf < 0) else ""
        un_s = f'{un:.1f}%' if un is not None else "-"
        sp_s = f'<span class="{"pos" if sp > 0 else "neg"}">{sp:+.2f}%</span>' if sp is not None else "-"
        er_s = f'{er:.2f}%{rate_arrow.get(m, "")}' if er is not None else "-"
        rows.append(
            f'      <tr><td>{m.replace("-", "/")}</td>'
            f'<td{cpi_color(cy)}>{cy:.1f}%</td>'
            f'<td{nf_cls}>{nf_s}</td>'
            f'<td>{un_s}</td>'
            f'<td>{er_s}</td>'
            f'<td>{sp_s}</td></tr>')

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        cards=cards,
        hypo=make_hypo(cur_yoy),
        rows="\n".join(rows),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}（{len(rows)}ヶ月）")


# -----------------------------------------
# GitHub Pages 自動 push
# -----------------------------------------
def push_to_github():
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "cpi_screen.py", "cpi_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update cpi report " + today],
        capture_output=True,
    )
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
            log("  Done: https://ichikon77.github.io/minervini/cpi.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("米インフレと雇用 チェック開始")

    try:
        cpi, nfp, unemp = fetch_bls()
        rates = fetch_effr()
        spx = fetch_spx_monthly()
    except Exception as e:
        log(f"エラー: データの取得に失敗しました: {e}")
        sys.exit(1)

    yoy = cpi_yoy(cpi)
    nfp_chg = nfp_change(nfp)
    fed = load_fedwatch()

    generate_html(yoy, nfp_chg, unemp, rates, spx, fed)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
