# -*- coding: utf-8 -*-
"""
SNS恐怖温度計（X投稿ワード数 × 日経平均） → sns.html → GitHub Pages公開

データ源: Yahooリアルタイム検索の内部API（X投稿数の推移）
  https://search.yahoo.co.jp/realtime/api/v1/transition?p=WORD&interval=86400&span=2592000&rkf=3
  - 認証不要・日次集計・過去30日まで遡及可能
  - 非公開APIのため仕様変更で止まるリスクあり（totan等のスクレイピング系と同程度）

発想: 「追証」「ロスカット」等の投稿数は暴落日にスパイクする
  （検証: 2026-07-17 日経-4%の日に「追証」1,537件 = 平時の10倍。
   7/28-30の暴落3日間も1,100件台の高原。平時は100-160件）
  → 個人投資家の悲鳴そのものを測る恐怖温度計。VIX・空売り比率と違う切り口。
  スパイク = 投げ売りクライマックス = 逆張り買い場の候補、を検証する。

やること:
  1. 各ワードの日次投稿数をAPIから取得し sns_history.json に蓄積
     （APIは30日しか遡れないため蓄積が命。毎日実行で途切れず貯める）
  2. 日経平均終値・前日比を併記（yfinance）
  3. スパイク検出: 過去20日中央値のN倍で色分け
  4. スパイク日の翌日以降の日経騰落を集計（逆張りシグナル検証）

実行: 毎日19:50（sns_run.bat）
"""

import os
import sys
import json
import time
import urllib.parse
import urllib.request
import subprocess
import datetime

import yfinance as yf
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_JSON = os.path.join(SCRIPT_DIR, "sns_history.json")
REPORT_HTML = "sns.html"

# トラッキング対象ワード（ユーザー指定）
# 表記ゆれはOR検索で1枠に統合（APIは「A OR B」構文に対応、重複ツイートは1件として数える）
# WORDS = [(表示名/履歴キー, 検索クエリ)]
WORDS = [
    ("追証（追い証）", "追証 OR 追い証"),
    ("強制決済", "強制決済"),
    ("ロスカット", "ロスカット"),
]

API = ("https://search.yahoo.co.jp/realtime/api/v1/transition"
       "?p={word}&interval=86400&span=2592000&rkf=3")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}

