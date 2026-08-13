# -*- coding: utf-8 -*-
"""
日経平均 理論株価 自動記録 → HTML出力 → GitHub Pages公開

データ源: https://nikkei225jp.com/data/per.php
実データは daily2.json（2009年からの日次）。col12=PER, col13=PBR。

- EPS = 日経平均 ÷ PER、BPS = 日経平均 ÷ PBR（エクセルと同方式、一致検証済み）
- 理論PBR: BPS × 0.87 / BPS × 0.82（過去の底値PBR水準）
- 理論株価: EPS × PER(10.5〜21.0、0.5刻み22本)
- 現在の日経平均に最も近い理論株価のセルをハイライト（いまのPER位置が分かる）
- 最新が上。履歴は riron_history.json に蓄積
"""

import os
import re
import sys
import json
import time
import subprocess
import datetime

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

PAGE_URL = "https://nikkei225jp.com/data/per.php"
DATA_URL = "https://nikkei225jp.com/_data/_nfsDATA/DAY/daily2.json"

HISTORY_JSON = os.path.join(SCRIPT_DIR, "riron_history.json")
REPORT_HTML = "riron.html"

PBR_LEVELS = [0.87, 0.82]
PER_LEVELS = [10.5 + 0.5 * i for i in range(22)]  # 10.5〜21.0

# 2624(iFreeETF日経225・年1回決算) 買い下がりラダー: (PERライン, 株数)
# 仮説⑭: 暴落の底 = 直前1年高値PERの67〜75% → 本命PER15.5前後
LADDER_ETF = "2624.T"
LADDER_PLAN = [(17.5, 2), (17.0, 4), (16.5, 12), (16.0, 24), (15.5, 48), (15.0, 96),
               (14.5, 192), (14.0, 384)]  # 14.5以下はオーバーシュート域（2018/12型=高値の67.7%）
LADDER_EXIT_PER = 20.0  # 出口: 各階層を独立ポジションとしてPER20戻りで売った場合の損益を表示

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": PAGE_URL,
}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# データ取得（requests → curl → PowerShell）
# -----------------------------------------
def _fetch_requests(url, timeout):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def _fetch_curl(url, timeout):
    cmd = ["curl", "-sL", "--max-time", str(timeout),
           "-A", HEADERS["User-Agent"],
           "-e", PAGE_URL,
           "-H", "Accept: */*",
           "-H", "Accept-Language: ja,en-US;q=0.9",
           "--compressed",
           url]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0 or not r.stdout or len(r.stdout) < 1000:
        raise RuntimeError(f"curl取得失敗 (rc={r.returncode}, len={len(r.stdout or '')})")
    if "403" in r.stdout[:200] and "Forbidden" in r.stdout[:500]:
        raise RuntimeError("curl: 403 Forbidden")
    return r.stdout


def _fetch_powershell(url, timeout):
    ps = (
        f"$ProgressPreference='SilentlyContinue'; "
        f"$r = Invoke-WebRequest -UseBasicParsing -Uri '{url}' -TimeoutSec {timeout} "
        f"-Headers @{{'Referer'='{PAGE_URL}'; 'Accept-Language'='ja,en-US;q=0.9'}} "
        f"-UserAgent '{HEADERS['User-Agent']}'; "
        f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        f"$r.Content"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore",
                       timeout=timeout + 30)
    if r.returncode != 0 or not r.stdout or len(r.stdout) < 1000:
        raise RuntimeError(f"powershell取得失敗 (rc={r.returncode}, len={len(r.stdout or '')})")
    return r.stdout


def fetch_with_retry(url, tries=4, timeout=60, wait=45):
    methods = [("requests", _fetch_requests), ("curl", _fetch_curl)]
    if os.name == "nt":
        methods.append(("powershell", _fetch_powershell))
    last_err = None
    for i in range(1, tries + 1):
        for name, fn in methods:
            try:
                return fn(url, timeout)
            except Exception as e:
                last_err = e
                log(f"  [{name}]方式は不可、次の方式へ ({i}/{tries})")
        if i < tries:
            time.sleep(wait)
    raise last_err


