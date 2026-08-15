# -*- coding: utf-8 -*-
"""
バフェット新規買いトラッカー → HTML → GitHub Pages

バークシャー・ハサウェイ(CIK 1067983)のSEC 13F-HR（四半期保有報告）を
EDGARから取得し、四半期間の差分から
  - 新規買い（前四半期に保有ゼロ → 保有あり）
  - 完全売却（保有あり → ゼロ）
を検出して、購入から売却（または現在）までの損益を推定する。

損益の推定:
  13Fには取得単価がないため、エントリー=購入四半期の平均終値、
  イグジット=売却四半期の平均終値、保有中=現在値で近似（yfinance）。

- buffett_history.json に四半期ごとの保有スナップショットを蓄積
- 新しい13F（四半期ごと、期末45日後）が出たときだけ差分更新
- ティッカーはTICKER_MAPで手動管理。未知の新規銘柄はログで警告
"""

import os
import re
import sys
import json
import time
import subprocess
import datetime
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_JSON = os.path.join(SCRIPT_DIR, "buffett_history.json")
REPORT_HTML = "buffett.html"

CIK = "1067983"
UA = {"User-Agent": "kabuchiwa research kabuchiwa@example.com"}
START_PERIOD = "2022-09-30"   # この期以降の13Fを使用（新規判定は2022Q4→2023Q1から）

# 発行体名の先頭一致 → ティッカー（新規銘柄が出たらここに追記。ログで警告が出る）
TICKER_MAP = {
    "CAPITAL ONE": "COF", "DIAGEO": "DEO", "HORTON D R": "DHI", "D R HORTON": "DHI",
    "NVR INC": "NVR", "LENNAR": "LEN", "CHUBB": "CB", "LIBERTY MEDIA": "LLYVA",
    "SIRIUS XM": "SIRI", "LIBERTY SIRIUS": "LSXMA", "ATLANTA BRAVES": "BATRK",
    "ULTA BEAUTY": "ULTA", "HEICO": "HEI.A", "DOMINO": "DPZ", "POOL CORP": "POOL",
    "CONSTELLATION BRANDS": "STZ", "NUCOR": "NUE", "UNITEDHEALTH": "UNH",
    "LAMAR": "LAMR", "ALLEGION": "ALLE", "ALPHABET": "GOOGL",
    "APPLE": "AAPL", "AMERICAN EXPRESS": "AXP", "COCA COLA": "KO",
    "BANK AMERICA": "BAC", "BANK OF AMERICA": "BAC", "CHEVRON": "CVX",
    "OCCIDENTAL": "OXY", "KRAFT HEINZ": "KHC", "MOODYS": "MCO", "MOODY": "MCO",
    "DAVITA": "DVA", "VERISIGN": "VRSN", "CITIGROUP": "C", "KROGER": "KR",
    "VISA": "V", "MASTERCARD": "MA", "AMAZON": "AMZN", "T-MOBILE": "TMUS",
    "CHARTER COMM": "CHTR", "LOUISIANA PACIFIC": "LPX", "LOUISIANA-PACIFIC": "LPX",
    "ALLY FINL": "ALLY", "AON": "AON", "MARSH": "MMC", "PARAMOUNT": "PARA",
    "HP INC": "HPQ", "CELANESE": "CE", "GENERAL MTRS": "GM", "ACTIVISION": "ATVI",
    "MARKEL": "MKL", "GLOBE LIFE": "GL", "MCKESSON": "MCK", "RH ": "RH",
    "TAIWAN SEMICON": "TSM", "JEFFERIES": "JEF", "STONECO": "STNE", "NU HLDGS": "NU",
    "FLOOR & DECOR": "FND", "VERIZON": "VZ", "PROCTER": "PG", "JOHNSON": "JNJ",
    "LIBERTY LATIN": "LILA", "SPDR S&P": "SPY", "VANGUARD": "VOO",
    "BANK AMER": "BAC", "VITESSE": "VTS", "FLOOR &AMP; DECOR": "FND",
    "LOUISIANA PAC": "LPX", "SPDR S&AMP;P": "SPY", "SNOWFLAKE": "SNOW",
    "LIBERTY LIVE": "LLYVA", "NEW YORK TIMES": "NYT", "DELTA AIR": "DAL",
    "MACYS": "M", "MACY'S": "M",
}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fetch(url, binary=False):
    req = urllib.request.Request(url, headers=UA)
    data = urllib.request.urlopen(req, timeout=40).read()
    return data if binary else data.decode("utf-8", errors="replace")


