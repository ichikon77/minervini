# -*- coding: utf-8 -*-
"""
ビッグテック創業者・トップの自社株売買（インサイダー取引） → HTML → GitHub Pages

Mag7 + SpaceX の創業者・現トップのSEC Form 4（役員の自社株売買報告）を
openinsider.com の人物ページから取得し、insider_history.json に蓄積する。

- S - Sale: 売り（赤） / P - Purchase: 買い（緑） / その他（オプション行使等）はグレー
- 提出から7日以内の新着はハイライト + NEWバッジ
- Page/Brinのように直近取引がない人物は「直近の報告なし」と表示
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
HISTORY_JSON = os.path.join(SCRIPT_DIR, "insider_history.json")
FORM144_JSON = os.path.join(SCRIPT_DIR, "insider_form144.json")  # 売却予定の事前通知（Form 4より早く出る）
REPORT_HTML = "insider.html"

BASE = "http://openinsider.com"

# (表示名, 会社/立場, openinsiderの人物パス)
PEOPLE = [
    ("イーロン・マスク", "TSLA / SPCX 創業者・CEO", "/insider/Musk-Elon/1494730"),
    ("ジェフ・ベゾス", "AMZN 創業者・Exec Chair", "/insider/Bezos-Jeffrey-P/1043298"),
    ("マーク・ザッカーバーグ", "META 創業者・CEO", "/insider/Zuckerberg-Mark/1548760"),
    ("ジェンスン・フアン", "NVDA 創業者・CEO", "/insider/Huang-Jen-Hsun/1197649"),
    ("ティム・クック", "AAPL CEO", "/insider/Cook-Timothy-D/1214156"),
    ("サティア・ナデラ", "MSFT CEO", "/insider/Nadella-Satya/1513142"),
    ("スンダー・ピチャイ", "GOOGL CEO", "/insider/Pichai-Sundar/1534753"),
    ("アンディ・ジャシー", "AMZN CEO", "/insider/Jassy-Andrew-R/1374545"),
    ("ラリー・ペイジ", "GOOGL 共同創業者", "/insider/Page-Larry/1295231"),
    ("セルゲイ・ブリン", "GOOGL 共同創業者", "/insider/Brin-Sergey/1295032"),
]

NEW_DAYS = 7          # 提出日がこの日数以内ならNEW
SHOW_PER_PERSON = 15  # 人物ごとの表示行数


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# 取得・パース
# -----------------------------------------
def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(5)
    raise last


def parse_person(path):
    """人物ページから取引リストを返す。
    行: {filing, trade_date, ticker, title, type, price, qty, owned, value}"""
    html = fetch(BASE + path)
    m = re.search(r'<table[^>]*class="tinytable"[^>]*>(.*?)</table>', html, re.S)
    if not m:
        return []
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 12 or not cells[1]:
            continue
        ticker = re.sub(r"[^A-Z.]", "", cells[3].split(">")[-1])
        rec = {
            "filing": cells[1][:10],
            "trade_date": cells[2],
            "ticker": ticker,
            "title": cells[5],
            "type": cells[6],
            "price": cells[7],
            "qty": cells[8],
            "owned": cells[9],
            "value": cells[11],
        }
        if rec["trade_date"] and ticker:
            out.append(rec)
    return out


# -----------------------------------------
# 10b5-1判定（SEC Form 4原本のチェックボックス）
# -----------------------------------------
SEC_UA = {"User-Agent": "kabuchiwa research kabuchiwa@example.com"}
PLAN_CACHE_FILE = os.path.join(SCRIPT_DIR, "insider_plan_cache.json")


def fetch_sec(url):
    req = urllib.request.Request(url, headers=SEC_UA)
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", errors="replace")


def build_plan_map(cik, max_filings=20):
    """人物CIKのForm 4原本を読み、{(取引日, ticker大文字): '10b5-1' or '非プラン'} を返す。
    aff10b5Oneチェックボックス(2023年〜義務化)がtrue/1なら予約売却。"""
    out = {}
    try:
        data = json.loads(fetch_sec(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"))
    except Exception as e:
        log(f"    submissions取得失敗 CIK{cik}: {e}")
        return out
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    n = 0
    for i in range(len(forms)):
        if forms[i] != "4" or n >= max_filings:
            continue
        accn = accs[i].replace("-", "")
        try:
            idx = fetch_sec(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/")
            raw = [x for x in re.findall(r'href="([^"]+\.xml)"', idx) if "xslF345" not in x]
            if not raw:
                continue
            xml = fetch_sec("https://www.sec.gov" + raw[0])
        except Exception:
            continue
        n += 1
        flags = set(f.strip().lower() for f in re.findall(r"<aff10b5One>([^<]*)", xml))
        is_plan = bool(flags & {"1", "true"})
        ticker = ""
        mt = re.search(r"<issuerTradingSymbol>([^<]*)", xml)
        if mt:
            ticker = mt.group(1).strip().upper()
        for d in set(re.findall(r"<transactionDate>\s*<value>([^<]*)", xml)):
            out[f"{d.strip()}|{ticker}"] = "10b5-1" if is_plan else "非プラン"
        time.sleep(0.2)
    return out


def load_plan_cache():
    if os.path.exists(PLAN_CACHE_FILE):
        try:
            return json.load(open(PLAN_CACHE_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_plan_cache(cache):
    json.dump(cache, open(PLAN_CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)


# -----------------------------------------
# Form 144（売却予定の事前通知。Form 4より先に出るため早期警戒に使う）
# -----------------------------------------
def fetch_form144(cik, max_filings=10):
    """人物CIKのSEC submissions一覧からForm 144（売却予定の事前通知。Form 4より先に
    出るため早期警戒として使う）を検出し、直近max_filings件のprimary_doc.xmlをパースして返す。
    戻り値: [{filing, notice_date, issuer, shares, value, plan_date, remarks, accn}, ...]（新しい順）"""
    try:
        data = json.loads(fetch_sec(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"))
    except Exception as e:
        log(f"    Form144 submissions取得失敗 CIK{cik}: {e}")
        return []
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    out = []
    n = 0
    for i in range(len(forms)):
        if forms[i] != "144" or n >= max_filings:
            continue
        n += 1
        accn_raw = accs[i]
        accn = accn_raw.replace("-", "")
        try:
            # index.htmは申告者(人物)CIK配下でも参照できる。中のdocument hrefから
            # 実ファイル（issuer CIK配下）の絶対パスを取り、そこからprimary_doc.xmlを取得
            idx = fetch_sec(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{accn_raw}-index.htm")
            hrefs = re.findall(r'href="(/Archives/edgar/data/[^"]+?primary_doc\.xml)"', idx)
            # xslNNNX01/primary_doc.xml はXSLT変換後のHTML表示用リンク（実体はXMLでもタグ構造が異なる）。
            # 生XMLの方（パスにxslが入らない側）を優先して選ぶ
            raw_hrefs = [h for h in hrefs if "/xsl" not in h]
            if not raw_hrefs:
                raw_hrefs = hrefs
            if not raw_hrefs:
                continue
            xml = fetch_sec("https://www.sec.gov" + raw_hrefs[0])
        except Exception as e:
            log(f"    Form144取得失敗 {accn_raw}: {e}")
            continue

        shares = re.search(r"<noOfUnitsSold>([^<]*)", xml)
        value = re.search(r"<aggregateMarketValue>([^<]*)", xml)
        sale_date = re.search(r"<approxSaleDate>([^<]*)", xml)
        plan_date = re.search(r"<planAdoptionDate>([^<]*)", xml)
        issuer_name = re.search(r"<issuerName>([^<]*)", xml)
        remarks = re.search(r"<remarks>([^<]*)", xml)
        out.append({
            "filing": dates[i],
            "notice_date": sale_date.group(1).strip() if sale_date else dates[i],
            "issuer": issuer_name.group(1).strip() if issuer_name else "",
            "shares": shares.group(1).strip() if shares else "",
            "value": value.group(1).strip() if value else "",
            "plan_date": plan_date.group(1).strip() if plan_date else "",
            "remarks": remarks.group(1).strip() if remarks else "",
            "accn": accn_raw,
        })
        time.sleep(0.2)
    return out


# -----------------------------------------
# Form 144 履歴
# -----------------------------------------
def load_form144_history():
    if os.path.exists(FORM144_JSON):
        try:
            return json.load(open(FORM144_JSON, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_form144_history(hist):
    json.dump(hist, open(FORM144_JSON, "w", encoding="utf-8"), ensure_ascii=False)


# -----------------------------------------
# 履歴（JSON蓄積。openinsiderは古い行が流れるため）
# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        with open(HISTORY_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(hist):
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))


def rec_key(r):
    return f'{r["filing"]}|{r["ticker"]}|{r["trade_date"]}|{r["qty"]}|{r["type"]}'


# -----------------------------------------
# HTML
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ビッグテック インサイダー売買 - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #f8fafc; margin: 26px 0 4px; }}
  h2 .role {{ font-size: 0.8rem; color: #94a3b8; font-weight: 400; margin-left: 10px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  table {{ border-collapse: collapse; font-size: 0.85rem; margin-bottom: 6px; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 8px 13px;
    text-align: right; font-weight: 600; white-space: nowrap;
  }}
  thead th:nth-child(-n+4) {{ text-align: left; }}
  td {{
    padding: 7px 13px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:nth-child(-n+4) {{ text-align: left; }}
  tr:hover td {{ background: #16213a; }}
  tr.new-row td {{ background: rgba(220,38,38,0.10); }}
  .sale {{ color: #f87171; font-weight: 700; }}
  .buy {{ color: #4ade80; font-weight: 700; }}
  .other {{ color: #94a3b8; }}
  .new-badge {{
    display: inline-block; background: #dc2626; color: white; font-size: 0.65rem;
    font-weight: bold; padding: 1px 6px; border-radius: 4px; margin-left: 6px;
  }}
  .none {{ color: #64748b; font-size: 0.85rem; margin: 4px 0 10px; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 18px; line-height: 1.8; max-width: 1010px; }}
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
    <a href="flow.html" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
    <a href="insider.html" class="active" style="border-color:#db2777">インサイダー売買</a>
    <a href="margin.html" style="border-color:#db2777">銘柄チェッカー</a>
    <a href="buffett.html" style="border-color:#db2777">バフェット</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>ビッグテック創業者・トップの自社株売買</h1>
  <p class="subtitle">最終更新: {updated} | 出所: SEC Form 4（openinsider.com経由） | <span class="sale">S=売り</span> / <span class="buy">P=買い</span> | 提出{new_days}日以内はNEW</p>
{sections}
  <p class="note">
    ・SEC Form 4 = 役員・10%超株主が自社株を売買したとき2営業日以内にSECへ提出が義務付けられる報告。<br>
    ・<b>プラン列</b>: Form 4原本の10b5-1チェックボックス（2023年〜義務化）を読取。
    「予約売却」= 事前に決めたスケジュールの機械的な売却（弱気シグナルではない）。
    <span style="color:#f87171">「非プラン ⚠」= その場の判断による売り</span> — こちらが本当の注目対象。
    「-」= 原本に記載なし（古い提出・判定対象外の取引種別など）。<br>
    ・S - Sale+OE はオプション行使で得た株の売却（報酬の現金化でシグナル性は低い）。
    <b>買い（P - Purchase）は自腹の意思表示なので重要度が高い</b>（マスクの2025/9のTSLA買いなど）。<br>
    ・ゲイツ（MSFT取締役退任済）とジョブズ（故人）はForm 4の提出義務がなくデータが存在しない。
    ペイジ/ブリンは近年ほぼ売買がないため「報告なし」が続く（それ自体が情報）。<br>
    ・履歴は蓄積式。openinsiderから古い行が消えてもこのページには残る。<br>
    ・<b>Form 144</b> = 役員等が今後株式を売却する予定であることの事前通知（SEC提出）。
    実際に売った確定報告（Form 4）より2〜数営業日ほど早く出るため、早期警戒に使える。
    ただしForm 144の提出＝必ず売却が実行されるわけではない（予定の上限を通知するだけ）。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def type_class(t):
    if t.startswith("S"):
        return "sale"
    if t.startswith("P"):
        return "buy"
    return "other"


def plan_cell(r):
    """10b5-1判定セル: 予約売却=グレー / 非プランの売り=赤太字⚠ / 買いや判定不能=- """
    p = r.get("plan", "-")
    if p == "10b5-1":
        return '<td style="color:#64748b">予約売却</td>'
    if p == "非プラン":
        if r["type"].startswith("S"):
            return '<td style="color:#f87171; font-weight:700">非プラン ⚠</td>'
        return '<td style="color:#94a3b8">非プラン</td>'
    return "<td>-</td>"


def make_recent_section(hist, today):
    """全員の取引をマージして直近5件（提出日順）"""
    merged = []
    for name, role, path in PEOPLE:
        for r in hist.get(path, {}).values():
            merged.append((name, r))
    merged.sort(key=lambda x: (x[1]["filing"], x[1]["trade_date"]), reverse=True)
    if not merged:
        return ""
    rows = []
    for name, r in merged[:5]:
        try:
            fd = datetime.date.fromisoformat(r["filing"])
            is_new = (today - fd).days <= NEW_DAYS
        except ValueError:
            is_new = False
        cls = type_class(r["type"])
        badge = ' <span class="new-badge">NEW</span>' if is_new else ""
        tr_cls = ' class="new-row"' if is_new else ""
        rows.append(
            f"      <tr{tr_cls}><td>{r['trade_date']}{badge}</td>"
            f"<td>{name}</td>"
            f"<td>{r['ticker']}</td>"
            f'<td class="{cls}">{r["type"]}</td>'
            f"{plan_cell(r)}"
            f"<td>{r['price']}</td>"
            f"<td>{r['qty']}</td>"
            f"<td>{r['value']}</td></tr>")
    return (
        '  <h2>直近動向<span class="role">全員からのピックアップ（提出日が新しい順に5件）</span></h2>\n'
        '  <table>\n    <thead><tr><th>取引日</th><th>人物</th><th>銘柄</th><th>種別</th><th>プラン</th>'
        "<th>単価</th><th>株数</th><th>金額</th></tr></thead>\n"
        "    <tbody>\n" + "\n".join(rows) + "\n    </tbody>\n  </table>")


def fmt_usd(v):
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "-"


def fmt_shares(v):
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "-"


def make_form144_table(rows, today):
    """Form 144（売却予定の事前通知）のテーブルHTML。空ならNone"""
    if not rows:
        return None
    trs = []
    for r in rows[:5]:
        try:
            fd = datetime.date.fromisoformat(r["filing"])
            is_new = (today - fd).days <= NEW_DAYS
        except ValueError:
            is_new = False
        badge = ' <span class="new-badge">NEW</span>' if is_new else ""
        tr_cls = ' class="new-row"' if is_new else ""
        trs.append(
            f"      <tr{tr_cls}><td>{r['filing']}{badge}</td>"
            f"<td>{r.get('issuer','-')}</td>"
            f"<td>{fmt_shares(r.get('shares'))}</td>"
            f"<td>{fmt_usd(r.get('value'))}</td>"
            f"<td>{r.get('plan_date','-')}</td>"
            f"<td style=\"text-align:left; white-space:normal; max-width:420px\">{r.get('remarks','') or '-'}</td></tr>")
    return (
        '  <h3 style="font-size:0.85rem; color:#94a3b8; margin:10px 0 4px;">Form 144（売却予定の事前通知。まだ実売却の確定報告(Form 4)ではない）</h3>\n'
        '  <table>\n    <thead><tr><th>提出日</th><th>対象銘柄</th><th>予定株数</th><th>予定金額</th>'
        "<th>プラン採用日</th><th>備考</th></tr></thead>\n"
        "    <tbody>\n" + "\n".join(trs) + "\n    </tbody>\n  </table>")


def generate_html(hist, f144_hist=None):
    f144_hist = f144_hist or {}
    today = datetime.date.today()
    sections = [make_recent_section(hist, today)]
    for name, role, path in PEOPLE:
        recs = list(hist.get(path, {}).values())
        recs.sort(key=lambda r: (r["filing"], r["trade_date"]), reverse=True)
        head = f'  <h2>{name}<span class="role">{role}</span></h2>'

        f144_rows = sorted(f144_hist.get(path, {}).values(), key=lambda r: r["filing"], reverse=True)
        f144_table = make_form144_table(f144_rows, today)

        if not recs:
            body = '\n  <p class="none">直近の報告なし（売買していない＝ホールド継続）</p>'
            if f144_table:
                body += "\n" + f144_table
            sections.append(head + body)
            continue
        rows = []
        for r in recs[:SHOW_PER_PERSON]:
            try:
                fd = datetime.date.fromisoformat(r["filing"])
                is_new = (today - fd).days <= NEW_DAYS
            except ValueError:
                is_new = False
            cls = type_class(r["type"])
            badge = ' <span class="new-badge">NEW</span>' if is_new else ""
            tr_cls = ' class="new-row"' if is_new else ""
            rows.append(
                f"      <tr{tr_cls}><td>{r['trade_date']}{badge}</td>"
                f"<td>{r['ticker']}</td>"
                f"<td>{r['title']}</td>"
                f'<td class="{cls}">{r["type"]}</td>'
                f"{plan_cell(r)}"
                f"<td>{r['price']}</td>"
                f"<td>{r['qty']}</td>"
                f"<td>{r['owned']}</td>"
                f"<td>{r['value']}</td></tr>")
        table = (
            '  <table>\n    <thead><tr><th>取引日</th><th>銘柄</th><th>肩書</th><th>種別</th><th>プラン</th>'
            "<th>単価</th><th>株数</th><th>取引後保有</th><th>金額</th></tr></thead>\n"
            "    <tbody>\n" + "\n".join(rows) + "\n    </tbody>\n  </table>")
        section = head + "\n" + table
        if f144_table:
            section += "\n" + f144_table
        sections.append(section)

    html = HTML_TEMPLATE.format(
        updated_date=today.isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        new_days=NEW_DAYS,
        sections="\n".join(sections),
    )
    out = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {out}")


# -----------------------------------------
# push
# -----------------------------------------
def push_to_github():
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "insider_history.json", "insider_form144.json", ".gitignore",
                    "insider_screen.py", "insider_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update insider report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/insider.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("インサイダー売買 チェック開始")
    hist = load_history()
    added = 0
    for name, role, path in PEOPLE:
        try:
            recs = parse_person(path)
        except Exception as e:
            log(f"  {name}: 取得失敗 {e}")
            continue
        bucket = hist.setdefault(path, {})
        n_new = 0
        for r in recs:
            k = rec_key(r)
            if k not in bucket:
                bucket[k] = r
                n_new += 1
        added += n_new
        log(f"  {name}: {len(recs)}件取得（新規 {n_new}）")
        time.sleep(2)  # openinsiderへの負荷配慮

    if added:
        save_history(hist)
        log(f"履歴保存: 新規{added}件")
    else:
        log("新しい報告はありませんでした")

    # 10b5-1判定: 判定未付与の取引がある人物だけSEC原本を読む
    plan_cache = load_plan_cache()
    for name, role, path in PEOPLE:
        bucket = hist.get(path, {})
        missing = [r for r in bucket.values() if "plan" not in r]
        if not missing:
            continue
        cik = path.rstrip("/").split("/")[-1]
        log(f"  {name}: 10b5-1判定を{len(missing)}件に付与中...")
        if cik not in plan_cache:
            plan_cache[cik] = build_plan_map(cik)
            save_plan_cache(plan_cache)
        pm = plan_cache[cik]
        for r in missing:
            key = f'{r["trade_date"]}|{r["ticker"]}'
            r["plan"] = pm.get(key, "-")
    save_history(hist)
    save_plan_cache(plan_cache)

    # Form 144（売却予定の事前通知）: Form 4より早く出るので毎回全人物チェック
    f144_hist = load_form144_history()
    f144_added = 0
    for name, role, path in PEOPLE:
        cik = path.rstrip("/").split("/")[-1]
        try:
            rows = fetch_form144(cik, max_filings=10)
        except Exception as e:
            log(f"  {name}: Form144取得失敗 {e}")
            continue
        bucket = f144_hist.setdefault(path, {})
        n_new = 0
        for r in rows:
            k = r["accn"]
            if k not in bucket:
                bucket[k] = r
                n_new += 1
        f144_added += n_new
        if rows:
            log(f"  {name}: Form144 {len(rows)}件取得（新規 {n_new}）")
        time.sleep(1)
    if f144_added:
        save_form144_history(f144_hist)
        log(f"Form144履歴保存: 新規{f144_added}件")

    generate_html(hist, f144_hist)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
