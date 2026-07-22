# -*- coding: utf-8 -*-
"""
信用評価損益率・信用取引残高 自動記録 → HTML出力 → GitHub Pages公開

データ源: https://nikkei225jp.com/data/sinyou.php
実データはページが読み込む dailyweek2.json（2009年からの週次データ）に
入っているため、それを直接取得してパースする。

- 週次データ（毎週金曜申し込み時点、翌週火曜頃更新）
- 履歴は shinyou_history.json に全期間蓄積
- shinyou.html を生成して git push（他スクリーナーと同方式）
- 色分け:
    売り残金額 > 800,000百万円 → 安心系（緑）
    買い残金額 > 5,000,000百万円 → 警戒系（赤）
    信用評価率: > -2 天井圏:調整警戒（ピンク） / -9〜-10 要警戒（オレンジ） /
                < -10 超警戒（赤太字）
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

PAGE_URL = "https://nikkei225jp.com/data/sinyou.php"
DATA_URL = "https://nikkei225jp.com/_data/_nfsDATA/DAY/dailyweek2.json"

HISTORY_JSON = os.path.join(SCRIPT_DIR, "shinyou_history.json")
REPORT_HTML = "shinyou.html"

SELL_ALERT = 800_000      # 売り残金額（百万円）: 超えたら安心系
BUY_ALERT = 5_000_000     # 買い残金額（百万円）: 超えたら警戒系

# 1570(日経レバ) 制度信用倍率 = 制度信用買残 / 制度信用売残（JPX週次PDF）
JPX_MARGIN_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"
JPX_BASE = "https://www.jpx.co.jp"
LEV_LOW = 1.0    # これ未満 = 売り方過多 → 踏み上げが起こりやすい（青）
LEV_HIGH = 5.0   # これ以上 = 信用買い過熱 → 急落時に追証連鎖の警戒（赤）

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": PAGE_URL,
}

FIELDS = ["売り残枚数", "売り残金額", "売り残変化", "買い残枚数", "買い残金額",
          "買い残変化", "信用倍率", "信用評価率"]


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# データ取得
# -----------------------------------------
def _fetch_requests(url, timeout):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def _fetch_curl(url, timeout):
    """requestsが403で弾かれる場合のフォールバック（curlはブラウザに近い挙動）"""
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
    """Windows純正HTTPスタック経由（ブラウザと同等に扱われやすい）"""
    ps = (
        f"$ProgressPreference='SilentlyContinue'; "
        f"$r = Invoke-WebRequest -UseBasicParsing -Uri '{url}' -TimeoutSec {timeout} "
        f"-UserAgent '{HEADERS['User-Agent']}' "
        f"-Headers @{{'Referer'='{PAGE_URL}'; 'Accept-Language'='ja,en-US;q=0.9'}}; "
        f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        f"$r.Content"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore",
                       timeout=timeout + 30)
    if r.returncode != 0 or not r.stdout or len(r.stdout) < 1000:
        err = (r.stderr or "")[:200]
        raise RuntimeError(f"PowerShell取得失敗 (len={len(r.stdout or '')}) {err}")
    return r.stdout


def fetch_with_retry(url, tries=3, timeout=60, wait=45):
    last_err = None
    methods = [("requests", _fetch_requests), ("curl", _fetch_curl)]
    if os.name == "nt":
        methods.append(("powershell", _fetch_powershell))
    for i in range(1, tries + 1):
        for method, fn in methods:
            try:
                return fn(url, timeout)
            except Exception as e:
                last_err = e
                log(f"  [{method}]方式は不可、次の方式へ ({i}/{tries})")
        if i < tries:
            time.sleep(wait)
    raise last_err


def fetch_weekly_data():
    """dailyweek2.json から (日付ISO, {列: 値}) のリストを返す（日付昇順）

    JSON行の構造: [タイムスタンプms, 日経平均, 出来高?,
                   売り残枚数, 売り残金額, 買い残枚数, 買い残金額,
                   信用評価率, 信用倍率, ...投資部門別データ...]
    信用データがない行（空文字）はスキップ
    """
    s = fetch_with_retry(DATA_URL)
    start = s.find('[')
    end = s.rfind(']')
    if start < 0 or end < 0:
        raise ValueError("データJSONの形式が想定と異なります")
    raw = json.loads(s[start:end + 1].replace('""', 'null'))

    out = []
    for row in raw:
        if len(row) < 9 or row[3] is None or row[3] == "":
            continue
        try:
            d = datetime.datetime.fromtimestamp(row[0] / 1000).date()
            rec = {
                "売り残枚数": float(row[3]),
                "売り残金額": float(row[4]),
                "買い残枚数": float(row[5]),
                "買い残金額": float(row[6]),
                "信用評価率": float(row[7]),
                "信用倍率": float(row[8]),
                "売り残変化": None,  # 後で前週比から計算
                "買い残変化": None,
            }
        except (TypeError, ValueError):
            continue
        out.append((d.isoformat(), rec))

    out.sort(key=lambda x: x[0])
    # 前週比（金額の変化率）を計算
    for i in range(1, len(out)):
        prev = out[i - 1][1]
        cur = out[i][1]
        if prev["売り残金額"]:
            cur["売り残変化"] = round((cur["売り残金額"] / prev["売り残金額"] - 1) * 100, 2)
        if prev["買い残金額"]:
            cur["買い残変化"] = round((cur["買い残金額"] / prev["買い残金額"] - 1) * 100, 2)
    return out


def fetch_lev_ratios(hist):
    """JPX「銘柄別信用取引週末残高」PDFから1570の制度信用倍率を取得して履歴に追記。

    毎週第3営業日頃に前週金曜分が公表される。ページには直近5週分のPDFリンクが
    あるので、履歴に「レバ倍率」が無い週だけダウンロードしてパースする。
    行の形式(p48付近): ＮＥＸＴ ＦＵＮＤＳ 日経平均レバレッジ・… 数値12個
    （売残計,前週比,買残計,前週比,一般売,前週比,制度売,前週比,一般買,前週比,制度買,前週比）
    """
    import pdfplumber

    added = 0
    try:
        html = fetch_with_retry(JPX_MARGIN_PAGE, tries=2, wait=10)
    except Exception as e:
        log(f"  JPX残高ページの取得に失敗（レバ倍率はスキップ）: {e}")
        return 0
    links = re.findall(r'href="([^"]*syumatsu(\d{8})\d{2}\.pdf)"', html)
    for path, ymd in links:
        d = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        if d in hist and "レバ倍率" in hist[d]:
            continue
        if d not in hist:
            continue  # 信用評価率のデータがまだ無い週は次回に回す
        url = JPX_BASE + path
        try:
            import tempfile
            r = requests.get(url, headers=HEADERS, timeout=60)
            r.raise_for_status()
            tmp = os.path.join(tempfile.gettempdir(), "_jpx_margin_tmp.pdf")
            with open(tmp, "wb") as f:
                f.write(r.content)
            sell = buy = None
            with pdfplumber.open(tmp) as pdf:
                for pno in range(30, len(pdf.pages)):
                    txt = pdf.pages[pno].extract_text() or ""
                    for line in txt.splitlines():
                        if "ＮＥＸＴ" in line and "日経平均レバレッジ" in line:
                            seg = line[line.rfind("券") + 1:]
                            seg = seg.replace("▲ ", "-").replace("▲", "-")
                            toks = re.findall(r"-?[\d,]+", seg)
                            if len(toks) >= 12:
                                sell = int(toks[6].replace(",", ""))
                                buy = int(toks[10].replace(",", ""))
                            break
                    if sell is not None:
                        break
            try:
                os.remove(tmp)
            except OSError:
                pass
            if sell and buy is not None:
                hist[d]["レバ倍率"] = round(buy / sell, 2)
                hist[d]["レバ制度売残"] = sell
                hist[d]["レバ制度買残"] = buy
                added += 1
                log(f"  レバ倍率 {d}: 制度買 {buy:,} / 制度売 {sell:,} = {buy/sell:.2f}")
            else:
                log(f"  レバ倍率 {d}: 1570の行が見つかりませんでした")
        except Exception as e:
            log(f"  レバ倍率 {d}: 取得失敗 {e}")
    return added


# -----------------------------------------
# 履歴（JSON）
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
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>信用評価損益率・信用残 - {latest_date}</title>
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
  .legend {{ display: flex; gap: 14px; margin-bottom: 14px; font-size: 0.78rem; color: #94a3b8; flex-wrap: wrap; }}
  .chip {{ padding: 2px 10px; border-radius: 10px; }}
  .table-wrap {{
    overflow: auto;
    max-height: calc(100vh - 210px);
    max-width: 980px;
  }}
  table {{ border-collapse: collapse; font-size: 0.84rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 12px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child {{ text-align: left; }}
  td {{
    padding: 6px 12px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{ text-align: left; color: #94a3b8; }}
  tr:hover td {{ background: #16213a; }}
  .latest-row td {{ background: rgba(30,64,175,0.18); font-weight: bold; }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
  /* 売り残金額 80万超 = 買い戻し期待（緑ハイライト） */
  td.sell-calm {{ background: rgba(34,197,94,0.22); color: #86efac; font-weight: bold; }}
  /* 買い残金額 500万超 = 警戒系（赤ハイライト） */
  td.buy-warn {{ background: rgba(220,38,38,0.25); color: #fca5a5; font-weight: bold; }}
  /* 信用評価率のアラート */
  .rate-top {{ background: rgba(236,72,153,0.25); color: #f9a8d4; font-weight: bold; }}  /* > -2 天井圏:調整警戒 */
  .rate-normal {{ color: #e2e8f0; }}
  .rate-caution {{ color: #fbbf24; font-weight: bold; }}       /* -9 〜 -10 要警戒 */
  .rate-danger {{ background: rgba(220,38,38,0.3); color: #f87171; font-weight: bold; }} /* < -10 超警戒 */
  /* 1570制度信用倍率: <1 踏み上げ期待（青） / >=5 追証連鎖警戒（赤） */
  td.lev-low {{ background: rgba(59,130,246,0.28); color: #93c5fd; font-weight: bold; }}
  td.lev-high {{ background: rgba(220,38,38,0.25); color: #fca5a5; font-weight: bold; }}
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
    <a href="riron.html" style="border-color:#7c3aed">日経理論株価</a>
    <a href="gaikoku.html" style="border-color:#2563eb">海外投資家</a>
    <a href="saitei.html" style="border-color:#2563eb">裁定取引</a>
    <a href="shutai.html" style="border-color:#2563eb">投資主体別</a>
    <a href="shinyou.html" class="active" style="border-color:#d97706">信用評価率</a>
    <a href="touraku.html" style="border-color:#d97706">騰落レシオ</a>
    <a href="karauri.html" style="border-color:#d97706">空売り比率</a>
    <a href="vix.html" style="border-color:#d97706">VIX温度計</a>
    <a href="flow.html" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="insider.html" style="border-color:#db2777">インサイダー売買</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="margin.html" style="border-color:#db2777">銘柄別信用倍率</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
    <a href="buffett.html" style="border-color:#db2777">バフェット</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>信用評価損益率・信用取引残高</h1>
  <p class="subtitle">最終更新: {updated} | 出所: nikkei225jp.com | 週次（金曜申込時点） | 枚数:千株 金額:百万円</p>
  <div class="legend">
    <span class="chip" style="background:rgba(34,197,94,0.22); color:#86efac">売り残金額 {sell_alert:,}超 = 買い戻し期待</span>
    <span class="chip" style="background:rgba(220,38,38,0.25); color:#fca5a5">買い残金額 {buy_alert:,}超 = 警戒</span>
    <span>|</span>
    <span class="rate-top" style="padding:1px 8px">評価率 &gt;-2 天井圏:調整警戒</span>
    <span class="rate-caution">-9〜-10 要警戒</span>
    <span class="rate-danger" style="padding:1px 8px">&lt;-10 超警戒</span>
    <span>|</span>
    <span class="chip" style="background:rgba(59,130,246,0.28); color:#93c5fd">レバ倍率 &lt;1 踏み上げ期待</span>
    <span class="chip" style="background:rgba(220,38,38,0.25); color:#fca5a5">&ge;5 追証連鎖警戒</span>
  </div>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>日付</th>
        <th>売り残枚数</th>
        <th>売り残金額</th>
        <th>売り残変化</th>
        <th>買い残枚数</th>
        <th>買い残金額</th>
        <th>買い残変化</th>
        <th>信用倍率</th>
        <th>信用評価率</th>
        <th>1570制度信用倍率</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・信用評価率 = 信用買いをしている人たちの平均含み損益率（%）。含み損が小さい（-2超）＝天井圏で調整警戒、-15%割れで底値圏とされる。<br>
    ・売り残が多い（80万超）＝将来の買い戻し（買い圧力）が積み上がっている状態。<br>
    ・1570制度信用倍率 = 日経レバETFの制度信用買残÷売残（JPX銘柄別信用取引週末残高、毎週第3営業日頃公表）。
    <span style="color:#93c5fd">1未満=売り方過多で踏み上げが起こりやすい</span>、
    <span style="color:#fca5a5">数値が大きく膨らむと急落時に追証連鎖のきっかけになりやすい</span>。セルにカーソルで残高内訳。<br>
    ・<a href="{src_url}" style="color:#60a5fa">nikkei225jp.com 信用評価損益率</a> /
    <a href="https://www.jpx.co.jp/markets/statistics-equities/margin/05.html" style="color:#60a5fa">JPX 銘柄別信用取引週末残高</a>
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def fmt_int(v):
    return f"{v:,.0f}" if v is not None else "-"


def fmt_pct(v, signed=True):
    if v is None:
        return "-"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+.2f}%</span>' if signed else f"{v:.2f}"


def rate_class(v):
    if v is None:
        return "rate-normal"
    if v > -2:
        return "rate-top"
    if v < -10:
        return "rate-danger"
    if v <= -9:
        return "rate-caution"
    return "rate-normal"


def lev_cell(r):
    """1570制度信用倍率のセル（<1 青=踏み上げ期待 / >=5 赤=追証連鎖警戒）"""
    v = r.get("レバ倍率")
    if v is None:
        return "<td>-</td>"
    cls = ""
    if v < LEV_LOW:
        cls = ' class="lev-low"'
    elif v >= LEV_HIGH:
        cls = ' class="lev-high"'
    tip = ""
    if r.get("レバ制度売残"):
        tip = f' title="制度買残 {r["レバ制度買残"]:,} / 制度売残 {r["レバ制度売残"]:,}（口）"'
    return f"<td{cls}{tip}>{v:.2f}</td>"


def generate_html(hist):
    dates = sorted(hist.keys(), reverse=True)  # 最新が上
    rows = []
    for i, d in enumerate(dates):
        r = hist[d]
        cls = ' class="latest-row"' if i == 0 else ""
        sell_cls = ' class="sell-calm"' if (r.get("売り残金額") or 0) > SELL_ALERT else ""
        buy_cls = ' class="buy-warn"' if (r.get("買い残金額") or 0) > BUY_ALERT else ""
        rc = rate_class(r.get("信用評価率"))
        rate_v = r.get("信用評価率")
        rate_s = f"{rate_v:+.2f}" if rate_v is not None else "-"
        bairitsu = r.get("信用倍率")
        rows.append(
            f'      <tr{cls}><td>{d.replace("-", "/")}</td>'
            f'<td>{fmt_int(r.get("売り残枚数"))}</td>'
            f'<td{sell_cls}>{fmt_int(r.get("売り残金額"))}</td>'
            f'<td>{fmt_pct(r.get("売り残変化"))}</td>'
            f'<td>{fmt_int(r.get("買い残枚数"))}</td>'
            f'<td{buy_cls}>{fmt_int(r.get("買い残金額"))}</td>'
            f'<td>{fmt_pct(r.get("買い残変化"))}</td>'
            f'<td>{bairitsu if bairitsu is not None else "-"}</td>'
            f'<td class="{rc}">{rate_s}</td>'
            f'{lev_cell(r)}</tr>'
        )

    html = HTML_TEMPLATE.format(
        latest_date=dates[0].replace("-", "/") if dates else "-",
        rows="\n".join(rows),
        sell_alert=SELL_ALERT,
        buy_alert=BUY_ALERT,
        src_url=PAGE_URL,
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
                    "shinyou_screen.py", "shinyou_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update shinyou report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/shinyou.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("信用評価損益率 チェック開始")

    hist = load_history()

    try:
        weekly = fetch_weekly_data()
    except Exception as e:
        log(f"エラー: データの取得に失敗しました: {e}")
        sys.exit(1)

    log(f"サイト上のデータ: {len(weekly)}週分 ({weekly[0][0]} ～ {weekly[-1][0]})")

    added = 0
    for d, rec in weekly:
        if d in hist:
            continue
        hist[d] = rec
        added += 1
        log(f"追記: {d}  評価率={rec['信用評価率']:+.2f}% 売り残={rec['売り残金額']:,.0f} 買い残={rec['買い残金額']:,.0f}")

    lev_added = fetch_lev_ratios(hist)

    if added or lev_added:
        save_history(hist)
        log(f"履歴保存: {len(hist)}週分（レバ倍率 +{lev_added}週）")
    else:
        log("新しいデータはありませんでした")

    if not hist:
        log("データがないためHTMLは生成しません")
        return

    generate_html(hist)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
