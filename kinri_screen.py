# -*- coding: utf-8 -*-
"""
金利と為替（答え合わせダッシュボード） → HTML出力 → GitHub Pages公開

日米の金利・イールドカーブ・ドル円を並べ、「理論通りに動いているか」を検証する。
  理論1: 日米10年金利差が拡大 → 円安（ドル円上昇）、縮小 → 円高
  理論2: US10Y-US02Y（米カーブ）拡大 → 米銀行株(KBE)上昇
  理論3: JP10Y-JP02Y（日カーブ）拡大 → 日本の銀行株(1615.T)上昇
理論通り=青、逆行=赤（逆行は「何かが起きている」アラート）、微動=判定なし。

データ源:
  - US10Y: yfinance ^TNX / US02Y: yfinance 2YY=F(2年利回り先物)
  - JP10Y/JP02Y: 財務省 国債金利情報CSV（公式・前営業日分）
  - ドル円/KBE/1615.T: yfinance
履歴保存は不要（毎回2年分を取得して計算）。
"""

import os
import re
import sys
import json
import time
import subprocess
import datetime

import requests
import pandas as pd
import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_HTML = "kinri.html"

MOF_ALL_URL = "https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv"
MOF_CUR_URL = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

# 表示列: (ラベル, 営業日数)  年初来は別計算
PERIODS = [("3営業日", 3), ("5営業日", 5), ("15営業日", 15),
           ("1ヶ月", 21), ("3ヶ月", 63)]
COLS = ["3営業日", "5営業日", "15営業日", "1ヶ月", "3ヶ月", "年初来"]

# 判定の閾値（これ未満の微動はノイズ扱いで判定しない）
RATE_TH_BPS = 2.0   # 金利・スプレッドの変化
PX_TH_PCT = 0.3     # ドル円・銀行株の変化


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# データ取得
# -----------------------------------------
def wareki_to_date(s):
    """'R8.7.1' -> datetime.date(2026, 7, 1)"""
    m = re.match(r"([RHS])(\d+)\.(\d+)\.(\d+)", s.strip())
    if not m:
        return None
    base = {"R": 2018, "H": 1988, "S": 1925}[m.group(1)]
    try:
        return datetime.date(base + int(m.group(2)), int(m.group(3)), int(m.group(4)))
    except ValueError:
        return None


def fetch_jgb():
    """財務省CSVから JP02Y / JP10Y の日次系列を取得（全期間+当月分をマージ）"""
    recs = {}
    for url in [MOF_ALL_URL, MOF_CUR_URL]:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        txt = r.content.decode("shift_jis", errors="replace")
        for line in txt.splitlines():
            cols = line.split(",")
            if len(cols) < 11:
                continue
            d = wareki_to_date(cols[0])
            if d is None or d.year < 2023:
                continue

            def num(x):
                x = x.strip()
                try:
                    return float(x)
                except ValueError:
                    return None
            v2, v10 = num(cols[2]), num(cols[10])
            if v2 is not None and v10 is not None:
                recs[d] = (v2, v10)
    if not recs:
        raise RuntimeError("財務省CSVから金利データを取得できませんでした")
    idx = sorted(recs)
    jp2 = pd.Series([recs[d][0] for d in idx], index=pd.to_datetime(idx))
    jp10 = pd.Series([recs[d][1] for d in idx], index=pd.to_datetime(idx))
    log(f"  JGB金利: {len(idx)}日分（最新 {idx[-1]}）")
    return jp2, jp10


def fetch_yf():
    """yfinanceから US10Y/US02Y/ドル円/KBE/1615.T を取得"""
    tickers = ["^TNX", "2YY=F", "USDJPY=X", "KBE", "1615.T"]
    data = yf.download(tickers, period="2y", auto_adjust=True,
                       progress=False, group_by="ticker", threads=True)
    out = {}
    for t in tickers:
        close = data[t]["Close"].dropna()
        if len(close) < 100:
            raise RuntimeError(f"{t} のデータが不足（{len(close)}本）")
        close.index = close.index.tz_localize(None).normalize()
        out[t] = close
    log(f"  yfinance: {len(tickers)}銘柄（最新 {out['USDJPY=X'].index[-1].date()}）")
    return out


