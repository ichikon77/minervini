# -*- coding: utf-8 -*-
"""
イベント予定（マーケットカレンダー） → HTML出力 → GitHub Pages公開

これから来るイベントを近い順にリスト表示する。
  - FOMC / 日銀金融政策決定会合（公表済み年間日程を手動リスト管理）
  - 日本SQ（毎月第2金曜、3/6/9/12月はメジャーSQ）・米トリプルウィッチング
    （3/6/9/12月の第3金曜）はルールで自動計算
  - ビッグテック決算（MSFT/AMZN/GOOGL/NVDA、判明分を手動リスト管理）
  - 米雇用統計・米CPI（BLS公表スケジュールを手動リスト管理）

手動リストは年に数回の追記が必要。残りが少なくなると実行ログに警告を出す。
"""

import os
import sys
import time
import subprocess
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_HTML = "calendar.html"

# =========================================================
# 手動管理の日程リスト（公表され次第追記する）
# =========================================================

# FOMC結果発表日（現地）。日本時間では翌日早朝（夏3:00/冬4:00）
FOMC_DATES = [
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    "2027-01-27", "2027-03-17", "2027-04-28",
]

# 日銀会合の最終日（結果発表日）。出所: 日銀サイト
BOJ_DATES = [
    "2026-07-31", "2026-09-18", "2026-10-30", "2026-12-18",
]

# ビッグテック決算（現地日付）。yfinance calendar等で判明分を管理
EARNINGS = [
    ("2026-07-23", "GOOGL", "Alphabet 決算"),
    ("2026-07-23", "TSLA", "Tesla 決算"),
    ("2026-07-30", "MSFT", "Microsoft 決算"),
    ("2026-07-30", "META", "Meta 決算"),
    ("2026-07-31", "AMZN", "Amazon 決算"),
    ("2026-07-31", "AAPL", "Apple 決算"),
    ("2026-08-27", "NVDA", "NVIDIA 決算"),
    # SPCX(SpaceX)は上場直後で決算日程未公表。判明したら追記
]

# 米雇用統計（BLS公表スケジュール、現地8:30 = 日本21:30/22:30）
EMPLOYMENT_DATES = [
    "2026-08-07", "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
]

# 米CPI（BLS公表スケジュール、現地8:30）
CPI_DATES = [
    "2026-08-12", "2026-09-11", "2026-10-14", "2026-11-10", "2026-12-10",
]

# 水星逆行（開始日, 終了日）。出所: Old Farmer's Almanac
MERCURY_RETRO = [
    ("2026-06-29", "2026-07-23"),
    ("2026-10-24", "2026-11-13"),
    ("2027-02-09", "2027-03-03"),
    ("2027-06-10", "2027-07-04"),
    ("2027-10-07", "2027-10-28"),
]

# 米国市場の休場日（NYSE公式ルールから算出。年1回、翌年分を追記）
US_HOLIDAYS = [
    ("2026-09-07", "レイバーデー"),
    ("2026-11-26", "感謝祭"),
    ("2026-12-25", "クリスマス"),
    ("2027-01-01", "元日"),
    ("2027-01-18", "キング牧師記念日"),
    ("2027-02-15", "大統領の日"),
    ("2027-03-26", "聖金曜日"),
    ("2027-05-31", "戦没将兵記念日"),
    ("2027-06-18", "ジューンティーンス"),
    ("2027-07-05", "独立記念日"),
    ("2027-09-06", "レイバーデー"),
    ("2027-11-25", "感謝祭"),
    ("2027-12-24", "クリスマス"),
]

# 選挙関連（株価に影響の大きいもの。判明分を手動管理）
ELECTION_DATES = [
    ("2026-11-03", "🇺🇸 米中間選挙", "上院1/3・下院全議席改選。選挙年秋は荒れやすいアノマリー"),
]

# リスト残数がこの日数を切ったらログで警告
WARN_DAYS = 45


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# =========================================================
# ルール計算（SQ）
# =========================================================
def nth_friday(year, month, n):
    """その月のn番目の金曜日"""
    out = []
    cur = datetime.date(year, month, 1)
    while cur.month == month:
        if cur.weekday() == 4:
            out.append(cur)
        cur += datetime.timedelta(days=1)
    return out[n - 1]


