# -*- coding: utf-8 -*-
"""
銘柄別 制度信用倍率 検索ページ → HTML出力 → GitHub Pages公開

JPX「銘柄別信用取引残高」PDF から全銘柄の信用売残・買残をパースし、
margin_all_history.json に蓄積する。margin.html は証券コードを入力して過去の
制度信用倍率の推移をブラウザ内検索（JS）で表示する。カンマ区切りで複数銘柄の比較も可能。

- 2026-09-18分まで: 週次「銘柄別信用取引週末残高」(syumatsuYYYYMMDD00.pdf) → [制度売残, 制度買残]
- 2026-09-25分から: 日次「銘柄別信用取引残高」(YYYYMMDD_mtall.pdf, 毎日16:00目安) → [制度売残, 制度買残, 一般売残, 一般買残]
  JPXのフォーマット改定でURL・ファイル名・行構造が変わった。パーサは jpx_margin.py に共通化
  （haitou / jpminervini / shinyou も同じモジュールを使う）
- JSONのキー名 "weeks" は互換のため据え置き（中身は日付キー。日次化以降は営業日ごと）
- 倍率 = 制度信用買残 ÷ 制度信用売残
- 1未満 = 売り方過多（踏み上げ期待・水色） / 20以上 = 信用買い過熱（赤）
- データはJSONを margin.html と同時にpush（GitHub Pagesから fetch で読む）
"""

import os
import re
import sys
import json
import time
import tempfile
import subprocess
import datetime

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

JPX_MARGIN_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"
JPX_BASE = "https://www.jpx.co.jp"
HISTORY_JSON = os.path.join(SCRIPT_DIR, "margin_all_history.json")
REPORT_HTML = "margin.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# JPX PDF パース（jpx_margin.py に共通化）
# -----------------------------------------
import jpx_margin


def list_pdfs():
    """[(日付ISO, URL, fmt)] 古い順。fmt = "daily"(新) / "weekly"(旧)"""
    return jpx_margin.list_pdfs()


def parse_pdf(url):
    """全銘柄をパースして {code: [name, 制度売残, 制度買残, 一般売残, 一般買残]} を返す（新旧両対応）"""
    data = jpx_margin.parse_pdf(url)
    return {c: [v["name"], v["std_sell"], v["std_buy"], v["gen_sell"], v["gen_buy"]] for c, v in data.items()}


# -----------------------------------------
# 履歴（JSON）
# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        with open(HISTORY_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {"names": {}, "weeks": {}}


def save_history(hist):
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))


