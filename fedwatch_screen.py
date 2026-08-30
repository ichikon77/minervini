# -*- coding: utf-8 -*-
"""
FRB 利上げ/利下げ確率（FF金利先物から自前計算） → HTML出力 → GitHub Pages公開

データ源: yfinance の FF金利先物 (ZQ + 月コード + 年 + .CBT)
原理: FF先物価格 = 100 - その月の平均FF金利。
FOMCがある月は「会合前レート×日数 + 会合後レート×日数」の加重平均になるため、
クリーンな月（会合なし）の値と組み合わせて会合ごとの変化を逆算する
（CME FedWatchと同じ考え方）。

確率 = (会合後レート - 会合前レート) / 0.25 × 100
  プラス = 25bp利上げの織り込み度合い / マイナス = 25bp利下げの織り込み

- 毎朝8:25実行（米国市場引け後）
- 履歴は fedwatch_history.json に蓄積（初回はyfinanceの過去データから遡って構築）
- 列=会合（新しい会合が左）、行=日付（最新が上）… totan.htmlと同じ構成
"""

import os
import sys
import json
import time
import calendar
import subprocess
import datetime

import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_JSON = os.path.join(SCRIPT_DIR, "fedwatch_history.json")
REPORT_HTML = "fedwatch.html"

# FOMC決定日（2日目）。翌営業日から新レート適用とみなす。
# ※年8回の標準スケジュール。新しい年の日程が公表されたらここに追記する。
FOMC_DATES = [
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    "2027-01-27", "2027-03-17", "2027-04-28",
]

MONTH_CODES = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
               7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def contract_ticker(year, month):
    return f"ZQ{MONTH_CODES[month]}{str(year)[2:]}.CBT"


def month_has_meeting(year, month):
    return any(d.startswith(f"{year:04d}-{month:02d}") for d in FOMC_DATES)