def jp_sq_dates(start, months=14):
    """日本SQ: 毎月第2金曜。3/6/9/12月はメジャーSQ"""
    out = []
    y, m = start.year, start.month
    for _ in range(months):
        d = nth_friday(y, m, 2)
        if d >= start:
            major = m in (3, 6, 9, 12)
            out.append((d, "メジャーSQ" if major else "SQ", major))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def us_triple_witching(start, months=14):
    """米トリプルウィッチング: 3/6/9/12月の第3金曜"""
    out = []
    y, m = start.year, start.month
    for _ in range(months):
        if m in (3, 6, 9, 12):
            d = nth_friday(y, m, 3)
            if d >= start:
                out.append(d)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


# =========================================================
# イベント一覧の構築
# =========================================================
def build_events(today):
    """(date, 国旗, 名称, 補足, リンクhtml, 強調) のリスト（未来分のみ・日付順）"""
    ev = []

    def link(href, label):
        return f'<a href="{href}">{label}</a>'

    for s in FOMC_DATES:
        d = datetime.date.fromisoformat(s)
        if d >= today:
            ev.append((d, "🇺🇸", "FOMC（結果発表）",
                       "日本時間 翌日早朝", link("fedwatch.html", "FRB利上げ確率"), True))

    for s in BOJ_DATES:
        d = datetime.date.fromisoformat(s)
        if d >= today:
            ev.append((d, "🇯🇵", "日銀金融政策決定会合（結果発表）",
                       "昼頃発表・総裁会見15:30", link("totan.html", "日銀利上げ確率"), True))

    for d, kind, major in jp_sq_dates(today):
        ev.append((d, "🇯🇵", ("⚡ " if major else "") + kind,
                   "先物・オプション清算" if major else "オプション清算",
                   link("saitei.html", "裁定取引"), major))

    for d in us_triple_witching(today):
        ev.append((d, "🇺🇸", "⚡ トリプルウィッチング",
                   "指数先物・指数OP・個別OP同時満期", "", True))

    for s, ticker, name in EARNINGS:
        d = datetime.date.fromisoformat(s)
        if d >= today:
            ev.append((d, "🇺🇸", name, f"{ticker}・引け後発表（日本時間 翌朝）",
                       link("flow.html", "資金フロー"), False))

    for s in EMPLOYMENT_DATES:
        d = datetime.date.fromisoformat(s)
        if d >= today:
            ev.append((d, "🇺🇸", "米雇用統計",
                       "日本時間 21:30/22:30", link("fedwatch.html", "FRB利上げ確率"), False))

    for s in CPI_DATES:
        d = datetime.date.fromisoformat(s)
        if d >= today:
            ev.append((d, "🇺🇸", "米CPI",
                       "日本時間 21:30/22:30", link("fedwatch.html", "FRB利上げ確率"), False))

    for start_s, end_s in MERCURY_RETRO:
        start = datetime.date.fromisoformat(start_s)
        end = datetime.date.fromisoformat(end_s)
        if start >= today:
            ev.append((start, "☿", "水星逆行 開始",
                       f"{end.month}/{end.day}まで", "", False))
        if end >= today:
            note = "進行中→" if start < today else ""
            ev.append((end, "☿", "水星逆行 終了", note + "順行に戻る", "", False))

    for ds, name in US_HOLIDAYS:
        d = datetime.date.fromisoformat(ds)
        if d >= today:
            ev.append((d, "🇺🇸", f"米国市場 休場（{name}）",
                       "NY市場クローズ。日本は通常取引だが薄商い", "", False))

    for ds, name, note in ELECTION_DATES:
        d = datetime.date.fromisoformat(ds)
        if d >= today:
            ev.append((d, "", name, note, "", True))

    ev.sort(key=lambda x: x[0])
    return ev


def check_list_freshness(today):
    """手動リストの残りが少なければ警告"""
    for name, dates in [("FOMC_DATES", FOMC_DATES), ("BOJ_DATES", BOJ_DATES),
                        ("EARNINGS", [e[0] for e in EARNINGS]),
                        ("EMPLOYMENT_DATES", EMPLOYMENT_DATES), ("CPI_DATES", CPI_DATES)]:
        future = [d for d in dates if datetime.date.fromisoformat(d) >= today]
        if not future:
            log(f"  警告: {name} の未来日程が尽きました。追記が必要です")
        else:
            last = max(datetime.date.fromisoformat(d) for d in future)
            if (last - today).days < WARN_DAYS:
                log(f"  警告: {name} の最終日程が{(last - today).days}日後。そろそろ追記を")