def fetch_daily():
    """{日付ISO: {日経平均, PER, PBR}} を返す"""
    s = fetch_with_retry(DATA_URL)
    start = s.find('[')
    end = s.rfind(']')
    if start < 0 or end < 0:
        raise ValueError("データJSONの形式が想定と異なります")
    s = s[start:end + 1].replace('""', 'null')
    while ',,' in s:
        s = s.replace(',,', ',null,')
    s = s.replace('[,', '[null,').replace(',]', ',null]')
    raw = json.loads(s)

    out = {}
    for row in raw:
        if len(row) < 14 or row[1] is None or row[12] is None or row[13] is None:
            continue
        try:
            d = datetime.datetime.fromtimestamp(row[0] / 1000).date()
            nikkei = float(row[1])
            per = float(row[12])
            pbr = float(row[13])
            if per <= 0 or pbr <= 0:
                continue
            out[d.isoformat()] = {"日経平均": nikkei, "PER": per, "PBR": pbr}
        except (TypeError, ValueError):
            continue
    return out


def fetch_ladder_price():
    """2624の直近終値をyfinanceで取得（失敗したらNone: ラダー表はスキップ）"""
    try:
        import yfinance as yf
        h = yf.Ticker(LADDER_ETF).history(period="5d")["Close"]
        if len(h):
            return round(float(h.iloc[-1]), 1)
    except Exception as e:
        log(f"  2624価格の取得失敗（ラダー表はスキップ）: {e}")
    return None


def build_ladder_html(nikkei, eps, etf_price):
    """2624買い下がりラダー表のHTMLを生成（毎日EPS/現値から再計算）"""
    if etf_price is None:
        return ""
    # 出口価格: PER20戻り時の2624換算値（現値からの変化率を1倍で適用）
    exit_n = eps * LADDER_EXIT_PER
    exit_etf = etf_price * (exit_n / nikkei)

    rows = []
    sh_cum = 0
    cost_cum = 0.0
    for per, sh in LADDER_PLAN:
        target_n = eps * per
        drop = target_n / nikkei - 1
        etf = etf_price * (1 + drop)
        cost = etf * sh
        sh_cum += sh
        cost_cum += cost
        avg = cost_cum / sh_cum
        dev = (avg / etf - 1) * 100
        profit = (exit_etf - etf) * sh          # この階層だけをPER20で売った損益
        profit_pct = (exit_etf / etf - 1) * 100
        reached = target_n >= nikkei  # 既に到達済みのライン
        row_cls = ' style="background:rgba(56,189,248,0.12)"' if reached else ""
        rows.append(
            f'      <tr{row_cls}><td>PER {per:g}</td>'
            f'<td>{target_n:,.0f}</td>'
            f'<td>{drop * 100:+.1f}%</td>'
            f'<td><b>{etf:,.0f}</b></td>'
            f'<td>{sh}</td>'
            f'<td>{cost:,.0f}</td>'
            f'<td>{sh_cum}</td>'
            f'<td>{cost_cum:,.0f}</td>'
            f'<td>{avg:,.0f}</td>'
            f'<td>{dev:+.1f}%</td>'
            f'<td class="pos">+{profit:,.0f}</td>'
            f'<td class="pos">+{profit_pct:.1f}%</td></tr>')
    return f"""
  <h2 style="font-size:1.05rem; color:#cbd5e1; margin:26px 0 8px;">【実験】2624 買い下がりラダー（毎日自動再計算）</h2>
  <p style="font-size:0.78rem; color:#94a3b8; margin-bottom:10px;">
    仮説⑭（暴落の底=直前1年高値PERの67〜75%）に基づく買い下がり目安。本命PER15.5。
    iFreeETF日経225(2624・現値{etf_price:,.0f}円)を1倍連動と仮定してEPS×PERから換算。指値の置き直しに使う。</p>
  <div class="table-wrap" style="max-height:none; max-width:980px;">
  <table>
    <thead>
      <tr><th>買い場</th><th>日経換算</th><th>現値比</th><th>2624目安</th><th>株数</th><th>投入金額</th>
      <th>累計株数</th><th>累計金額</th><th>平均買値</th><th>乖離率</th>
      <th>PER20戻り収益額</th><th>収益率</th></tr>
    </thead>
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>
  </div>
  <p style="font-size:0.78rem; color:#64748b; margin-top:8px; line-height:1.8;">
    ・乖離率 = その階層まで買った時点の平均買値が当階層の株価より何%上か（≒その時点の含み損率）。<br>
    ・<b>PER20戻り収益</b> = <b>各階層を独立ポジションとして</b>、その階層の投入金額（累計ではない）に対し
    PER20（日経{exit_n:,.0f}円 ≒ 2624約{exit_etf:,.0f}円）まで戻したときに売った場合の収益額・収益率。
    下の階層ほど安く買えるぶん収益率が大きい。運用方針: 途中で買った階層は戻り局面で随時利確し、一番下の階層はPER20超まで持つ。<br>
    ・日経換算 = EPS × 各PER（EPSは毎日更新されるため、ラインの円換算も毎日動く。指値は週1回程度置き直す）。<br>
    ・2624目安は日経の下落率をそのまま適用した近似値。発注は成行でなく<b>指値</b>で（売買代金約3億円/日と薄いため）。<br>
    ・景気後退でEPS自体が削られると全ラインが下方シフトする点に注意（仮説⑭の注意書き参照）。投資判断は自己責任で。
  </p>"""


