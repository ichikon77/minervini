# -*- coding: utf-8 -*-
"""
空売り比率 自動記録 → HTML出力 → GitHub Pages公開

データ源: https://nikkei225jp.com/data/karauri.php
実データは daily2year.json（騰落レシオと同じファイル、直近2年の日次）。
行構造: [ts, 日経平均, 出来高(百万株), ..., 22:空売り比率(価格規制あり),
         24:空売り比率(価格規制なし), ...]
合計 = 規制あり + 規制なし（エクセルと複数日照合で確定済み）

- 日次。履歴は karauri_history.json に全期間蓄積（エクセル過去分も初回取込）
- karauri.html を生成して git push
- 列: 日付 / 日経平均 / 前日比 / 出来高 / 空売り比率合計 / 規制あり / 規制なし
- 最新が上
- 色: 合計40以上=薄赤、44以上=濃赤（空売り過熱 → 買い戻しによる反発期待）
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

PAGE_URL = "https://nikkei225jp.com/data/karauri.php"
DATA_URL = "https://nikkei225jp.com/_data/_nfsDATA/DAY/daily2year.json"

HISTORY_JSON = os.path.join(SCRIPT_DIR, "karauri_history.json")
REPORT_HTML = "karauri.html"

WARN_LIGHT = 40.0   # 合計がこれ以上 → 薄赤
WARN_HEAVY = 44.0   # 合計がこれ以上 → 濃赤

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
    """{日付ISO: {日経平均, 出来高, 規制あり, 規制なし, 合計}} を返す"""
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
        if len(row) < 25 or row[1] is None or row[22] is None or row[24] is None:
            continue
        try:
            d = datetime.datetime.fromtimestamp(row[0] / 1000).date()
            ari = float(row[22])
            nashi = float(row[24])
            out[d.isoformat()] = {
                "日経平均": float(row[1]),
                "出来高": float(row[2]) if row[2] is not None else None,
                "合計": round(ari + nashi, 1),
                "規制あり": ari,
                "規制なし": nashi,
            }
        except (TypeError, ValueError):
            continue
    return out


def compute_monthly(hist):
    """月別の平均値を計算 {月(YYYY-MM): {日数, 日経平均, 合計, 規制あり, 規制なし}}（各値は月内平均）"""
    buckets = {}
    for d, r in hist.items():
        ym = d[:7]
        b = buckets.setdefault(ym, {"n": 0, "日経平均": 0.0, "合計": 0.0, "規制あり": 0.0, "規制なし": 0.0})
        b["n"] += 1
        b["日経平均"] += r.get("日経平均") or 0.0
        b["合計"] += r.get("合計") or 0.0
        b["規制あり"] += r.get("規制あり") or 0.0
        b["規制なし"] += r.get("規制なし") or 0.0
    out = {}
    for ym, b in buckets.items():
        n = b["n"] or 1
        out[ym] = {
            "n": b["n"],
            "日経平均": round(b["日経平均"] / n, 2),
            "合計": round(b["合計"] / n, 1),
            "規制あり": round(b["規制あり"] / n, 1),
            "規制なし": round(b["規制なし"] / n, 1),
        }
    return out


def compute_diffs(hist):
    """前日比を全期間計算"""
    dates = sorted(hist.keys())
    for i, d in enumerate(dates):
        rec = hist[d]
        if i > 0:
            prev = hist[dates[i - 1]].get("日経平均")
            cur = rec.get("日経平均")
            rec["前日比"] = round(cur - prev, 2) if (prev is not None and cur is not None) else None
        else:
            rec["前日比"] = None


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
<title>空売り比率 - {latest_date}</title>
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
  .panels {{ display: flex; gap: 22px; align-items: flex-start; flex-wrap: wrap; }}
  .table-wrap {{
    overflow: auto;
    max-height: calc(100vh - 210px);
    max-width: 860px;
  }}
  .table-wrap.monthly {{ max-width: 480px; }}
  h2 {{ font-size: 0.95rem; color: #cbd5e1; margin-bottom: 8px; }}
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
  td.warn-light {{ background: rgba(220,38,38,0.18); color: #fecaca; }}
  td.warn-heavy {{ background: rgba(220,38,38,0.45); color: #fee2e2; font-weight: bold; }}
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
    <a href="kanryu.html" style="border-color:#7c3aed">還流ウォッチ</a>
    <a href="spriron.html" style="border-color:#7c3aed">SP500理論株価</a>
    <a href="riron.html" style="border-color:#7c3aed">日経理論株価</a>
    <a href="gaikoku.html" style="border-color:#2563eb">海外投資家</a>
    <a href="saitei.html" style="border-color:#2563eb">裁定取引</a>
    <a href="shutai.html" style="border-color:#2563eb">投資主体別</a>
    <a href="shinyou.html" style="border-color:#d97706">信用評価率</a>
    <a href="touraku.html" style="border-color:#d97706">騰落レシオ</a>
    <a href="karauri.html" class="active" style="border-color:#d97706">空売り比率</a>
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
  <h1>空売り比率</h1>
  <p class="subtitle">最終更新: {updated} | 出所: nikkei225jp.com | 日次 | 出来高:百万株 比率:%</p>
  <div class="legend">
    <span class="chip" style="background:rgba(220,38,38,0.18); color:#fecaca">合計40以上 = 空売り増加</span>
    <span class="chip" style="background:rgba(220,38,38,0.45); color:#fee2e2">合計44以上 = 空売り過熱（買い戻し反発に注意）</span>
  </div>
  <div class="panels">
  <div>
  <h2>日別</h2>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>日付</th>
        <th>日経平均</th>
        <th>前日比</th>
        <th>出来高</th>
        <th>空売り比率合計</th>
        <th>価格規制あり</th>
        <th>価格規制なし</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  </div>
  <div>
  <h2>月別平均（推移）</h2>
  <div class="table-wrap monthly">
  <table>
    <thead>
      <tr>
        <th>月</th>
        <th>空売り比率合計</th>
      </tr>
    </thead>
    <tbody>
{monthly_rows}
    </tbody>
  </table>
  </div>
  </div>
  </div>
  <p class="note">
    ・空売り比率 = 売り注文全体に占める空売りの割合（プライム市場）。40%超は歴史的に高水準で、空売りの買い戻しによる反発が起きやすいとされる。<br>
    ・価格規制あり = 直近安値から10%超下落した銘柄への空売り（規制対象）。規制なし = それ以外。<br>
    ・<a href="{src_url}" style="color:#60a5fa">nikkei225jp.com 空売り比率</a>
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def fmt_num(v, dec=0):
    if v is None:
        return "-"
    return f"{v:,.{dec}f}"


def fmt_diff(v):
    if v is None:
        return "-"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+,.2f}</span>'


def total_attr(v):
    if v is None:
        return ""
    if v >= WARN_HEAVY:
        return ' class="warn-heavy"'
    if v >= WARN_LIGHT:
        return ' class="warn-light"'
    return ""


def generate_html(hist):
    dates = sorted(hist.keys(), reverse=True)  # 最新が上
    rows = []
    for i, d in enumerate(dates):
        r = hist[d]
        row_cls = ' class="latest-row"' if i == 0 else ""
        total = r.get("合計")
        rows.append(
            f'      <tr{row_cls}><td>{d.replace("-", "/")}</td>'
            f'<td>{fmt_num(r.get("日経平均"), 2)}</td>'
            f'<td>{fmt_diff(r.get("前日比"))}</td>'
            f'<td>{fmt_num(r.get("出来高"))}</td>'
            f'<td{total_attr(total)}>{fmt_num(total, 1)}</td>'
            f'<td>{fmt_num(r.get("規制あり"), 1)}</td>'
            f'<td>{fmt_num(r.get("規制なし"), 1)}</td></tr>'
        )

    monthly = compute_monthly(hist)
    months = sorted(monthly.keys(), reverse=True)  # 最新月が上
    monthly_rows = []
    for i, ym in enumerate(months):
        m = monthly[ym]
        row_cls = ' class="latest-row"' if i == 0 else ""
        total = m.get("合計")
        monthly_rows.append(
            f'      <tr{row_cls}><td>{ym.replace("-", "/")}</td>'
            f'<td{total_attr(total)}>{fmt_num(total, 1)}</td></tr>'
        )

    html = HTML_TEMPLATE.format(
        latest_date=dates[0].replace("-", "/") if dates else "-",
        rows="\n".join(rows),
        monthly_rows="\n".join(monthly_rows),
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
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)  # 他スクリプトとのgit競合・放置ロック対策
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    os.path.basename(HISTORY_JSON), ".gitignore",
                    "karauri_screen.py", "karauri_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update karauri report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/karauri.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("空売り比率 チェック開始")

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
        compute_diffs(hist)
        save_history(hist)
        latest = max(hist)
        log(f"追記: {added}日分（計 {len(hist)}日） 最新 {latest}: 合計={hist[latest].get('合計')}%")
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
