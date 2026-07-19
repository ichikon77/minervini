# -*- coding: utf-8 -*-
"""
グローバル資金フロー（テーマ別リターン・ヒートマップ） → HTML出力 → GitHub Pages公開

データ源: Yahoo Finance (yfinance)
各資産クラス・テーマETFの騰落率を 5/10/15営業日・1ヶ月(21)・3ヶ月(63)・年初来 で計算し、
「世界のマネーがどこへ向かっているか」をヒートマップ表示する。

- 毎日実行（米国市場の引け後、朝8:20）
- 1ヶ月リターンで降順ソート
- プラス=緑の濃淡、マイナス=赤の濃淡
- 履歴保存は不要（毎回yfinanceから1年分取得して計算）
"""

import os
import sys
import json
import time
import subprocess
import datetime

import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_HTML = "flow.html"

# (ticker, 表示名, グループ)
# 資産クラスはこの記載順で固定表示（株式: 米国→日本→アジア → 債券 → 現物資産 → 通貨 → REIT）
# テーマは3営業日リターンの降順で表示
THEMES = [
    # 資産クラス: 株式(米国)
    ("^GSPC",    "S&P500",           "資産クラス"),
    ("^DJI",     "NYダウ",            "資産クラス"),
    ("^IXIC",    "NASDAQ",           "資産クラス"),
    # 株式(日本)
    ("^N225",    "日経平均",          "資産クラス"),
    ("1306.T",   "TOPIX",            "資産クラス"),
    ("2516.T",   "東証グロース250",     "資産クラス"),
    # 株式(アジア・新興国)
    ("MCHI",     "中国株",            "資産クラス"),
    ("INDA",     "インド株",          "資産クラス"),
    ("VNM",      "ベトナム株",         "資産クラス"),
    ("EEM",      "新興国株",          "資産クラス"),
    # 債券
    ("TLT",      "米長期債",          "資産クラス"),
    # 現物資産・暗号資産
    ("GLD",      "金",               "資産クラス"),
    ("BTC-USD",  "ビットコイン",       "資産クラス"),
    # 通貨・不動産
    ("DX-Y.NYB", "ドル指数",          "資産クラス"),
    ("VNQ",      "REIT(米不動産)",    "資産クラス"),
    # テーマ・セクター（表示は3営業日降順）
    ("MAGS",     "マグニフィセント7",   "テーマ"),
    ("SMH",      "半導体",            "テーマ"),
    ("XLK",      "テック",            "テーマ"),
    ("IGV",      "ソフトウェア/SaaS",  "テーマ"),
    ("ITA",      "防衛",             "テーマ"),
    ("XLE",      "エネルギー(石油)",    "テーマ"),
    ("XLF",      "金融",             "テーマ"),
    ("XLV",      "ヘルスケア",         "テーマ"),
    ("XLU",      "公益(守り)",        "テーマ"),
    ("GDX",      "金鉱株",            "テーマ"),
    ("URA",      "ウラン・原子力",      "テーマ"),
    ("ARKX",     "宇宙",             "テーマ"),
    ("QTUM",     "量子コンピューター",   "テーマ"),
    ("JETS",     "航空",             "テーマ"),
    ("IYT",      "鉄道・運輸",         "テーマ"),
    ("PEJ",      "エンタメ・レジャー",   "テーマ"),
    ("ESPO",     "ゲーム・eスポーツ",   "テーマ"),
]