def enrich(hist):
    """EPS/BPS/前日差を計算して埋める"""
    dates = sorted(hist.keys())
    for i, d in enumerate(dates):
        r = hist[d]
        nikkei, per, pbr = r["日経平均"], r["PER"], r["PBR"]
        r["EPS"] = round(nikkei / per, 2)
        r["BPS"] = round(nikkei / pbr, 2)
        r["前日差"] = round(nikkei - hist[dates[i - 1]]["日経平均"], 2) if i > 0 else None


# -----------------------------------------
# 履歴
# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        with open(HISTORY_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(hist):
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)


# -----------------------------------------
# HTML出力
# -----------------------------------------
HTML_HEAD = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>日経平均 理論株価 - {latest_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .legend {{ display: flex; gap: 12px; margin-bottom: 14px; font-size: 0.78rem; color: #94a3b8; flex-wrap: wrap; align-items: center; }}
  .chip {{ padding: 2px 10px; border-radius: 10px; }}
  .table-wrap {{
    overflow: auto;
    max-height: calc(100vh - 200px);
  }}
  table {{ border-collapse: collapse; font-size: 0.8rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 8px 10px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child {{
    text-align: left; position: sticky; left: 0; z-index: 3; background: #1e293b;
  }}
  thead th.sep {{ border-left: 2px solid #334155; }}
  td {{
    padding: 5px 10px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{
    text-align: left; color: #94a3b8; position: sticky; left: 0;
    background: #0f172a; z-index: 1;
  }}
  td.sep {{ border-left: 2px solid #334155; }}
  tr:hover td {{ filter: brightness(1.3); }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
  td.roe {{ color: #c4b5fd; }}                                                            /* 暗黙ROE（紫） */
  td.cheap {{ background: rgba(14,116,144,0.3); color: #a5f3fc; }}                       /* 割安（薄青） */
  td.deep-cheap {{ background: rgba(2,132,199,0.65); color: #e0f2fe; font-weight: bold; }} /* 歴史的割安（濃青） */
  td.hot {{ background: rgba(190,60,60,0.28); color: #fecaca; }}                          /* 過熱気味（薄赤） */
  td.deep-hot {{ background: rgba(220,38,38,0.6); color: #fee2e2; font-weight: bold; }}   /* 歴史的過熱（濃赤） */
  td.below {{ background: rgba(14,116,144,0.55); color: #a5f3fc; font-weight: bold; }}  /* 現値のすぐ下 = 水色 */
  td.above {{ background: rgba(190,60,60,0.4); color: #fecaca; font-weight: bold; }}    /* 現値のすぐ上 = 薄赤 */
  td.month-low {{ background: rgba(56,189,248,0.22); color: #bae6fd; font-weight: bold; cursor: help; }}  /* 月間最安値 = 薄水色 */
  td.pbrline {{ color: #7dd3fc; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.8; }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="map.html" style="border-color:#94a3b8">デッキの見方</a>
    <a href="calendar.html" style="border-color:#94a3b8">イベント予定</a>
    <a href="cpi.html" style="border-color:#7c3aed">米インフレと雇用</a>
    <a href="fedwatch.html" style="border-color:#7c3aed">FRB利上げ確率</a>
    <a href="totan.html" style="border-color:#7c3aed">日銀利上げ確率</a>
    <a href="kinri.html" style="border-color:#7c3aed">金利と為替</a>
    <a href="spriron.html" style="border-color:#7c3aed">SP500理論株価</a>
    <a href="riron.html" class="active" style="border-color:#7c3aed">日経理論株価</a>
    <a href="gaikoku.html" style="border-color:#2563eb">海外投資家</a>
    <a href="saitei.html" style="border-color:#2563eb">裁定取引</a>
    <a href="shutai.html" style="border-color:#2563eb">投資主体別</a>
    <a href="shinyou.html" style="border-color:#d97706">信用評価率</a>
    <a href="touraku.html" style="border-color:#d97706">騰落レシオ</a>
    <a href="karauri.html" style="border-color:#d97706">空売り比率</a>
    <a href="vix.html" style="border-color:#d97706">VIX温度計</a>
    <a href="flow.html" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
    <a href="insider.html" style="border-color:#db2777">インサイダー売買</a>
    <a href="margin.html" style="border-color:#db2777">銘柄チェッカー</a>
    <a href="buffett.html" style="border-color:#db2777">バフェット</a>
    <a href="kijitsu.html" style="border-color:#db2777">信用期日</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>日経平均 理論株価（PER・PBRレンジ）</h1>
  <p class="subtitle">最終更新: {updated} | 出所: nikkei225jp.com | EPS=日経平均÷PER、BPS=日経平均÷PBR</p>
  <div class="legend">
    <span class="chip" style="background:rgba(14,116,144,0.55); color:#a5f3fc">現値のすぐ下の理論株価</span>
    <span class="chip" style="background:rgba(190,60,60,0.4); color:#fecaca">現値のすぐ上の理論株価</span>
    <span>この2セルの間に現在の日経平均がいる</span>
    <span class="chip" style="color:#7dd3fc">理論PBR = 過去の底値PBR水準（BPS×0.87 / ×0.82）</span>
  </div>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>日付</th>
        <th>日経平均</th>
        <th>前日差</th>
        <th>PER</th>
        <th>PBR</th>
        <th>EPS</th>
        <th>ROE</th>
        <th>BPS</th>
        <th class="sep">PBR0.87</th>
        <th>PBR0.82</th>
{per_headers}
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・PER/PBR列の色: <span style="background:rgba(2,132,199,0.65); padding:1px 6px">濃青</span> = 歴史的割安（PER&lt;13 / PBR&lt;1.2）、
    <span style="background:rgba(14,116,144,0.3); padding:1px 6px">薄青</span> = 割安（PER&lt;17.5 / PBR&lt;1.3）、
    <span style="background:rgba(190,60,60,0.28); padding:1px 6px">薄赤</span> = 過熱気味（PER&gt;19 / PBR&gt;1.8）、
    <span style="background:rgba(220,38,38,0.6); padding:1px 6px">濃赤</span> = 歴史的過熱（PER&gt;20 / PBR&gt;1.9）。<br>
    ・日経平均列の<span style="background:rgba(56,189,248,0.22); padding:1px 6px; color:#bae6fd">薄水色</span> = その月の最安値（前月最安値=攻防の分岐点）。<br>
    ・<b>2001年7月からの実績（接近148回）</b>: 前月最安値に接近（+1.5%以内）した場合、サポート成功（割れず+2%以上反発）<b>33%</b>、10日以内に割れたのは<b>64%</b>（もみ合い3%）。<br>
    ・<b>割れた後に「さらに5%以上沈む」確率は通常時の約1.4倍</b>（通常29%→割れ後41%、割れ113回の実績）。参考: S&amp;P500は約1.5倍（18%→28%）。<br>
    <br>
    <b style="color:#94a3b8">【仮説メモ】</b><br>
    ・<b>仮説① 歴史的買い場</b>: PERとPBRが両方「濃青」＝2つの物差しが揃って割安。過去17年で2012年6月（アベノミクス前夜）と2025年4月（関税ショック底）のみ。数年に一度の大底シグナル。<br>
    ・<b>仮説② 成長相場の押し目</b>: PERが薄青×PBRが薄赤の「ねじれ」＋<b>EPSが増加基調</b>＝利益成長に株価が追いついていない状態。2026年に7回出現（5/18-20, 6/8, 6/10-11, 7/17）、うち検証可能な6回は5〜10営業日後に+7%以上で反発。<br>
    ・<b>注意</b>: 同じねじれでもEPSが減少中なら業績崩壊型（2009年リーマン後: PER40超は利益消滅の見かけ）。買い場ではなく警戒。ねじれを見たらまずEPS列の推移を確認すること。<br>
    ・<b>仮説②の需給条件（次週以降要検証）</b>: 過去の反発6回（2026/5〜6月）は<a href="shinyou.html" style="color:#60a5fa">信用売り残</a>80万超（買い戻し期待の緑点灯中）＋<a href="karauri.html" style="color:#60a5fa">空売り比率</a>40前後で発生＝売り方の踏み上げが反発の燃料だった。一方2026/7/17のねじれは売り残79.5万（7/3週に-25.5%と急減）・空売り比率34.5と燃料不足の状態で出現。この型で自律反発できるかは実需買い（海外勢等）次第と推測 → <b>7/17型の結果を次週以降検証すること</b>。<br>
    <br>
    ・理論株価 = EPS × 各PER。現在の日経平均がどのPER水準にいるか、青いセルの位置で分かる。<br>
    ・理論PBR = BPS × 0.87 / 0.82（過去の暴落時に底となったPBR水準。ここまで下がると歴史的底値圏）。<br>
    ・ROE = PBR ÷ PER（暗黙ROE）。日経平均全体の「稼ぐ力」。2009年3%台→2010年代8%前後→2026年10%超と構造的に上昇。PBRの高さが正当化されるかはROE次第で、ROEが崩れる兆候が出たら高PBRは警戒。<br>
    ・<a href="{src_url}" style="color:#60a5fa">nikkei225jp.com 日経平均PER</a>
  </p>
{ladder}
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def per_class(v):
    """PER: <13 濃青 / <17.5 薄青 / >20 濃赤 / >19 薄赤"""
    if v < 13:
        return ' class="deep-cheap"'
    if v < 17.5:
        return ' class="cheap"'
    if v > 20:
        return ' class="deep-hot"'
    if v > 19:
        return ' class="hot"'
    return ""


def pbr_class(v):
    """PBR: <1.2 濃青 / <1.3 薄青 / >1.9 濃赤 / >1.8 薄赤"""
    if v < 1.2:
        return ' class="deep-cheap"'
    if v < 1.3:
        return ' class="cheap"'
    if v > 1.9:
        return ' class="deep-hot"'
    if v > 1.8:
        return ' class="hot"'
    return ""


def generate_html(hist):
    dates = sorted(hist.keys(), reverse=True)  # 最新が上

    sep_attr = ' class="sep"'
    per_headers = "\n".join(
        f'        <th{sep_attr if i == 0 else ""}>PER{p:g}</th>'
        for i, p in enumerate(PER_LEVELS))

    # 月ごとの最安値の日を特定（前月最安値=サポートラインの可視化）
    month_low_date = {}
    for d in dates:
        ym = d[:7]
        v = hist[d]["日経平均"]
        if ym not in month_low_date or v < hist[month_low_date[ym]]["日経平均"]:
            month_low_date[ym] = d
    low_dates = set(month_low_date.values())

    rows = []
    for d in dates:
        r = hist[d]
        nikkei, eps, bps = r["日経平均"], r["EPS"], r["BPS"]
        diff = r.get("前日差")
        diff_s = "-" if diff is None else f'<span class="{"pos" if diff > 0 else ("neg" if diff < 0 else "")}">{diff:+,.2f}</span>'
        nikkei_attr = ' class="month-low" title="この月の最安値（サポートライン候補）"' if d in low_dates else ''

        # 現値を挟む2セルだけハイライト（すぐ下=水色 / すぐ上=薄赤）
        theos = [round(eps * p) for p in PER_LEVELS]
        lower_i = upper_i = None
        for i2 in range(len(theos) - 1):
            if theos[i2] <= nikkei < theos[i2 + 1]:
                lower_i, upper_i = i2, i2 + 1
                break
        if lower_i is None:  # レンジ外（全部上 or 全部下）
            if nikkei < theos[0]:
                upper_i = 0
            else:
                lower_i = len(theos) - 1

        cells = [f'<td>{d.replace("-", "/")}</td>',
                 f'<td{nikkei_attr}>{nikkei:,.2f}</td>',
                 f'<td>{diff_s}</td>',
                 f'<td{per_class(r["PER"])}>{r["PER"]:.2f}</td>',
                 f'<td{pbr_class(r["PBR"])}>{r["PBR"]:.2f}</td>',
                 f'<td>{eps:,.2f}</td>',
                 f'<td class="roe">{r["PBR"] / r["PER"] * 100:.1f}%</td>',
                 f'<td>{bps:,.2f}</td>']
        for j, lv in enumerate(PBR_LEVELS):
            sep = ' sep' if j == 0 else ''
            cells.append(f'<td class="pbrline{sep}">{bps * lv:,.0f}</td>')
        for i, (p, t) in enumerate(zip(PER_LEVELS, theos)):
            cls = []
            if i == 0:
                cls.append('sep')
            if i == lower_i:
                cls.append('below')
            elif i == upper_i:
                cls.append('above')
            attr = f' class="{" ".join(cls)}"' if cls else ''
            cells.append(f'<td{attr}>{t:,}</td>')
        rows.append('      <tr>' + "".join(cells) + '</tr>')

    # 2624買い下がりラダー表（最新日のEPS/日経現値から再計算）
    latest = hist[dates[0]]
    ladder = build_ladder_html(latest["日経平均"], latest["EPS"], fetch_ladder_price())

    html = HTML_HEAD.format(
        latest_date=dates[0].replace("-", "/") if dates else "-",
        per_headers=per_headers,
        rows="\n".join(rows),
        src_url=PAGE_URL,
        ladder=ladder,
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")


# -----------------------------------------
# GitHub Pages 自動 push
# -----------------------------------------
def push_to_github():
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    os.path.basename(HISTORY_JSON), ".gitignore",
                    "riron_screen.py", "riron_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update riron report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/riron.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("日経平均 理論株価 チェック開始")

    hist = load_history()

    try:
        daily = fetch_daily()
    except Exception as e:
        log(f"エラー: データの取得に失敗しました: {e}")
        sys.exit(1)

    log(f"サイト上のデータ: {len(daily)}日分 ({min(daily)} ～ {max(daily)})")

    added = 0
    for d, rec in daily.items():
        if d in hist:
            continue
        hist[d] = rec
        added += 1

    if added:
        enrich(hist)
        save_history(hist)
        log(f"追記: {added}日分（計 {len(hist)}日）")
    else:
        log("新しいデータはありませんでした")

    if not hist:
        return

    generate_html(hist)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
