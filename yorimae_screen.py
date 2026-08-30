# -*- coding: utf-8 -*-
"""
寄り前チェック（夜間先物ギャップ×ADR） → yorimae.html → GitHub Pages公開

ディーラーの朝のミーティング資料の再現。毎朝7:15（CME引け後・東京寄り前）に実行し、
「今日の寄り付きがどこから始まり、その動きは米株・為替で説明がつくのか」を1ページで出す。

① 夜間先物ギャップ
  CME日経平均先物（円建て NIY=F、なければドル建て NKD=F）の夜間終値と
  前日の日経平均終値のギャップ。現物はほぼ夜間先物の水準に揃って寄り付くため、
  これが「今朝の寄り付き目安」になる。

② 理論ギャップとの答え合わせ（kinri流）
  夜間の日経先物の変動は、理屈上「米株(S&P500)の変動×ベータ + ドル円の変動×為替感応度」で
  だいたい説明できる。過去1年の日次データで回帰係数を自前推定し、
    理論ギャップ% = b1 × S&P500前日騰落% + b2 × ドル円変化%
  を計算。実測ギャップとの乖離 = 日本固有の夜間要因（海外勢の日本株への強弱など）。
  乖離が小さければ青（理論通り）、大きければ赤（日本固有要因あり）で表示。
  ※ドル円の「前日東京引け時点」は取得できないためNY終値で近似。厳密な分解ではなく目安。

③ ADRギャップ
  主要ADR銘柄（NYSE/OTC）の米国終値を円換算し、東京の前日終値と比較。
  ADR倍率（1ADR=何株）はハードコードせず、直近20日の (ADR×ドル円)÷東京終値 の中央値を
  「平常時の換算比率」として使う（株式分割や倍率変更に自動追随。平均ギャップは定義上ほぼ0になり、
  今朝の値=夜間に付いた固有のプレミアム/ディスカウントを表す）。

データ: yfinance のみ。実行: 毎朝7:15（yorimae_run.bat）。--nopush でpush省略。
"""

import os
import sys
import time
import subprocess
import datetime

import numpy as np
import pandas as pd
import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_HTML = "yorimae.html"

# 主要ADR銘柄（ADRティッカー, 東証コード, 表示名）
ADR_LIST = [
    ("TM", "7203.T", "トヨタ"),
    ("SONY", "6758.T", "ソニーG"),
    ("MUFG", "8306.T", "三菱UFJ"),
    ("SMFG", "8316.T", "三井住友FG"),
    ("MFG", "8411.T", "みずほFG"),
    ("HMC", "7267.T", "ホンダ"),
    ("TAK", "4502.T", "武田薬品"),
    ("NMR", "8604.T", "野村HD"),
    ("IX", "8591.T", "オリックス"),
    ("NTDOY", "7974.T", "任天堂"),
    ("SFTBY", "9984.T", "ソフトバンクG"),
    ("HTHIY", "6501.T", "日立"),
    ("TOELY", "8035.T", "東京エレクトロン"),
    ("FRCOY", "9983.T", "ファーストリテ"),
    ("KMTUY", "6301.T", "コマツ"),
]

BETA_LOOKBACK = 250   # 理論ギャップの回帰に使う日数（約1年）
RATIO_WINDOW = 20     # ADR換算比率の中央値を取る日数
DEVIATION_TH = 0.5    # 実測-理論ギャップの乖離がこの%を超えたら「日本固有要因あり」(赤)


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# データ取得
# -----------------------------------------
def last_price(ticker, prefer_intraday=True):
    """直近の取引値。夜間・時間外を含む最新値が欲しいので5分足の最後を優先"""
    t = yf.Ticker(ticker)
    if prefer_intraday:
        try:
            h = t.history(period="2d", interval="5m")["Close"].dropna()
            if len(h):
                return float(h.iloc[-1])
        except Exception:
            pass
    try:
        h = t.history(period="5d")["Close"].dropna()
        if len(h):
            return float(h.iloc[-1])
    except Exception:
        pass
    return None


def daily_closes(ticker, period="2y"):
    try:
        h = yf.Ticker(ticker).history(period=period)["Close"].dropna()
        h.index = h.index.tz_localize(None).normalize()
        return h
    except Exception:
        return pd.Series(dtype=float)