def next_month(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


# -----------------------------------------
# 必要な限月を決めて一括取得
# -----------------------------------------
def needed_contracts():
    """全会合の計算に必要な限月 (year, month) の集合"""
    months = set()
    for d in FOMC_DATES:
        y, m = int(d[:4]), int(d[5:7])
        months.add((y, m))              # 会合月（分解用）
        months.add(next_month(y, m))    # 翌月（クリーンならr_after）
    return sorted(months)


def fetch_futures():
    """{(year,month): {日付ISO: 織り込みレート%}} を返す"""
    months = needed_contracts()
    tickers = {contract_ticker(y, m): (y, m) for y, m in months}
    data = yf.download(list(tickers.keys()), period="1y", auto_adjust=False,
                       progress=False, group_by="ticker", threads=True)
    out = {}
    for t, (y, m) in tickers.items():
        try:
            close = data[t]["Close"].dropna()
        except Exception:
            log(f"  {t}: データなし")
            continue
        if len(close) < 5:
            continue
        out[(y, m)] = {idx.strftime("%Y-%m-%d"): round(100 - v, 4)
                       for idx, v in close.items()}
    return out


# -----------------------------------------
# 会合ごとの織り込み確率を計算
# -----------------------------------------
def calc_probs_for_date(rates_at, date_iso):
    """ある日付時点の各会合の変化織り込み確率 {会合ラベル: prob%} を返す

    rates_at: {(y,m): レート%} その日時点で分かっている各限月の織り込みレート
      （forward-fillされた値。会合が過ぎて先物が取引終了した月も、
       最後に観測された確定レートとして常に含まれる＝実現レートとして使える）

    全FOMC_DATESを時系列順（古い→新しい）に必ず通し、r_prev（直前会合の
    決定後レート）のチェーンを日付に関わらず最後まで引き継ぐ。過去の会合は
    もう不確実性がなく実現レートが確定しているので、date_iso <= d で
    チェーンを切ってはいけない（切ると、それ以降の全会合が
    r_prev=Noneのままcontinueされて消えてしまう）。
    date_iso以前（＝もう終わった）会合はチェーンの土台として使うだけで、
    resultには入れない（表に出すのは未来の会合のみ）。
    """
    result = {}
    r_prev = None  # 直前会合の会合後レート（実現 or 織り込み）

    for d in sorted(FOMC_DATES):
        y, m = int(d[:4]), int(d[5:7])
        day = int(d[8:10])
        n_days = calendar.monthrange(y, m)[1]
        eff = day + 1  # 新レート適用開始日

        avg_m = rates_at.get((y, m))
        ny, nm = next_month(y, m)
        next_clean = not month_has_meeting(ny, nm)
        r_next = rates_at.get((ny, nm)) if next_clean else None

        w_before = (eff - 1) / n_days
        w_after = (n_days - eff + 1) / n_days

        try:
            if next_clean and r_next is not None:
                r_after = r_next
                if r_prev is not None:
                    r_before = r_prev
                elif avg_m is not None and w_before > 0:
                    r_before = (avg_m - w_after * r_after) / w_before
                else:
                    r_prev = r_after
                    continue
            else:
                if r_prev is None or avg_m is None or w_after <= 0:
                    continue
                r_before = r_prev
                r_after = (avg_m - w_before * r_before) / w_after

            prob = (r_after - r_before) / 0.25 * 100
            r_prev = r_after
            if d > date_iso:
                label = f"{y}/{m:02d}"
                result[label] = round(prob)
        except Exception:
            continue
    return result


def build_history(futures):
    """全取引日について確率を計算 {日付: {会合: prob}}

    各限月の先物は、その月が過ぎる（会合が確定する）と出来高が細り、
    最終的には取引されなくなる（seriesにその日のキーが無くなる）。
    そこで forward-fill: 直前に観測された終値を、その月がrates_atに
    出てこなくなった日以降も「確定レート」として引き継ぐ。これが無いと
    会合が過ぎた月のavg_mがNoneに戻り、calc_probs_for_dateのr_prevチェーンが
    そこで切れて以降の会合が全部消える。
    """
    all_dates = set()
    for series in futures.values():
        all_dates.update(series.keys())

    last_known = {}  # ym -> 直前に観測された値（forward-fill用）
    hist = {}
    for d in sorted(all_dates):
        for ym, series in futures.items():
            if d in series:
                last_known[ym] = series[d]
        rates_at = dict(last_known)  # その日までに分かっている全限月（forward-fill済み）
        probs = calc_probs_for_date(rates_at, d)
        if probs:
            hist[d] = probs
    return hist


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
# HTML出力（totan.htmlと同構成）
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FRB利上げ/利下げ確率 - {latest_date}</title>
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
  .legend {{ display: flex; gap: 12px; margin-bottom: 14px; font-size: 0.78rem; color: #94a3b8; flex-wrap: wrap; }}
  .table-wrap {{
    overflow: auto;
    max-height: calc(100vh - 200px);
    max-width: 900px;
  }}
  table {{ border-collapse: collapse; font-size: 0.88rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 10px 14px;
    text-align: right; font-weight: 600;
    position: sticky; top: 0; white-space: nowrap; z-index: 2;
  }}
  thead th:first-child {{
    text-align: left; position: sticky; left: 0; z-index: 3;
  }}
  td {{
    padding: 8px 14px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{
    text-align: left; color: #94a3b8; position: sticky; left: 0;
    background: #0f172a; z-index: 1;
  }}
  tr:hover td {{ background: #16213a; }}
  tr:hover td:first-child {{ background: #16213a; }}
  .latest-row td {{ background: rgba(30,64,175,0.18); font-weight: bold; }}
  .hike-hi {{ color: #f87171; font-weight: bold; }}
  .hike-mid {{ color: #fbbf24; }}
  .cut-hi {{ color: #38bdf8; font-weight: bold; }}
  .cut-mid {{ color: #7dd3fc; }}
  .flat {{ color: #64748b; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.8; }}
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
    <a href="fedwatch.html" class="active" style="border-color:#7c3aed">FRB利上げ確率</a>
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
  <h1>FRB 利上げ/利下げ 織り込み確率</h1>
  <p class="subtitle">最終更新: {updated} | 出所: FF金利先物（CBOT、yfinance経由）から自前計算 | 単位: %</p>
  <div class="legend">
    <span class="hike-hi">プラス = 25bp利上げの織り込み（50%超は赤太字）</span>
    <span class="cut-hi">マイナス = 25bp利下げの織り込み（-50%超は青太字）</span>
    <span class="flat">±10%未満 = 据え置き優勢</span>
  </div>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>日付</th>
{headers}
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・FF金利先物価格 = 100 − その月の平均FF金利。FOMCがある月は会合前後の日割り加重平均になる性質を使い、
    会合ごとのレート変化の織り込みを逆算（CME FedWatchと同じ原理の簡易版）。<br>
    ・確率 = 織り込まれたレート変化 ÷ 25bp。+100%なら25bp利上げを完全織り込み、+50%なら五分五分。<br>
    ・FOMC日程は標準スケジュールに基づく仮置き。日程変更時はスクリプトのFOMC_DATESを更新。<br>
    ・<a href="totan.html" style="color:#60a5fa">日銀利上げ確率</a>と並べて見ると日米金利差の方向（→ドル円 → 日経EPS）が読める。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def prob_class(v):
    if v is None:
        return "flat"
    if v >= 50:
        return "hike-hi"
    if v >= 10:
        return "hike-mid"
    if v <= -50:
        return "cut-hi"
    if v <= -10:
        return "cut-mid"
    return "flat"


def meeting_sort_key(label):
    y, m = label.split("/")
    return int(y) * 100 + int(m)


def generate_html(hist):
    meetings = sorted({m for rec in hist.values() for m in rec},
                      key=meeting_sort_key, reverse=True)  # 新しい会合が左
    dates = sorted(hist.keys(), reverse=True)

    headers = "\n".join(f'        <th>{m}会合</th>' for m in meetings)
    rows = []
    for i, d in enumerate(dates):
        rec = hist[d]
        cls = ' class="latest-row"' if i == 0 else ""
        cells = []
        for m in meetings:
            v = rec.get(m)
            if v is None:
                cells.append("<td>-</td>")
            else:
                cells.append(f'<td><span class="{prob_class(v)}">{v:+d}%</span></td>')
        rows.append(f'      <tr{cls}><td>{d.replace("-", "/")}</td>' + "".join(cells) + '</tr>')

    html = HTML_TEMPLATE.format(
        latest_date=dates[0].replace("-", "/") if dates else "-",
        headers=headers,
        rows="\n".join(rows),
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
                    "fedwatch_screen.py", "fedwatch_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update fedwatch report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/fedwatch.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("FRB利上げ/利下げ確率 チェック開始")

    try:
        futures = fetch_futures()
    except Exception as e:
        log(f"エラー: FF先物の取得に失敗しました: {e}")
        sys.exit(1)

    if len(futures) < 4:
        log(f"エラー: 取得できた限月が少なすぎます（{len(futures)}本）")
        sys.exit(1)

    log(f"FF先物: {len(futures)}限月取得")

    new_hist = build_history(futures)
    if not new_hist:
        log("エラー: 確率を計算できませんでした")
        sys.exit(1)

    hist = load_history()
    added = 0
    for d, rec in new_hist.items():
        if d not in hist or hist[d] != rec:
            hist[d] = rec
            added += 1
    save_history(hist)

    latest = max(hist)
    summary = " / ".join(f"{m}:{p:+d}%" for m, p in sorted(hist[latest].items()))
    log(f"更新: {added}日分（計 {len(hist)}日） 最新 {latest}: {summary}")

    generate_html(hist)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
