# -*- coding: utf-8 -*-
"""
海外投資家 現物+先物 売買動向 自動記録 → HTML出力 → GitHub Pages公開

- 現物（二市場売買差引）: nikkei225jp.com の dailyweek2.json（shutaiと同じ、col12=海外）
- 先物: JPX「投資部門別取引状況」週間CSV
  https://www.jpx.co.jp/markets/statistics-derivatives/sector/index.html
  新フォーマット(2026-04-23掲載分以降): Tousi_DV_W_YYYYMMDD_YYYYMMDD.csv
  帳票種別: 301=日経225先物, 313=日経225mini, 314=TOPIX先物, 316=ミニTOPIX先物
  投資部門コード60=海外投資家, 数量金額区分2=金額, 「売-差引 Balance」列が買い越し額
  ※CSVの差引は売側/買側の一方にのみ記載されるので、買-売の符号で統一する

- 列: 週 / 現物(二市場) / 日経225 / 日経225mini / TOPIX / TOPIXmini / 4先物計 / 現物先物合算
- 最新が上。単位: 百万円（元データは円）
- 履歴は gaikoku_history.json に蓄積
"""

import os
import re
import sys
import csv
import io
import json
import time
import subprocess
import datetime

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SHUTAI_JSON_URL = "https://nikkei225jp.com/_data/_nfsDATA/DAY/dailyweek2.json"
SHUTAI_PAGE = "https://nikkei225jp.com/data/shutai.php"
JPX_INDEX = "https://www.jpx.co.jp/markets/statistics-derivatives/sector/index.html"
JPX_ARCHIVE_2026 = "https://www.jpx.co.jp/markets/statistics-derivatives/sector/00-archives-00.html"
JPX_BASE = "https://www.jpx.co.jp"

HISTORY_JSON = os.path.join(SCRIPT_DIR, "gaikoku_history.json")
REPORT_HTML = "gaikoku.html"

# 帳票種別 → 列名
PRODUCTS = {"301": "日経225", "313": "日経225mini", "314": "TOPIX", "316": "TOPIXmini"}
INVESTOR_FOREIGN = "60"  # 海外投資家

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# 取得（requests → curl → PowerShell）
# -----------------------------------------
def _fetch_requests(url, timeout, referer=None):
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    r = requests.get(url, headers=h, timeout=timeout)
    r.raise_for_status()
    return r.content


def _fetch_curl(url, timeout, referer=None):
    cmd = ["curl", "-sL", "--max-time", str(timeout),
           "-A", HEADERS["User-Agent"], "--compressed"]
    if referer:
        cmd += ["-e", referer]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout or len(r.stdout) < 500:
        raise RuntimeError(f"curl取得失敗 (rc={r.returncode}, len={len(r.stdout or b'')})")
    return r.stdout


def _fetch_powershell(url, timeout, referer=None):
    ref = f"'Referer'='{referer}'; " if referer else ""
    ps = (
        f"$ProgressPreference='SilentlyContinue'; "
        f"$r = Invoke-WebRequest -UseBasicParsing -Uri '{url}' -TimeoutSec {timeout} "
        f"-Headers @{{{ref}'Accept-Language'='ja,en-US;q=0.9'}} "
        f"-UserAgent '{HEADERS['User-Agent']}'; "
        f"$b = $r.RawContentStream.ToArray(); "
        f"[Convert]::ToBase64String($b)"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=timeout + 30)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"powershell取得失敗 (rc={r.returncode})")
    import base64
    data = base64.b64decode(r.stdout.strip())
    if len(data) < 500:
        raise RuntimeError(f"powershell取得失敗 (len={len(data)})")
    return data


def fetch_bytes(url, tries=3, timeout=60, wait=30, referer=None):
    methods = [("requests", _fetch_requests), ("curl", _fetch_curl)]
    if os.name == "nt":
        methods.append(("powershell", _fetch_powershell))
    last_err = None
    for i in range(1, tries + 1):
        for name, fn in methods:
            try:
                return fn(url, timeout, referer)
            except Exception as e:
                last_err = e
                log(f"  [{name}]方式は不可、次の方式へ ({i}/{tries})")
        if i < tries:
            time.sleep(wait)
    raise last_err


# -----------------------------------------
# 現物（海外・二市場差引）: dailyweek2.json col12
# -----------------------------------------
def fetch_genbutsu():
    """{金曜日付ISO: 海外差引(円)} を返す"""
    data = fetch_bytes(SHUTAI_JSON_URL, referer=SHUTAI_PAGE).decode("utf-8", errors="ignore")
    start = data.find('[')
    end = data.rfind(']')
    raw = json.loads(data[start:end + 1].replace('""', 'null'))
    out = {}
    for row in raw:
        if len(row) < 24 or row[12] is None:
            continue
        d = datetime.datetime.fromtimestamp(row[0] / 1000).date()
        out[d.isoformat()] = float(row[12]) * 1_000_000  # 百万円→円
    return out