# -----------------------------------------
# 13F取得
# -----------------------------------------
def list_13f_filings():
    """[(periodOfReport, accession)] 古い順。13F-HR/Aは同期のHRを上書き"""
    data = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{int(CIK):010d}.json"))
    recent = data["filings"]["recent"]
    out = {}
    for i in range(len(recent["form"])):
        form = recent["form"][i]
        if not form.startswith("13F-HR"):
            continue
        period = recent.get("reportDate", recent.get("periodOfReport", [""] * 999))[i]
        acc = recent["accessionNumber"][i]
        if period < START_PERIOD:
            continue
        # 同一periodの複数提出（HR + 機密解除のHR/A等）は全部集めてマージする
        # ※HR/Aが「追加開示のみ」の場合があり、上書きすると既存銘柄が消えるため
        out.setdefault(period, []).append(acc)
    return sorted(out.items())


def parse_13f(acc):
    """infotable XMLをパースして {cusip: {"name":, "shares":, "value":}} を返す"""
    accn = acc.replace("-", "")
    idx = fetch(f"https://www.sec.gov/Archives/edgar/data/{CIK}/{accn}/")
    xmls = re.findall(r'href="([^"]+\.xml)"', idx)
    info_url = None
    for x in xmls:
        if "primary_doc" not in x:
            info_url = "https://www.sec.gov" + x
            break
    if not info_url:
        raise RuntimeError("infotable XMLが見つかりません")
    xml = fetch(info_url)
    out = {}
    for e in re.findall(r"<(?:\w+:)?infoTable>(.*?)</(?:\w+:)?infoTable>", xml, re.S):
        def tag(name):
            m = re.search(rf"<(?:\w+:)?{name}>([^<]*)</(?:\w+:)?{name}>", e)
            return m.group(1).strip() if m else ""
        cusip = tag("cusip").upper()
        if not cusip:
            continue
        rec = out.setdefault(cusip, {"name": tag("nameOfIssuer").upper(), "shares": 0, "value": 0})
        rec["shares"] += int(tag("sshPrnamt") or 0)
        rec["value"] += int(tag("value") or 0)
    return out


def to_quarter(period):
    """'2026-03-31' -> '2026Q1'"""
    y, m = period[:4], int(period[5:7])
    return f"{y}Q{(m + 2) // 3}"


def find_ticker(name):
    for key, tk in TICKER_MAP.items():
        if name.startswith(key) or key in name:
            return tk
    return None


# -----------------------------------------
# 価格（四半期平均・現在値）— ディスクにキャッシュして再実行で積み上げ
# -----------------------------------------
PRICE_CACHE_FILE = os.path.join(SCRIPT_DIR, "buffett_price_cache.json")
_PRICE_CACHE = {}
if os.path.exists(PRICE_CACHE_FILE):
    try:
        _PRICE_CACHE = json.load(open(PRICE_CACHE_FILE, encoding="utf-8"))
    except Exception:
        _PRICE_CACHE = {}


def _save_cache():
    try:
        json.dump(_PRICE_CACHE, open(PRICE_CACHE_FILE, "w", encoding="utf-8"))
    except Exception:
        pass


def quarter_avg_price(ticker, quarter):
    """四半期の平均終値（キャッシュはディスク永続。四半期平均は不変なので再取得不要）"""
    key = f"{ticker}|{quarter}"
    if key in _PRICE_CACHE:
        return _PRICE_CACHE[key]
    import yfinance as yf
    y, q = int(quarter[:4]), int(quarter[5])
    start = datetime.date(y, 3 * q - 2, 1)
    end = (datetime.date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1))
    try:
        h = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())["Close"]
        v = round(float(h.mean()), 2) if len(h) else None
    except Exception:
        v = None
    _PRICE_CACHE[key] = v
    _save_cache()
    return v


def current_price(ticker):
    """現在値（当日内のみキャッシュ）"""
    key = f"{ticker}|now|{datetime.date.today().isoformat()}"
    if key in _PRICE_CACHE:
        return _PRICE_CACHE[key]
    import yfinance as yf
    try:
        h = yf.Ticker(ticker).history(period="5d")["Close"]
        v = round(float(h.iloc[-1]), 2) if len(h) else None
    except Exception:
        v = None
    _PRICE_CACHE[key] = v
    _save_cache()
    return v


# -----------------------------------------
# 履歴・イベント構築
# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        with open(HISTORY_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {"snapshots": {}}


def save_history(hist):
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))


