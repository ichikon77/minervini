# -*- coding: utf-8 -*-
"""
投資主体別売買動向 自動記録 → HTML出力 → GitHub Pages公開

データ源: https://nikkei225jp.com/data/shutai.php
実データは dailyweek2.json（信用評価損益率と同じファイル）に入っている。
行構造: [ts, 日経平均, 出来高, ...(信用データ)...,
  9:証券自己, 11:個人, 12:海外, 14:投資信託, 20:信託銀行, 22:個人(現金), 23:個人(信用)]
（列マッピングはユーザーのエクセルと複数日照合して確定済み）

- 週次データ。履歴は shutai_history.json に全期間蓄積
- shutai.html を生成して git push
- 列: 日付 / 日経平均 / 変化(週) / 海外 / 個人 / 個人(現金) / 個人(信用) /
      投資信託 / 信託銀行 / 証券自己（単位: 百万円）
- 最新が上
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

PAGE_URL = "https://nikkei225jp.com/data/shutai.php"
DATA_URL = "https://nikkei225jp.com/_data/_nfsDATA/DAY/dailyweek2.json"

HISTORY_JSON = os.path.join(SCRIPT_DIR, "shutai_history.json")
REPORT_HTML = "shutai.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": PAGE_URL,
}

COLS = ["海外", "個人", "個人現金", "個人信用", "投資信託", "信託銀行", "証券自己"]


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# データ取得（requests → curl → PowerShell の3段フォールバック）
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
    """Windows専用: Invoke-WebRequestはTLS指紋がブラウザ扱いで403を回避できる"""
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


def fetch_weekly_data():
    """(日付ISO, {列: 値}) のリスト（日付昇順）。日経平均と週次変化率も含む"""
    s = fetch_with_retry(DATA_URL)
    start = s.find('[')
    end = s.rfind(']')
    if start < 0 or end < 0:
        raise ValueError("データJSONの形式が想定と異なります")
    raw = json.loads(s[start:end + 1].replace('""', 'null'))

    out = []
    for row in raw:
        if len(row) < 24 or row[3] is None or row[9] is None:
            continue
        try:
            d = datetime.datetime.fromtimestamp(row[0] / 1000).date()
            rec = {
                "日経平均": float(row[1]),
                "海外": float(row[12]),
                "個人": float(row[11]),
                "個人現金": float(row[22]),
                "個人信用": float(row[23]),
                "投資信託": float(row[14]),
                "信託銀行": float(row[20]),
                "証券自己": float(row[9]),
                "変化週": None,
            }
        except (TypeError, ValueError):
            continue
        out.append((d.isoformat(), rec))

    out.sort(key=lambda x: x[0])
    for i in range(1, len(out)):
        prev = out[i - 1][1]["日経平均"]
        cur = out[i][1]
        if prev:
            cur["変化週"] = round((cur["日経平均"] / prev - 1) * 100, 2)
    return out


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
<title>投資主体別売買動向 - {latest_date}</title>
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
    max-width: 1120px;
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
  .nk {{ color: #e2e8f0; }}
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
    <a href="daikin.html">売買代金</a>
    <a href="shinyou.html">信用評価率</a>
    <a href="shutai.html" class="active">投資主体別</a>
    <a href="gaikoku.html">海外投資家</a>
    <a href="touraku.html">騰落レシオ</a>
    <a href="karauri.html">空売り比率</a>
    <a href="riron.html">日経理論株価</a>
    <a href="map.html">デッキの見方</a>
  </nav>
  <h1>投資主体別売買動向</h1>
  <p class="subtitle">最終更新: {updated} | 出所: nikkei225jp.com | 週次 | 単位: 百万円（プラス=買い越し）</p>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>日付</th>
        <th>日経平均</th>
        <th>変化(週)</th>
        <th>海外</th>
        <th>個人</th>
        <th>個人(現金)</th>
        <th>個人(信用)</th>
        <th>投資信託</th>
        <th>信託銀行</th>
        <th>証券自己</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・プラス=買い越し（緑）、マイナス=売り越し（赤）。<br>
    ・信託銀行はGPIF等の年金基金の売買を反映するとされる。<br>
    ・<a href="{src_url}" style="color:#60a5fa">nikkei225jp.com 投資主体別売買動向</a>
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def fmt_amt(v):
    if v is None:
        return "-"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+,.0f}</span>'


def fmt_chg(v):
    if v is None:
        return "-"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    arrow = "▲" if v > 0 else ("▼" if v < 0 else "")
    return f'<span class="{cls}">{arrow}{abs(v):.2f}%</span>'


def generate_html(hist):
    dates = sorted(hist.keys(), reverse=True)  # 最新が上
    rows = []
    for i, d in enumerate(dates):
        r = hist[d]
        cls = ' class="latest-row"' if i == 0 else ""
        nk = r.get("日経平均")
        rows.append(
            f'      <tr{cls}><td>{d.replace("-", "/")}</td>'
            f'<td class="nk">{nk:,.2f}</td>'
            f'<td>{fmt_chg(r.get("変化週"))}</td>'
            f'<td>{fmt_amt(r.get("海外"))}</td>'
            f'<td>{fmt_amt(r.get("個人"))}</td>'
            f'<td>{fmt_amt(r.get("個人現金"))}</td>'
            f'<td>{fmt_amt(r.get("個人信用"))}</td>'
            f'<td>{fmt_amt(r.get("投資信託"))}</td>'
            f'<td>{fmt_amt(r.get("信託銀行"))}</td>'
            f'<td>{fmt_amt(r.get("証券自己"))}</td></tr>'
        )

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
                    "shutai_screen.py", "shutai_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update shutai report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/shutai.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("投資主体別売買動向 チェック開始")

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

    if added:
        save_history(hist)
        latest = weekly[-1]
        log(f"追記: {added}週分（計 {len(hist)}週） 最新 {latest[0]}: 海外={latest[1]['海外']:+,.0f} 個人={latest[1]['個人']:+,.0f}")
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