PERIODS = [("3営業日", 3), ("5営業日", 5), ("10営業日", 10), ("15営業日", 15),
           ("1ヶ月", 21), ("3ヶ月", 63)]  # + 年初来は別計算


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# リターン計算
# -----------------------------------------
def fetch_returns():
    tickers = [t for t, _, _ in THEMES]
    data = yf.download(tickers, period="2y", auto_adjust=True,
                       progress=False, group_by="ticker", threads=True)

    rows = []
    for t, name, group in THEMES:
        try:
            close = data[t]["Close"].dropna()
        except Exception:
            log(f"  {name}({t}): データなし、スキップ")
            continue
        if len(close) < 70:
            log(f"  {name}({t}): データ不足、スキップ")
            continue

        rec = {"ticker": t, "name": name, "group": group,
               "last_date": close.index[-1].strftime("%Y-%m-%d"),
               "price": float(close.iloc[-1])}
        for label, n in PERIODS:
            if len(close) > n:
                rec[label] = round((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100, 2)
            else:
                rec[label] = None
        # 年初来
        year = close.index[-1].year
        ytd_base = close[close.index.year < year]
        if len(ytd_base):
            rec["年初来"] = round((close.iloc[-1] / ytd_base.iloc[-1] - 1) * 100, 2)
        else:
            rec["年初来"] = None
        rows.append(rec)
    return rows


# -----------------------------------------
# HTML出力
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>グローバル資金フロー - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 22px 0 10px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .table-wrap {{ overflow-x: auto; max-width: 980px; }}
  table {{ border-collapse: collapse; font-size: 0.86rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 12px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child {{ text-align: left; }}
  td {{
    padding: 7px 12px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{ text-align: left; font-weight: 600; }}
  td.tk {{ color: #64748b; font-size: 0.78rem; text-align: left; }}
  tr:hover td {{ filter: brightness(1.25); }}
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
    <a href="shutai.html">投資主体別</a>
    <a href="gaikoku.html">海外投資家</a>
    <a href="touraku.html">騰落レシオ</a>
    <a href="karauri.html">空売り比率</a>
    <a href="riron.html">日経理論株価</a>
    <a href="spriron.html">SP500理論株価</a>
    <a href="flow.html" class="active">資金フロー</a>
    <a href="map.html">デッキの見方</a>
  </nav>
  <h1>グローバル資金フロー</h1>
  <p class="subtitle">最終更新: {updated} | 出所: Yahoo Finance | 各テーマETF/指数の騰落率(%) | 資産クラス=固定順 / テーマ=3営業日リターン降順</p>
{sections}
  <p class="note">
    ・世界のマネーがどの資産・テーマに向かっているかを騰落率で観察する。緑=資金流入（上昇）、赤=資金流出（下落）。<br>
    ・5/10/15営業日の並びで「加速中か失速中か」が分かる（右肩上がりの緑=ローテーション初動、5営業日だけ赤=直近で変調）。<br>
    ・1ヶ月=21営業日、3ヶ月=63営業日。年初来は前年末終値比。ETFは米国上場のため為替の影響を含む。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""

COLS = ["3営業日", "5営業日", "10営業日", "15営業日", "1ヶ月", "3ヶ月", "年初来"]


def cell(v):
    if v is None:
        return "<td>-</td>"
    # 緑/赤の濃淡: ±15%で最大濃度
    alpha = min(0.6, abs(v) / 15 * 0.6)
    color = "34,197,94" if v > 0 else "239,68,68"
    style = f' style="background:rgba({color},{alpha:.2f})"' if abs(v) >= 0.05 else ""
    return f"<td{style}>{v:+.2f}%</td>"


def generate_html(rows):
    theme_order = [t for t, _, _ in THEMES]  # 定義順
    sections = []
    for group in ["資産クラス", "テーマ"]:
        grp = [r for r in rows if r["group"] == group]
        if group == "資産クラス":
            # 定義順で固定（株式: 米国→日本→アジア → 債券 → 金 → BTC → 通貨 → REIT）
            grp.sort(key=lambda r: theme_order.index(r["ticker"]))
        else:
            # 3営業日リターンの降順（直近の資金流入が上に）
            grp.sort(key=lambda r: (r.get("3営業日") is None, -(r.get("3営業日") or 0)))
        body = []
        for r in grp:
            tds = "".join(cell(r.get(c)) for c in COLS)
            body.append(f'      <tr><td>{r["name"]}</td><td class="tk">{r["ticker"]}</td>{tds}</tr>')
        header = "".join(f"<th>{c}</th>" for c in COLS)
        sections.append(
            f'  <h2>{group}</h2>\n  <div class="table-wrap">\n  <table>\n'
            f'    <thead><tr><th>テーマ</th><th style="text-align:left">ticker</th>{header}</tr></thead>\n'
            f'    <tbody>\n' + "\n".join(body) + '\n    </tbody>\n  </table>\n  </div>')

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        sections="\n".join(sections),
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
                    "flow_screen.py", "flow_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update flow report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/flow.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("グローバル資金フロー チェック開始")

    try:
        rows = fetch_returns()
    except Exception as e:
        log(f"エラー: データの取得に失敗しました: {e}")
        sys.exit(1)

    if len(rows) < 10:
        log(f"エラー: 取得テーマが少なすぎます（{len(rows)}件）")
        sys.exit(1)

    log(f"取得: {len(rows)}テーマ（基準日 {rows[0]['last_date']}）")

    generate_html(rows)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