def build_positions(snapshots):
    """スナップショット差分から新規買いポジション履歴を構築。
    返り値: [{cusip,name,ticker,entry_q,exit_q(None=保有中),peak_shares,last_shares}]"""
    quarters = sorted(snapshots)
    positions = []
    open_pos = {}  # cusip -> position
    for i, q in enumerate(quarters):
        cur = snapshots[q]
        prev = snapshots[quarters[i - 1]] if i > 0 else {}
        if i == 0:
            continue  # 基準期（新規判定不能）
        # 新規買い
        for cusip, rec in cur.items():
            if cusip not in prev and cusip not in open_pos:
                pos = {"cusip": cusip, "name": rec["name"],
                       "ticker": find_ticker(rec["name"]),
                       "entry_q": to_quarter(q), "exit_q": None,
                       "peak_shares": rec["shares"], "last_shares": rec["shares"]}
                open_pos[cusip] = pos
                positions.append(pos)
        # 保有更新・売却
        for cusip, pos in list(open_pos.items()):
            if cusip in cur:
                pos["last_shares"] = cur[cusip]["shares"]
                pos["peak_shares"] = max(pos["peak_shares"], cur[cusip]["shares"])
            else:
                pos["exit_q"] = to_quarter(q)
                del open_pos[cusip]
    return positions


def quarters_between(q1, q2):
    y1, n1 = int(q1[:4]), int(q1[5])
    y2, n2 = int(q2[:4]), int(q2[5])
    return (y2 - y1) * 4 + (n2 - n1)