# -----------------------------------------
# 先物: JPX週間CSV（新フォーマットのみ）
# -----------------------------------------
def list_jpx_csvs():
    """(週末日付ISO, 週開始ISO, CSVフルURL) のリスト（新フォーマットのみ、日付昇順）"""
    links = {}
    for page in (JPX_INDEX, JPX_ARCHIVE_2026):
        try:
            html = fetch_bytes(page).decode("utf-8", errors="ignore")
        except Exception as e:
            log(f"  一覧取得失敗 ({page}): {e}")
            continue
        for m in re.finditer(r'href="([^"]*Tousi_DV_W_(\d{8})_(\d{8})\.csv)"', html):
            path, d_from, d_to = m.groups()
            iso_to = f"{d_to[:4]}-{d_to[4:6]}-{d_to[6:]}"
            iso_from = f"{d_from[:4]}-{d_from[4:6]}-{d_from[6:]}"
            url = path if path.startswith("http") else JPX_BASE + path
            links[iso_to] = (iso_from, url)
    return sorted((k, v[0], v[1]) for k, v in links.items())


def parse_jpx_csv(data):
    """CSVから海外投資家の4先物の差引金額(円)を返す {列名: 差引}"""
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("CSVのエンコーディング不明")

    result = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 11 or row[0] not in PRODUCTS:
            continue
        # 新フォーマット: [帳票種別, サイクル, 年月週, 自, 至, 投資部門, 数量金額区分, 売, 売-差引, 買, 買-差引, 合計]
        if row[5].strip() != INVESTOR_FOREIGN or row[6].strip() != "2":
            continue
        try:
            sales = float(row[7] or 0)
            buys = float(row[9] or 0)
        except ValueError:
            continue
        # 差引 = 買 - 売（買い越しがプラス）
        result[PRODUCTS[row[0]]] = buys - sales

    missing = [v for v in PRODUCTS.values() if v not in result]
    if missing:
        raise ValueError(f"先物データ不足: {missing}")
    return result


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
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>海外投資家 現物+先物 - {latest_date}</title>
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
  .table-wrap {{
    overflow: auto;
    max-height: calc(100vh - 190px);
    max-width: 1150px;
  }}
  table {{ border-collapse: collapse; font-size: 0.84rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 12px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child {{ text-align: left; }}
  thead th.divider {{ border-left: 2px solid #334155; }}
  td.divider {{ border-left: 2px solid #334155; }}
  td {{
    padding: 6px 12px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{ text-align: left; color: #94a3b8; }}
  tr:hover td {{ background: #16213a; }}
  .latest-row td {{ background: rgba(30,64,175,0.18); font-weight: bold; }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
  .total {{ font-weight: bold; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.8; }}
</style>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="minervini_report_v2.html">米国株 (Minervini)</a>
    <a href="haitou.html">日本株 (配当)</a>
    <a href="jpminervini.html">日本株 (Minervini)</a>
    <a href="saitei.html">裁定取引</a>
    <a href="totan.html">日銀利上げ確率</a>
    <a href="fedwatch.html">FRB利上げ確率</a>
    <a href="kinri.html">金利と為替</a>
    <a href="vix.html">VIX温度計</a>
    <a href="daikin.html">売買代金</a>
    <a href="shinyou.html">信用評価率</a>
    <a href="margin.html">銘柄別信用倍率</a>
    <a href="shutai.html">投資主体別</a>
    <a href="gaikoku.html" class="active">海外投資家</a>
    <a href="touraku.html">騰落レシオ</a>
    <a href="karauri.html">空売り比率</a>
    <a href="riron.html">日経理論株価</a>
    <a href="spriron.html">SP500理論株価</a>
    <a href="flow.html">資金フロー</a>
    <a href="map.html">デッキの見方</a>
  </nav>
  <h1>海外投資家 現物+先物 売買動向</h1>
  <p class="subtitle">最終更新: {updated} | 出所: JPX投資部門別取引状況・nikkei225jp.com | 週次 | 単位: 百万円（プラス=買い越し）</p>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>週</th>
        <th>現物(二市場)</th>
        <th class="divider">日経225</th>
        <th>日経225mini</th>
        <th>TOPIX</th>
        <th>TOPIXmini</th>
        <th>4先物計</th>
        <th class="divider">現物先物合算</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・現物 = 東証・名証二市場の海外投資家売買差引（投資主体別売買動向と同値）。<br>
    ・先物 = JPX投資部門別取引状況（週間）の海外投資家 取引金額差引。日経225=ラージ、TOPIXmini=ミニTOPIX先物。<br>
    ・JPX先物データは毎週第4営業日（通常木曜）15:30頃公表。現物より数日遅れるため、最新週は現物のみ先行表示されることがある。<br>
    ・<a href="{jpx_url}" style="color:#60a5fa">JPX 投資部門別取引状況</a> / <a href="{shutai_url}" style="color:#60a5fa">nikkei225jp.com 投資主体別</a>
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def fmt_mm(v):
    """円 → 百万円表示"""
    if v is None:
        return "-"
    mm = v / 1_000_000
    cls = "pos" if mm > 0 else ("neg" if mm < 0 else "")
    return f'<span class="{cls}">{mm:+,.0f}</span>'


def generate_html(hist):
    dates = sorted(hist.keys(), reverse=True)
    rows = []
    for i, d in enumerate(dates):
        r = hist[d]
        cls = ' class="latest-row"' if i == 0 else ""
        futs = [r.get("日経225"), r.get("日経225mini"), r.get("TOPIX"), r.get("TOPIXmini")]
        fut_total = sum(f for f in futs if f is not None) if any(f is not None for f in futs) else None
        gen = r.get("現物")
        total = (gen + fut_total) if (gen is not None and fut_total is not None) else None
        label = r.get("週ラベル") or d.replace("-", "/")
        rows.append(
            f'      <tr{cls}><td>{label}</td>'
            f'<td>{fmt_mm(gen)}</td>'
            f'<td class="divider">{fmt_mm(futs[0])}</td>'
            f'<td>{fmt_mm(futs[1])}</td>'
            f'<td>{fmt_mm(futs[2])}</td>'
            f'<td>{fmt_mm(futs[3])}</td>'
            f'<td class="total">{fmt_mm(fut_total)}</td>'
            f'<td class="divider total">{fmt_mm(total)}</td></tr>'
        )

    html = HTML_TEMPLATE.format(
        latest_date=dates[0].replace("-", "/") if dates else "-",
        rows="\n".join(rows),
        jpx_url=JPX_INDEX,
        shutai_url="https://nikkei225jp.com/data/shutai.php",
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
                    "gaikoku_screen.py", "gaikoku_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update gaikoku report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/gaikoku.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("海外投資家 現物+先物 チェック開始")

    hist = load_history()

    # 1) 現物（全週）
    try:
        genbutsu = fetch_genbutsu()
        log(f"現物データ: {len(genbutsu)}週分")
    except Exception as e:
        log(f"エラー: 現物データの取得に失敗: {e}")
        genbutsu = {}

    # 2) 先物CSV一覧（新フォーマット分）
    try:
        csvs = list_jpx_csvs()
        log(f"JPX先物CSV: {len(csvs)}週分 ({csvs[0][0]} ～ {csvs[-1][0]})" if csvs else "JPX先物CSV: 0件")
    except Exception as e:
        log(f"エラー: JPX一覧の取得に失敗: {e}")
        csvs = []

    added = 0

    # 先物: 未記録の週だけCSVを取得
    for week_end, week_start, url in csvs:
        if week_end in hist and hist[week_end].get("日経225") is not None:
            continue
        try:
            data = fetch_bytes(url, referer=JPX_INDEX)
            futs = parse_jpx_csv(data)
        except Exception as e:
            log(f"エラー: {week_end} のCSV取得/解析に失敗: {e}")
            continue
        rec = hist.setdefault(week_end, {})
        rec.update(futs)
        rec["週ラベル"] = f'{week_start.replace("-", "/")}~{week_end[5:].replace("-", "/")}'
        added += 1
        log(f"先物追記: {week_end}  225={futs['日経225']/1e6:+,.0f} mini={futs['日経225mini']/1e6:+,.0f} "
            f"TPX={futs['TOPIX']/1e6:+,.0f} TPXm={futs['TOPIXmini']/1e6:+,.0f} (百万円)")
        time.sleep(2)

    # 現物: 履歴にある週 + 現物が取れている週を突き合わせ
    for week_end, val in genbutsu.items():
        if week_end < "2026-04-13":
            continue  # 先物の新フォーマット開始前は対象外
        rec = hist.get(week_end)
        if rec is None:
            rec = hist.setdefault(week_end, {})
            rec["週ラベル"] = week_end.replace("-", "/")
        if rec.get("現物") != val:
            rec["現物"] = val
            added += 1

    if added:
        save_history(hist)
        log(f"履歴保存: {len(hist)}週分（{added}項目更新）")
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
