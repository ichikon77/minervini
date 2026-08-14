# -*- coding: utf-8 -*-
"""
ジム・クレイマー Lightning Round 検証 → cramer.html → GitHub Pages公開

データ源: usa-option.com（マカベェさんの日本語訳ブログ）の連番記事
  https://usa-option.com/lightning-round-NNNN/
  本文構造:「社名 (TICKER)」→ コメント の繰り返し + 「M/DのLightning Round」の放送日

やること:
  1. 記事から銘柄・コメント・放送日を抽出、コメントを3段階判定（強気/中立/弱気）
     - 判定は関西弁の口癖キーワードで、逆接（〜やが、あかん）に対応するため
       「最後に出現した極性語」を採用（日本語は結論が文末に来るため）
     - 判定不能は「?」として表示（隠さない）
  2. cramer_history.json に蓄積（2025年放送分以降、記事番号1185〜）
  3. 放送日から 2週間/1ヶ月/2ヶ月/3ヶ月/6ヶ月後の騰落をyfinanceで計算
  4. 冒頭に集計表: 全期間/直近1年 × 強気/中立/弱気 × 各期間の的中率
     （強気の的中=上昇、弱気の的中=下落）+ 平均リターン

実行: 毎日08:50（cramer_run.bat）。新記事を自動検出して追記。
初回バックフィルは --backfill N で記事N件ずつ取得（Windowsなら --backfill 400 で一括）
"""

import os
import re
import sys
import json
import time
import subprocess
import datetime
import urllib.request

import yfinance as yf
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_JSON = os.path.join(SCRIPT_DIR, "cramer_history.json")
REPORT_HTML = "cramer.html"

BASE = "https://usa-option.com/lightning-round-{}/"
FIRST_ARTICLE = 1185       # 2025-01-09投稿（放送2024-12-11）から。放送2025-01-01以降を採用
MIN_BROADCAST = "2025-01-01"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}

PERIODS = [("2週間", 14), ("1ヶ月", 30), ("2ヶ月", 61), ("3ヶ月", 91), ("6ヶ月", 183)]

# 3段階判定キーワード（最後に出現した極性語で判定）
BULL_WORDS = ["買うべき", "買いや", "買い増す", "買いたい", "買っても", "素晴らしい",
              "強く推", "推してもいい", "オススメ", "おすすめ", "気に入っとる", "大好きや",
              "好きやで", "割安", "良い銘柄", "いい銘柄", "最高の", "勝者や", "本物や",
              "持つべき", "保有すべき", "上がると思う",
              "いいと思うで", "ええと思うで", "良いと思うで", "強く支持", "支持しとる",
              "支持し続けたい", "支持するで", "信頼しとる", "信頼を失ったことはない",
              "優良な", "優良や", "応援しとる", "イチオシ", "好調や", "絶好調",
              "買い始めるには良い", "買うには良い", "良い水準", "いい水準", "ええ水準",
              "買い時", "買い場や", "よくやっとる", "うまくやっとる", "見事や",
              "市場を支配する", "支配することになる", "王者や", "チャンピオンや", "ナンバーワンや",
              "強気やで", "強気なんや", "買い始めるべき", "買うべきやろう"]
BEAR_WORDS = ["売りや", "売るべき", "売った方", "あかんで", "あかんのや", "持ちたくない",
              "手を出さない", "手を出したくない", "避けるべき", "好みじゃない", "好きじゃない",
              "好きではない", "傷ついてほしくない", "興味ない", "興味がない", "推せない",
              "買わない", "ダメや", "駄目や", "下がると思う", "リスクが高すぎ",
              "最悪のチャート", "最悪の銘柄", "ひどいチャート", "酷いチャート", "チャートが崩れ",
              "推奨できない", "推奨しない", "勧められない", "勧めない", "オススメできない",
              "おすすめできない", "近づかない", "近寄らない", "触らない", "パスや", "パスやで",
              "見送るべき", "降りるべき", "逃げるべき", "危険や", "問題が多すぎ", "終わっとる",
              "投機銘柄", "完全な投機", "ただの投機", "博打や", "ギャンブルや", "宝くじ",
              "期待外れ", "支持できない", "支持し続けることはできない", "支持することはできない",
              "見限", "失望した", "がっかりや", "降参や", "諦めた", "さじを投げ"]