SPIKE_LIGHT = 3.0   # 過去20日中央値の3倍以上 → 薄赤
SPIKE_HEAVY = 6.0   # 6倍以上 → 濃赤


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# 取得
# -----------------------------------------
def fetch_word_counts(word):
    """{日付ISO: 件数} を返す（直近30日、当日は集計途中なので除外）"""
    url = API.format(word=urllib.parse.quote(word))
    req = urllib.request.Request(url, headers={
        **UA, "Referer": f"https://search.yahoo.co.jp/realtime/search?p={urllib.parse.quote(word)}"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    data = json.loads(raw)
    out = {}
    today = datetime.date.today().isoformat()
    for e in data.get("tweetTransition", {}).get("entry", []):
        d = datetime.datetime.fromtimestamp(e["from"]).date().isoformat()
        if d >= today:
            continue  # 当日は集計途中・翌日以降に確定値を取る
        out[d] = e["count"]
    return out


def fetch_nikkei():
    """{日付ISO: (終値, 前日比%)} 直近3ヶ月分"""
    close = yf.download("^N225", period="3mo", auto_adjust=True,
                        progress=False)["Close"].squeeze().dropna()
    out = {}
    prev = None
    for idx, v in close.items():
        d = idx.date().isoformat()
        chg = round((v / prev - 1) * 100, 2) if prev else None
        out[d] = (round(float(v), 2), chg)
        prev = v
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


# -----------------------------------------
# スパイク判定
# -----------------------------------------
def spike_level(hist, word, date):
    """過去20日（date以前・date除く）の中央値と比較。(倍率, level 0/1/2)"""
    dates = sorted(d for d in hist.keys() if d < date and hist[d].get(word) is not None)
    window = [hist[d][word] for d in dates[-20:]]
    v = hist.get(date, {}).get(word)
    if v is None or len(window) < 5:
        return None, 0
    med = sorted(window)[len(window) // 2]
    if med <= 0:
        med = 1
    ratio = v / med
    level = 2 if ratio >= SPIKE_HEAVY else (1 if ratio >= SPIKE_LIGHT else 0)
    return round(ratio, 1), level


# -----------------------------------------
# HTML
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SNS恐怖温度計 - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 24px 0 10px; border-left: 4px solid #d97706; padding-left: 10px; }}
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
  .table-wrap {{ overflow: auto; max-height: 70vh; max-width: 1000px; }}
  table {{ border-collapse: collapse; font-size: 0.84rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 8px 12px;
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
  td.spike1 {{ background: rgba(220,38,38,0.18); color: #fecaca; }}
  td.spike2 {{ background: rgba(220,38,38,0.45); color: #fee2e2; font-weight: bold; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.9; max-width: 1000px; }}
  .evidence {{
    background: rgba(59,130,246,0.08); border: 1px solid rgba(59,130,246,0.3);
    border-radius: 8px; padding: 10px 14px; font-size: 0.8rem; line-height: 1.8;
    max-width: 1000px; margin-bottom: 16px;
  }}
  .evidence b {{ color: #93c5fd; }}
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
    <a href="spriron.html" style="border-color:#7c3aed">SP500理論株価</a>
    <a href="riron.html" style="border-color:#7c3aed">日経理論株価</a>
    <a href="gaikoku.html" style="border-color:#2563eb">海外投資家</a>
    <a href="saitei.html" style="border-color:#2563eb">裁定取引</a>
    <a href="shutai.html" style="border-color:#2563eb">投資主体別</a>
    <a href="shinyou.html" style="border-color:#d97706">信用評価率</a>
    <a href="touraku.html" style="border-color:#d97706">騰落レシオ</a>
    <a href="karauri.html" style="border-color:#d97706">空売り比率</a>
    <a href="vix.html" style="border-color:#d97706">VIX温度計</a>
    <a href="sns.html" class="active" style="border-color:#d97706">SNS恐怖温度計</a>
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
  <h1>SNS恐怖温度計 — X投稿ワード数 × 日経平均</h1>
  <p class="subtitle">最終更新: {updated} | 出所: Yahooリアルタイム検索（X投稿の日次件数） | ワード: {words}</p>
  <div class="evidence">
    <b>発想:</b> 「追証」「ロスカット」の投稿数は暴落日にスパイクする＝個人投資家の悲鳴のリアルタイム温度計。<br>
    実測: 2026-07-17（日経-4%）に「追証」<b>1,537件</b>（平時の約10倍）、7/28-30の暴落3日間も1,100件台の高原、平時は100〜160件。<br>
    スパイク=投げ売りクライマックス=逆張り買い場の候補、という仮説を蓄積データで検証していく。
    VIX（オプション市場）・空売り比率（売り方の建玉）と違い、これは<b>個人のパニックの生の声</b>を測る。
  </div>
  <div class="legend">
    <span class="chip" style="background:rgba(220,38,38,0.18); color:#fecaca">過去20日中央値の{light}倍以上</span>
    <span class="chip" style="background:rgba(220,38,38,0.45); color:#fee2e2">{heavy}倍以上（パニック）</span>
    <span style="margin-left:8px">最新が上。投稿数は前日までの確定値</span>
  </div>
{spike_summary}
  <h2>日次推移</h2>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>日付</th><th>日経平均</th><th>前日比</th>
{word_headers}
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・投稿数はYahooリアルタイム検索の集計（X全体のサンプリングであり絶対数ではなく推移を見る）。当日分は集計途中のため翌日に確定値を記録。<br>
    ・APIの遡及限界が30日のため、それ以前は蓄積分のみ（このページの運用開始: 2026-08-15、以後毎日蓄積）。<br>
    ・スパイク判定 = その日の件数 ÷ 過去20日の中央値。土日祝の投稿も含む（市場休場日は日経平均が「-」）。<br>
    ・関連デッキ: <a href="vix.html" style="color:#60a5fa">VIX温度計</a>（機関の恐怖） /
    <a href="karauri.html" style="color:#60a5fa">空売り比率</a>（売り方の過熱) /
    <a href="shinyou.html" style="color:#60a5fa">信用評価率</a>（個人の含み損） — SNSは個人のパニックの「声」。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def generate_html(hist, nikkei):
    dates = sorted(hist.keys(), reverse=True)
    word_headers = "\n".join(f"        <th>{label}</th>" for label, _ in WORDS)

    rows = []
    spike_days = []  # (date, word, ratio) スパイク記録
    for i, d in enumerate(dates):
        rec = hist[d]
        cls = ' class="latest-row"' if i == 0 else ""
        nk = nikkei.get(d)
        nk_s = f"{nk[0]:,.0f}" if nk else "-"
        if nk and nk[1] is not None:
            chg_cls = "pos" if nk[1] > 0 else ("neg" if nk[1] < 0 else "")
            chg_s = f'<span class="{chg_cls}">{nk[1]:+.2f}%</span>'
        else:
            chg_s = "-"
        cells = []
        for w, _ in WORDS:
            v = rec.get(w)
            if v is None:
                cells.append("<td>-</td>")
                continue
            ratio, level = spike_level(hist, w, d)
            if level == 2:
                cells.append(f'<td class="spike2" title="中央値の{ratio}倍">{v:,}</td>')
                spike_days.append((d, w, ratio))
            elif level == 1:
                cells.append(f'<td class="spike1" title="中央値の{ratio}倍">{v:,}</td>')
                spike_days.append((d, w, ratio))
            else:
                cells.append(f"<td>{v:,}</td>")
        rows.append(f'      <tr{cls}><td>{d.replace("-", "/")}</td>'
                    f'<td>{nk_s}</td><td>{chg_s}</td>' + "".join(cells) + "</tr>")

    # スパイク履歴サマリ
    if spike_days:
        items = []
        seen_dates = []
        for d, w, r in sorted(spike_days, reverse=True):
            if d not in seen_dates:
                seen_dates.append(d)
        for d in seen_dates[:10]:
            ws = [f"{w}×{r}" for dd, w, r in spike_days if dd == d]
            nk = nikkei.get(d)
            nk_s = f"日経{nk[1]:+.2f}%" if nk and nk[1] is not None else ""
            items.append(f'<li>{d.replace("-","/")} {nk_s} — {" / ".join(ws)}</li>')
        spike_summary = (
            '  <h2>スパイク履歴（中央値3倍以上の日）</h2>\n'
            '  <ul style="font-size:0.82rem; line-height:2; color:#cbd5e1; padding-left:24px;">\n'
            + "\n".join(items) + "\n  </ul>")
    else:
        spike_summary = ""

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        words="・".join(label for label, _ in WORDS),
        light=SPIKE_LIGHT, heavy=SPIKE_HEAVY,
        spike_summary=spike_summary,
        word_headers=word_headers,
        rows="\n".join(rows),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}（{len(dates)}日分）")


# -----------------------------------------
# push
# -----------------------------------------
def push_to_github():
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)  # 他スクリプトとのgit競合・放置ロック対策
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "sns_history.json", "sns_screen.py", "sns_run.bat",
                    ".gitignore"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update sns report " + today],
        capture_output=True)
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
            log("  Done: https://ichikon77.github.io/minervini/sns.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("SNS恐怖温度計 チェック開始")

    hist = load_history()
    added = 0
    for w, query in WORDS:
        try:
            counts = fetch_word_counts(query)
        except Exception as e:
            log(f"  {w}: 取得失敗 {e}")
            continue
        n_new = 0
        for d, v in counts.items():
            rec = hist.setdefault(d, {})
            if rec.get(w) != v:
                rec[w] = v
                n_new += 1
        added += n_new
        log(f"  {w}: {len(counts)}日分取得（更新{n_new}）")
        time.sleep(1.5)

    if added:
        save_history(hist)
        log(f"履歴保存: {len(hist)}日分（{added}項目更新）")
    else:
        log("新しいデータはありませんでした")

    if not hist:
        log("データがないためHTMLは生成しません")
        sys.exit(1)

    try:
        nikkei = fetch_nikkei()
    except Exception as e:
        log(f"日経平均の取得に失敗（表は投稿数のみ）: {e}")
        nikkei = {}

    generate_html(hist, nikkei)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