# -----------------------------------------
# HTML
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>バフェット新規買いトラッカー - {updated_date}</title>
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
    padding: 14px 18px; min-width: 210px;
  }}
  .card .label {{ font-size: 0.75rem; color: #94a3b8; margin-bottom: 4px; }}
  .card .value {{ font-size: 1.35rem; font-weight: 700; color: #f8fafc; }}
  .card .sub {{ font-size: 0.78rem; color: #94a3b8; margin-top: 4px; line-height: 1.5; }}
  .table-wrap {{ overflow-x: auto; max-width: 1150px; }}
  table {{ border-collapse: collapse; font-size: 0.85rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 12px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:nth-child(-n+3) {{ text-align: left; }}
  td {{
    padding: 7px 12px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:nth-child(-n+3) {{ text-align: left; }}
  tr:hover td {{ background: #16213a; }}
  .pos {{ color: #4ade80; font-weight: 700; }}
  .neg {{ color: #f87171; font-weight: 700; }}
  .hold {{ color: #93c5fd; font-weight: 700; }}
  .sold {{ color: #94a3b8; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.8; max-width: 1100px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
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
    <a href="buffett.html" class="active" style="border-color:#db2777">バフェット</a>
    <a href="cramer.html" style="border-color:#db2777">クレイマー</a>
    <a href="kijitsu.html" style="border-color:#db2777">信用期日</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>バフェット新規買いトラッカー</h1>
  <p class="subtitle">最終更新: {updated} | 出所: SEC 13F-HR（バークシャー・ハサウェイ CIK 1067983） | 2023Q1以降の「保有ゼロ→新規取得」を追跡</p>
  <div class="cards">
{cards}
  </div>
  <h2>新規買いポジションの一覧（新しい順）</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>購入期</th><th>銘柄</th><th>ticker</th><th>推定取得単価</th><th>状態</th><th>売却期</th><th>売却/現在単価</th><th>推定損益</th><th>保有期間</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・13F（四半期末45日後公表）の保有差分から「新規買い」（前期ゼロ→保有）と「完全売却」（保有→ゼロ）を検出。<br>
    ・<b>取得単価・売却単価は当該四半期の平均終値による推定</b>（13Fに実際の売買価格は載らないため）。
    保有中の損益は現在値との比較。バークシャーの実際の損益とは誤差がある。<br>
    ・保有期間は四半期単位（13Fの解像度の限界）。期中に買って期中に売った銘柄（往復）は13Fに現れない。<br>
    ・<a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001067983&type=13F&dateb=&owner=include&count=40" style="color:#60a5fa">SEC EDGAR: Berkshire 13F一覧</a>
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def generate_html(positions):
    latest_q = None
    rows = []
    wins = losses = 0
    hold_qs = []
    open_pls = []

    enriched = []
    for p in positions:
        tk = p["ticker"]
        entry_px = quarter_avg_price(tk, p["entry_q"]) if tk else None
        if p["exit_q"]:
            exit_px = quarter_avg_price(tk, p["exit_q"]) if tk else None
            end_q = p["exit_q"]
        else:
            exit_px = current_price(tk) if tk else None
            end_q = None
        pl = None
        if entry_px and exit_px:
            pl = (exit_px / entry_px - 1) * 100
        enriched.append((p, entry_px, exit_px, pl))

    for p, entry_px, exit_px, pl in sorted(enriched, key=lambda x: x[0]["entry_q"], reverse=True):
        if p["exit_q"]:
            state = '<span class="sold">売却済み</span>'
            qs = quarters_between(p["entry_q"], p["exit_q"])
            hold_qs.append(qs)
            if pl is not None:
                wins += pl > 0
                losses += pl <= 0
            dur = f"{qs}四半期"
            exit_q_s = p["exit_q"]
        else:
            state = '<span class="hold">保有中</span>'
            today_q = to_quarter(datetime.date.today().isoformat())
            qs = quarters_between(p["entry_q"], today_q)
            if pl is not None:
                open_pls.append(pl)
            dur = f"{qs}四半期〜"
            exit_q_s = "-"
        pl_s = "-"
        if pl is not None:
            cls = "pos" if pl > 0 else "neg"
            pl_s = f'<span class="{cls}">{pl:+.1f}%</span>'
        name = p["name"].title()[:28]
        tk_s = p["ticker"] or "?"
        entry_s = f"${entry_px:,.2f}" if entry_px else "-"
        exit_s = f"${exit_px:,.2f}" if exit_px else "-"
        rows.append(
            f"      <tr><td>{p['entry_q']}</td><td>{name}</td><td>{tk_s}</td>"
            f"<td>{entry_s}</td><td>{state}</td><td>{exit_q_s}</td>"
            f"<td>{exit_s}</td><td>{pl_s}</td><td>{dur}</td></tr>")

    total_closed = wins + losses
    win_rate = f"{wins / total_closed * 100:.0f}%" if total_closed else "-"
    avg_hold = f"{sum(hold_qs) / len(hold_qs):.1f}四半期" if hold_qs else "-"
    avg_open = f"{sum(open_pls) / len(open_pls):+.1f}%" if open_pls else "-"
    n_open = sum(1 for p in positions if not p["exit_q"])

    cards = (
        f'    <div class="card"><div class="label">新規買いの勝率（売却済み・推定）</div>'
        f'<div class="value">{win_rate}</div>'
        f'<div class="sub">{wins}勝{losses}敗 / 売却済み{total_closed}銘柄<br>2023Q1以降の新規買いが対象</div></div>\n'
        f'    <div class="card"><div class="label">平均保有期間（売却済み）</div>'
        f'<div class="value">{avg_hold}</div>'
        f'<div class="sub">13Fの解像度=四半期単位</div></div>\n'
        f'    <div class="card"><div class="label">新規買いのうち保有継続中</div>'
        f'<div class="value">{n_open}銘柄</div>'
        f'<div class="sub">平均含み損益 {avg_open}（取得=購入期平均値の推定）<br>'
        f'※AAPL/KO/AXP等の古参保有は対象外（全保有は13F原本参照）</div></div>')

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        cards=cards,
        rows="\n".join(rows),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}（新規買い{len(positions)}件）")


# -----------------------------------------
# push
# -----------------------------------------
def push_to_github():
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "buffett_history.json", ".gitignore",
                    "buffett_screen.py", "buffett_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update buffett report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/buffett.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("バフェット新規買いトラッカー チェック開始")
    hist = load_history()
    snaps = hist["snapshots"]

    try:
        filings = list_13f_filings()
    except Exception as e:
        log(f"エラー: EDGAR取得に失敗: {e}")
        sys.exit(1)
    log(f"13F-HR: {len(filings)}本（{filings[0][0]} ～ {filings[-1][0]}）")

    added = 0
    for period, accs in filings:
        if period in snaps:
            continue
        log(f"  {period} ({to_quarter(period)}) をパース中（{len(accs)}提出）...")
        merged = {}
        ok = True
        for acc in accs:
            try:
                data = parse_13f(acc)
            except Exception as e:
                log(f"  {period}/{acc}: 失敗 {e}")
                ok = False
                continue
            for cusip, rec in data.items():
                if cusip in merged:
                    # 同一提出期の重複CUSIPは大きい方（HR/Aの完全版想定）を採用
                    if rec["shares"] > merged[cusip]["shares"]:
                        merged[cusip] = rec
                else:
                    merged[cusip] = rec
        if not ok and not merged:
            continue
        snaps[period] = merged
        added += 1
        save_history(hist)
        log(f"  {period}: {len(merged)}銘柄")
        time.sleep(1)

    if not added:
        log("新しい13Fはありませんでした")

    positions = build_positions(snaps)
    unknown = [p["name"] for p in positions if not p["ticker"]]
    if unknown:
        log(f"  警告: ティッカー未登録の新規銘柄あり → TICKER_MAPに追記を: {unknown}")

    generate_html(positions)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