NEUT_WORDS = ["待つべき", "待たないとあかん", "待たんとあかん", "待たなあかん", "待ちや",
              "様子見", "ホールド", "持ち続け", "半分売",
              "悪くない", "五分五分", "どちらとも", "分からん", "わからん", "難しい",
              "のほうが好き", "の方が好き", "のほうがいい", "の方がいい",
              "のほうがええ", "の方がええ", "派なんや", "投機枠", "余地はある",
              "良い価格で買える", "もっと良い価格", "もっと安く買える", "押し目を待"]

# 仮定・条件節のパターン: この直後に続く極性語は「発言者の推奨」ではないため無効化
# 例:「買いたいのであれば」「買うのなら」→ bullとして数えない
COND_PATTERNS = [r'買いたいの(?:であれば|なら)', r'買うの(?:であれば|なら)', r'買いたければ',
                 r'売りたいの(?:であれば|なら)', r'売るの(?:であれば|なら)', r'売りたければ',
                 r'持ちたいの(?:であれば|なら)', r'持つの(?:であれば|なら)',
                 # もし/仮定節の中の上げ下げ予想は銘柄への推奨ではない
                 # 例:「もし金利が下がると思うなら、買い始めるべき」の「下がると思う」
                 r'もし[^。]{0,30}(?:上がる|下がる)と思うなら', r'(?:上がる|下がる)と思うなら']


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# 記事の取得・解析
# -----------------------------------------
def fetch_article(num):
    """記事番号 → dict or None(404)"""
    url = BASE.format(num)
    try:
        req = urllib.request.Request(url, headers=UA)
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return parse_article(num, url, html)


def parse_article(num, url, html):
    m = re.search(r'"datePublished":"(\d{4}-\d{2}-\d{2})T', html)
    posted = m.group(1) if m else None

    text = re.sub(r'<script.*?</script>', '', html, flags=re.S)
    text = re.sub(r'<style.*?</style>', '', text, flags=re.S)
    text = re.sub(r'<[^>]+>', '\n', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&gt;', '>').replace('&#8217;', "'")

    # 放送日「M/DのLightning Round」
    mb = re.search(r'の(\d{1,2})/(\d{1,2})のLightning Round', text)
    broadcast = None
    if mb and posted:
        bm, bd = int(mb.group(1)), int(mb.group(2))
        py, pm, pdd = map(int, posted.split("-"))
        year = py - 1 if (bm, bd) > (pm, pdd) else py
        try:
            broadcast = datetime.date(year, bm, bd).isoformat()
        except ValueError:
            pass
    if broadcast is None:
        broadcast = posted

    # 本文の銘柄ブロック抽出（「こんにちはマカベェです」〜「参考にさせてもらいます」）
    mm = re.search(r'こんにちはマカベェです。(.*?)(?:参考にさせてもら|応援よろしく)', text, re.S)
    body = mm.group(1) if mm else text
    lines = [l.strip() for l in body.split('\n') if l.strip()]

    stocks = []
    cur = None
    for l in lines:
        tm = re.match(r'^(.{2,70}?)\s*\(([A-Z]{1,5})\)\s*$', l)
        if tm and not re.match(r'^\d', tm.group(1)):
            if cur and cur["comment"]:
                stocks.append(cur)
            cur = {"ticker": tm.group(2), "name": tm.group(1).strip(), "comment": ""}
        elif cur is not None:
            if "Lightning Round" in l or l.startswith("ジム・クレイマー"):
                continue
            cur["comment"] = (cur["comment"] + " " + l).strip()
    if cur and cur["comment"]:
        stocks.append(cur)

    for s in stocks:
        s["stance"] = classify(s["comment"])

    return {"num": num, "url": url, "posted": posted, "broadcast": broadcast, "stocks": stocks}


def classify(comment):
    """最後に出現した極性語で3段階判定。無ければ ?
    - 中立語を先にマッチし、その範囲と重なる強弱語は無視
      （例:「Intelのほうが好きやで」→「のほうが好き」が「好きやで」に勝つ）
    - 仮定・条件節（「買いたいのであれば」等）と重なる強弱語も無視
      （発言者の推奨ではなく聞き手の仮定のため）"""
    dead_spans = []   # 極性語として数えない範囲（引用文+中立語+条件節）
    hits = []
    # カギ括弧内の引用（他人のセリフ）は発言者の推奨ではないため丸ごと無効化
    # 例:「うわ、そんな株は持ちたくない」と言ってしまうんや → この持ちたくないは数えない
    for m in re.finditer(r'「[^」]*」', comment):
        dead_spans.append((m.start(), m.end()))
    for w in NEUT_WORDS:
        for m in re.finditer(re.escape(w), comment):
            if not any(not (m.end() <= ds or m.start() >= de) for ds, de in dead_spans):
                dead_spans.append((m.start(), m.end()))
                hits.append((m.start(), "neutral"))
    for pat in COND_PATTERNS:
        for m in re.finditer(pat, comment):
            dead_spans.append((m.start(), m.end()))

    def in_dead(s, e):
        return any(not (e <= ds or s >= de) for ds, de in dead_spans)

    for w in BULL_WORDS:
        for m in re.finditer(re.escape(w), comment):
            if not in_dead(m.start(), m.end()):
                hits.append((m.start(), "bull"))
    for w in BEAR_WORDS:
        for m in re.finditer(re.escape(w), comment):
            if not in_dead(m.start(), m.end()):
                hits.append((m.start(), "bear"))
    if not hits:
        return "?"
    hits.sort()
    return hits[-1][1]


# -----------------------------------------
# 履歴
# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        with open(HISTORY_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {"articles": {}}


def save_history(hist):
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)


