# -*- coding: utf-8 -*-
"""
VIX温度計（MACDクロスの答え合わせ） → HTML出力 → GitHub Pages公開

基本説:
  VIXのMACD(12-26-9)がデッドクロス(DC)  → VIXの勢いが弱まる → 株は上がりやすい期間
  VIXのMACD(12-26-9)がゴールデンクロス(GC) → VIXの勢いが強まる → 株は下がりやすい期間
だまし:
  GC後でもCFTCのE-mini S&P500投機筋ネットポジションでショートが極端に
  溜まっていると、踏み上げ（ショートカバー）で株が上がる「だまし」になりやすい。

構成:
  ① 今日の温度計カード（現在の状態・経過日数・CFTCポジション・だまし警戒）
  ② クロスイベント表（クロス〜次のクロスのS&P500/日経平均騰落を答え合わせ）
     青=説通り / 黄=GC後の上昇だがショート極端（だまし・説明可能） / 赤=逆行
  ③ 直近日次ミニ表（VIX・MACD・シグナル・株価前日比）

データ源:
  - VIX/S&P500/日経平均: yfinance（5年分。統計は5年、イベント表は直近2年を表示）
  - CFTC建玉: publicreporting.cftc.gov (Socrata API, legacy futures-only 6dca-aqww)
    E-MINI S&P 500 の非商業(投機筋) long-short。毎週金曜公表（火曜時点）。
    パーセンタイルは過去3年(156週)基準、下位20%以下を「ショート極端」とする。
履歴JSONなし（毎回再計算、flow/kinriと同方式）。
"""

import os
import sys
import json
import time
import subprocess
import datetime
import urllib.request
from statistics import mean

import pandas as pd
import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_HTML = "vix.html"

CFTC_URL = ("https://publicreporting.cftc.gov/resource/6dca-aqww.json"
            "?$where=starts_with(market_and_exchange_names,'E-MINI%20S%26P%20500%20-')"
            "&$select=report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all"
            "&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=170")

SHORT_WARN_NET = -150000  # ネット枚数がこれ以下なら「だまし：踏み上げ警戒」（濃黄）
SHORT_CAUTION_NET = -90000  # ネット枚数がこれ以下なら「だまし：踏み上げ要注意」（薄黄）
STOCHRSI_K_MAX = 30       # Stoch RSIのGCを「押し目完成」と数えるKの上限（売られすぎ圏）
STOCHRSI_K_MIN = 80       # Stoch RSIのDCを「天井完成」と数えるKの下限（買われすぎ圏）
EVENT_SHOW_YEARS = 2      # イベント表に表示する期間
DAILY_ROWS = 20           # 日次ミニ表の行数


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# データ取得
# -----------------------------------------
def fetch_prices():
    out = {}
    for t, name in [("^VIX", "VIX"), ("^GSPC", "SPX"), ("^N225", "N225")]:
        close = yf.Ticker(t).history(period="5y")["Close"].dropna()
        if len(close) < 300:
            raise RuntimeError(f"{t} のデータが不足（{len(close)}本）")
        close.index = close.index.tz_localize(None).normalize()
        out[name] = close
    log(f"  yfinance: VIX {len(out['VIX'])}本（最新 {out['VIX'].index[-1].date()}）")
    return out


