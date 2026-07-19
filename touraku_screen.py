# -*- coding: utf-8 -*-
"""
騰落レシオ 自動記録 → HTML出力 → GitHub Pages公開

データ源: https://nikkei225jp.com/data/touraku.php
実データは daily2year.json（直近2年の日次データ）。
行構造: [ts, 日経平均, 出来高(百万株), null, null, 値上がり銘柄数, 値下がり銘柄数,
         25日騰落レシオ, ...]

- 騰落レシオ(25/15/10/6日) = 期間内値上がり銘柄数合計 ÷ 値下がり銘柄数合計 × 100
  （サイト掲載の25日値と自前計算が一致することを検証済み。15/10/6日は自前計算）
- 日次。履歴は touraku_history.json に全期間蓄積（エクセル過去分も初回取込）
- touraku.html を生成して git push
- 列: 日付 / 日経平均 / 前日比 / 出来高 / 値上がり / 値下がり / レシオ25 / 15 / 10 / 6
- 最新が上
- 色: レシオが低いほど青が濃く（売られすぎ=自律反発期待）、高いほど朱色が濃く（過熱）
  50以下は特別ハイライト（自律反発期待大）
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

PAGE_URL = "https://nikkei225jp.com/data/touraku.php"
DATA_URL = "https://nikkei225jp.com/_data/_nfsDATA/DAY/daily2year.json"

HISTORY_JSON = os.path.join(SCRIPT_DIR, "touraku_history.json")
REPORT_HTML = "touraku.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": PAGE_URL,
}

RATIO_PERIODS = [25, 15, 10, 6]


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
    """{日付ISO: {日経平均, 出来高, 値上がり, 値下がり}} を返す"""
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
        if len(row) < 8 or row[1] is None or row[5] is None or row[6] is None:
            continue
        try:
            d = datetime.datetime.fromtimestamp(row[0] / 1000).date()
            out[d.isoformat()] = {
                "日経平均": float(row[1]),
                "出来高": float(row[2]) if row[2] is not None else None,
                "値上がり": int(row[5]),
                "値下がり": int(row[6]),
            }
        except (TypeError, ValueError):
            continue
    return out


# -----------------------------------------
# 騰落レシオ計算（検証済み: サイト25日値と一致）
# -----------------------------------------
def compute_ratios(hist):
    """履歴全体に対し、各日の25/15/10/6日レシオと前日比を計算して埋める"""
    dates = sorted(hist.keys())
    for i, d in enumerate(dates):
        rec = hist[d]
        # 前日比
        if i > 0:
            prev = hist[dates[i - 1]].get("日経平均")
            cur = rec.get("日経平均")
            rec["前日比"] = round(cur - prev, 2) if (prev is not None and cur is not None) else None
        else:
            rec["前日比"] = None
        # レシオ
        for n in RATIO_PERIODS:
            key = f"レシオ{n}"
            if i + 1 >= n:
                window = dates[i - n + 1:i + 1]
                ups = sum(hist[x]["値上がり"] for x in window if hist[x].get("値上がり") is not None)
                downs = sum(hist[x]["値下がり"] for x in window if hist[x].get("値下がり") is not None)
                rec[key] = round(ups / downs * 100, 2) if downs else None
            else:
                rec.setdefault(key, None)  # エクセル取込値があれば残す


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
<title>騰落レシオ - {latest_date}</title>
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
  .legend {{ display: flex; gap: 10px; margin-bottom: 14px; font-size: 0.78rem; color: #94a3b8; flex-wrap: wrap; align-items: center; }}
  .chip {{ padding: 2px 10px; border-radius: 10px; }}
  .table-wrap {{
    overflow: auto;
    max-height: calc(100vh - 210px);
    max-width: 1080px;
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
  tr:hover td {{ filter: brightness(1.25); }}
  .latest-row td:first-child {{ color: #bfdbfe; font-weight: bold; }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
  td.rebound {{ outline: 2px solid #38bdf8; outline-offset: -2px; font-weight: bold; }}
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
    <a href="daikin.html">売買代金</a>
    <a href="shinyou.html">信用評価率</a>
    <a href="shutai.html">投資主体別</a>
    <a href="gaikoku.html">海外投資家</a>
    <a href="touraku.html" class="active">騰落レシオ</a>
    <a href="karauri.html">空売り比率</a>
    <a href="riron.html">日経理論株価</a>
    <a href="spriron.html">SP500理論株価</a>
    <a href="flow.html">資金フロー</a>
    <a href="map.html">デッキの見方</a>
  </nav>
  <h1>騰落レシオ</h1>
  <p class="subtitle">最終更新: {updated} | 出所: nikkei225jp.com | 日次 | 出来高:百万株</p>
  <div class="legend">
    <span class="chip" style="background:rgba(2,132,199,0.55); color:#e0f2fe">低い=売られすぎ(青)</span>
    <span>⇔</span>
    <span class="chip" style="background:rgba(234,88,12,0.55); color:#ffedd5">高い=過熱(朱)</span>
    <span>|</span>
    <span class="chip" style="outline:2px solid #38bdf8; color:#7dd3fc">50以下 = 自律反発期待大</span>
    <span>| 目安: 70以下 売られすぎ / 120以上 過熱</span>
  </div>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>日付</th>
        <th>日経平均</th>
        <th>前日比</th>
        <th>出来高</th>
        <th>値上がり</th>
        <th>値下がり</th>
        <th>レシオ25日</th>
        <th>15日</th>
        <th>10日</th>
        <th>6日</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・騰落レシオ = 期間内の値上がり銘柄数合計 ÷ 値下がり銘柄数合計 × 100（プライム市場）。<br>
    ・100が中立。120以上は買われすぎ（過熱）、70以下は売られすぎ、50近辺は歴史的な底値圏で自律反発期待が高い。<br>
    ・<a href="{src_url}" style="color:#60a5fa">nikkei225jp.com 騰落レシオ</a>
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def ratio_style(v):
    """レシオ値 → グラデーション背景（低=青濃、高=朱濃、100付近=無色）"""
    if v is None:
        return "", "-"
    txt = f"{v:.2f}"
    # 50以下: 特別ハイライト（青最濃 + 枠）
    cls = ' class="rebound"' if v <= 50 else ""
    if v < 100:
        # 100→50 で青の濃さ 0→0.65
        alpha = min(0.65, (100 - v) / 50 * 0.65)
        style = f' style="background:rgba(2,132,199,{alpha:.2f})"'
    elif v > 100:
        # 100→150 で朱の濃さ 0→0.65
        alpha = min(0.65, (v - 100) / 50 * 0.65)
        style = f' style="background:rgba(234,88,12,{alpha:.2f})"'
    else:
        style = ""
    return cls + style, txt


def fmt_num(v, dec=0):
    if v is None:
        return "-"
    return f"{v:,.{dec}f}"


def fmt_diff(v):
    if v is None:
        return "-"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+,.2f}</span>'


def generate_html(hist):
    dates = sorted(hist.keys(), reverse=True)  # 最新が上
    rows = []
    for i, d in enumerate(dates):
        r = hist[d]
        row_cls = ' class="latest-row"' if i == 0 else ""
        cells = [f'<td>{d.replace("-", "/")}</td>',
                 f'<td>{fmt_num(r.get("日経平均"), 2)}</td>',
                 f'<td>{fmt_diff(r.get("前日比"))}</td>',
                 f'<td>{fmt_num(r.get("出来高"))}</td>',
                 f'<td>{fmt_num(r.get("値上がり"))}</td>',
                 f'<td>{fmt_num(r.get("値下がり"))}</td>']
        for n in RATIO_PERIODS:
            attr, txt = ratio_style(r.get(f"レシオ{n}"))
            cells.append(f'<td{attr}>{txt}</td>')
        rows.append(f'      <tr{row_cls}>' + "".join(cells) + '</tr>')

    html = HTML_TEMPLATE.format(
        latest_date=dates[0].replace("-", "/") if dates else "-",
        rows="\n".join(rows),
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
                    "touraku_screen.py", "touraku_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update touraku report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/touraku.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("騰落レシオ チェック開始")

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
        compute_ratios(hist)
        save_history(hist)
        latest = max(hist)
        r = hist[latest]
        log(f"追記: {added}日分（計 {len(hist)}日） 最新 {latest}: 25日={r.get('レシオ25')}")
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