def update_articles(hist, backfill=0):
    """新記事の検出+バックフィル。backfill>0なら未取得の古い記事もN件まで取得"""
    known = set(int(k) for k in hist["articles"].keys())
    added = 0

    # バックフィル（FIRST_ARTICLE〜既知の最大まで の穴埋め）
    if backfill > 0:
        upper = max(known) if known else FIRST_ARTICLE + 400
        todo = [n for n in range(FIRST_ARTICLE, upper) if n not in known]
        log(f"バックフィル対象: {len(todo)}件（今回は最大{backfill}件）")
        for n in todo[:backfill]:
            try:
                art = fetch_article(n)
            except Exception as e:
                log(f"  #{n}: 取得失敗 {e}")
                continue
            if art is None:
                hist["articles"][str(n)] = {"num": n, "skip": True}  # 欠番
            else:
                hist["articles"][str(n)] = art
                added += 1
                if added % 20 == 0:
                    log(f"  バックフィル {added}件目 #{n} ({art['broadcast']})")
                    save_history(hist)
            time.sleep(1.2)

    # 新記事（既知の最大+1から、404が3連続するまで）
    start = max(known) + 1 if known else FIRST_ARTICLE
    misses = 0
    n = start
    while misses < 3:
        try:
            art = fetch_article(n)
        except Exception as e:
            log(f"  #{n}: 取得失敗 {e}")
            break
        if art is None:
            misses += 1
        else:
            misses = 0
            hist["articles"][str(n)] = art
            added += 1
            log(f"  新記事 #{n} 放送{art['broadcast']} {len(art['stocks'])}銘柄")
        n += 1
        time.sleep(1.2)

    if added:
        save_history(hist)
        log(f"記事 {added}件 追加（計{len(hist['articles'])}件）")
    return added


# -----------------------------------------
# リターン計算
# -----------------------------------------
def collect_entries(hist):
    """検証対象の銘柄エントリ一覧（放送日がMIN_BROADCAST以降）"""
    entries = []
    for art in hist["articles"].values():
        if art.get("skip") or not art.get("broadcast"):
            continue
        if art["broadcast"] < MIN_BROADCAST:
            continue
        for s in art.get("stocks", []):
            entries.append({
                "num": art["num"], "url": art["url"], "broadcast": art["broadcast"],
                "ticker": s["ticker"], "name": s["name"],
                "comment": s["comment"], "stance": s["stance"],
            })
    return entries


def compute_returns(entries):
    """各エントリに 2週間〜6ヶ月後の騰落%を付与"""
    tickers = sorted({e["ticker"] for e in entries})
    log(f"価格取得: {len(tickers)}ティッカー")
    closes = {}
    CHUNK = 150
    for ci in range(0, len(tickers), CHUNK):
        chunk = tickers[ci:ci + CHUNK]
        data = yf.download(chunk, start="2024-12-01", auto_adjust=True,
                           progress=False, group_by="ticker", threads=True)
        for t in chunk:
            try:
                s = data[t]["Close"].dropna() if len(chunk) > 1 else data["Close"].dropna()
                if len(s) > 10:
                    closes[t] = s
            except Exception:
                continue
        time.sleep(1)
    log(f"  取得成功: {len(closes)}ティッカー")

    for e in entries:
        s = closes.get(e["ticker"])
        e["rets"] = {}
        if s is None:
            continue
        b = pd.Timestamp(e["broadcast"])
        i0 = s.index.searchsorted(b)
        if i0 >= len(s):
            continue
        base = float(s.iloc[i0])
        e["base_price"] = round(base, 2)
        for label, days in PERIODS:
            target = b + pd.Timedelta(days=days)
            if target > s.index[-1]:
                e["rets"][label] = None  # まだ到来していない
                continue
            j = s.index.searchsorted(target)
            if j >= len(s):
                e["rets"][label] = None
            else:
                e["rets"][label] = round((float(s.iloc[j]) / base - 1) * 100, 1)
    return entries