# -----------------------------------------
# HTML出力（検索はブラウザ内JS）
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>銘柄チェッカー - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 20px 0 8px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .searchbox {{ display: flex; gap: 10px; margin-bottom: 6px; flex-wrap: wrap; }}
  .searchbox input {{
    background: #1e293b; border: 1px solid #334155; color: #f8fafc;
    padding: 9px 14px; border-radius: 8px; font-size: 1rem; width: 300px;
  }}
  .searchbox input:focus {{ outline: none; border-color: #3b82f6; }}
  .searchbox button {{
    background: #1e40af; border: 1px solid #3b82f6; color: #bfdbfe;
    padding: 9px 22px; border-radius: 8px; font-size: 1rem; cursor: pointer; font-weight: 700;
  }}
  .searchbox button:hover {{ background: #1d4ed8; }}
  .hint {{ font-size: 0.78rem; color: #64748b; margin-bottom: 18px; }}
  .err {{ color: #f87171; font-size: 0.9rem; margin: 10px 0; }}
  table {{ border-collapse: collapse; font-size: 0.88rem; margin-bottom: 8px; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 14px;
    text-align: right; font-weight: 600; white-space: nowrap;
  }}
  thead th:first-child {{ text-align: left; }}
  td {{
    padding: 8px 14px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{ text-align: left; color: #94a3b8; }}
  tr:hover td {{ background: #16213a; }}
  td.low {{ background: rgba(56,189,248,0.28); color: #7dd3fc; font-weight: bold; }}
  td.high {{ background: rgba(220,38,38,0.28); color: #fca5a5; font-weight: bold; }}
  td.ratio {{ font-weight: 700; color: #f8fafc; }}
  td.thin {{ color: #64748b; font-weight: 400; }}
  td.calc {{ color: #cbd5e1; }}
  .summary {{ font-size: 0.9rem; color: #e2e8f0; margin: 0 0 8px; line-height: 1.7; }}
  .summary b {{ color: #f8fafc; }}
  .thin-note {{ color: #94a3b8; }}
  tr.split td {{ background: rgba(245,158,11,0.14); color: #fcd34d; font-size: 0.8rem; border-top: 2px solid #f59e0b; border-bottom: 2px solid #f59e0b; }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.8; max-width: 900px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
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
    <a href="karauri.html" style="border-color:#d97706">空売り比率</a>
    <a href="vix.html" style="border-color:#d97706">VIX温度計</a>
    <a href="sns.html" style="border-color:#d97706">SNS恐怖温度計</a>
    <a href="flow.html" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
    <a href="tenkan.html" style="border-color:#db2777">並び転換</a>
    <a href="insider.html" style="border-color:#db2777">インサイダー売買</a>
    <a href="margin.html" class="active" style="border-color:#db2777">銘柄チェッカー</a>
    <a href="buffett.html" style="border-color:#db2777">バフェット</a>
    <a href="cramer.html" style="border-color:#db2777">クレイマー</a>
    <a href="kijitsu.html" style="border-color:#db2777">信用期日</a>
    <a href="kijitsu_us.html" style="border-color:#db2777">下落日数(US)</a>
    <a href="fx_corr.html" style="border-color:#db2777">円安/円高相関</a>
    <a href="kazami.html" style="border-color:#db2777">風見表</a>
    <a href="roei.html" style="border-color:#db2777">情報漏洩銘柄検証</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>銘柄チェッカー</h1>
  <p class="subtitle">最終更新: {updated} | 出所: JPX 銘柄別信用取引残高（2026-09-25分から日次、それ以前は週次） | 収録: {n_codes}銘柄 × {n_weeks}日付分</p>
  <div class="searchbox">
    <input type="text" id="codes" placeholder="証券コード（例: 5411 または 5411,7203,1570）"
           onkeydown="if(event.key==='Enter')search()">
    <button onclick="search()">検索</button>
  </div>
  <p class="hint">カンマ区切りで複数銘柄を同時比較できます。倍率 = 制度信用買残 ÷ 制度信用売残。<span style="color:#7dd3fc">1未満 = 売り方過多（踏み上げ期待）</span> / <span style="color:#fca5a5">20以上 = 信用買い過熱</span></p>
  <div id="result"></div>
  <p class="note">
    ・JPX「銘柄別信用取引残高」から制度信用（と一般信用）の残高を毎日自動で蓄積。<a href="https://www.jpx.co.jp/markets/statistics-equities/margin/01.html" style="color:#60a5fa">JPX 銘柄別信用取引残高</a><br>
    ・<b>2026-09-25分からJPXが週次→日次公表に改定</b>。2026-06-12〜09-18分は週次（毎週金曜申込時点）、09-25分以降は営業日ごとの申込時点（翌日16:00頃公表）。表の1行が「1週」から「1営業日」に変わっているので、行数で期間を数えないこと。<br>
    ・日次化以降は一般信用の売残・買残も収録（週次期間は「-」）。権利確定日前に一般信用売残が急増し翌日消えるのは優待クロス（つなぎ売り）で、株価への売り圧力ではない。<br>
    ・単位は株（ETFは口）。<br>
    ・<b>倍率の信頼度</b>: 売り残が1万株未満、または買残の1/50未満の日は「売り残僅少」としてグレー表示（※印）。分母が小さいと倍率が暴れるため、その銘柄では倍率ではなく買残の推移を見る。<br>
    　根拠: 全銘柄で売り残の少ない下位25%は倍率の10週変動係数が0.73、多い上位25%は0.31（2026-09-27時点・週次データ）。<br>
    ・<b>買残10期比</b>: 制度買残が10期前（10行前。週次期間は10週、日次期間は10営業日≒2週）から何%増減したか。倍率の分母（売り残）のノイズを含まない「個人が買い下がって残高を積んでいるか」の指標。<br>
    ・<b>膨張率</b>: 直近の倍率 ÷ 直近10期の倍率中央値。その銘柄の平常に対してどれだけ膨らんだか（銘柄間で比べられる）。<br>
    ・<b>株式分割</b>: 隣り合う日で制度買残（±4%）と一般買残（±6%）が同じ整数倍（2〜5・10倍）に跳んだら権利落ち日と判定し、黄色の区切り行を入れる（日次データ=2026-09-25以降のみ。週次期間は1系列しか無く実需の振れと区別できないため判定しない）。残高は分割前の株数のまま残す（倍率は株数に依らないので連続して読める）。買残10期比だけは分割後株数に揃えて計算。英字コード銘柄（285A等）は2026-09-25以降の日次からの蓄積（旧週次パーサが読めなかったため）。<br>
    ・買残10期比・膨張率は計算値であり売買判定ではない。色付けは信頼度のグレーのみ。履歴が積もった時点で、対照群と比べて事後リターンに差があるかを検証してから判定色を検討する。
  </p>
  <p class="updated">最終更新: {updated}</p>
<script>
let DATA = null;
let FUND = null;
// 倍率の信頼度: 売り残がこの株数未満、または買残の 1/THIN_SELL_RATIO 未満なら「売り残僅少」＝倍率は参考外（グレー表示）
//   根拠: 2026-09-27 時点の全3,769銘柄で、売り残下位25%の倍率10週変動係数は0.73、上位25%は0.31（分母が小さいほど倍率が暴れる）
const THIN_SELL_ABS = 10000;
const THIN_SELL_RATIO = 50;
// 10期トレンド指標（買残10期比・膨張率）の窓幅と、中央値を出すのに必要な最低期数（1期=1行。週次期間は1週、日次期間は1営業日）
const TREND_WEEKS = 10;
const TREND_MIN = 6;

async function loadData() {{
  if (DATA) return DATA;
  const res = await fetch('margin_all_history.json');
  DATA = await res.json();
  return DATA;
}}

async function loadFund() {{
  if (FUND !== null) return FUND;
  try {{
    const res = await fetch('margin_fundamentals.json');
    FUND = await res.json();
  }} catch (e) {{
    FUND = {{}};
  }}
  return FUND;
}}

function fmt(n) {{ return n.toLocaleString('ja-JP'); }}

function pctCell(v, suffix) {{
  if (v === null || v === undefined) return '<td>-</td>';
  const cls = v > 0 ? 'pos' : (v < 0 ? 'neg' : '');
  const sign = v > 0 ? '+' : '';
  return '<td><span class="' + cls + '">' + sign + v.toLocaleString('ja-JP') + (suffix || '') + '</span></td>';
}}

function fundHtml(code, fund) {{
  const s = fund.stocks ? fund.stocks[code] : null;
  if (!s) {{
    return '<p class="hint">業績・ベータ・レーティングは時価総額' + (fund.fund_min_oku || 500)
         + '億円以上の銘柄のみ対応（毎週土曜更新）</p>';
  }}
  let h = '';
  // 四半期業績
  if (s.quarters && s.quarters.length) {{
    h += '<h3 style="font-size:0.92rem; color:#cbd5e1; margin:14px 0 6px;">四半期業績（直近' + s.quarters.length + '四半期・単位百万円）</h3>';
    h += '<table><thead><tr><th>四半期</th><th>売上高</th><th>売上YoY</th><th>営業利益</th><th>営利YoY</th><th>営業利益率</th><th>営利率YoY</th></tr></thead><tbody>';
    for (const q of s.quarters) {{
      h += '<tr><td>' + q[0] + '</td><td>' + (q[1] !== null ? fmt(q[1]) : '-') + '</td>'
         + pctCell(q[4], '%')
         + '<td>' + (q[2] !== null ? fmt(q[2]) : '-') + '</td>'
         + pctCell(q[5], '%')
         + '<td>' + (q[3] !== null ? q[3].toFixed(1) + '%' : '-') + '</td>'
         + pctCell(q[6], 'bps') + '</tr>';
    }}
    h += '</tbody></table>';
    h += '<p class="hint" style="margin-top:4px">出所: かぶたん（8四半期・YoYは前年同期比を自前計算）。営業利益率が過去平均から切り上がっているかに注目。銀行等は営業益の概念がないため「-」。</p>';
  }}
  // ベータ + レーティング
  h += '<div style="display:flex; gap:26px; flex-wrap:wrap; margin-top:10px;">';
  const corrCell = function(v) {{
    if (v === null || v === undefined) return '<td>-</td>';
    let style = '';
    if (v < 0.3) style = ' style="color:#f87171; font-weight:700"';       // 低相関=指数と別の生き物
    else if (v >= 0.6) style = ' style="color:#4ade80"';                  // 高相関=指数連動
    return '<td' + style + '>' + v.toFixed(2) + '</td>';
  }};
  h += '<div><h3 style="font-size:0.92rem; color:#cbd5e1; margin:0 0 6px;">ベータ値・相関係数（2年日次）</h3>'
     + '<table><thead><tr><th></th><th>対日経平均</th><th>対TOPIX</th></tr></thead><tbody>'
     + '<tr><td style="text-align:left">ベータ</td>'
     + '<td>' + (s.beta && s.beta[0] !== null ? s.beta[0].toFixed(2) : '-') + '</td>'
     + '<td>' + (s.beta && s.beta[1] !== null ? s.beta[1].toFixed(2) : '-') + '</td></tr>'
     + '<tr><td style="text-align:left">相関係数</td>'
     + corrCell(s.beta ? s.beta[2] : null)
     + corrCell(s.beta ? s.beta[3] : null)
     + '</tr></tbody></table>'
     + '<p class="hint" style="margin-top:4px">ベータ1未満=指数が1%動いてもそれ以下しか動かない。'
     + '<span style="color:#f87171">相関0.3未満</span>=指数と無関係に動く（指数上昇を前提にした投資には不向き）。ベータは相関とセットで見る</p></div>';
  const r = s.rating;
  if (r) {{
    const kai = (r.target && r.price) ? ((r.target / r.price - 1) * 100) : null;
    h += '<div><h3 style="font-size:0.92rem; color:#cbd5e1; margin:0 0 6px;">アナリスト評価（' + r.n + '名）</h3>'
       + '<table><thead><tr><th>平均スコア</th><th>強気買/買/中立/売/強気売</th><th>目標株価 平均</th><th>現値からの乖離</th><th>最高</th><th>最低</th></tr></thead><tbody><tr>'
       + '<td>' + r.mean.toFixed(2) + '</td>'
       + '<td>' + (r.dist ? r.dist.join(' / ') : '-') + '</td>'
       + '<td>' + (r.target ? fmt(Math.round(r.target)) + '円' : '-') + '</td>'
       + (kai !== null ? pctCell(Math.round(kai * 10) / 10, '%') : '<td>-</td>')
       + '<td>' + (r.high ? fmt(Math.round(r.high)) : '-') + '</td>'
       + '<td>' + (r.low ? fmt(Math.round(r.low)) : '-') + '</td>'
       + '</tr></tbody></table>'
       + '<p class="hint" style="margin-top:4px">スコア: 1=強気買い〜5=売り。目標株価から大きく下方乖離している銘柄は注目（1社のレーティングを鵜呑みにしない）</p></div>';
  }} else if (s.mcap_oku !== null && fund.rating_min_oku && s.mcap_oku < fund.rating_min_oku) {{
    h += '<div><p class="hint">レーティングは時価総額' + fund.rating_min_oku + '億円以上のみ（この銘柄: ' + fmt(s.mcap_oku) + '億円）</p></div>';
  }}
  h += '</div>';
  return h;
}}

async function search() {{
  const raw = document.getElementById('codes').value.trim();
  const out = document.getElementById('result');
  if (!raw) {{ out.innerHTML = ''; return; }}
  out.innerHTML = '<p class="hint">読み込み中...</p>';
  let data, fund;
  try {{
    data = await loadData();
    fund = await loadFund();
  }} catch (e) {{
    out.innerHTML = '<p class="err">データの読み込みに失敗しました</p>';
    return;
  }}
  const codes = raw.split(/[,、\\s]+/).map(c => c.trim()).filter(c => c);
  const weeks = Object.keys(data.weeks).sort().reverse();  // 新しい順
  let html = '';
  for (const code of codes) {{
    const name = data.names[code];
    if (!name) {{
      html += '<p class="err">' + code + ': データがありません（制度信用の対象外か、コード誤り）</p>';
      continue;
    }}
    html += '<h2>' + code + ' ' + name + '</h2>';
    html += fundHtml(code, fund);
    html += '<h3 style="font-size:0.92rem; color:#cbd5e1; margin:14px 0 6px;">制度信用倍率の推移（2026-09-25分から日次、それ以前は週次）</h3>';
    const rows = [];
    for (const w of weeks) {{
      const rec = data.weeks[w][code];
      if (!rec) {{ rows.push({{w: w, none: true}}); continue; }}
      const sell = rec[0], buy = rec[1];
      const gsell = rec.length >= 4 ? rec[2] : null, gbuy = rec.length >= 4 ? rec[3] : null;
      const ratio = sell > 0 ? buy / sell : null;
      // 売り残僅少 = 倍率の分母が小さく数字が暴れる → 倍率は参考外扱い
      const thin = (sell < THIN_SELL_ABS) || (sell * THIN_SELL_RATIO < buy);
      rows.push({{w: w, sell: sell, buy: buy, ratio: ratio, thin: thin, gsell: gsell, gbuy: gbuy}});
    }}
    // 株式分割の検出（2026-10-11、285A キオクシア 1→3 で発覚）: 隣り合う行で制度買残が整数倍（2〜5・10倍、誤差±4%）に跳び、
    // 一般買残（あれば）も同じ倍率なら分割（権利落ち日）と判定。数字は補正せず「分割前の株数」のまま残し、区切り行で明示する。
    // 買残10期比だけは分割をまたぐと意味を失うので、分割後株数に揃えた adj を掛けて計算する（表示は生の数字）。
    const SPLIT_KS = [2, 3, 4, 5, 10];
    for (let i = rows.length - 1; i >= 0; i--) {{
      const r = rows[i];
      if (r.none) continue;
      let prev = null;
      for (let j = i + 1; j < rows.length; j++) {{ if (!rows[j].none) {{ prev = rows[j]; break; }} }}
      r.split = null;
      // 日次データ（2026-09-25〜、一般買残あり）のみ判定: 制度買残 ±4% かつ 一般買残 ±6% が同じ整数倍、最低1,000株。
      // 独立した2系列が同倍率で跳ぶのは分割以外にほぼ無い（9/29の群=東京エレクトロン5分割・三井金属10分割・三越伊勢丹2分割等が実際と一致）。
      // 週次期間（〜9/18）は1系列・週1点なので実需の振れと区別がつかず（りそな・ゆうちょ・テルモ等の誤検出）、判定しない。
      // なお実際の分割では売残は k の5〜8割しか増えない（権利付最終日前に売り方が建玉を閉じる）ので、売残は判定に使わない。
      if (prev && prev.buy >= 1000 && r.buy > 0 && prev.gbuy && r.gbuy) {{
        for (const k of SPLIT_KS) {{
          const okBuy = Math.abs((r.buy / prev.buy) / k - 1) <= 0.04;
          const okG = Math.abs((r.gbuy / prev.gbuy) / k - 1) <= 0.06;
          if (okBuy && okG) {{
            r.split = {{k: k, prevRatio: prev.ratio, buyChg: (r.buy / (prev.buy * k) - 1) * 100, sellChg: prev.sell > 0 ? (r.sell / (prev.sell * k) - 1) * 100 : null}};
            break;
          }}
        }}
      }}
    }}
    // adj: その行より新しい分割の倍率の積（分割前の行は ×k で分割後株数に揃う）
    let adj = 1;
    for (let i = 0; i < rows.length; i++) {{
      const r = rows[i];
      if (r.none) continue;
      r.adj = adj;
      if (r.split) adj *= r.split.k;
    }}
    // 前週比・10週指標は古い順で計算してから新しい順で表示（rows は新しい順）
    for (let i = rows.length - 1; i >= 0; i--) {{
      const r = rows[i];
      if (r.none) continue;
      // 前週比
      r.chg = null;
      if (r.ratio !== null) {{
        for (let j = i + 1; j < rows.length; j++) {{
          if (!rows[j].none && rows[j].ratio !== null) {{ r.chg = r.ratio - rows[j].ratio; break; }}
        }}
      }}
      // 買残の10週変化率: 10週前（rows[i+10]）との比較
      r.buy10 = null;
      const base = rows[i + TREND_WEEKS];
      if (base && !base.none && base.buy > 0) r.buy10 = ((r.buy * r.adj) / (base.buy * base.adj) - 1) * 100;   // 分割をまたぐ場合は株数を揃える
      // 膨張率: 直近倍率 ÷ 直近10週（当週含む）の倍率中央値
      r.med10 = null; r.exp10 = null;
      const win = [];
      for (let j = i; j < Math.min(rows.length, i + TREND_WEEKS); j++) {{
        if (!rows[j].none && rows[j].ratio !== null) win.push(rows[j].ratio);
      }}
      if (win.length >= TREND_MIN && r.ratio !== null) {{
        win.sort((a, b) => a - b);
        const m = win.length % 2 ? win[(win.length - 1) / 2] : (win[win.length / 2 - 1] + win[win.length / 2]) / 2;
        r.med10 = m;
        r.exp10 = m > 0 ? r.ratio / m : null;
      }}
    }}
    // 直近週のサマリー（事実の計算値のみ・判定はしない）
    const latest = rows.find(r => !r.none && r.ratio !== null);
    if (latest) {{
      let s = '倍率 <b>' + latest.ratio.toFixed(2) + 'x</b>';
      if (latest.med10 !== null) s += '（' + TREND_WEEKS + '期中央値 ' + latest.med10.toFixed(2) + 'x → 膨張率 <b>' + latest.exp10.toFixed(2) + '倍</b>）';
      if (latest.buy10 !== null) s += '　／　買い残 ' + TREND_WEEKS + '期で <b>' + (latest.buy10 > 0 ? '+' : '') + latest.buy10.toFixed(0) + '%</b>';
      if (latest.thin) s += '　／　<span class="thin-note">売り残僅少（' + fmt(latest.sell) + '株）のため倍率は参考外</span>';
      html += '<p class="summary">' + s + '</p>';
    }}
    html += '<table><thead><tr><th>申込日</th><th>制度買残</th><th>買残' + TREND_WEEKS + '期比</th><th>制度売残</th><th>制度信用倍率</th><th>前期比</th><th>膨張率<br><span style="font-weight:400">÷' + TREND_WEEKS + '期中央値</span></th><th>一般売残</th><th>一般買残</th></tr></thead><tbody>';
    for (const r of rows) {{
      if (r.none) {{
        html += '<tr><td>' + r.w.replace(/-/g, '/') + '</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td></tr>';
        continue;
      }}
      let cls = 'ratio';
      if (r.thin) cls = 'thin';
      else if (r.ratio !== null && r.ratio < 1) cls = 'low';
      else if (r.ratio !== null && r.ratio >= 20) cls = 'high';
      let ratioStr = r.ratio !== null ? r.ratio.toFixed(2) + 'x' : '-';
      if (r.thin && r.ratio !== null) ratioStr += '<span title="売り残僅少（' + THIN_SELL_ABS.toLocaleString('ja-JP') + '株未満 または 買残の1/' + THIN_SELL_RATIO + '未満）。倍率は参考外">※</span>';
      let chgStr = '-';
      if (r.chg !== null && r.chg !== undefined) {{
        const sign = r.chg > 0 ? '+' : '';
        chgStr = '<span class="' + (r.chg > 0 ? 'pos' : 'neg') + '">' + sign + r.chg.toFixed(2) + '</span>';
      }}
      const buy10Str = (r.buy10 !== null) ? ((r.buy10 > 0 ? '+' : '') + r.buy10.toFixed(0) + '%') : '-';
      const expStr = (r.exp10 !== null) ? r.exp10.toFixed(2) + '倍' : '-';
      if (r.split) {{
        const sp = r.split;
        let note = r.w.replace(/-/g, '/') + ' 権利落ち: <b>1→' + sp.k + ' 株式分割</b>（この行より下は分割前の株数、倍率は株数に依らないのでそのまま比較可）。倍率 '
          + (sp.prevRatio !== null ? sp.prevRatio.toFixed(2) + 'x' : '-') + ' → ' + (r.ratio !== null ? r.ratio.toFixed(2) + 'x' : '-');
        note += '／分割後株数で揃えた前期比: 買残 ' + (sp.buyChg > 0 ? '+' : '') + sp.buyChg.toFixed(1) + '%';
        if (sp.sellChg !== null) note += '・売残 ' + (sp.sellChg > 0 ? '+' : '') + sp.sellChg.toFixed(1) + '%';
        if (sp.sellChg !== null && r.ratio !== null && sp.prevRatio !== null && r.ratio > sp.prevRatio && sp.sellChg < -5 && Math.abs(sp.buyChg) < 5) note += '（倍率上昇の主因は売り方の減少＝踏み上げ燃料の減少）';
        else if (r.ratio !== null && sp.prevRatio !== null && r.ratio > sp.prevRatio && sp.buyChg > 5) note += '（倍率上昇の主因は買い残の増加）';
        html += '<tr class="split"><td colspan="9">' + note + '</td></tr>';
      }}
      html += '<tr><td>' + r.w.replace(/-/g, '/') + '</td><td>' + fmt(r.buy) + '</td><td class="calc">' + buy10Str + '</td><td>'
            + fmt(r.sell) + '</td><td class="' + cls + '">' + ratioStr + '</td><td>' + chgStr + '</td><td class="calc' + (r.thin ? ' thin' : '') + '">' + expStr + '</td><td class="calc">' + (r.gsell !== null ? fmt(r.gsell) : '-') + '</td><td class="calc">' + (r.gbuy !== null ? fmt(r.gbuy) : '-') + '</td></tr>';
    }}
    html += '</tbody></table>';
  }}
  out.innerHTML = html;
}}
</script>
</body>
</html>
"""


def generate_html(hist):
    weeks = sorted(hist["weeks"])
    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_codes=len(hist["names"]),
        n_weeks=len(weeks),
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
                    "margin_all_history.json", ".gitignore",
                    "margin_screen.py", "margin_run.bat", "jpx_margin.py"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update margin report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/margin.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("銘柄別制度信用倍率 チェック開始")

    hist = load_history()

    try:
        pdfs = list_pdfs()
    except Exception as e:
        log(f"エラー: JPXページの取得に失敗しました: {e}")
        sys.exit(1)
    if not pdfs:
        log("エラー: JPXページにPDFリンクが見つかりません（サイト改定の可能性。jpx_margin.list_pdfs を確認）")
        sys.exit(1)
    log(f"JPXサイト上のPDF: {len(pdfs)}本 ({pdfs[0][0]} ～ {pdfs[-1][0]})")

    added = 0
    for d, url, fmt in pdfs:
        if d in hist["weeks"]:
            continue
        log(f"  {d} 分（{fmt}）をパース中...")
        try:
            data = parse_pdf(url)
        except Exception as e:
            log(f"  {d}: パース失敗 {e}")
            continue
        if len(data) < 3000:
            log(f"  {d}: 銘柄数が少なすぎるためスキップ（{len(data)}）")
            continue
        hist["weeks"][d] = {c: [v[1], v[2], v[3], v[4]] for c, v in data.items()}
        for c, v in data.items():
            # 新フォーマットはETF等の銘柄名が列交錯で壊れることがあるので、既知の名前を優先
            if c not in hist["names"] or (fmt == "weekly"):
                hist["names"][c] = v[0]
        added += 1
        save_history(hist)  # 1本ごとに保存（途中で落ちても再開できる）
        log(f"  {d}: {len(data)}銘柄を追加・保存")

    if added:
        log(f"履歴: {len(hist['weeks'])}日付分 / {len(hist['names'])}銘柄")
    else:
        log("新しいデータはありませんでした")

    if not hist["weeks"]:
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