# -----------------------------------------
# 理論ギャップの回帰係数（過去1年: 日経リターン ~ SPX前日リターン + ドル円変化）
# -----------------------------------------
def estimate_betas(n225, spx, fx):
    """b0 + b1*SPX前日リターン + b2*ドル円日次変化 で日経日次リターンを回帰。
    (b1, b2, 決定係数R2) を返す。データ不足時はNone"""
    n_ret = n225.pct_change().dropna()
    s_ret = spx.pct_change().dropna()
    f_ret = fx.pct_change().dropna()

    rows = []
    for t, y in n_ret.iloc[-BETA_LOOKBACK:].items():
        # 東京のt日に対して「その時点で判明している直近の米国終値リターン」= t-1日以前の最新
        s = s_ret.asof(t - pd.Timedelta(days=1))
        f = f_ret.asof(t)
        if pd.notna(s) and pd.notna(f):
            rows.append((y, s, f))
    if len(rows) < 100:
        return None
    arr = np.array(rows)
    y, X = arr[:, 0], np.column_stack([np.ones(len(arr)), arr[:, 1], arr[:, 2]])
    coef, res, _, _ = np.linalg.lstsq(X, y, rcond=None)
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - res[0] / ss_tot if len(res) and ss_tot > 0 else None
    return coef[1], coef[2], r2


# -----------------------------------------
# ADRギャップ
# -----------------------------------------
def calc_adr_gaps():
    log("ADRギャップを計算中...")
    fx_now = last_price("JPY=X")
    out = []
    for adr, tyo, name in ADR_LIST:
        try:
            a = daily_closes(adr, period="3mo")
            j = daily_closes(tyo, period="3mo")
            f = daily_closes("JPY=X", period="3mo")
            if len(a) < RATIO_WINDOW or len(j) < RATIO_WINDOW:
                continue
            # 平常時の換算比率: 直近20日の (ADR×為替)/東京終値 の中央値
            df = pd.DataFrame({"a": a, "f": f}).dropna()
            df["af"] = df["a"] * df["f"]
            merged = pd.concat([df["af"], j], axis=1, keys=["af", "j"]).dropna()
            if len(merged) < RATIO_WINDOW:
                continue
            ratio = float((merged["af"] / merged["j"]).iloc[-RATIO_WINDOW:].median())
            adr_last = float(a.iloc[-1])          # 今朝の米国終値
            tyo_prev = float(j.iloc[-1])          # 東京の前日終値
            implied = adr_last * (fx_now or float(f.iloc[-1])) / ratio
            gap = (implied / tyo_prev - 1) * 100
            out.append({"name": name, "adr": adr, "tyo": tyo.replace(".T", ""),
                        "tyo_prev": tyo_prev, "implied": implied, "gap": gap})
        except Exception as e:
            log(f"  {adr}: skip ({e})")
    out.sort(key=lambda r: -r["gap"])
    log(f"  ADR: {len(out)}銘柄")
    return out