def build_frame():
    """全系列を日付でマージした DataFrame（前値埋め）"""
    jp2, jp10 = fetch_jgb()
    y = fetch_yf()
    df = pd.DataFrame({
        "US10Y": y["^TNX"],
        "US02Y": y["2YY=F"],
        "JP10Y": jp10,
        "JP02Y": jp2,
        "USDJPY": y["USDJPY=X"],
        "KBE": y["KBE"],
        "JPBANK": y["1615.T"],
    }).sort_index()
    df = df.ffill().dropna()
    df["US_CURVE"] = df["US10Y"] - df["US02Y"]      # 米イールドカーブ
    df["JP_CURVE"] = df["JP10Y"] - df["JP02Y"]      # 日イールドカーブ
    df["USJP10"] = df["US10Y"] - df["JP10Y"]        # 日米10年金利差
    return df


# -----------------------------------------
# 変化量の計算
# -----------------------------------------
def changes(df, col, kind):
    """各期間の変化量を返す。kind='rate'ならbps、'px'なら%"""
    s = df[col]
    cur = float(s.iloc[-1])
    out = {"今日": cur}
    for label, n in PERIODS:
        if len(s) > n:
            base = float(s.iloc[-1 - n])
            out[label] = (cur - base) * 100 if kind == "rate" else (cur / base - 1) * 100
        else:
            out[label] = None
    # 年初来（前年末終値比）
    year = s.index[-1].year
    prev = s[s.index.year < year]
    if len(prev):
        base = float(prev.iloc[-1])
        out["年初来"] = (cur - base) * 100 if kind == "rate" else (cur / base - 1) * 100
    else:
        out["年初来"] = None
    return out


# -----------------------------------------
# 判定（答え合わせ）
# -----------------------------------------
def judge(drv_bps, ans_pct, drv_name, ans_name):
    """
    drv_bps: 理論の起点（金利差の変化 bps）、ans_pct: 答え側（価格の変化 %）
    戻り値: (css_class, tooltip)
    """
    if drv_bps is None or ans_pct is None:
        return "", ""
    tip = f"{drv_name} {drv_bps:+.1f}bps / {ans_name} {ans_pct:+.2f}%"
    if abs(drv_bps) < RATE_TH_BPS or abs(ans_pct) < PX_TH_PCT:
        return "j-none", tip + " → 微動のため判定なし"
    if (drv_bps > 0) == (ans_pct > 0):
        return "j-ok", tip + " → 同方向（理論通り）"
    return "j-ng", tip + " → 逆行（要注意）"


# -----------------------------------------
# FRB/日銀の織り込み差コメント
# -----------------------------------------
def orikomi_comment():
    try:
        fed = json.load(open(os.path.join(SCRIPT_DIR, "fedwatch_history.json"), encoding="utf-8"))
        boj = json.load(open(os.path.join(SCRIPT_DIR, "totan_history.json"), encoding="utf-8"))
        fed_latest = fed[sorted(fed)[-1]]
        boj_latest = boj[sorted(boj)[-1]]
        # 今後6ヶ月以内の会合の確率を合算（積み上げ利上げ幅の期待値の proxy）
        today = datetime.date.today()
        lim = today + datetime.timedelta(days=185)

        def within(meetings):
            tot, items = 0.0, []
            for k in sorted(meetings):
                y, m = int(k[:4]), int(k[5:7])
                d = datetime.date(y, m, 28)
                if today <= d <= lim:
                    tot += meetings[k]
                    items.append(f"{m}月{meetings[k]:+.0f}%")
            return tot, items
        f_tot, f_items = within(fed_latest)
        b_tot, b_items = within(boj_latest)
        diff = f_tot - b_tot
        if diff > 15:
            concl = "米側の利上げ観測が強く、日米金利差は<b>拡大方向 → 円安バイアス</b>"
        elif diff < -15:
            concl = "日本側の利上げ観測が強く、日米金利差は<b>縮小方向 → 円高バイアス</b>"
        else:
            concl = "日米の利上げ観測は拮抗しており、金利差の方向感は<b>中立</b>"
        return (f"今後6ヶ月の織り込み: FRB {'/'.join(f_items)}（計{f_tot:+.0f}%） vs "
                f"日銀 {'/'.join(b_items)}（計{b_tot:+.0f}%） → {concl}")
    except Exception as e:
        log(f"  織り込みコメント生成をスキップ: {e}")
        return ""