# -----------------------------------------
# 集計（的中率）
# -----------------------------------------
def summarize(entries, since=None):
    """{stance: {period: (的中率%, 平均リターン%, N)}}"""
    out = {}
    for stance in ("bull", "neutral", "bear"):
        grp = [e for e in entries if e["stance"] == stance
               and (since is None or e["broadcast"] >= since)]
        per = {}
        for label, _ in PERIODS:
            vals = [e["rets"].get(label) for e in grp if e.get("rets", {}).get(label) is not None]
            if not vals:
                per[label] = None
                continue
            if stance == "bear":
                hit = sum(1 for v in vals if v < 0)
            else:
                hit = sum(1 for v in vals if v > 0)
            per[label] = (round(hit / len(vals) * 100), round(sum(vals) / len(vals), 1), len(vals))
        out[stance] = per
    return out


# -----------------------------------------
# HTML
# -----------------------------------------
STANCE_LABEL = {"bull": ("強気 🟢", "#4ade80"), "neutral": ("中立/判定不能", "#64748b"),
                "bear": ("弱気 🔴", "#f87171"), "?": ("中立/判定不能", "#64748b")}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>クレイマー Lightning Round 検証 - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 24px 0 10px; border-left: 4px solid #db2777; padding-left: 10px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  table {{ border-collapse: collapse; font-size: 0.84rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 8px 12px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child {{ text-align: left; }}
  td {{
    padding: 7px 12px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{ text-align: left; }}
  tr:hover td {{ background: #16213a; }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
  .dim {{ color: #64748b; font-size: 0.76rem; }}
  .comment {{ color: #94a3b8; font-size: 0.78rem; text-align: left; white-space: normal; max-width: 520px; }}
  .table-wrap {{ overflow: auto; max-height: 72vh; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.9; max-width: 1150px; }}
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
    <a href="insider.html" style="border-color:#db2777">インサイダー売買</a>
    <a href="margin.html" style="border-color:#db2777">銘柄チェッカー</a>
    <a href="buffett.html" style="border-color:#db2777">バフェット</a>
    <a href="cramer.html" class="active" style="border-color:#db2777">クレイマー</a>
    <a href="kijitsu.html" style="border-color:#db2777">信用期日</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>ジム・クレイマー Lightning Round 検証</h1>
  <p class="subtitle">最終更新: {updated} | 発言{n_total}件（2025年1月放送分〜） | 出所: <a href="https://usa-option.com/category/stock/" style="color:#60a5fa">usa-option.com（マカベェさん訳）</a> | 騰落はyfinance終値ベース</p>

  <h2>的中率まとめ — クレイマーの言うことは当たるのか</h2>
{summary}
  <p class="dim" style="margin-top:6px">的中の定義: 強気=その後上昇 / 弱気=その後下落。平均は単純平均リターン。N=検証可能な発言数（発言が新しすぎて期間未到来のものは除く）。中立・判定不能の発言は集計対象外（下の一覧には表示）。</p>

  <h2>発言と答え合わせ（放送日の新しい順）</h2>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>放送日</th><th>銘柄</th><th>判定</th><th style="text-align:left">コメント（マカベェさん訳）</th>
        <th>2週間</th><th>1ヶ月</th><th>2ヶ月</th><th>3ヶ月</th><th>6ヶ月</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・Lightning Round = 視聴者の電話質問にクレイマーが即答するコーナー（Mad Money内）。準備なしの即答なので「本音の初期反応」が出やすい。<br>
    ・3段階判定はコメント内の言い回しから自動判定（最後に出た結論語を採用）。逆接や比較（「〜のほうが好き」）は中立になりやすい。
    判定不能は「?」で表示。<b>原文コメントを併記しているので、判定が怪しいものは自分の目で確認する</b>。<br>
    ・騰落は放送日の翌取引日終値を起点に、各期間後（暦日）の直近終値で計算。期間がまだ到来していない発言は「-」。<br>
    ・訳文は<a href="https://usa-option.com/category/stock/" style="color:#60a5fa">アメリカ発ーマカベェの米株取引</a>より。感謝。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def fmt_ret(v):
    if v is None:
        return "<td>-</td>"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<td class="{cls}">{v:+.1f}%</td>'


def summary_table(entries):
    rows = []
    header = "".join(f"<th>{label}</th>" for label, _ in PERIODS)
    one_year_ago = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()
    for title, since in [("全期間（2025年1月〜）", None), ("直近1年", one_year_ago)]:
        stats = summarize(entries, since)
        body = []
        for stance in ("bull", "bear"):
            label, color = STANCE_LABEL[stance]
            tds = []
            for plabel, _ in PERIODS:
                v = stats[stance].get(plabel)
                if v is None:
                    tds.append("<td>-</td>")
                else:
                    rate, avg, n = v
                    avg_cls = "pos" if avg > 0 else ("neg" if avg < 0 else "")
                    tds.append(f'<td><b>{rate}%</b> <span class="{avg_cls}">({avg:+.1f}%)</span>'
                               f' <span class="dim">N={n}</span></td>')
            body.append(f'      <tr><td style="color:{color}; font-weight:700">{label}</td>'
                        + "".join(tds) + "</tr>")
        rows.append(
            f'  <h3 style="font-size:0.88rem; color:#94a3b8; margin:10px 0 6px">{title}</h3>\n'
            f'  <div class="table-wrap" style="max-height:none">\n  <table>\n'
            f'    <thead><tr><th>判定</th>{header}</tr></thead>\n'
            f'    <tbody>\n' + "\n".join(body) + '\n    </tbody>\n  </table>\n  </div>')
    return "\n".join(rows)


def generate_html(entries):
    entries = sorted(entries, key=lambda e: (e["broadcast"], e["num"]), reverse=True)
    trs = []
    for e in entries:
        label, color = STANCE_LABEL.get(e["stance"], STANCE_LABEL["?"])
        rets = e.get("rets", {})
        tds = "".join(fmt_ret(rets.get(p)) for p, _ in PERIODS)
        trs.append(
            f'      <tr><td>{e["broadcast"]}</td>'
            f'<td><b>{e["ticker"]}</b> <span class="dim">{e["name"][:22]}</span></td>'
            f'<td style="color:{color}; font-weight:700">{label}</td>'
            f'<td class="comment">{e["comment"][:180]}</td>'
            f'{tds}</tr>')

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_total=len(entries),
        summary=summary_table(entries),
        rows="\n".join(trs),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}（{len(entries)}件）")


# -----------------------------------------
# push
# -----------------------------------------
def push_to_github():
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "cramer_history.json", "cramer_screen.py", "cramer_run.bat",
                    ".gitignore"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update cramer report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/cramer.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def reclassify_all(hist):
    """辞書更新後にhistory全体の判定を今のclassifyで付け直す"""
    changed = 0
    total = 0
    for art in hist["articles"].values():
        if art.get("skip"):
            continue
        for s in art.get("stocks", []):
            total += 1
            new = classify(s["comment"])
            if new != s.get("stance"):
                changed += 1
                s["stance"] = new
    save_history(hist)
    log(f"再判定: {total}件中 {changed}件を更新")


def main():
    log("クレイマー Lightning Round チェック開始")
    backfill = 0
    for a in sys.argv:
        if a.startswith("--backfill"):
            try:
                backfill = int(a.split("=")[1]) if "=" in a else int(sys.argv[sys.argv.index(a) + 1])
            except (ValueError, IndexError):
                backfill = 400

    hist = load_history()
    update_articles(hist, backfill=backfill)

    # 毎回、全発言を現在の判定辞書で付け直す（辞書改良が過去分にも自動反映されるように。
    # 1,500件程度なら1秒未満なのでコスト無視できる）
    reclassify_all(hist)

    entries = collect_entries(hist)
    if not entries:
        log("検証対象の発言がありません（まず --backfill 400 でバックフィルしてください）")
        return
    log(f"検証対象: {len(entries)}発言")

    entries = compute_returns(entries)
    generate_html(entries)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