def fetch_cftc():
    """CFTC投機筋ネットポジションの週次リスト [(date, net), ...] 新しい順"""
    req = urllib.request.Request(CFTC_URL, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    rows = []
    for d in data:
        try:
            net = int(d["noncomm_positions_long_all"]) - int(d["noncomm_positions_short_all"])
            rows.append((datetime.date.fromisoformat(d["report_date_as_yyyy_mm_dd"][:10]), net))
        except (KeyError, ValueError):
            continue
    rows.sort(reverse=True)
    if len(rows) < 100:
        raise RuntimeError(f"CFTCデータが不足（{len(rows)}週）")
    log(f"  CFTC: {len(rows)}週分（最新 {rows[0][0]} ネット {rows[0][1]:+,}枚）")
    return rows


def cftc_at(rows, date, window=156):
    """dateの直近公表値と、その時点から遡る3年のパーセンタイル(0=最ショート,100=最ロング)"""
    d = date.date() if hasattr(date, "date") else date
    hist = [(rd, n) for rd, n in rows if rd <= d]
    if not hist:
        return None, None, None
    rd, net = hist[0]
    base = sorted(n for _, n in hist[:window])
    below = sum(1 for b in base if b < net)
    pct = below / len(base) * 100
    return rd, net, pct


# -----------------------------------------
# MACDクロスと答え合わせ
# -----------------------------------------
def macd_cross(vix):
    """MACD 12-26-9。シグナル線はSMA（ユーザーのTradingViewインジ
    CM_Ult_MacD_MTF と同方式。標準のEMAシグナルではない点に注意）"""
    ema12 = vix.ewm(span=12, adjust=False).mean()
    ema26 = vix.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.rolling(9).mean()
    diff = (macd - signal).dropna()
    crosses = []
    for i in range(1, len(diff)):
        if diff.iloc[i-1] <= 0 < diff.iloc[i]:
            crosses.append((diff.index[i], "GC"))
        elif diff.iloc[i-1] >= 0 > diff.iloc[i]:
            crosses.append((diff.index[i], "DC"))
    return macd, signal, diff, crosses


def period_return(series, d0, d1):
    s = series[(series.index >= d0)]
    if d1 is not None:
        s = s[s.index <= d1]
    if len(s) < 2:
        return None
    return (s.iloc[-1] / s.iloc[0] - 1) * 100


def stoch_rsi(close, rsi_len=14, stoch_len=14, k_smooth=3, d_smooth=3):
    """TradingView標準の Stoch RSI (3,3,14,14)。K, D を返す"""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / rsi_len, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / rsi_len, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss)
    rmin = rsi.rolling(stoch_len).min()
    rmax = rsi.rolling(stoch_len).max()
    stoch = (rsi - rmin) / (rmax - rmin) * 100
    K = stoch.rolling(k_smooth).mean()
    D = K.rolling(d_smooth).mean()
    return K, D


def stochrsi_gc_dates(K, D, k_max):
    """KがDを上抜けした日のうち、K<=k_max（売られすぎ圏）のもの"""
    kd = (K - D).dropna()
    out = []
    for i in range(1, len(kd)):
        if kd.iloc[i-1] <= 0 < kd.iloc[i] and float(K.loc[kd.index[i]]) <= k_max:
            out.append(kd.index[i])
    return out


def stochrsi_dc_dates(K, D, k_min):
    """KがDを下抜けした日のうち、K>=k_min（買われすぎ圏）のもの"""
    kd = (K - D).dropna()
    out = []
    for i in range(1, len(kd)):
        if kd.iloc[i-1] >= 0 > kd.iloc[i] and float(K.loc[kd.index[i]]) >= k_min:
            out.append(kd.index[i])
    return out


def build_events(px, cftc_rows, crosses, srsi_gc, srsi_dc):
    """クロスイベントごとの答え合わせレコード（新しい順）

    GC: 「SPXのStoch RSIが売られすぎ圏(K<=30)でGCするまで」の騰落（押し目完成ルール）。
        先にVIXがDCすればそこで打ち切り（前提消滅）。
    DC: 「SPXのStoch RSIが買われすぎ圏(K>=80)でDCするまで」の騰落（天井完成ルール）。
        先にVIXがGCすればそこで打ち切り。日経も同じ区間で計測。
    """
    events = []
    last_px_date = px["SPX"].index[-1]
    for j, (d, kind) in enumerate(crosses):
        d_next = crosses[j+1][0] if j+1 < len(crosses) else None
        rd, net, pct = cftc_at(cftc_rows, d)
        rec = {
            "date": d, "kind": kind,
            "cftc_date": rd, "net": net, "pct": pct,
            "short_level": short_level(net),
            "end_reason": "",
        }
        if kind == "GC":
            exits = srsi_gc
            done_word, cut_word, wait_word = "押し目完成", "VIX DCで打切", "押し目待ち"
        else:
            exits = srsi_dc
            done_word, cut_word, wait_word = "天井完成", "VIX GCで打切", "天井待ち"
        hit = next((x for x in exits if x > d), None)
        if hit is not None and (d_next is None or hit <= d_next):
            end = hit
            rec["ongoing"] = False
            rec["end_reason"] = done_word
        elif d_next is not None:
            end = d_next
            rec["ongoing"] = False
            rec["end_reason"] = cut_word
        else:
            end = last_px_date
            rec["ongoing"] = True
            rec["end_reason"] = wait_word
        r_spx = period_return(px["SPX"], d, end)
        if r_spx is None:
            continue
        rec["spx"] = r_spx
        rec["n225"] = period_return(px["N225"], d, end)
        rec["days"] = (end - d).days
        rec["end_date"] = end
        events.append(rec)
    events.reverse()
    return events


