# -*- coding: utf-8 -*-
"""
円安/円高相関ランキング（USD/JPYとの日次リターン相関） → fx_corr.html → GitHub Pages公開

考え方:
  個別銘柄の日次リターンとUSD/JPYの日次リターンの相関係数を計算し、
  「円安で株価が上がりやすい銘柄(相関が高い)」「円高で株価が上がりやすい銘柄(相関が低い/負)」
  をそれぞれTop50でランキングする。輸出企業(自動車・電機・機械)は円安と相関が高くなりやすく、
  内需・輸入コスト型(食品・小売・電力等)は円安と相関が低い/負になりやすいという一般的な傾向の
  検証・スクリーニング用途。

相関期間: 過去1年（直近の市場構造を反映）と過去3年（より安定した傾向）の両方を算出・表示。
対象銘柄: 時価総額1,000億円以上・直近四半期営業黒字（kijitsu_screen.pyと同一ユニバース・
  margin_all_history.json + margin_mcap_cache.json + margin_fundamentals.jsonを再利用）。
対話相手の為替: USD/JPY（yfinance "JPY=X"）。

注意: これは統計的な相関関係であり因果や今後も同じ関係が続く保証ではない。
  為替感応度は事業構造の変化（ヘッジ比率・海外生産比率等）で変わるため、
  単独の売買根拠ではなく他の情報と重ねる1要素として使う。

データ: yfinance（株価・USD/JPY）。
実行: 毎日（fx_corr_run.bat）。--test で100銘柄のみ・push無し。
"""

import os
import sys
import json
import time
import subprocess
import datetime

import yfinance as yf
import pandas as pd
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MARGIN_HISTORY = os.path.join(SCRIPT_DIR, "margin_all_history.json")
MCAP_CACHE = os.path.join(SCRIPT_DIR, "margin_mcap_cache.json")
FUNDAMENTALS = os.path.join(SCRIPT_DIR, "margin_fundamentals.json")
REPORT_HTML = "fx_corr.html"

MIN_MCAP_OKU = 1000     # 時価総額の下限（億円）
TOP_N = 50              # 円安/円高それぞれのランキング件数
CHUNK = 150
PRICE_HISTORY_DAYS = 1150  # 3年分+バッファ


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def progress(label, i, total, t0):
    if i == 0:
        return
    elapsed = time.time() - t0
    eta = elapsed / i * (total - i)
    log(f"  {label} {i}/{total} ({i/total*100:.0f}%) 経過{elapsed/60:.0f}分 残り約{eta/60:.0f}分")


# -----------------------------------------
# 対象銘柄（kijitsu_screen.pyと同一ユニバース: 時価総額1,000億円以上・営業黒字）
# -----------------------------------------
def load_universe():
    with open(MARGIN_HISTORY, encoding="utf-8") as f:
        h = json.load(f)
    names = h["names"]
    caps = {}
    if os.path.exists(MCAP_CACHE):
        caps = json.load(open(MCAP_CACHE, encoding="utf-8"))
    codes = sorted(c for c in names if (caps.get(c) or 0) >= MIN_MCAP_OKU)

    dropped = 0
    if os.path.exists(FUNDAMENTALS):
        fund = json.load(open(FUNDAMENTALS, encoding="utf-8")).get("stocks", {})
        kept = []
        for c in codes:
            q = fund.get(c, {}).get("quarters")
            if q and q[0][2] is not None and q[0][2] < 0:
                dropped += 1
                continue  # 直近四半期の営業赤字
            kept.append(c)
        codes = kept
        log(f"  営業赤字フィルタ: {dropped}銘柄を除外")
    return codes, names, caps


