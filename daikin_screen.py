# -*- coding: utf-8 -*-
"""
Yahoo!ファイナンス「売買代金上位ランキング」自動記録 → HTML出力 → GitHub Pages公開

https://finance.yahoo.co.jp/stocks/ranking/tradingValueHigh (1-50位)
https://finance.yahoo.co.jp/stocks/ranking/tradingValueHigh?market=all&term=daily&page=2..4 (51-200位)

- 毎日200位まで取得し daikin_history.json に蓄積（直近15営業日分のみ保持）
  ※2026-07-16以前の過去データ（エクセル由来）は100位まで
- daikin.html を生成して git push（他スクリーナーと同方式）
- 表示: 新しい日付が左。順位/コード/名称/取引値/前日比%/売買代金 + 順位変動（連続日数）
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

URLS = [
    "https://finance.yahoo.co.jp/stocks/ranking/tradingValueHigh?market=all&term=daily",
    "https://finance.yahoo.co.jp/stocks/ranking/tradingValueHigh?market=all&term=daily&page=2",
    "https://finance.yahoo.co.jp/stocks/ranking/tradingValueHigh?market=all&term=daily&page=3",
    "https://finance.yahoo.co.jp/stocks/ranking/tradingValueHigh?market=all&term=daily&page=4",
]
TOP_N = 200  # 記録する順位数

HISTORY_JSON = os.path.join(SCRIPT_DIR, "daikin_history.json")
REPORT_HTML = "daikin.html"
KEEP_DAYS = 15  # 保持する営業日数

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# Yahoo!ファイナンスから取得
# -----------------------------------------
def fetch_with_retry(url, tries=5, timeout=60, wait=60):
    last_err = None
    for i in range(1, tries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            log(f"  取得失敗 ({i}/{tries}): {e}")
            if i < tries:
                time.sleep(wait)
    raise last_err


def extract_ranking(html):
    """ページ内の mainRankingList.results 配列(JSON)を抽出"""
    i = html.find('"mainRankingList":{"results":[')
    if i < 0:
        raise ValueError("ランキングデータが見つかりません（ページ構造が変わった可能性）")
    start = html.find('[', i)
    depth = 0
    for j in range(start, len(html)):
        if html[j] == '[':
            depth += 1
        elif html[j] == ']':
            depth -= 1
            if depth == 0:
                return json.loads(html[start:j + 1])
    raise ValueError("ランキングJSONの解析に失敗")


def to_int(s):
    if s is None:
        return None
    try:
        return int(str(s).replace(",", ""))
    except ValueError:
        return None


def to_float(s):
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "").replace("+", ""))
    except ValueError:
        return None


def fetch_today():
    """(日付'YYYY-MM-DD', [{rank, code, name, price, pct, value}×100]) を返す"""
    entries = []
    as_of = None
    for url in URLS:
        r = fetch_with_retry(url)
        arr = extract_ranking(r.text)
        for item in arr:
            tv = (item.get("rankingResult") or {}).get("tradingValue") or {}
            if as_of is None and tv.get("updateDateTime"):
                # "2026/07/16 18:40" → その日のランキング日付
                as_of = tv["updateDateTime"].split(" ")[0].replace("/", "-")
            entries.append({
                "rank": int(item["rank"]),
                "code": str(item["stockCode"]),
                "name": str(item["stockName"]),
                # 株価は0.5円刻み等で小数が付くことがある（例: トヨタ 2,944.5）
                "price": to_float(item.get("savePrice")),
                "pct": to_float(tv.get("changePriceRate")),
                "value": to_int(tv.get("tradingValue")),
            })
        time.sleep(2)

    if len(entries) < TOP_N:
        raise ValueError(f"取得件数が不足: {len(entries)}件（{TOP_N}件必要）")
    entries.sort(key=lambda x: x["rank"])
    if as_of is None:
        as_of = datetime.date.today().isoformat()
    return as_of, entries[:TOP_N]


# -----------------------------------------
# 履歴（JSON）
# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        with open(HISTORY_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(hist):
    # 直近KEEP_DAYS日分だけ残す
    dates = sorted(hist.keys(), reverse=True)[:KEEP_DAYS]
    hist = {d: hist[d] for d in dates}
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)
    return hist


# -----------------------------------------
# 順位変動（連続日数）の計算
# -----------------------------------------
def build_rank_maps(hist):
    """{date: {code: rank}} を作る"""
    return {d: {e["code"]: e["rank"] for e in entries} for d, entries in hist.items()}


def calc_streak(code, date, dates_desc, rank_maps):
    """dateにおけるcodeの順位変動と連続日数を返す
    戻り値: (direction, streak)
      direction: 'up'(順位改善) / 'down'(悪化) / 'same' / 'new'(前日圏外)
      streak: 同方向が何日連続か
    """
    idx = dates_desc.index(date)
    prev_dates = dates_desc[idx + 1:]  # dateより古い日（新→古順）

    def direction_at(i):
        """dates_desc[i] の日の変動方向"""
        d = dates_desc[i]
        cur = rank_maps[d].get(code)
        if cur is None or i + 1 >= len(dates_desc):
            return None
        prev = rank_maps[dates_desc[i + 1]].get(code)
        if prev is None:
            return "new"
        if cur < prev:
            return "up"
        if cur > prev:
            return "down"
        return "same"

    d0 = direction_at(idx)
    if d0 is None:
        return ("new", 0) if prev_dates else ("same", 0)
    if d0 == "new":
        return ("new", 0)
    if d0 == "same":
        return ("same", 0)

    streak = 1
    i = idx + 1
    while i + 1 < len(dates_desc):
        if direction_at(i) == d0:
            streak += 1
            i += 1
        else:
            break
    return (d0, streak)


# -----------------------------------------
# HTML出力
# -----------------------------------------
HTML_HEAD = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>売買代金ランキング - {latest_date}</title>
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
  .blocks {{ display: flex; gap: 18px; overflow-x: auto; align-items: flex-start;
             max-height: calc(100vh - 170px); overflow-y: auto; }}
  .day-block {{ flex: 0 0 auto; }}
  .day-title {{
    font-size: 0.95rem; font-weight: bold; color: #bfdbfe; background: #1e40af;
    padding: 6px 12px; border-radius: 6px 6px 0 0; text-align: center;
    position: sticky; top: 0; z-index: 3;
  }}
  table {{ border-collapse: collapse; font-size: 0.78rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 6px 8px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 33px; z-index: 2;
  }}
  thead th.l {{ text-align: left; }}
  td {{
    padding: 4px 8px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td.l {{ text-align: left; }}
  td.name {{ max-width: 13em; overflow: hidden; text-overflow: ellipsis; }}
  tr:hover td {{ background: #16213a; }}
  .up {{ color: #4ade80; font-weight: bold; }}
  .down {{ color: #f87171; font-weight: bold; }}
  .same {{ color: #64748b; }}
  .new {{ color: #fbbf24; font-weight: bold; }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
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
    <a href="margin.html" style="border-color:#2563eb">銘柄別信用倍率</a>
    <a href="insider.html" style="border-color:#2563eb">インサイダー売買</a>
    <a href="buffett.html" style="border-color:#2563eb">バフェット</a>
    <a href="shinyou.html" style="border-color:#d97706">信用評価率</a>
    <a href="touraku.html" style="border-color:#d97706">騰落レシオ</a>
    <a href="karauri.html" style="border-color:#d97706">空売り比率</a>
    <a href="vix.html" style="border-color:#d97706">VIX温度計</a>
    <a href="flow.html" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" class="active" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
  </nav>
  <h1>売買代金ランキング TOP200</h1>
  <p class="subtitle">最終更新: {updated} | 出所: Yahoo!ファイナンス | 直近{days}営業日分 | 売買代金は億円</p>
  <div class="blocks">
{blocks}
  </div>
  <p class="note">
    ・変動列: 前日からの順位変動。↑3=3日連続で順位上昇中、↓2=2日連続下落中、→=変わらず、NEW=前日圏外から登場。<br>
    ・2026/07/16以前の過去データは100位まで（エクセル記録からの取り込み）。07/17以降は200位まで。<br>
    ・<a href="https://finance.yahoo.co.jp/stocks/ranking/tradingValueHigh" style="color:#60a5fa">Yahoo!ファイナンス 売買代金ランキング</a>
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def fmt_num(v):
    return f"{v:,}" if isinstance(v, (int, float)) and v is not None else "-"


def fmt_pct(v):
    if v is None:
        return "-"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+.2f}%</span>'


def fmt_move(direction, streak):
    if direction == "new":
        return '<span class="new">NEW</span>'
    if direction == "same":
        return '<span class="same">→</span>'
    if direction == "up":
        return f'<span class="up">↑{streak}</span>'
    return f'<span class="down">↓{streak}</span>'


def generate_html(hist):
    dates_desc = sorted(hist.keys(), reverse=True)  # 新しい日が先頭（左）
    rank_maps = build_rank_maps(hist)

    blocks = []
    for d in dates_desc:
        rows = []
        for e in hist[d]:
            direction, streak = calc_streak(e["code"], d, dates_desc, rank_maps)
            oku = e["value"] / 1e8 if e.get("value") else None
            rows.append(
                f'<tr><td>{e["rank"]}</td>'
                f'<td class="l">{e["code"]}</td>'
                f'<td class="l name" title="{e["name"]}">{e["name"]}</td>'
                f'<td>{fmt_num(e.get("price"))}</td>'
                f'<td>{fmt_pct(e.get("pct"))}</td>'
                f'<td>{fmt_num(round(oku) if oku else None)}</td>'
                f'<td>{fmt_move(direction, streak)}</td></tr>'
            )
        dd = d.replace("-", "/")
        blocks.append(
            f'  <div class="day-block">\n'
            f'    <div class="day-title">{dd}</div>\n'
            f'    <table>\n'
            f'      <thead><tr><th>順位</th><th class="l">コード</th><th class="l">名称</th>'
            f'<th>取引値</th><th>前日比</th><th>売買代金</th><th>変動</th></tr></thead>\n'
            f'      <tbody>\n' + "\n".join(rows) + '\n      </tbody>\n    </table>\n  </div>'
        )

    html = HTML_HEAD.format(
        latest_date=dates_desc[0].replace("-", "/") if dates_desc else "-",
        days=len(dates_desc),
        blocks="\n".join(blocks),
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
                    "daikin_screen.py", "daikin_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update daikin report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/daikin.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("Yahoo 売買代金ランキング チェック開始")

    hist = load_history()

    try:
        as_of, entries = fetch_today()
    except Exception as e:
        log(f"エラー: ランキングの取得に失敗しました: {e}")
        sys.exit(1)

    if as_of in hist and hist[as_of] == entries:
        log(f"{as_of} は記録済み・変化なし")
    else:
        hist[as_of] = entries
        hist = save_history(hist)
        log(f"記録: {as_of}  {len(entries)}銘柄（保持: {len(hist)}日分）")

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