# =========================================================
# HTML出力
# =========================================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>イベント予定 - {updated_date}</title>
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
  table {{ border-collapse: collapse; font-size: 0.9rem; max-width: 900px; width: 100%; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 14px;
    text-align: left; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  td {{
    padding: 9px 14px; border-bottom: 1px solid #1e293b;
    white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  tr:hover td {{ background: #16213a; }}
  tr.ghead td {{
    background: #1e293b; color: #fbbf24; font-weight: 700;
    font-size: 0.82rem; padding: 7px 14px; border-top: 2px solid #334155;
  }}
  tr.ghead:hover td {{ background: #1e293b; }}
  tr.soon td {{ background: rgba(30,64,175,0.18); }}
  tr.today td {{ background: rgba(251,191,36,0.18); font-weight: 700; }}
  td.days {{ color: #94a3b8; text-align: right; }}
  td.days.near {{ color: #fbbf24; font-weight: 700; }}
  td.event {{ font-weight: 600; }}
  td.event.big {{ color: #f8fafc; }}
  td.detail {{ color: #64748b; font-size: 0.8rem; white-space: normal; }}
  td.deck a {{ color: #60a5fa; text-decoration: none; font-size: 0.8rem; }}
  td.deck a:hover {{ text-decoration: underline; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.8; max-width: 900px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="map.html" style="border-color:#94a3b8">デッキの見方</a>
    <a href="calendar.html" class="active" style="border-color:#94a3b8">イベント予定</a>
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
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
  </nav>
  <h1>イベント予定</h1>
  <p class="subtitle">最終更新: {updated} | FOMC・日銀会合・SQ・ビッグテック決算・米指標 | 近い順</p>
  <table>
    <thead><tr><th>日付</th><th style="text-align:right">あと</th><th>イベント</th><th>補足</th><th>関連デッキ</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
  <p class="note">
    ・SQ=毎月第2金曜（3/6/9/12月は⚡メジャーSQ=先物+オプション同時清算）。米⚡トリプルウィッチング=3/6/9/12月の第3金曜。いずれもルールから自動計算。<br>
    ・☿水星逆行はアノマリー参考（相場の乱れ・急変が多いとされる期間）。出所: Old Farmer's Almanac。<br>
    ・FOMC/日銀会合/決算/米指標の日程は公表分を手動管理（新日程の公表時に追記。残り{warn_days}日を切るとログに警告）。<br>
    ・米指標の発表は現地8:30（日本時間: 夏21:30/冬22:30）。FOMC結果は日本時間翌日早朝（夏3:00/冬4:00）。米決算は引け後（日本時間翌朝5〜7時頃）。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""

WEEKDAYS_JP = ["月", "火", "水", "木", "金", "土", "日"]


def generate_html(events, today):
    rows = []
    current_group = None

    def group_of(d):
        delta = (d - today).days
        # 今週=今度の日曜まで、来週=その次の日曜まで
        days_to_sunday = 6 - today.weekday()
        if delta <= days_to_sunday:
            return "今週"
        if delta <= days_to_sunday + 7:
            return "来週"
        if d.month == today.month and d.year == today.year:
            return "今月"
        return f"{d.year}年{d.month}月"

    shown = 0
    for d, flag, name, detail, deck, big in events:
        if shown >= 40:
            break
        g = group_of(d)
        if g != current_group:
            rows.append(f'      <tr class="ghead"><td colspan="5">【{g}】</td></tr>')
            current_group = g
        delta = (d - today).days
        days_txt = "今日" if delta == 0 else f"{delta}日"
        tr_cls = ' class="today"' if delta == 0 else (' class="soon"' if delta <= 3 else "")
        days_cls = ' class="days near"' if delta <= 3 else ' class="days"'
        ev_cls = ' class="event big"' if big else ' class="event"'
        wd = WEEKDAYS_JP[d.weekday()]
        rows.append(
            f"      <tr{tr_cls}><td>{d.month}/{d.day}({wd})</td>"
            f"<td{days_cls}>{days_txt}</td>"
            f"<td{ev_cls}>{flag} {name}</td>"
            f'<td class="detail">{detail}</td>'
            f'<td class="deck">{deck}</td></tr>')
        shown += 1

    html = HTML_TEMPLATE.format(
        updated_date=today.isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        rows="\n".join(rows),
        warn_days=WARN_DAYS,
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}（{shown}件）")


# -----------------------------------------
# GitHub Pages 自動 push
# -----------------------------------------
def push_to_github():
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "calendar_screen.py", "calendar_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update calendar " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/calendar.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("イベント予定 チェック開始")
    today = datetime.date.today()

    check_list_freshness(today)
    events = build_events(today)
    log(f"未来のイベント: {len(events)}件（直近: {events[0][0]} {events[0][2]}）" if events else "イベントなし")

    generate_html(events, today)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