# -----------------------------------------
# USD/JPY・日経平均取得
# -----------------------------------------
def fetch_usdjpy():
    log("USD/JPYを取得中...")
    df = yf.download("JPY=X", period="3y", auto_adjust=True, progress=False)
    close = df["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    close = close.dropna()
    log(f"  USD/JPY: {len(close)}日分")
    return close


def fetch_nikkei():
    """日経平均自体もUSD/JPYと正相関を持つため、個別銘柄の対日経平均の超過リターンを
    為替と相関させることで「市場全体の為替感応度を除いた、その銘柄固有の為替感応度」を測る"""
    log("日経平均を取得中...")
    df = yf.download("^N225", period="3y", auto_adjust=True, progress=False)
    close = df["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    close = close.dropna()
    log(f"  日経平均: {len(close)}日分")
    return close


# -----------------------------------------
# 相関係数計算
# -----------------------------------------
def calc_correlations(codes, names, caps, fx_close, nikkei_close):
    log(f"相関係数を計算中（{len(codes)}銘柄）...")
    fx_ret_all = fx_close.pct_change().dropna()
    nk_ret_all = nikkei_close.pct_change().dropna()
    # 日経平均自体のUSD/JPYとの相関（超過リターン相関の比較対象として表示）
    nikkei_corr_6m = _corr_over_period(nk_ret_all, fx_ret_all, days=126)
    nikkei_corr_1y = _corr_over_period(nk_ret_all, fx_ret_all, days=252)
    nikkei_corr_3y = _corr_over_period(nk_ret_all, fx_ret_all, days=756)
    log(f"  日経平均自体のUSD/JPY相関: 6ヶ月={nikkei_corr_6m} 1年={nikkei_corr_1y} 3年={nikkei_corr_3y}")

    results = []
    t0 = time.time()
    for ci in range(0, len(codes), CHUNK):
        chunk = codes[ci:ci + CHUNK]
        tickers = [c + ".T" for c in chunk]
        try:
            data = yf.download(tickers, period="3y", auto_adjust=True,
                               progress=False, group_by="ticker", threads=True)
        except Exception as e:
            log(f"  chunk dl error: {e}")
            continue
        for c in chunk:
            ticker = c + ".T"
            try:
                close = data[ticker]["Close"].dropna()
            except Exception:
                continue
            if len(close) < 260:
                continue
            stock_ret = close.pct_change().dropna()
            # 対日経平均の超過リターン（同じ日付だけ揃えて引く）
            common_idx = stock_ret.index.intersection(nk_ret_all.index)
            excess_ret = stock_ret.loc[common_idx] - nk_ret_all.loc[common_idx]

            corr_6m = _corr_over_period(stock_ret, fx_ret_all, days=126)
            corr_1y = _corr_over_period(stock_ret, fx_ret_all, days=252)
            corr_3y = _corr_over_period(stock_ret, fx_ret_all, days=756)
            excess_corr_6m = _corr_over_period(excess_ret, fx_ret_all, days=126)
            excess_corr_1y = _corr_over_period(excess_ret, fx_ret_all, days=252)
            excess_corr_3y = _corr_over_period(excess_ret, fx_ret_all, days=756)
            if corr_6m is None and corr_1y is None and corr_3y is None:
                continue
            results.append({
                "code": c,
                "name": names.get(c, c),
                "mcap": caps.get(c, 0),
                "corr_6m": corr_6m,
                "corr_1y": corr_1y,
                "corr_3y": corr_3y,
                "excess_corr_6m": excess_corr_6m,
                "excess_corr_1y": excess_corr_1y,
                "excess_corr_3y": excess_corr_3y,
                "price": float(close.iloc[-1]),
            })
        progress("相関計算", min(ci + CHUNK, len(codes)), len(codes), t0)
    log(f"計算完了: {len(results)}銘柄")
    return results, nikkei_corr_6m, nikkei_corr_1y, nikkei_corr_3y


def _corr_over_period(stock_ret, fx_ret, days):
    # daysは252営業日/年の米国基準。日本市場は年245日程度しかなく、
    # period="3y"取得だと3年分でも約735日 < 756日になるため、
    # 「完全にdays分あること」を要求せず8割以上あれば直近分で計算する
    # （2026-08-16: 全銘柄で3年相関がNoneになるバグの修正）
    s = stock_ret.iloc[-days:]
    if len(s) < days * 0.8:  # データ不足（新規上場等）は算出しない
        return None
    f = fx_ret.reindex(s.index).dropna()
    common = s.index.intersection(f.index)
    if len(common) < days * 0.7:
        return None
    c = np.corrcoef(s.loc[common], f.loc[common])[0, 1]
    if np.isnan(c):
        return None
    return round(float(c), 3)


# -----------------------------------------
# HTML
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>円安/円高相関ランキング - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.1rem; margin: 24px 0 10px; color: #f1f5f9; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .evidence {{
    background: rgba(59,130,246,0.08); border: 1px solid rgba(59,130,246,0.3);
    border-radius: 8px; padding: 10px 14px; font-size: 0.8rem; line-height: 1.8;
    max-width: 1150px; margin-bottom: 16px;
  }}
  .evidence b {{ color: #93c5fd; }}
  .tables-wrap {{ display: flex; flex-direction: column; gap: 28px; }}
  .table-col {{ max-width: 1150px; }}
  .table-wrap {{ overflow: auto; max-height: 620px; }}
  table {{ border-collapse: collapse; font-size: 0.82rem; width: 100%; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 7px 10px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:nth-child(-n+2) {{ text-align: left; }}
  td {{
    padding: 6px 10px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:nth-child(-n+2) {{ text-align: left; }}
  tr:hover td {{ background: #16213a; }}
  .rank {{ color: #64748b; font-size: 0.76rem; }}
  .yenweak td {{ }}
  .yenweak .corr {{ color: #f87171; font-weight: 600; }}
  .yenstrong .corr {{ color: #60a5fa; font-weight: 600; }}
  .dim {{ color: #64748b; font-size: 0.74rem; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 20px; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.9; max-width: 1150px; }}
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
    <a href="fx_corr.html" class="active" style="border-color:#db2777">円安/円高相関</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>円安/円高相関ランキング — USD/JPYとの日次リターン相関</h1>
  <p class="subtitle">最終更新: {updated} | 対象: 時価総額{min_mcap}億円以上・直近四半期の営業黒字（{n_total}銘柄で算出） | 相関期間: 6ヶ月・1年・3年 | 参考: 日経平均自体のUSD/JPY相関 6ヶ月{nikkei_corr_6m}・1年{nikkei_corr_1y}・3年{nikkei_corr_3y}</p>
  <div class="evidence">
    <b>考え方:</b> 各銘柄の日次リターンとUSD/JPYの日次リターンの相関係数を算出。
    <b>相関が高い(+に近い)=USD/JPYが上がる(円安)と株価も上がりやすい銘柄</b>（輸出企業・海外売上比率が高い銘柄に多い傾向）、
    <b>相関が低い/負=円安で株価が下がりやすい・円高で株価が上がりやすい銘柄</b>（内需・輸入コスト型に多い傾向）。<br>
    ・<b>単純相関</b>=個別銘柄の日次リターンそのものと為替の相関。日経平均自体もUSD/JPYと正の相関（表の上に表示）を持つため、
    単純相関が高いだけでは「市場全体が円安に反応しているだけ」を拾ってしまう可能性がある。<br>
    ・<b>対日経平均・超過相関</b>=個別銘柄のリターンから日経平均のリターンを引いた「超過リターン」と為替の相関。
    市場全体の動きを除いた、<b>その銘柄固有の為替感応度</b>に近い指標。単純相関と超過相関が両方高い銘柄ほど、
    「市場平均以上に為替に敏感」という傾向が強いと解釈できる。<br>
    ・6ヶ月相関=足元の市場構造、1年相関=直近の傾向、3年相関=より安定した長期傾向。すべて同じ方向を向いている銘柄ほど傾向が安定している目安。<br>
    ・<b>セルの色（超過相関のみ）</b>=6ヶ月/1年/3年の各カラム独立に、その表の方向（円安表=プラスが大きい順・円高表=マイナスが大きい順）で
    全銘柄中の上位30位以内=濃い赤・上位50位以内=薄い赤。<b>3期間すべてに色が付いている銘柄=期間によらず為替感応度が上位</b>で傾向が安定。<br>
    ・個別銘柄の日次リターンは決算・地合い等のノイズが大きいため、相関係数自体は0.2〜0.4程度に収まることが多く、
    0.6を超えるような強い相関はまれ。値の大小そのものよりランキングの相対比較として見る。<br>
    ・これは統計的な相関関係であり因果関係ではない。為替感応度は事業構造の変化（ヘッジ比率・海外生産移管等）で変わるため、
    単独の売買根拠ではなく他の情報と重ねる1要素として使う。<br>
    ・営業赤字フィルタ = 直近四半期の営業利益が赤字の銘柄は除外（<a href="margin.html" style="color:#60a5fa">銘柄チェッカー</a>の週次業績データより）。
  </div>
  <div class="tables-wrap">
    <div class="table-col">
      <h2>円安相関 Top{top_n}（対日経平均・超過相関(1年)が高い順）</h2>
      <div class="table-wrap">
      <table>
        <thead><tr>
          <th>コード</th><th>銘柄</th><th>時価総額<br>(億円)</th><th>現値</th>
          <th>単純相関<br>(6ヶ月)</th><th>単純相関<br>(1年)</th><th>単純相関<br>(3年)</th>
          <th>超過相関<br>(6ヶ月)</th><th>超過相関<br>(1年)</th><th>超過相関<br>(3年)</th>
        </tr></thead>
        <tbody>
{rows_weak}
        </tbody>
      </table>
      </div>
    </div>
    <div class="table-col">
      <h2>円高相関 Top{top_n}（対日経平均・超過相関(1年)が低い/負の順）</h2>
      <div class="table-wrap">
      <table>
        <thead><tr>
          <th>コード</th><th>銘柄</th><th>時価総額<br>(億円)</th><th>現値</th>
          <th>単純相関<br>(6ヶ月)</th><th>単純相関<br>(1年)</th><th>単純相関<br>(3年)</th>
          <th>超過相関<br>(6ヶ月)</th><th>超過相関<br>(1年)</th><th>超過相関<br>(3年)</th>
        </tr></thead>
        <tbody>
{rows_strong}
        </tbody>
      </table>
      </div>
    </div>
  </div>
  <p class="note">
    ・<b>ランキングのソート基準</b> = 対日経平均・超過相関の1年値を優先（市場全体の為替感応度を除いた、その銘柄固有の感応度でランキング）。
    超過相関(1年)が無い銘柄は超過相関(6ヶ月)→(3年)→単純相関(1年)の順で代替。6ヶ月列は足元の変化を見る参考、単純相関も参考として併記。<br>
    ・<b>データ不足銘柄</b> = 新規上場等で6ヶ月/1年/3年分の日次データが揃わない銘柄はその期間の相関が算出できないため「-」表示または対象外。<br>
    ・為替相関は事業構造だけでなく市場全体のリスクオン/オフ気分とも絡むため、単純な「円安メリット株リスト」として使うより、
    決算資料等で実際の想定為替レート・海外売上比率も併せて確認することを推奨。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def _fmt_corr(v):
    return f'{v:+.2f}' if v is not None else '<span class="dim">-</span>'


# 表示するカラムの順序。build_row / ランク計算で共通に使う
METRIC_KEYS = ["corr_6m", "corr_1y", "corr_3y", "excess_corr_6m", "excess_corr_1y", "excess_corr_3y"]
# 色付け対象は超過相関の3カラムのみ（単純相関は参考値なので色を付けない）
COLORED_KEYS = {"excess_corr_6m", "excess_corr_1y", "excess_corr_3y"}


def build_rank_maps(valid, direction):
    """色付け対象カラムごとに「その表の方向で全銘柄中の順位」を計算する。
    direction=+1: 円安表（値が大きいほど上位） / -1: 円高表（値が小さいほど上位）
    戻り値: {metric_key: {code: rank(1始まり)}}"""
    rank_maps = {}
    for key in COLORED_KEYS:
        vals = [(r["code"], r[key]) for r in valid if r[key] is not None]
        vals.sort(key=lambda x: x[1], reverse=(direction > 0))
        rank_maps[key] = {code: i + 1 for i, (code, v) in enumerate(vals)}
    return rank_maps


def _cell(r, key, rank_maps):
    """相関セル。超過相関カラムのみ、その表の方向で上位30位以内=濃い赤、50位以内=薄い赤"""
    v = r[key]
    if v is None:
        return '<td class="corr"><span class="dim">-</span></td>'
    style = ""
    if key in COLORED_KEYS:
        rank = rank_maps[key].get(r["code"])
        if rank is not None:
            if rank <= 30:
                style = ' style="background:rgba(239,68,68,0.35)"'
            elif rank <= 50:
                style = ' style="background:rgba(239,68,68,0.15)"'
    return f'<td class="corr"{style}>{v:+.2f}</td>'


def build_row(rank, r, rank_maps):
    cells = "".join(_cell(r, key, rank_maps) for key in METRIC_KEYS)
    return (f'          <tr><td class="rank">{rank}</td>'
            f'<td>{r["code"]} {r["name"]}</td>'
            f'<td>{r["mcap"]:,.0f}</td>'
            f'<td>{r["price"]:,.0f}</td>'
            f'{cells}</tr>')


def generate_html(results, nikkei_corr_6m, nikkei_corr_1y, nikkei_corr_3y):
    # ソート基準: 超過相関(対日経平均)の1年値を優先（市場全体の為替感応度を除いた
    # 銘柄固有の感応度でランキング。6ヶ月はノイズが大きいため列表示のみで採用せず）。
    # 無ければ超過相関(6ヶ月)→(3年)→単純相関(1年)の順で代替。
    def sort_key(r):
        for key in ("excess_corr_1y", "excess_corr_6m", "excess_corr_3y", "corr_1y"):
            if r[key] is not None:
                return r[key]
        return 0

    valid = [r for r in results
             if r["excess_corr_6m"] is not None or r["excess_corr_1y"] is not None
             or r["excess_corr_3y"] is not None]
    ranked = sorted(valid, key=sort_key, reverse=True)

    top_weak = ranked[:TOP_N]                # 円安相関Top（相関高い順）
    top_strong = list(reversed(ranked[-TOP_N:]))  # 円高相関Top（相関低い順）

    # セル色用の順位: 円安表は「大きいほど上位」、円高表は「小さいほど上位」で独立に計算
    rank_weak = build_rank_maps(valid, direction=+1)
    rank_strong = build_rank_maps(valid, direction=-1)

    rows_weak = "\n".join(build_row(i + 1, r, rank_weak) for i, r in enumerate(top_weak))
    rows_strong = "\n".join(build_row(i + 1, r, rank_strong) for i, r in enumerate(top_strong))

    def fmt_nk(v):
        return f'{v:+.2f}' if v is not None else '-'

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        min_mcap=MIN_MCAP_OKU,
        n_total=len(valid),
        top_n=TOP_N,
        nikkei_corr_6m=fmt_nk(nikkei_corr_6m),
        nikkei_corr_1y=fmt_nk(nikkei_corr_1y),
        nikkei_corr_3y=fmt_nk(nikkei_corr_3y),
        rows_weak=rows_weak,
        rows_strong=rows_strong,
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
    wait_for_git_lock(SCRIPT_DIR)  # 他スクリプトとのgit競合・放置ロック対策
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML, "fx_corr_screen.py", "fx_corr_run.bat"],
                   check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update fx_corr report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/fx_corr.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    test = "--test" in sys.argv
    log("円安/円高相関ランキング 開始" + ("（テスト: 100銘柄）" if test else ""))

    codes, names, caps = load_universe()
    log(f"対象ユニバース: {len(codes)}銘柄（時価総額{MIN_MCAP_OKU}億以上・営業黒字）")
    if test:
        codes = codes[:100]

    fx_close = fetch_usdjpy()
    nikkei_close = fetch_nikkei()
    results, nikkei_corr_6m, nikkei_corr_1y, nikkei_corr_3y = calc_correlations(codes, names, caps, fx_close, nikkei_close)

    if not results:
        log("該当銘柄なし。HTMLは生成しません")
        return

    generate_html(results, nikkei_corr_6m, nikkei_corr_1y, nikkei_corr_3y)

    if "--nopush" in sys.argv or test:
        log("push スキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