def short_level(net):
    """CFTCネット枚数からだましレベルを返す: 2=警戒(-15万超), 1=要注意(-9万超), 0=なし"""
    if net is None:
        return 0
    if net <= SHORT_WARN_NET:
        return 2
    if net <= SHORT_CAUTION_NET:
        return 1
    return 0


def judge_cell(kind, r, level):
    """(css_class, 判定語)"""
    if r is None:
        return "", ""
    theory = (r > 0) if kind == "DC" else (r < 0)
    if theory:
        return "j-ok", "説通り"
    if kind == "GC" and r > 0 and level == 2:
        return "j-mid", "だまし：踏み上げ警戒"
    if kind == "GC" and r > 0 and level == 1:
        return "j-mid2", "だまし：踏み上げ要注意"
    return "j-ng", "逆行"


# -----------------------------------------
# HTML出力
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VIX温度計 - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 24px 0 10px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .cards {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }}
  .card {{
    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    padding: 14px 18px; min-width: 210px;
  }}
  .card .label {{ font-size: 0.75rem; color: #94a3b8; margin-bottom: 4px; }}
  .card .value {{ font-size: 1.35rem; font-weight: 700; color: #f8fafc; }}
  .card .sub {{ font-size: 0.78rem; color: #94a3b8; margin-top: 4px; line-height: 1.5; }}
  .card.warn {{ border-color: #f59e0b; }}
  .card.warn .value {{ color: #fbbf24; }}
  .state-dc {{ color: #4ade80; }}
  .state-gc {{ color: #f87171; }}
  .legend {{ display: flex; gap: 16px; margin: 12px 0; font-size: 0.8rem; color: #94a3b8; flex-wrap: wrap; }}
  .legend span {{ display: flex; align-items: center; gap: 5px; }}
  .sw {{ width: 12px; height: 12px; border-radius: 3px; display: inline-block; }}
  .table-wrap {{ overflow-x: auto; max-width: 1080px; }}
  table {{ border-collapse: collapse; font-size: 0.86rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 13px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child, thead th:nth-child(2) {{ text-align: left; }}
  td {{
    padding: 7px 13px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{ text-align: left; color: #94a3b8; }}
  td:nth-child(2) {{ text-align: left; font-weight: 700; }}
  tr:hover td {{ background: #16213a; }}
  tr.ongoing td {{ background: rgba(30,64,175,0.18); font-weight: 700; }}
  td.kind-dc {{ color: #4ade80; }}
  td.kind-gc {{ color: #f87171; }}
  td.j-ok {{ background: rgba(59,130,246,0.28); }}
  td.j-mid {{ background: rgba(251,191,36,0.32); }}
  td.j-mid2 {{ background: rgba(251,191,36,0.14); }}
  td.j-ng {{ background: rgba(239,68,68,0.34); }}
  td.pos {{ color: #4ade80; }}
  td.neg {{ color: #f87171; }}
  td[title] {{ cursor: help; }}
  .stats {{
    max-width: 1080px; margin: 10px 0; background: #1e293b;
    border: 1px solid #334155; border-radius: 8px; padding: 11px 16px;
    font-size: 0.84rem; line-height: 1.7; color: #cbd5e1;
  }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.8; max-width: 1080px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="map.html" style="border-color:#94a3b8">デッキの見方</a>
    <a href="calendar.html" style="border-color:#94a3b8">イベント予定</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
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
    <a href="vix.html" class="active" style="border-color:#d97706">VIX温度計</a>
    <a href="flow.html" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
  </nav>
  <h1>VIX温度計（MACDクロスの答え合わせ）</h1>
  <p class="subtitle">最終更新: {updated} | 出所: Yahoo Finance・CFTC | VIX MACD(12-26-9) | DC後=上がりやすい/GC後=下がりやすい(説)の検証</p>
  <div class="cards">
{cards}
  </div>
{stats}
  <h2>クロスイベントの答え合わせ（DC=天井完成まで / GC=押し目完成まで / 直近{show_years}年）</h2>
  <div class="legend">
    <span><span class="sw" style="background:rgba(59,130,246,0.6)"></span>説通り</span>
    <span><span class="sw" style="background:rgba(251,191,36,0.6)"></span>だまし：踏み上げ警戒（ショート-15万枚超）</span>
    <span><span class="sw" style="background:rgba(251,191,36,0.25)"></span>だまし：踏み上げ要注意（ショート-9万枚超）</span>
    <span><span class="sw" style="background:rgba(239,68,68,0.7)"></span>逆行</span>
    <span style="color:#64748b">※CFTC列は「クロス時点の直近公表値」。( )内は過去3年パーセンタイル（0%=最ショート）</span>
  </div>
  <div class="table-wrap">
  <table>
    <thead><tr><th>クロス日</th><th>種類</th><th>継続</th><th>終了日</th><th>終了理由</th><th>S&amp;P500</th><th>判定</th><th>日経平均</th><th>判定</th><th>CFTC投機筋ネット</th></tr></thead>
    <tbody>
{event_rows}
    </tbody>
  </table>
  </div>
  <h2>直近の日次データ（{daily_rows}営業日）</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>日付</th><th>状態</th><th>VIX</th><th>MACD</th><th>シグナル</th><th>乖離</th><th>S&amp;P500前日比</th><th>日経前日比</th></tr></thead>
    <tbody>
{daily_rows_html}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・<b>基本説</b>: VIXのMACDがデッドクロス(DC)=恐怖の勢いが弱まる→株高になりやすい。ゴールデンクロス(GC)=恐怖の勢いが強まる→株安になりやすい。<br>
    ・<b>GCの判定期間（押し目完成ルール）</b>: GC後の下落は「S&amp;P500のStoch RSI(3,3,14,14)が売られすぎ圏(K&le;{k_max})でK&gt;Dにゴールデンクロスするまで」続くとみなす。
    それが押し目完成=下落期間の終了。ただし先にVIXのMACDがDCに戻ったら前提消滅として打ち切り。<br>
    ・<b>DCの判定期間（天井完成ルール）</b>: DC後の上昇は「S&amp;P500のStoch RSIが買われすぎ圏(K&ge;{k_min})でK&lt;Dにデッドクロスするまで」続くとみなす。
    それが天井完成=上昇期間の終了。先にVIXのMACDがGCしたら打ち切り。<br>
    ・MACDは12-26-9、<b>シグナル線はSMA</b>（TradingViewのCM_Ult_MacD_MTFと同方式）。標準のEMAシグナルとはクロス日が1日前後ズレることがある。<br>
    ・<b>だまし（2段階）</b>: GC後でもCFTC E-mini S&amp;P500の投機筋ショートが溜まっていると踏み上げで株が上がりやすい。
    ネット<b>-15万枚超=踏み上げ警戒</b>（過去5年、GC後の逆行の主戦場）、<b>-9万〜-15万枚=踏み上げ要注意</b>（燃料あり）。
    -9万枚未満でのだましは5年で1回のみ（素直にGC通り読んでよい圏）。ただし-15万枚超でも説通り下げた例は複数あり、だまし確定ではなく「燃料がある」の意。<br>
    ・CFTC建玉は毎週金曜公表（火曜時点）のため数日のラグがある。<a href="https://publicreporting.cftc.gov/" style="color:#60a5fa">CFTC Public Reporting</a> / <a href="https://jp.investing.com/economic-calendar/cftc-s-p-500-speculative-positions-1619" style="color:#60a5fa">investing.com 表示版</a><br>
    ・DC側の勝率は生クロス（フィルタなし）ベースで、1〜数日で反転する「ヒゲ」も含むため体感より低めに出る。GC側は押し目完成ルール適用後の成績（「VIX DCで打切」の行は前提消滅のため参考値）。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def make_cards(px, macd, signal, diff, crosses, cftc_rows, K, D):
    vix_now = float(px["VIX"].iloc[-1])
    last_d, last_kind = crosses[-1]
    days = (diff.index[-1] - last_d).days
    rd, net, pct = cftc_at(cftc_rows, diff.index[-1])
    level = short_level(net)

    if last_kind == "DC":
        state_html = '<span class="state-dc">デッドクロス後</span>'
        state_sub = "株は上がりやすい期間（説）"
    else:
        state_html = '<span class="state-gc">ゴールデンクロス後</span>'
        state_sub = "株は下がりやすい期間（説）"

    cards = []
    cards.append(
        f'    <div class="card"><div class="label">現在の状態</div>'
        f'<div class="value">{state_html}</div>'
        f'<div class="sub">{last_d.date()} から {days}日経過<br>{state_sub}</div></div>')
    # GC中は押し目完成、DC中は天井完成（SPX Stoch RSIのクロス）待ちの状態を表示
    k_now, d_now = float(K.iloc[-1]), float(D.iloc[-1])
    if last_kind == "GC":
        if k_now <= STOCHRSI_K_MAX and k_now > d_now:
            dip_txt, dip_sub = "押し目完成", "売られすぎ圏でK>Dにクロス済み<br>下落期間は終了とみなす"
        elif k_now <= STOCHRSI_K_MAX:
            dip_txt, dip_sub = "売られすぎ圏", f"K<={STOCHRSI_K_MAX}に到達。K>Dのクロス待ち"
        else:
            dip_txt, dip_sub = "押し目待ち", f"K<={STOCHRSI_K_MAX}への低下 → K>Dクロスを待つ"
        label = "SPX Stoch RSI（押し目判定）"
    else:
        if k_now >= STOCHRSI_K_MIN and k_now < d_now:
            dip_txt, dip_sub = "天井完成", "買われすぎ圏でK<Dにクロス済み<br>上昇期間は終了とみなす"
        elif k_now >= STOCHRSI_K_MIN:
            dip_txt, dip_sub = "買われすぎ圏", f"K>={STOCHRSI_K_MIN}に到達。K<Dのクロス待ち"
        else:
            dip_txt, dip_sub = "天井待ち", f"K>={STOCHRSI_K_MIN}への上昇 → K<Dクロスを待つ"
        label = "SPX Stoch RSI（天井判定）"
    cards.append(
        f'    <div class="card"><div class="label">{label}</div>'
        f'<div class="value">{dip_txt}</div>'
        f'<div class="sub">K {k_now:.1f} / D {d_now:.1f}<br>{dip_sub}</div></div>')
    recent = " → ".join(f"{float(v):+.2f}" for v in diff.iloc[-4:])
    near = "（クロス際どい）" if abs(float(diff.iloc[-1])) < 0.05 else ""
    cards.append(
        f'    <div class="card"><div class="label">VIX</div>'
        f'<div class="value">{vix_now:.2f}</div>'
        f'<div class="sub">MACD {float(macd.iloc[-1]):+.2f} / シグナル {float(signal.iloc[-1]):+.2f}<br>'
        f'乖離の推移 {recent}{near}</div></div>')
    pct_txt = f"{pct:.0f}%" if pct is not None else "-"
    zone = ("だまし圏: 踏み上げ警戒 (-15万枚超)" if level == 2 else
            "だまし圏: 踏み上げ要注意 (-9万枚超)" if level == 1 else
            "だまし圏(-9万枚)まで余裕あり")
    cards.append(
        f'    <div class="card"><div class="label">CFTC投機筋ネット（{rd}時点）</div>'
        f'<div class="value">{net:+,}枚</div>'
        f'<div class="sub">{zone}<br>'
        f'<span title="その時点から遡る3年基準。0%=最ショート">参考: 過去3年パーセンタイル {pct_txt}</span></div></div>')
    if last_kind == "GC" and level == 2:
        cards.append(
            '    <div class="card warn"><div class="label">シグナル注意報</div>'
            '<div class="value">だまし：踏み上げ警戒</div>'
            '<div class="sub">GC後だがショートが-15万枚超<br>踏み上げによる上昇に注意</div></div>')
    elif last_kind == "GC" and level == 1:
        cards.append(
            '    <div class="card warn"><div class="label">シグナル注意報</div>'
            '<div class="value">だまし：踏み上げ要注意</div>'
            '<div class="sub">GC後でショートが-9万枚超<br>踏み上げの燃料はある水準</div></div>')
    return "\n".join(cards), (last_kind, days, net, pct)


def make_stats(events):
    """5年分イベントの勝率集計。DC=天井完成、GC=押し目完成に至った回のみを本則として集計"""
    parts = []
    for kind, jp, done in [("DC", "デッドクロス後（上がれば説通り・天井完成まで）", "天井完成"),
                           ("GC", "ゴールデンクロス後（下がれば説通り・押し目完成まで）", "押し目完成")]:
        evs = [e for e in events if e["kind"] == kind and e["end_reason"] == done]
        if not evs:
            continue
        for idx, label in [("spx", "S&P500"), ("n225", "日経平均")]:
            rs = [e[idx] for e in evs if e[idx] is not None]
            if not rs:
                continue
            win = sum(1 for r in rs if (r > 0 if kind == "DC" else r < 0))
            if idx == "spx":
                dama = sum(1 for e in evs if kind == "GC" and e["spx"] is not None
                           and e["spx"] > 0 and e["short_level"] >= 1)
                dama_txt = f"（うち だまし {dama}回）" if kind == "GC" else ""
                parts.append(f"<b>{jp}</b> {label}: {win}/{len(rs)}回 説通り "
                             f"({win/len(rs)*100:.0f}%){dama_txt} 平均{mean(rs):+.2f}%")
            else:
                parts.append(f"{label}: {win}/{len(rs)}回 ({win/len(rs)*100:.0f}%) 平均{mean(rs):+.2f}%")
    cut_gc = sum(1 for e in events if e["kind"] == "GC" and e["end_reason"] == "VIX DCで打切")
    cut_dc = sum(1 for e in events if e["kind"] == "DC" and e["end_reason"] == "VIX GCで打切")
    return ('  <div class="stats">過去5年の成績: ' + " ／ ".join(parts) +
            f"<br>※完成前にVIXが反対クロスして打ち切り（統計から除外）: DC側{cut_dc}回 / GC側{cut_gc}回</div>")


def make_event_rows(events):
    cutoff = pd.Timestamp(datetime.date.today() - datetime.timedelta(days=365 * EVENT_SHOW_YEARS))
    rows = []
    for e in events:  # 新しい順（最新のクロスが最上段）
        if e["date"] < cutoff:
            continue
        kind_cls = "kind-dc" if e["kind"] == "DC" else "kind-gc"
        kind_txt = "DC" if e["kind"] == "DC" else "GC"
        tr_cls = ' class="ongoing"' if e["ongoing"] else ""
        days_txt = f'{e["days"]}日' + ("(進行中)" if e["ongoing"] else "")

        reason = e["end_reason"]
        end_txt = "-" if e["ongoing"] else str(e["end_date"].date())
        cells = [f'<td>{e["date"].date()}</td>',
                 f'<td class="{kind_cls}">{kind_txt}</td>',
                 f'<td>{days_txt}</td>',
                 f'<td>{end_txt}</td>',
                 f'<td style="text-align:left; color:#94a3b8; font-size:0.8rem">{reason}</td>']
        for idx in ["spx", "n225"]:
            r = e[idx]
            cls, word = judge_cell(e["kind"], r, e["short_level"])
            if r is None:
                cells.append("<td>-</td><td>-</td>")
                continue
            pn = "pos" if r > 0 else "neg"
            tip = ""
            if cls == "j-mid":
                tip = f' title="GC後の上昇だがショート-15万枚超（ネット{e["net"]:+,}枚）→ 踏み上げの可能性大"'
            elif cls == "j-mid2":
                tip = f' title="GC後の上昇でショート-9万枚超（ネット{e["net"]:+,}枚）→ 踏み上げの燃料あり"'
            cells.append(f'<td class="{pn}">{r:+.2f}%</td><td class="{cls}"{tip}>{word}</td>')
        if e["net"] is not None:
            mark = " ⚠⚠" if e["short_level"] == 2 else (" ⚠" if e["short_level"] == 1 else "")
            pct_tip = f' title="参考: 過去3年パーセンタイル {e["pct"]:.0f}%（0%=最ショート）"' if e["pct"] is not None else ""
            cells.append(f'<td{pct_tip}>{e["net"]:+,}枚{mark}</td>')
        else:
            cells.append("<td>-</td>")
        rows.append(f"      <tr{tr_cls}>" + "".join(cells) + "</tr>")
    return "\n".join(rows)


def make_daily_rows(px, macd, signal, diff, crosses):
    spx_chg = px["SPX"].pct_change() * 100
    n_chg = px["N225"].pct_change() * 100
    rows = []
    for d in reversed(diff.index[-DAILY_ROWS:]):
        state = "DC" if diff.loc[d] < 0 else "GC"
        st_cls = "kind-dc" if state == "DC" else "kind-gc"
        sv = spx_chg.get(d)
        nv = n_chg.get(d)

        def pchg(v):
            if v is None or pd.isna(v):
                return "<td>-</td>"
            pn = "pos" if v > 0 else "neg"
            return f'<td class="{pn}">{v:+.2f}%</td>'
        rows.append(
            f'      <tr><td>{d.date()}</td><td class="{st_cls}">{state}側</td>'
            f'<td>{float(px["VIX"].loc[d]):.2f}</td>'
            f'<td>{float(macd.loc[d]):+.3f}</td><td>{float(signal.loc[d]):+.3f}</td>'
            f'<td>{float(diff.loc[d]):+.3f}</td>{pchg(sv)}{pchg(nv)}</tr>')
    return "\n".join(rows)


def generate_html(px, cftc_rows):
    macd, signal, diff, crosses = macd_cross(px["VIX"])
    K, D = stoch_rsi(px["SPX"])
    srsi_gc = stochrsi_gc_dates(K, D, STOCHRSI_K_MAX)
    srsi_dc = stochrsi_dc_dates(K, D, STOCHRSI_K_MIN)
    events = build_events(px, cftc_rows, crosses, srsi_gc, srsi_dc)
    cards, state = make_cards(px, macd, signal, diff, crosses, cftc_rows, K, D)

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        cards=cards,
        stats=make_stats(events),
        event_rows=make_event_rows(events),
        daily_rows_html=make_daily_rows(px, macd, signal, diff, crosses),
        show_years=EVENT_SHOW_YEARS,
        daily_rows=DAILY_ROWS,
        k_max=STOCHRSI_K_MAX,
        k_min=STOCHRSI_K_MIN,
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")

    kind, days, net, pct = state
    log(f"  現在: {kind}後 {days}日経過 / VIX {float(px['VIX'].iloc[-1]):.2f} / "
        f"CFTCネット {net:+,}枚 ({pct:.0f}%)")


# -----------------------------------------
# GitHub Pages 自動 push
# -----------------------------------------
def push_to_github():
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "vix_screen.py", "vix_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update vix report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/vix.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("VIX温度計 チェック開始")

    try:
        px = fetch_prices()
    except Exception as e:
        log(f"エラー: 株価データの取得に失敗しました: {e}")
        sys.exit(1)

    try:
        cftc_rows = fetch_cftc()
    except Exception as e:
        log(f"CFTCの取得に失敗（だまし判定なしで続行）: {e}")
        cftc_rows = []

    generate_html(px, cftc_rows)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