# -----------------------------------------
# HTML出力
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>金利と為替 - {updated_date}</title>
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
  .legend {{ display: flex; gap: 16px; margin-bottom: 14px; font-size: 0.8rem; color: #94a3b8; flex-wrap: wrap; }}
  .legend span {{ display: flex; align-items: center; gap: 5px; }}
  .sw {{ width: 12px; height: 12px; border-radius: 3px; display: inline-block; }}
  .table-wrap {{ overflow-x: auto; max-width: 980px; }}
  table {{ border-collapse: collapse; font-size: 0.87rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 13px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child, thead th:nth-child(2) {{ text-align: left; }}
  td {{
    padding: 8px 13px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{ text-align: left; font-weight: 700; }}
  td.desc {{ color: #64748b; font-size: 0.76rem; text-align: left; }}
  td.now {{ font-weight: 700; color: #f8fafc; }}
  tr:hover td {{ filter: brightness(1.2); }}
  tr.sep td {{ border-top: 2px solid #334155; }}
  td.j-ok {{ background: rgba(59,130,246,0.28); }}
  td.j-ng {{ background: rgba(239,68,68,0.34); font-weight: 700; }}
  td.j-none {{ color: #64748b; }}
  td[title] {{ cursor: help; }}
  .orikomi {{
    max-width: 980px; margin-top: 16px; background: #1e293b;
    border: 1px solid #334155; border-radius: 8px; padding: 12px 16px;
    font-size: 0.85rem; line-height: 1.7; color: #cbd5e1;
  }}
  .orikomi b {{ color: #fbbf24; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.8; max-width: 980px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
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
    <a href="kinri.html" class="active">金利と為替</a>
    <a href="daikin.html">売買代金</a>
    <a href="shinyou.html">信用評価率</a>
    <a href="shutai.html">投資主体別</a>
    <a href="gaikoku.html">海外投資家</a>
    <a href="touraku.html">騰落レシオ</a>
    <a href="karauri.html">空売り比率</a>
    <a href="riron.html">日経理論株価</a>
    <a href="spriron.html">SP500理論株価</a>
    <a href="flow.html">資金フロー</a>
    <a href="map.html">デッキの見方</a>
  </nav>
  <h1>金利と為替（答え合わせダッシュボード）</h1>
  <p class="subtitle">最終更新: {updated} | 出所: 財務省・Yahoo Finance | 金利=bps変化（1bp=0.01%）、ドル円=%変化</p>
  <div class="legend">
    <span><span class="sw" style="background:rgba(59,130,246,0.6)"></span>理論通り（同方向）</span>
    <span><span class="sw" style="background:rgba(239,68,68,0.7)"></span>逆行（何かが起きているアラート）</span>
    <span><span class="sw" style="background:#334155"></span>微動のため判定なし</span>
    <span style="color:#64748b">※判定セルにマウスを乗せると根拠の数値を表示</span>
  </div>
  <div class="table-wrap">
  <table>
    <thead><tr><th>系列</th><th>説明</th><th>今日</th>{period_headers}</tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
{orikomi}
  <p class="note">
    ・<b>理論1</b>: 日米10年金利差が拡大→円安（ドル円上昇）、縮小→円高。ドル円の行は日米10Y差の変化と突き合わせて判定。<br>
    ・<b>理論2</b>: US10Y−US02Y（米イールドカーブ）拡大→米銀行株(KBE)上昇。米カーブの行はKBEと突き合わせ。<br>
    ・<b>理論3</b>: JP10Y−JP02Y（日カーブ）拡大→日本の銀行株(1615 東証銀行ETF)上昇。日カーブの行は1615.Tと突き合わせ。<br>
    ・<span style="color:#f87171">赤（逆行）が続く場合</span>は、金利以外の要因（介入観測・リスクオフ・信用不安など）が支配しているサイン。<br>
    ・US02YはCBOT 2年利回り先物(2YY=F)の値。日本国債金利は財務省公表値（前営業日分）。閾値: 金利±{rate_th}bps未満・価格±{px_th}%未満は判定なし。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def fmt_chg(v, kind):
    if v is None:
        return "-"
    return f"{v:+.1f}bps" if kind == "rate" else f"{v:+.2f}%"


def dir_style(v, kind):
    """判定なし行の方向色（ごく薄い緑/赤）"""
    if v is None:
        return ""
    th = RATE_TH_BPS if kind == "rate" else PX_TH_PCT
    if abs(v) < th:
        return ""
    c = "34,197,94" if v > 0 else "239,68,68"
    return f' style="background:rgba({c},0.10)"'


def generate_html(df):
    us10 = changes(df, "US10Y", "rate")
    us02 = changes(df, "US02Y", "rate")
    jp10 = changes(df, "JP10Y", "rate")
    jp02 = changes(df, "JP02Y", "rate")
    fx = changes(df, "USDJPY", "px")
    usc = changes(df, "US_CURVE", "rate")
    jpc = changes(df, "JP_CURVE", "rate")
    usjp = changes(df, "USJP10", "rate")
    kbe = changes(df, "KBE", "px")
    jpb = changes(df, "JPBANK", "px")

    rows_html = []

    def plain_row(name, desc, ch, kind, fmt_now, sep=False):
        tds = [f'<td>{name}</td><td class="desc">{desc}</td><td class="now">{fmt_now}</td>']
        for c in COLS:
            v = ch.get(c)
            tds.append(f"<td{dir_style(v, kind)}>{fmt_chg(v, kind)}</td>")
        cls = ' class="sep"' if sep else ""
        rows_html.append(f"      <tr{cls}>" + "".join(tds) + "</tr>")

    def judged_row(name, desc, ch, kind, fmt_now, drv_ch, drv_name, ans_ch, ans_name,
                   drv_is_self, sep=False):
        """drv_is_self=True: この行自身が理論の起点（カーブ行）で答えは価格側。
           False: この行が答え側（ドル円行）で起点は金利差。"""
        tds = [f'<td>{name}</td><td class="desc">{desc}</td><td class="now">{fmt_now}</td>']
        for c in COLS:
            v = ch.get(c)
            if drv_is_self:
                cls, tip = judge(v, ans_ch.get(c), drv_name, ans_name)
            else:
                cls, tip = judge(drv_ch.get(c), v, drv_name, ans_name)
            attr = f' class="{cls}"' if cls else ""
            attr += f' title="{tip}"' if tip else ""
            tds.append(f"<td{attr}>{fmt_chg(v, kind)}</td>")
        cls_tr = ' class="sep"' if sep else ""
        rows_html.append(f"      <tr{cls_tr}>" + "".join(tds) + "</tr>")

    plain_row("US10Y", "米10年金利", us10, "rate", f'{us10["今日"]:.3f}%')
    plain_row("US02Y", "米2年金利", us02, "rate", f'{us02["今日"]:.3f}%')
    plain_row("JP10Y", "日10年金利", jp10, "rate", f'{jp10["今日"]:.3f}%')
    plain_row("JP02Y", "日2年金利", jp02, "rate", f'{jp02["今日"]:.3f}%')
    judged_row("ドル円", "vs 日米10Y差(理論1)", fx, "px", f'{fx["今日"]:.2f}円',
               usjp, "日米10Y差", None, "ドル円", drv_is_self=False)
    judged_row("US10Y-US02Y", "米カーブ vs 米銀行株(理論2)", usc, "rate",
               f'{usc["今日"] * 100:+.1f}bps', None, "米カーブ", kbe, "米銀行株KBE",
               drv_is_self=True, sep=True)
    judged_row("JP10Y-JP02Y", "日カーブ vs 日銀行株(理論3)", jpc, "rate",
               f'{jpc["今日"] * 100:+.1f}bps', None, "日カーブ", jpb, "東証銀行1615",
               drv_is_self=True)

    period_headers = "".join(f"<th>{c}</th>" for c in COLS)
    ori = orikomi_comment()
    ori_html = f'  <div class="orikomi">{ori}</div>' if ori else ""

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        period_headers=period_headers,
        rows="\n".join(rows_html),
        orikomi=ori_html,
        rate_th=f"{RATE_TH_BPS:.0f}",
        px_th=f"{PX_TH_PCT:.1f}",
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")

    # コンソールにも要約表示
    log(f"  US10Y {us10['今日']:.3f}% / JP10Y {jp10['今日']:.3f}% / "
        f"日米差 {usjp['今日'] * 100:.0f}bps / ドル円 {fx['今日']:.2f}円")


# -----------------------------------------
# GitHub Pages 自動 push
# -----------------------------------------
def push_to_github():
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "kinri_screen.py", "kinri_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update kinri report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/kinri.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("金利と為替 チェック開始")

    try:
        df = build_frame()
    except Exception as e:
        log(f"エラー: データの取得に失敗しました: {e}")
        sys.exit(1)

    log(f"マージ後: {len(df)}日分（最新 {df.index[-1].date()}）")

    generate_html(df)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