# -----------------------------------------
# HTML
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>寄り前チェック - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 22px 0 10px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .cards {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 14px; }}
  .card {{
    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    padding: 14px 18px; min-width: 215px;
  }}
  .card .label {{ font-size: 0.75rem; color: #94a3b8; margin-bottom: 4px; }}
  .card .value {{ font-size: 1.35rem; font-weight: 700; color: #f8fafc; }}
  .card .sub {{ font-size: 0.78rem; color: #94a3b8; margin-top: 4px; line-height: 1.5; }}
  .pos {{ color: #4ade80; font-weight: 700; }}
  .neg {{ color: #f87171; font-weight: 700; }}
  .ok {{ color: #93c5fd; font-weight: 700; }}
  .warn {{ color: #fca5a5; font-weight: 700; }}
  .table-wrap {{ overflow-x: auto; max-width: 900px; }}
  table {{ border-collapse: collapse; font-size: 0.85rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 8px 12px;
    text-align: right; font-weight: 600; white-space: nowrap;
  }}
  thead th:nth-child(-n+2) {{ text-align: left; }}
  td {{
    padding: 7px 12px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:nth-child(-n+2) {{ text-align: left; }}
  tr:hover td {{ background: #16213a; }}
  .evidence {{
    background: rgba(59,130,246,0.08); border: 1px solid rgba(59,130,246,0.3);
    border-radius: 8px; padding: 10px 14px; font-size: 0.8rem; line-height: 1.8;
    max-width: 1100px; margin-bottom: 16px;
  }}
  .evidence b {{ color: #93c5fd; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.9; max-width: 1100px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="map.html" style="border-color:#94a3b8">デッキの見方</a>
    <a href="calendar.html" style="border-color:#94a3b8">イベント予定</a>
    <a href="yorimae.html" class="active" style="border-color:#94a3b8">寄り前</a>
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
  <h1>寄り前チェック — 夜間先物ギャップ × ADR</h1>
  <p class="subtitle">最終更新: {updated}（毎朝7:15・CME引け後） | データ: CME日経先物・S&amp;P500・ドル円・主要ADR（yfinance）</p>
  <div class="evidence">
    <b>使い方:</b> 日経平均の現物は夜間先物の水準にほぼ揃って寄り付くため、①のギャップが「今朝の寄り付き目安」。
    ②はその夜間変動が<b>米株とドル円で説明がつくか</b>の答え合わせ（過去1年の回帰で理論値を計算）。
    実測と理論の乖離が小さければ<span class="ok">理論通り（青）</span>=米国由来の動き、
    乖離が±{dev_th}%超なら<span class="warn">日本固有要因（赤）</span>=海外勢の日本株への強弱や日本関連ニュースが夜間に動いた可能性。
    ③のADRギャップは個別銘柄の「今朝の寄り付き目安」。プラス=米国市場で東京終値より高く買われた。
  </div>
{cards}
  <h2>ADRギャップ（米国終値の円換算 vs 東京前日終値）</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>銘柄</th><th>コード</th><th>東京前日終値</th><th>ADR円換算</th><th>ギャップ</th></tr></thead>
    <tbody>
{adr_rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・<b>夜間先物</b> = CME日経平均先物（円建てNIY=F、取得不可時はドル建てNKD=F）の直近値。大証ナイトセッションとほぼ同水準。<br>
    ・<b>理論ギャップ</b> = b1×S&amp;P500前日騰落% + b2×ドル円変化%（係数は過去1年の日次回帰で自前推定、ページ下部に係数表示）。
    ドル円の起点はNY終値で近似しているため厳密な分解ではなく目安。<br>
    ・<b>ADR換算比率</b> = 直近{ratio_window}日の（ADR価格×ドル円）÷東京終値の中央値。倍率をハードコードしないため株式分割にも自動追随する。
    定義上、平常時のギャップは0近辺になり、表示される値は「昨晩ついた固有のプレミアム/ディスカウント」。<br>
    ・ADRは米国での流動性が薄い銘柄（OTC系）ほどノイズが大きい。大型のTM/SONY/MUFG等を優先的に信頼する。<br>
    ・{beta_note}
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def fmt_pct(v, digits=2):
    if v is None:
        return "-"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+.{digits}f}%</span>'


def generate_html(data):
    cards = []

    n225_prev = data["n225_prev"]
    fut = data["fut_last"]
    gap_pct = data["gap_pct"]
    gap_yen = data["gap_yen"]

    cards.append(
        f'    <div class="cards">\n'
        f'    <div class="card"><div class="label">日経平均 前日終値</div>'
        f'<div class="value">{n225_prev:,.0f}円</div>'
        f'<div class="sub">{data["n225_date"]} 大引け</div></div>\n')

    if fut is not None:
        cards.append(
            f'    <div class="card"><div class="label">CME日経先物（夜間）→ 寄り付き目安</div>'
            f'<div class="value">{fut:,.0f}円</div>'
            f'<div class="sub">ギャップ {fmt_pct(gap_pct)}（{gap_yen:+,.0f}円）・{data["fut_ticker"]}</div></div>\n')
    else:
        cards.append(
            '    <div class="card"><div class="label">CME日経先物（夜間）</div>'
            '<div class="value">取得失敗</div><div class="sub">yfinance側の一時的な問題の可能性</div></div>\n')

    theo = data["theo_gap"]
    if theo is not None and gap_pct is not None:
        dev = gap_pct - theo
        if abs(dev) <= DEVIATION_TH:
            judge = '<span class="ok">理論通り（米国由来の動き）</span>'
        else:
            direction = "日本買い" if dev > 0 else "日本売り"
            judge = f'<span class="warn">日本固有要因あり（{direction}方向に{abs(dev):.2f}%）</span>'
        cards.append(
            f'    <div class="card"><div class="label">理論ギャップとの答え合わせ</div>'
            f'<div class="value">{fmt_pct(theo)}</div>'
            f'<div class="sub">実測 {fmt_pct(gap_pct)} − 理論 = 乖離 {dev:+.2f}%<br>{judge}</div></div>\n')

    cards.append(
        f'    <div class="card"><div class="label">S&amp;P500（前日）</div>'
        f'<div class="value">{fmt_pct(data["spx_ret"])}</div>'
        f'<div class="sub">NASDAQ {fmt_pct(data["ndx_ret"])}</div></div>\n'
        f'    <div class="card"><div class="label">ドル円</div>'
        f'<div class="value">{data["fx_now"]:.2f}円</div>'
        f'<div class="sub">NY前日終値比 {fmt_pct(data["fx_chg"])}</div></div>\n'
        '    </div>')

    adr_rows = []
    for r in data["adr"]:
        adr_rows.append(
            f'      <tr><td>{r["name"]}</td><td>{r["tyo"]} / {r["adr"]}</td>'
            f'<td>{r["tyo_prev"]:,.0f}円</td><td>{r["implied"]:,.0f}円</td>'
            f'<td>{fmt_pct(r["gap"])}</td></tr>')

    if data["betas"]:
        b1, b2, r2 = data["betas"]
        beta_note = (f'<b>回帰係数（過去{BETA_LOOKBACK}日）</b>: 日経リターン ≈ {b1:.2f}×S&P500前日 + {b2:.2f}×ドル円変化'
                     f'（決定係数R²={r2:.2f}）' if r2 is not None else
                     f'<b>回帰係数（過去{BETA_LOOKBACK}日）</b>: b1={b1:.2f}, b2={b2:.2f}')
    else:
        beta_note = '回帰係数: データ不足のため理論ギャップは非表示'

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        dev_th=DEVIATION_TH,
        ratio_window=RATIO_WINDOW,
        cards="".join(cards),
        adr_rows="\n".join(adr_rows),
        beta_note=beta_note,
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")


# -----------------------------------------
# push
# -----------------------------------------
def push_to_github():
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)  # 他スクリプトとのgit競合・放置ロック・中断rebase対策
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "yorimae_screen.py", "yorimae_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update yorimae report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/yorimae.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("寄り前チェック 開始")

    # 日経平均の前日終値
    n225 = daily_closes("^N225")
    if n225.empty:
        log("エラー: 日経平均を取得できません")
        sys.exit(1)
    n225_prev = float(n225.iloc[-1])
    n225_date = n225.index[-1].strftime("%m/%d")

    # 夜間先物（円建て優先）
    fut_last, fut_ticker = None, None
    for tk in ("NIY=F", "NKD=F"):
        v = last_price(tk)
        if v and v > 10000:  # 日経水準のサニティチェック
            fut_last, fut_ticker = v, tk
            break
    gap_pct = (fut_last / n225_prev - 1) * 100 if fut_last else None
    gap_yen = fut_last - n225_prev if fut_last else None
    log(f"日経前日終値 {n225_prev:,.0f} / 夜間先物 {fut_last} ({fut_ticker}) / ギャップ {gap_pct}")

    # 米株・ドル円
    spx = daily_closes("^GSPC")
    ndx = daily_closes("^IXIC")
    fx = daily_closes("JPY=X")
    spx_ret = float((spx.iloc[-1] / spx.iloc[-2] - 1) * 100) if len(spx) >= 2 else None
    ndx_ret = float((ndx.iloc[-1] / ndx.iloc[-2] - 1) * 100) if len(ndx) >= 2 else None
    fx_now = last_price("JPY=X") or (float(fx.iloc[-1]) if len(fx) else None)
    fx_chg = (fx_now / float(fx.iloc[-1]) - 1) * 100 if fx_now and len(fx) else None

    # 理論ギャップ
    betas = estimate_betas(n225, spx, fx)
    theo_gap = None
    if betas and spx_ret is not None and fx_chg is not None:
        b1, b2, _ = betas
        theo_gap = b1 * spx_ret + b2 * fx_chg
    log(f"S&P500前日 {spx_ret} / ドル円変化 {fx_chg} / 理論ギャップ {theo_gap}")

    # ADR
    adr = calc_adr_gaps()

    generate_html({
        "n225_prev": n225_prev, "n225_date": n225_date,
        "fut_last": fut_last, "fut_ticker": fut_ticker,
        "gap_pct": gap_pct, "gap_yen": gap_yen,
        "spx_ret": spx_ret, "ndx_ret": ndx_ret,
        "fx_now": fx_now, "fx_chg": fx_chg,
        "theo_gap": theo_gap, "betas": betas,
        "adr": adr,
    })

    if "--nopush" in sys.argv:
        log("push スキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
