# -*- coding: utf-8 -*-
"""
S&P500 理論株価 自動記録 → HTML出力 → GitHub Pages公開

データ源（3つ）:
1. S&P500株価（日次）: https://nikkeiyosoku.com/spx/data/ のテーブル（日付/始値/高値/安値/終値）
2. 予想PER（週次・金曜更新）: https://www.barrons.com/market-data/stocks/us/pe-yields
   「S&P 500 Index」行の Estimate^（Forward 12 months, Birinyi Associates）
3. 予想EPS（週次）: https://stock-marketdata.com/eps-nasdaq.html
   表「日付|NASDAQ100実績|NASDAQ100予想|S&P500実績|S&P500予想|...」のS&P500予想EPS列

- PER(予想EPSから) = S&P500終値 ÷ 予想EPS（毎日変動。週最終営業日のみ予想PERと一致）
- 理論株価 = 予想EPS × [14.62, 16.37, 22.82, 24.00, 26.00]
  (14.62=コロナ2020/3/16底, 16.37=Tech底入れ2022/10/10, 22.82=コロナ2020/3/18,
   24=かなり高い, 26=過去最高水準)
- 予想PER/EPSは週次のため、新しい週次値が出るまで直近値を引き継ぐ（エクセルと同運用）
- 最新が上。履歴は spriron_history.json に蓄積。エクセル過去分も初回取込
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

PRICE_URL = "https://nikkeiyosoku.com/spx/data/"
PER_URL = "https://www.barrons.com/market-data/stocks/us/pe-yields"
EPS_URL = "https://stock-marketdata.com/eps-nasdaq.html"

HISTORY_JSON = os.path.join(SCRIPT_DIR, "spriron_history.json")
REPORT_HTML = "spriron.html"

THEO_PERS = [14.62, 16.37, 22.82, 24.00, 26.00]
THEO_NOTES = ["コロナ底'20/3/16", "Tech底'22/10/10", "コロナ'20/3/18", "かなり高い", "過去最高水準"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# 取得（requests → curl → PowerShell）
# -----------------------------------------
def _fetch_requests(url, timeout):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def _fetch_curl(url, timeout):
    cmd = ["curl", "-sL", "--max-time", str(timeout),
           "-A", HEADERS["User-Agent"],
           "-H", "Accept-Language: ja,en-US;q=0.9",
           "--compressed", url]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0 or not r.stdout or len(r.stdout) < 1000:
        raise RuntimeError(f"curl取得失敗 (rc={r.returncode}, len={len(r.stdout or '')})")
    return r.stdout


def _fetch_powershell(url, timeout):
    ps = (
        f"$ProgressPreference='SilentlyContinue'; "
        f"$r = Invoke-WebRequest -UseBasicParsing -Uri '{url}' -TimeoutSec {timeout} "
        f"-Headers @{{'Accept-Language'='ja,en-US;q=0.9'}} "
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


def fetch_with_retry(url, tries=3, timeout=60, wait=30):
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


# -----------------------------------------
# 1. S&P500株価（日次）
# -----------------------------------------
def fetch_prices():
    """{日付ISO: 終値} 直近約1ヶ月分"""
    html = fetch_with_retry(PRICE_URL)
    out = {}
    # 行形式: <td>2026/07/17</td><td>7,447.52</td>...終値は5番目のセル
    for m in re.finditer(
            r'<td[^>]*>(\d{4}/\d{2}/\d{2})</td>\s*'
            r'<td[^>]*>([\d,.]+)</td>\s*<td[^>]*>([\d,.]+)</td>\s*'
            r'<td[^>]*>([\d,.]+)</td>\s*<td[^>]*>([\d,.]+)</td>', html):
        d = m.group(1).replace('/', '-')
        close = float(m.group(5).replace(',', ''))
        out[d] = close
    if not out:
        raise ValueError("S&P500株価が取得できません（ページ構造変更の可能性）")
    return out


# -----------------------------------------
# 2. 予想PER（Barron's, 週次）
# -----------------------------------------
def fetch_forward_per():
    """(基準日ISO or None, 予想PER) を返す"""
    html = fetch_with_retry(PER_URL)
    # "S&amp;P 500 Index|25.50|24.72|21.54" → 3つ目(Estimate^)が予想PER
    text = re.sub(r'<[^>]+>', '|', html)
    text = re.sub(r'\|+', '|', text)
    m = re.search(r'S&amp;P 500 Index\|([\d.]+)\|([\d.]+)\|([\d.]+)', text)
    if not m:
        m = re.search(r'S&P 500 Index\|([\d.]+)\|([\d.]+)\|([\d.]+)', text)
    if not m:
        raise ValueError("Barron'sから予想PERが取得できません")
    est = float(m.group(3))
    # 基準日（FRIDAY, JULY 17, 2026 など）
    dm = re.search(r'(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY),\s+(\w+)\s+(\d+),\s+(\d{4})', html)
    as_of = None
    if dm:
        months = {m2.upper(): i for i, m2 in enumerate(
            ['JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE', 'JULY',
             'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER'], 1)}
        mon = months.get(dm.group(2).upper())
        if mon:
            as_of = f"{dm.group(4)}-{mon:02d}-{int(dm.group(3)):02d}"
    return as_of, est


# -----------------------------------------
# 3. 予想EPS（stock-marketdata, 週次）
# -----------------------------------------
def fetch_forward_eps():
    """{日付ISO: 予想EPS} 直近数週分を返す"""
    html = fetch_with_retry(EPS_URL)
    text = re.sub(r'<[^>]+>', '|', html)
    text = re.sub(r'\|+', '|', text)
    # ヘッダ: 日付|NASDAQ100実績|NASDAQ100予想|S&P500実績|S&P500予想|ラッセル実績|ラッセル予想
    i = text.find('日付 |NASDAQ100|実績EPS')
    if i < 0:
        i = text.find('NASDAQ100|実績EPS')
    if i < 0:
        raise ValueError("stock-marketdataのEPS表が見つかりません")
    seg = text[i:i + 8000]
    out = {}
    for m in re.finditer(r'(\d{4}/\d{2}/\d{2})\s*\|\s*([\d,.]+)\s*\|\s*([\d,.]+)\s*\|\s*([\d,.]+)\s*\|\s*([\d,.]+)\s*\|', seg):
        d = m.group(1).replace('/', '-')
        sp_forward = float(m.group(5).replace(',', ''))  # 5番目 = S&P500予想EPS
        out[d] = sp_forward
    if not out:
        raise ValueError("S&P500予想EPSが取得できません")
    return out


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


def recompute(hist):
    """前日差と、予想PER/EPSの引き継ぎ、PER(予想EPSから)を再計算"""
    dates = sorted(hist.keys())
    last_per = last_eps = None
    for i, d in enumerate(dates):
        r = hist[d]
        if r.get("予想PER") is not None:
            last_per = r["予想PER"]
        elif last_per is not None:
            r["予想PER"] = last_per
        if r.get("予想EPS") is not None:
            last_eps = r["予想EPS"]
        elif last_eps is not None:
            r["予想EPS"] = last_eps
        if r.get("SP500") and r.get("予想EPS"):
            r["PER計算"] = round(r["SP500"] / r["予想EPS"], 2)
        r["前日差"] = round(r["SP500"] - hist[dates[i - 1]]["SP500"], 2) if i > 0 and hist[dates[i - 1]].get("SP500") else None


# -----------------------------------------
# HTML出力
# -----------------------------------------
HTML_HEAD = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>S&P500 理論株価 - {latest_date}</title>
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
    max-width: 1150px;
  }}
  table {{ border-collapse: collapse; font-size: 0.82rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 8px 11px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child {{
    text-align: left; position: sticky; left: 0; z-index: 3; background: #1e293b;
  }}
  thead th.sep {{ border-left: 2px solid #334155; }}
  thead th .small {{ display: block; font-size: 0.68rem; font-weight: normal; color: #64748b; }}
  td {{
    padding: 5px 11px; border-bottom: 1px solid #1e293b;
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
  td.weekly {{ color: #fbbf24; font-weight: bold; }}  /* 週次一致日（予想PER=PER計算） */
  td.below {{ background: rgba(14,116,144,0.55); color: #a5f3fc; font-weight: bold; }}
  td.above {{ background: rgba(190,60,60,0.4); color: #fecaca; font-weight: bold; }}
  td.month-low {{ background: rgba(56,189,248,0.22); color: #bae6fd; font-weight: bold; cursor: help; }}  /* 月間最安値 */
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
    <a href="yorimae.html" style="border-color:#94a3b8">寄り前</a>
    <a href="cpi.html" style="border-color:#7c3aed">米インフレと雇用</a>
    <a href="fedwatch.html" style="border-color:#7c3aed">FRB利上げ確率</a>
    <a href="totan.html" style="border-color:#7c3aed">日銀利上げ確率</a>
    <a href="kinri.html" style="border-color:#7c3aed">金利と為替</a>
    <a href="spriron.html" class="active" style="border-color:#7c3aed">SP500理論株価</a>
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
  <h1>S&P500 理論株価（予想PER・予想EPS）</h1>
  <p class="subtitle">最終更新: {updated} | 株価: nikkeiyosoku.com / 予想PER: Barron's (Birinyi, 金曜更新) / 予想EPS: stock-marketdata.com</p>
  <div class="legend">
    <span class="chip" style="background:rgba(14,116,144,0.55); color:#a5f3fc">現値のすぐ下の理論株価</span>
    <span class="chip" style="background:rgba(190,60,60,0.4); color:#fecaca">現値のすぐ上の理論株価</span>
    <span class="chip" style="color:#fbbf24">黄色 = 週次更新日（予想PERとPER計算が一致）</span>
  </div>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>日付</th>
        <th>S&amp;P500</th>
        <th>前日差</th>
        <th>予想PER<span class="small">週次(Barron's)</span></th>
        <th>PER<span class="small">予想EPSから</span></th>
        <th>予想EPS<span class="small">週次</span></th>
{theo_headers}
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・S&amp;P500列の<span style="background:rgba(56,189,248,0.22); padding:1px 6px; color:#bae6fd">薄水色</span> = その月の最安値（前月最安値=攻防の分岐点）。<br>
    ・<b>2001年7月からの実績（接近149回）</b>: 前月最安値に接近（+1.5%以内）した場合、サポート成功（割れず+2%以上反発）<b>30%</b>、10日以内に割れたのは<b>62%</b>（もみ合い8%）。<br>
    ・<b>割れた後に「さらに5%以上沈む」確率は通常時の約1.5倍</b>（通常18%→割れ後28%、割れ109回の実績）。参考: 日経平均は約1.4倍（29%→41%）。<br>
    ・理論株価 = 予想EPS × 各PER。現値を挟む2セルに色が付く（水色=すぐ下、薄赤=すぐ上）。<br>
    ・PER基準: 14.62=コロナショック底(2020/3/16)、16.37=ハイテク株底入れ(2022/10/10)、22.82=コロナ直前(2020/3/18)、24=かなり高い、26=過去最高水準。<br>
    ・予想PER(Barron's)と予想EPSは週1回（金曜）更新。平日は直近値を引き継ぐため、「PER:予想EPSから」と「予想PER」が一致するのは週次更新日のみ（黄色表示）。<br>
    ・<a href="{price_url}" style="color:#60a5fa">S&P500株価</a> /
    <a href="{per_url}" style="color:#60a5fa">Barron's P/E &amp; Yields</a> /
    <a href="{eps_url}" style="color:#60a5fa">stock-marketdata 予想EPS</a>
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


SP_LOW_ATTR = ' class="month-low" title="この月の最安値（攻防の分岐点）"'


def generate_html(hist):
    dates = sorted(hist.keys(), reverse=True)

    theo_headers = "\n".join(
        f'        <th{" class=" + chr(34) + "sep" + chr(34) if i == 0 else ""}>PER{p:g}<span class="small">{note}</span></th>'
        for i, (p, note) in enumerate(zip(THEO_PERS, THEO_NOTES)))

    # 月ごとの最安値の日（前月最安値=攻防ラインの可視化）
    month_low_date = {}
    for d in dates:
        v = hist[d].get("SP500")
        if v is None:
            continue
        ym = d[:7]
        if ym not in month_low_date or v < hist[month_low_date[ym]]["SP500"]:
            month_low_date[ym] = d
    low_dates = set(month_low_date.values())

    rows = []
    for d in dates:
        r = hist[d]
        sp = r.get("SP500")
        eps = r.get("予想EPS")
        if sp is None:
            continue
        diff = r.get("前日差")
        sp_attr = SP_LOW_ATTR if d in low_dates else ""
        diff_s = "-" if diff is None else f'<span class="{"pos" if diff > 0 else ("neg" if diff < 0 else "")}">{diff:+,.2f}</span>'

        theos = [round(eps * p, 2) if eps else None for p in THEO_PERS]
        lower_i = upper_i = None
        if eps:
            for i2 in range(len(theos) - 1):
                if theos[i2] <= sp < theos[i2 + 1]:
                    lower_i, upper_i = i2, i2 + 1
                    break
            if lower_i is None:
                if sp < theos[0]:
                    upper_i = 0
                else:
                    lower_i = len(theos) - 1

        per_w = r.get("予想PER")
        per_c = r.get("PER計算")
        # 週次一致日（予想PERとPER計算がほぼ一致）
        weekly = per_w is not None and per_c is not None and abs(per_w - per_c) < 0.005
        w_attr = ' class="weekly"' if weekly else ''

        cells = [f'<td>{d.replace("-", "/")}</td>',
                 f'<td{sp_attr}>{sp:,.2f}</td>',
                 f'<td>{diff_s}</td>',
                 f'<td{w_attr}>{per_w:.2f}</td>' if per_w is not None else '<td>-</td>',
                 f'<td{w_attr}>{per_c:.2f}</td>' if per_c is not None else '<td>-</td>',
                 f'<td>{eps:,.2f}</td>' if eps else '<td>-</td>']
        for i, t in enumerate(theos):
            cls = []
            if i == 0:
                cls.append('sep')
            if i == lower_i:
                cls.append('below')
            elif i == upper_i:
                cls.append('above')
            attr = f' class="{" ".join(cls)}"' if cls else ''
            cells.append(f'<td{attr}>{t:,.2f}</td>' if t else '<td>-</td>')
        rows.append('      <tr>' + "".join(cells) + '</tr>')

    html = HTML_HEAD.format(
        latest_date=dates[0].replace("-", "/") if dates else "-",
        theo_headers=theo_headers,
        rows="\n".join(rows),
        price_url=PRICE_URL,
        per_url=PER_URL,
        eps_url=EPS_URL,
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
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)  # 他スクリプトとのgit競合・放置ロック対策
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    os.path.basename(HISTORY_JSON), ".gitignore",
                    "spriron_screen.py", "spriron_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update spriron report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/spriron.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("S&P500 理論株価 チェック開始")

    hist = load_history()
    errors = []

    # 1. 株価（日次）
    try:
        prices = fetch_prices()
        added = 0
        for d, close in prices.items():
            if d not in hist:
                hist[d] = {"SP500": close}
                added += 1
            elif hist[d].get("SP500") is None:
                hist[d]["SP500"] = close
        log(f"株価: {len(prices)}日分取得（新規{added}日）")
    except Exception as e:
        errors.append(f"株価: {e}")
        log(f"エラー: 株価取得失敗: {e}")

    # 2. 予想PER（週次）
    try:
        as_of, est = fetch_forward_per()
        log(f"予想PER: {est}（基準日 {as_of}）")
        if as_of and as_of in hist:
            hist[as_of]["予想PER"] = est
        elif as_of:
            hist[as_of] = {"SP500": None, "予想PER": est}
        else:
            # 基準日不明なら最新の株価日に紐付け
            latest = max((d for d in hist if hist[d].get("SP500")), default=None)
            if latest:
                hist[latest]["予想PER"] = est
    except Exception as e:
        errors.append(f"予想PER: {e}")
        log(f"エラー: 予想PER取得失敗: {e}")

    # 3. 予想EPS（週次）
    try:
        eps_map = fetch_forward_eps()
        log(f"予想EPS: {len(eps_map)}週分取得（最新 {max(eps_map)}: {eps_map[max(eps_map)]}）")
        for d, v in eps_map.items():
            if d in hist:
                hist[d]["予想EPS"] = v
            else:
                hist[d] = {"SP500": None, "予想EPS": v}
    except Exception as e:
        errors.append(f"予想EPS: {e}")
        log(f"エラー: 予想EPS取得失敗: {e}")

    if len(errors) == 3:
        log("全データ源の取得に失敗しました")
        sys.exit(1)

    recompute(hist)
    save_history(hist)
    log(f"履歴保存: {len(hist)}日分")

    generate_html(hist)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
