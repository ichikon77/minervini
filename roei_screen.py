# -*- coding: utf-8 -*-
"""
情報漏洩銘柄検証 → roei.html → GitHub Pages公開

国内上場企業の「不正アクセス・情報漏洩・ランサムウェア」公表事案（roei_incidents.json、手動管理）について、
公表後 1・3・5・10・15・30営業日の株価騰落と、TOPIX（1306 ETF）に対する超過リターンを毎日埋めていく。
種別ごとの集計（中央値／平均／マイナス率）で「どの種類の事故が、何日後まで売られるか」を答え合わせする。

■ 起点と日数の定義
  - 公表時刻で起点を変える（compute() のdocstring参照）: after=公表日終値→翌営業日、pre=前営業日終値→公表日、intra=前営業日終値→翌営業日（当日列あり）
  - 騰落% = 終値 ÷ 基準値 − 1。超過 = 銘柄の騰落 − 同じ日付のTOPIX(1306)の騰落
■ Xの投稿数
  snsデッキと同じ Yahooリアルタイム検索の推移APIで「不正アクセス」「情報漏洩」等の日次投稿数を取り、
  直近7日の合計と、その前の3週間の平均（7日換算）を比べる

実行: 月〜金 20:00（roei_run.bat。休場日は投稿をスキップ）。--nopush でpush省略。--card で速報カード画像（drafts/）を生成。
事案の追加: roei_incidents.json に1行足して実行するだけ。
"""

import os
import re
import sys
import json
import time
import datetime
import subprocess
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INCIDENTS_JSON = os.path.join(SCRIPT_DIR, "roei_incidents.json")
REPORT_HTML = "roei.html"
DRAFTS_DIR = os.path.join(SCRIPT_DIR, "drafts")
BENCH = "1306.T"          # TOPIX連動ETF（^TOPXはyfinanceで取れないため）
HORIZONS = [1, 3, 5, 10, 15, 30]
WORDS = [
    ("不正アクセス", "不正アクセス"),
    ("情報漏洩", "情報漏洩 OR 情報漏えい"),
    ("個人情報流出", "個人情報流出 OR 個人情報漏えい"),
    ("ランサムウェア", "ランサムウェア"),
    ("サイバー攻撃", "サイバー攻撃"),
    ("お詫び", "お詫びとお知らせ"),
]
YAHOO_API = ("https://search.yahoo.co.jp/realtime/api/v1/transition"
             "?p={word}&interval=86400&span=2592000&rkf=3")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# 事案・株価
# -----------------------------------------
def load_incidents():
    d = json.load(open(INCIDENTS_JSON, encoding="utf-8"))
    inc = d["incidents"]
    inc.sort(key=lambda r: (r["date"], r["code"]))
    return inc


def fetch_closes(codes, start):
    tickers = sorted({c + ".T" for c in codes} | {BENCH})
    data = yf.download(tickers, start=start, auto_adjust=True, progress=False, threads=True)["Close"]
    if isinstance(data, pd.Series):
        data = data.to_frame()
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    return fill_missing_last_day(data)


def fill_missing_last_day(data):
    """日付が変わった直後などに、Yahooの日足で当日分が一部銘柄だけ NaN になることがある。
       基準(1306)に当日があるのに欠けている銘柄は、1時間足の最終バー → fast_info.lastPrice の順で埋める"""
    if BENCH not in data or data[BENCH].dropna().empty:
        return data
    last = data[BENCH].dropna().index[-1]
    missing = [t for t in data.columns if t != BENCH and pd.isna(data.at[last, t]) if last in data.index]
    if not missing:
        return data
    log(f"  {last.date()} の終値が欠けている銘柄 {len(missing)} 件 → 最新値/1時間足で補完")
    # fast_info.lastPrice は大引け（引け値）そのもの。ただし翌営業日の寄り付き後は翌日の値になるので、当日〜翌朝9時前だけ使う
    now = datetime.datetime.now()
    use_last = (now.date() == last.date()) or (now.date() == (last + pd.Timedelta(days=1)).date() and now.hour < 9)
    for t in missing:
        try:
            tk = yf.Ticker(t)
            lp = tk.fast_info.get("lastPrice") if use_last else None
            if lp:
                data.at[last, t] = float(lp)
                continue
            h = tk.history(period="5d", interval="1h", auto_adjust=True)   # 最終バーは引け板を含まないことがある（近似）
            if len(h):
                h.index = pd.to_datetime(h.index).tz_localize(None)
                day = h[h.index.normalize() == last]
                if len(day):
                    data.at[last, t] = float(day["Close"].iloc[-1])
        except Exception as e:
            log(f"    補完失敗 {t}: {e}")
    still = [t for t in missing if pd.isna(data.at[last, t])]
    if still:
        log(f"  補完できず: {still}")
    return data


def _hm(text):
    m = re.match(r"\s*(\d{1,2}):(\d{2})", text or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def timing(flag, auto=None):
    """公表時刻 → ('pre'|'intra'|'after', 不明フラグ, 根拠)
       flag: JSONの time（pre/intra/after/"HH:MM"/?）。auto: 報道初出 "HH:MM"（公表日当日, Googleニュース）
       不明(?)は引け後扱い（公表日の値動きを数えない）。ただし報道初出が 15:30 より前なら、公表はそれ以前なので場中(9時前なら寄り前)に昇格"""
    f = (flag or "?").strip()
    if f in ("pre", "intra", "after"):
        return f, False, "手動"
    hm = _hm(f)
    if hm is not None:
        return ("pre" if hm < 9 * 60 else "after" if hm >= 15 * 60 + 30 else "intra"), False, f"公表{f}"
    a = _hm(auto or "")
    if a is not None and a < 15 * 60 + 30:
        return ("pre" if a < 9 * 60 else "intra"), False, f"報道初出{auto}"
    return "after", True, "時刻不明→引け後扱い"


GNEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"


NEWS_WORDS = ("不正アクセス", "漏えい", "漏洩", "流出", "紛失", "サイバー")


def _fetch_text_deadline(url, deadline_sec):
    """urlopen をデーモンスレッドで実行し、deadline_sec 秒で見切る（2026-10-08 20:00 の定時実行が
       Googleニュース問い合わせで無期限ハングし投稿が止まった事故の対策。socket timeout は DNS や
       細切れ受信では効かないため、壁時計で打ち切る）"""
    import threading
    box = {}

    def _run():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            box["data"] = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        except Exception as e:
            box["err"] = e

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(deadline_sec)
    if th.is_alive():
        raise TimeoutError(f"{deadline_sec}秒以内に応答なし")
    if "err" in box:
        raise box["err"]
    return box["data"]


def news_first_time(r):
    """Googleニュース検索RSSで、公表日当日の最も早い報道時刻(JST "HH:MM")を返す。見つからなければ None。
       Googleの検索結果は雑音が多いので、見出しに 検索語(kw) と 事故語(NEWS_WORDS) の両方を含む記事だけ使う。
       報道時刻は公表時刻の上限（公表はそれ以前）としてだけ使う。公表日より前の日に該当記事があれば警告（公表日の誤り候補）"""
    import email.utils
    import html as _html
    kw = r.get("kw") or r["name"]
    d0 = datetime.date.fromisoformat(r["date"])
    q = f'"{kw}" (不正アクセス OR 漏えい OR 漏洩 OR 流出 OR 紛失) after:{(d0 - datetime.timedelta(days=1)).isoformat()} before:{(d0 + datetime.timedelta(days=2)).isoformat()}'
    url = GNEWS_RSS.format(q=urllib.parse.quote(q))
    xml = _fetch_text_deadline(url, 25)
    hits = []
    for m in re.finditer(r"<item><title>(.*?)</title>.*?<pubDate>([^<]+)</pubDate>", xml, re.S):
        title = _html.unescape(m.group(1))
        if kw not in title and r["name"] not in title:
            continue
        if not any(w in title for w in NEWS_WORDS):
            continue
        try:
            dt = email.utils.parsedate_to_datetime(m.group(2)).astimezone(datetime.timezone(datetime.timedelta(hours=9)))
        except Exception:
            continue
        hits.append((dt, title))
    hits.sort()
    first_on_day = next((dt for dt, _ in hits if dt.date() == d0), None)
    earlier = [(dt, t) for dt, t in hits if dt.date() < d0]
    if earlier:
        log(f"  ⚠ {r['name']}: 公表日({d0})より前の {earlier[0][0]:%m/%d %H:%M} に報道あり → 公表日の誤り候補: {earlier[0][1][:60]}")
        r["time_auto_note"] = f"前日{earlier[0][0]:%m/%d %H:%M}に報道あり・公表日要確認"
    return first_on_day.strftime("%H:%M") if first_on_day else None


def save_incidents(inc):
    """roei_incidents.json を1事案1行の形式で書き戻す（time_auto の記録用）"""
    d = json.load(open(INCIDENTS_JSON, encoding="utf-8"))
    keep = ("date", "time", "time_auto", "time_auto_checked", "time_auto_note", "kw", "code", "name", "service", "type", "scale", "note", "url", "auto")
    rows = [json.dumps({k: r[k] for k in keep if k in r}, ensure_ascii=False) for r in sorted(inc, key=lambda r: (r["date"], r["code"]))]
    txt = ('{\n  "_説明": ' + json.dumps(d["_説明"], ensure_ascii=False)
           + ',\n  "_aliases": ' + json.dumps(d.get("_aliases", {}), ensure_ascii=False)
           + ',\n  "_ignore": ' + json.dumps(d.get("_ignore", []), ensure_ascii=False)
           + ',\n  "incidents": [\n    ' + ',\n    '.join(rows) + '\n  ]\n}\n')
    with open(INCIDENTS_JSON, "w", encoding="utf-8") as f:
        f.write(txt)


def fill_auto_times(inc):
    """time が ? の事案について、報道初出時刻を自動で調べて time_auto に記録（公表から10日間は毎回再確認、以後は記録を使う）"""
    today = datetime.date.today()
    changed = False
    t_start = time.time()
    BUDGET_SEC = 120   # 全体の時間予算。超えたら残りは次回に回す（株価計算・投稿を優先）
    for r in inc:
        if (r.get("time") or "?").strip() != "?":
            continue
        d0 = datetime.date.fromisoformat(r["date"])
        if r.get("time_auto_checked") and (today - d0).days > 10:
            continue
        if time.time() - t_start > BUDGET_SEC:
            log(f"  報道初出の調査は時間予算({BUDGET_SEC}秒)超過 → {r['name']} 以降は次回に回す")
            break
        try:
            t = news_first_time(r)
        except Exception as e:
            log(f"  報道初出の取得失敗 {r['name']}: {e}")
            continue
        r["time_auto_checked"] = today.isoformat()
        if t:
            r["time_auto"] = t
        log(f"  報道初出 {r['date']} {r['name']}: {t or '当日の報道なし'}")
        changed = True
    if changed:
        try:
            save_incidents(inc)
        except Exception as e:
            log(f"  roei_incidents.json 書き戻し失敗: {e}")


def compute(inc, closes):
    """各事案に base / day0 / 当日 / 各horizonの(株騰落, TOPIX騰落, 超過) を付ける
       after: 基準=公表日終値, 1営業日後=翌営業日終値
       pre:   基準=前営業日終値, 1営業日後=公表日終値
       intra: 基準=前営業日終値, 当日=公表日終値(引けまでの反応), 1営業日後=翌営業日終値（当日分を含む）"""
    bench = closes[BENCH].dropna()
    for r in inc:
        t = r["code"] + ".T"
        s = closes[t].dropna() if t in closes else pd.Series(dtype=float)
        r["ok"] = len(s) > 5
        r["ret"] = {}
        r["same_day"] = None
        kind, unknown, basis = timing(r.get("time"), None if r.get("auto") else r.get("time_auto"))   # 自動検出分は内容未確認なので昇格させない
        r["timing"], r["time_unknown"], r["time_basis"] = kind, unknown, basis
        if not r["ok"]:
            r["err"] = "株価取得不可"
            continue
        a = pd.Timestamp(r["date"])
        on = s[s.index == a]                 # 公表日の終値（休場日公表なら空）
        before = s[s.index < a]
        after = s[s.index > a]
        if a in bench.index and not len(on):
            # 取引日なのにこの銘柄の終値だけ無い＝データ源の遅れ。休場日扱いにして誤計算しないよう保留
            r["err"] = "公表日の株価が未取得（データ源の遅れ・次回再計算）"
            continue
        if kind == "after" and len(on):
            base = float(on.iloc[-1]); base_dt = a
            day0 = after.index[0] if len(after) else None
        elif kind == "pre" and len(on):
            if before.empty:
                r["err"] = "基準日の株価なし"; continue
            base = float(before.iloc[-1]); base_dt = before.index[-1]
            day0 = a
        else:                                 # intra / 不明 / 休場日公表
            if not len(on):
                r["timing"], r["time_unknown"] = "closed", False
            if before.empty:
                r["err"] = "基準日の株価なし"; continue
            base = float(before.iloc[-1]); base_dt = before.index[-1]
            if kind == "intra" and len(on):
                r["same_day"] = {"date": a.date().isoformat(), "stock": (float(on.iloc[-1]) / base - 1) * 100}
            day0 = after.index[0] if len(after) else None
        bb = bench[bench.index <= base_dt]
        base_b = float(bb.iloc[-1]) if len(bb) else None
        if r["same_day"] and base_b:
            r["same_day"]["bench"] = (float(bench.asof(a)) / base_b - 1) * 100
            r["same_day"]["excess"] = r["same_day"]["stock"] - r["same_day"]["bench"]
        if day0 is None:
            r["err"] = "翌営業日の株価が未取得（データ源の遅れ）" if len(bench[bench.index > a]) else "翌営業日がまだ来ていない"
            r["base"] = base
            continue
        pos = s.index.get_loc(day0)
        r["day0"] = day0.date().isoformat()
        r["base"] = base
        for n in HORIZONS:
            i = pos + n - 1
            if i < len(s):
                dt = s.index[i]
                rs = (float(s.iloc[i]) / base - 1) * 100
                rb = (float(bench.asof(dt)) / base_b - 1) * 100 if base_b else None
                r["ret"][n] = {"date": dt.date().isoformat(), "stock": rs, "bench": rb,
                               "excess": (rs - rb) if rb is not None else None}
            else:
                r["ret"][n] = None
        r["last"] = {"date": s.index[-1].date().isoformat(), "stock": (float(s.iloc[-1]) / base - 1) * 100,
                     "days": len(s) - pos,
                     "chg1d": (float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100 if len(s) >= 2 else None}
    return inc


def summarize(inc):
    """種別別に horizon ごとの超過リターン（中央値／平均／マイナス率）"""
    groups = {}
    for r in inc:
        if not r.get("ok") or not r.get("ret"):
            continue
        groups.setdefault(r["type"], []).append(r)
    groups["全事案"] = [r for r in inc if r.get("ok") and r.get("ret")]
    out = {}
    for g, rows in groups.items():
        out[g] = {}
        for n in HORIZONS:
            vals = [r["ret"][n]["excess"] for r in rows if r["ret"].get(n) and r["ret"][n]["excess"] is not None]
            raw = [r["ret"][n]["stock"] for r in rows if r["ret"].get(n)]
            if vals:
                out[g][n] = {"n": len(vals), "med": float(np.median(vals)), "mean": float(np.mean(vals)),
                             "neg": sum(1 for v in vals if v < 0) / len(vals) * 100,
                             "raw_med": float(np.median(raw))}
            else:
                out[g][n] = None
    return out


# -----------------------------------------
# Xの投稿数（Yahooリアルタイム検索）
# -----------------------------------------
def fetch_word_counts(word):
    url = YAHOO_API.format(word=urllib.parse.quote(word))
    req = urllib.request.Request(url, headers={**UA, "Referer": f"https://search.yahoo.co.jp/realtime/search?p={urllib.parse.quote(word)}"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    data = json.loads(raw)
    out = {}
    today = datetime.date.today().isoformat()
    for e in data.get("tweetTransition", {}).get("entry", []):
        d = datetime.datetime.fromtimestamp(e["from"]).date().isoformat()
        if d < today:
            out[d] = e["count"]
    return out


def word_stats():
    rows = []
    for label, q in WORDS:
        try:
            c = fetch_word_counts(q)
        except Exception as e:
            log(f"  Yahooリアルタイム {label}: 取得失敗 {e}")
            continue
        days = sorted(c)
        if len(days) < 10:
            continue
        last7 = sum(c[d] for d in days[-7:])
        prev = [c[d] for d in days[:-7]]
        prev7 = (sum(prev) / len(prev) * 7) if prev else None
        ratio = (last7 / prev7) if prev7 else None
        peak_day = max(days[-7:], key=lambda d: c[d])
        rows.append({"label": label, "q": q, "last7": last7, "prev7": prev7, "ratio": ratio,
                     "peak_day": peak_day, "peak": c[peak_day], "series": [c[d] for d in days[-14:]], "dates": days[-14:]})
        time.sleep(1)
    rows.sort(key=lambda r: -(r["ratio"] or 0))
    return rows


# -----------------------------------------
# HTML
# -----------------------------------------
def nav_html():
    """map.html のナビを読んでそのまま使う（並び順・色の正本）。roei を active に"""
    try:
        s = open(os.path.join(SCRIPT_DIR, "map.html"), encoding="utf-8").read()
        m = re.search(r'<nav class="nav">.*?</nav>', s, re.S)
        nav = m.group(0)
        nav = re.sub(r' class="active"', "", nav)
        nav = nav.replace('<a href="roei.html"', '<a href="roei.html" class="active"')
        if 'href="roei.html"' not in nav:
            nav = nav.replace('<a href="kasetsu.html"', '<a href="roei.html" class="active" style="border-color:#db2777">情報漏洩銘柄検証</a>\n    <a href="kasetsu.html"')
        return nav
    except Exception:
        return '<nav class="nav"><a href="map.html" style="border-color:#94a3b8">デッキの見方</a></nav>'


CHIWAWA = ('<div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;">'
           '<svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>'
           'かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>')

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>情報漏洩銘柄検証 - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 22px 0 10px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{ color: #60a5fa; text-decoration: none; background: #1e293b; padding: 5px 14px; border-radius: 6px; border: 1px solid #334155; }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .evidence {{ background: rgba(59,130,246,0.08); border: 1px solid rgba(59,130,246,0.3); border-radius: 8px; padding: 10px 14px; font-size: 0.8rem; line-height: 1.8; max-width: 1200px; margin-bottom: 16px; }}
  .evidence b {{ color: #93c5fd; }}
  .num {{ color: #fbbf24; font-weight: 700; }}
  .table-wrap {{ overflow-x: auto; max-width: 1400px; }}
  table {{ border-collapse: collapse; font-size: 0.8rem; }}
  thead th {{ background: #1e293b; color: #94a3b8; padding: 6px 8px; text-align: right; font-weight: 600; white-space: nowrap; vertical-align: bottom; }}
  thead th.l {{ text-align: left; }}
  thead th.grp {{ text-align: center; color: #fbbf24; border-bottom: 1px solid #334155; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #1e293b; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; vertical-align: top; }}
  td.l {{ text-align: left; }}
  td.wrap {{ white-space: normal; max-width: 260px; color: #cbd5e1; }}
  tr:hover td {{ background: #16213a; }}
  .pos {{ color: #4ade80; }} .neg {{ color: #f87171; }}
  .ex {{ font-size: 0.72rem; color: #94a3b8; }}
  .tag {{ display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 0.72rem; border: 1px solid #475569; color: #cbd5e1; }}
  .tag.t1 {{ border-color: #f87171; color: #fca5a5; }} .tag.t2 {{ border-color: #fbbf24; color: #fde68a; }} .tag.t3 {{ border-color: #a78bfa; color: #c4b5fd; }} .tag.t4 {{ border-color: #60a5fa; color: #93c5fd; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.9; max-width: 1200px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .bar {{ display: inline-block; height: 8px; background: #3b82f6; vertical-align: middle; }}
  .total td {{ border-top: 1px solid #475569; font-weight: 700; }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  {chiwawa}
  {nav}
  <h1>情報漏洩銘柄検証 — 不正アクセス・情報漏洩を公表した企業の株価は、その後どう動いたか</h1>
  <p class="subtitle">最終更新: {updated}（月〜金 20:00） | 対象: 国内上場企業、2026年9月以降の公表事案 {n_inc}件 | 株価: yfinance（配当調整済み終値） | 対照: TOPIX連動ETF 1306</p>
  <div class="evidence">
    <b>見方:</b>
    <b class="num">①</b> 事案ごとに、公表後 <b>1・3・5・10・15・30営業日</b> の終値を「公表前の最後の終値」と比べる（上段＝株価の騰落、下段の小文字＝同じ日のTOPIX(1306)を引いた<b>超過</b>）。
    <b class="num">②</b> 公表時刻で起点を変える。<b>引け後</b>公表＝基準は公表日の終値、1営業日後は翌営業日。<b>寄り前</b>公表＝基準は前営業日の終値、1営業日後は公表日当日。<b>場中</b>公表＝基準は前営業日の終値、公表日の引けまでの反応を「当日」列に出し、1営業日後は翌営業日（当日分を含む）。時刻不明は<b>引け後扱い</b>（公表日の値動きは数えない）で <span class="num">?</span>。ただしGoogleニュースで公表日当日 15:30 より前の報道が見つかれば、公表はそれ以前なので場中（9時前なら寄り前）に自動で昇格し、根拠「報道初出HH:MM」を添える。
    <b class="num">③</b> 上の集計表は<b>超過リターン</b>の中央値／平均／マイナス率（TOPIXより弱かった事案の割合）。「全事案」行が土台、種別行は内訳。N が小さいうちは目安。
    <b class="num">④</b> 下のXの投稿数は Yahooリアルタイム検索の日次件数。直近7日の合計を、その前3週間の平均（7日換算）と比べた倍率。ニュースの熱量の目安で、株価との因果は主張しない。
  </div>

  <h2>③ 種別ごとの超過リターン（銘柄 − TOPIX、%）</h2>
  <div class="table-wrap">
  <table>
    <thead>
      <tr><th class="l">種別</th><th>N</th>{sum_head}</tr>
      <tr><th class="l"></th><th></th>{sum_sub}</tr>
    </thead>
    <tbody>
{sum_rows}
    </tbody>
  </table>
  </div>

  <h2>①② 事案別マトリクス（上段: 株価騰落%、下段: TOPIX比の超過%）</h2>
  <div class="table-wrap">
  <table>
    <thead>
      <tr><th class="l">公表日</th><th class="l">時刻</th><th class="l">企業（コード）</th><th class="l">種別</th><th class="l">対象・規模</th><th>当日<br><span class="ex">（場中公表のみ）</span></th><th>1営業日後</th><th>3日後</th><th>5日後</th><th>10日後</th><th>15日後</th><th>30日後</th><th>直近</th></tr>
    </thead>
    <tbody>
{inc_rows}
    </tbody>
  </table>
  </div>

  <h2>④ Xの投稿数（Yahooリアルタイム検索、直近7日 vs その前3週間の平均）</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th class="l">語</th><th>直近7日 合計</th><th>前3週の平均（7日換算）</th><th>倍率</th><th>直近7日のピーク</th><th class="l">直近14日の推移</th></tr></thead>
    <tbody>
{word_rows}
    </tbody>
  </table>
  </div>

  <p class="note">
    ・事案は手動で追加（roei_incidents.json）。TDnetの適時開示に出ないプレスリリースのみの公表も多いため、見落としがあれば随時追加する。<br>
    ・「規模」は公表時点の数字。続報で増えることが多い（例: タイムズカーは第1報「可能性」→第2報660万件確定）。表の規模欄は最新の公表値に手で更新する。<br>
    ・騰落は配当調整済み終値。time 欄は "pre"/"intra"/"after" または "HH:MM"（9:00前＝寄り前、15:30以降＝引け後、それ以外＝場中として自動判定）。"?" は引け後扱い＋Googleニュースの報道初出で自動補正（kw 欄＝検索語、既定は企業名）。<br>
    ・同じ日に複数の事案が公表されると、物流3社（9/29〜9/30）のように業界全体の連想売りが混ざる。種別だけでなく時期の重なりも見る。<br>
    ・このページは観測と答え合わせの記録で、売買の推奨ではない。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""

TYPE_CLS = {"ランサム": "t1", "不正アクセス": "t2", "委託先": "t3", "内部不正": "t4", "設定不備": "t4"}


def fmt(v, digits=1, plain=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    if plain:
        return f"{v:+.{digits}f}"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+.{digits}f}</span>'


def generate_html(inc, summ, words):
    sum_head = "".join(f"<th>{n}営業日後</th>" for n in HORIZONS)
    sum_sub = "".join('<th class="ex">中央値 / 平均 / マイナス率</th>' for _ in HORIZONS)
    sum_rows = []
    order = [g for g in summ if g != "全事案"] + (["全事案"] if "全事案" in summ else [])
    for g in order:
        cells = []
        nmax = 0
        for n in HORIZONS:
            c = summ[g][n]
            if c:
                nmax = max(nmax, c["n"])
                cells.append(f'<td>{fmt(c["med"])} / {fmt(c["mean"])} / <span class="ex">{c["neg"]:.0f}%</span>'
                             f'<br><span class="ex">N={c["n"]}</span></td>')
            else:
                cells.append('<td class="ex">-</td>')
        cls = ' class="total"' if g == "全事案" else ""
        sum_rows.append(f'      <tr{cls}><td class="l">{g}</td><td>{nmax}</td>{"".join(cells)}</tr>')
    if not sum_rows:
        sum_rows.append('      <tr><td colspan="8" style="text-align:center;color:#64748b">事案なし</td></tr>')

    inc_rows = []
    for r in sorted(inc, key=lambda x: (x["date"], x["code"]), reverse=True):
        tag = f'<span class="tag {TYPE_CLS.get(r["type"], "")}">{r["type"]}</span>'
        kind = r.get("timing", "intra")
        tlabel = {"pre": "寄り前", "intra": "場中", "after": "引け後", "closed": "休場日"}[kind]
        traw = (r.get("time") or "").strip()
        if r.get("time_unknown"):
            auto = r.get("time_auto")
            tcell = ('<span class="num" title="公表時刻が不明。引け後扱いで計算">?</span>'
                     + (f'<br><span class="ex">報道{auto}〜</span>' if auto else ""))
        else:
            sub = traw if re.match(r"\d{1,2}:\d{2}", traw) else ("" if r.get("time_basis") == "手動" else r.get("time_basis", ""))
            tcell = tlabel + (f'<br><span class="ex">{sub}</span>' if sub else "")
        name = f'{r["name"]}（{r["code"]}）'
        scale = f'{r.get("service", "")}<br><span class="ex">{r.get("scale", "")}</span>'
        if r.get("auto"):
            scale = '<span class="tag t4" title="報道見出しから自動検出。内容は未確認">自動検出</span> ' + scale
        if r.get("note"):
            scale += f'<br><span class="ex">{r["note"]}</span>'
        cells = []
        sd = r.get("same_day")
        if sd:
            cells.append(f'<td>{fmt(sd["stock"])}<br><span class="ex">{fmt(sd.get("excess"), plain=True) if sd.get("excess") is not None else ""}</span></td>')
        else:
            cells.append('<td class="ex">—</td>')
        if not r.get("ok") or not r.get("ret"):
            cells.append(f'<td colspan="7" class="ex">{r.get("err", "-")}</td>')
        else:
            for n in HORIZONS:
                c = r["ret"].get(n)
                if c:
                    cells.append(f'<td>{fmt(c["stock"])}<br><span class="ex">{fmt(c["excess"], plain=True)}</span></td>')
                else:
                    cells.append('<td class="ex">（未到達）</td>')
            L = r.get("last")
            cells.append(f'<td>{fmt(L["stock"])}<br><span class="ex">{L["days"]}日目 {L["date"][5:]}</span></td>' if L else "<td>-</td>")
        inc_rows.append(f'      <tr><td class="l">{r["date"][5:].replace("-", "/")}</td><td class="l">{tcell}</td><td class="l">{name}</td>'
                        f'<td class="l">{tag}</td><td class="l wrap">{scale}</td>{"".join(cells)}</tr>')
    if not inc_rows:
        inc_rows.append('      <tr><td colspan="13" style="text-align:center;color:#64748b">事案なし</td></tr>')

    word_rows = []
    for w in words:
        mx = max(w["series"]) or 1
        bars = "".join(f'<span class="bar" style="width:{max(2, int(v / mx * 40))}px; margin-right:2px; opacity:{0.5 if i < 7 else 1}" title="{d}: {v:,}"></span>'
                       for i, (d, v) in enumerate(zip(w["dates"], w["series"])))
        ratio = f'{w["ratio"]:.1f}倍' if w["ratio"] else "-"
        hot = ' class="num"' if (w["ratio"] or 0) >= 2 else ""
        word_rows.append(f'      <tr><td class="l">{w["label"]}<br><span class="ex">{w["q"]}</span></td><td>{w["last7"]:,}</td>'
                         f'<td>{w["prev7"]:,.0f}</td><td{hot}>{ratio}</td><td>{w["peak_day"][5:]} {w["peak"]:,}</td><td class="l">{bars}</td></tr>')
    if not word_rows:
        word_rows.append('      <tr><td colspan="6" style="text-align:center;color:#64748b">取得できず</td></tr>')

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_inc=len(inc), chiwawa=CHIWAWA, nav=nav_html(),
        sum_head=sum_head, sum_sub=sum_sub, sum_rows="\n".join(sum_rows),
        inc_rows="\n".join(inc_rows), word_rows="\n".join(word_rows),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")


# -----------------------------------------
# 速報カード（兜町の電光掲示板風）
# -----------------------------------------
def make_card(inc, summ, words, only_latest=True):
    """速報カード: 東証アローズの電光ボード風（濃紺の地・パネル枡・白箱の現在値・赤緑の騰落）"""
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(DRAFTS_DIR, exist_ok=True)
    rows = [r for r in inc if r.get("ok") and r.get("ret")]
    # 本日公表でまだ1営業日後が無い事案も一覧に載せる（場中公表なら「当日」の騰落、引け後なら「本日公表」）
    fresh = [r for r in inc if r.get("ok") and not r.get("ret") and r.get("err") == "翌営業日がまだ来ていない"]
    rows += fresh
    rows.sort(key=lambda r: (r["date"], r["code"]), reverse=True)
    W, H = 1200, 675
    BG = (0, 85, 234); PANEL = (10, 16, 32); BAR = (22, 34, 60); LINE = (90, 150, 240)   # 外枠は #0055EA（アローズの青）、パネルは黒
    WH = (240, 240, 235); GR = (150, 165, 190); AMB = (255, 196, 60); GN = (80, 220, 110); RD = (255, 85, 80); BOXBG = (235, 238, 242); BOXTX = (10, 14, 30)
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    fp = next((p for p in [r"C:\Windows\Fonts\meiryob.ttc", r"C:\Windows\Fonts\YuGothB.ttc", r"C:\Windows\Fonts\msgothic.ttc",
                          "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"] if os.path.exists(p)), None)
    F = lambda sz: ImageFont.truetype(fp, sz) if fp else ImageFont.load_default()
    f28, f22, f19, f16, f14, f12 = F(28), F(22), F(19), F(16), F(14), F(12)
    today = datetime.date.today()
    mday = max([r["last"]["date"] for r in rows if r.get("last")], default=today.isoformat())

    def panel(x0, y0, x1, y1, title, en):
        d.rectangle([x0, y0, x1, y1], fill=PANEL, outline=LINE)
        d.rectangle([x0, y0, x1, y0 + 26], fill=BAR)
        d.text((x0 + 10, y0 + 4), title, font=f16, fill=WH)
        d.text((x0 + 12 + d.textlength(title, font=f16), y0 + 8), en, font=f12, fill=GR)

    def box(x, y, w, text, col=BOXTX, fnt=None, h=32):
        fnt = fnt or f22
        d.rectangle([x, y, x + w, y + h], fill=BOXBG)
        tw = d.textlength(text, font=fnt)
        bb = d.textbbox((0, 0), text, font=fnt)
        th = bb[3] - bb[1]
        d.text((x + w - tw - 8, y + (h - th) / 2 - bb[1]), text, font=fnt, fill=col)

    def c(v):
        return GN if v is None or v >= 0 else RD

    # ---- ヘッダー
    TITLE = "「情報漏洩銘柄」株価ウォッチ"
    d.text((20, 10), TITLE, font=f28, fill=(255, 255, 255))
    d.text((20 + d.textlength(TITLE, font=f28) + 14, 20), "Data Breach Stock Watch", font=f14, fill=(190, 210, 240))
    d.text((W - 360, 14), f"{mday} 大引け時点  数字＝公表前終値比の騰落率 %", font=f14, fill=(220, 230, 245))
    d.text((W - 360, 32), "下段の小数字＝TOPIX(1306)比の超過", font=f12, fill=(190, 210, 240))

    # ---- 上段3パネル
    top_y, top_h = 56, 170
    pw = (W - 20 * 2 - 12 * 2) // 3
    px = [20, 20 + pw + 12, 20 + 2 * (pw + 12)]
    # P1 本日が初日
    panel(px[0], top_y, px[0] + pw, top_y + top_h, "本日 公表後初日", "First Session")
    first = [(r, r["ret"][1], "初日") for r in rows if r.get("day0") == mday and r["ret"].get(1)]
    first += [(r, r["same_day"], "当日・場中公表") for r in inc if r.get("same_day") and r["same_day"].get("date") == mday and r["same_day"].get("excess") is not None]
    # 引け後公表（または休場日）でまだ終値がついていないもの: 騰落率は出さず「翌営業日が初日」とだけ示す
    pending = [r for r in inc if r.get("ok") and not r.get("ret") and r.get("err") == "翌営業日がまだ来ていない"
               and not (r.get("same_day") and r["same_day"].get("date") == mday)]
    yy = top_y + 34
    if first:
        for r, cc, lab in first[:3]:
            d.text((px[0] + 10, yy), f'{r["name"]} {r["code"]}', font=f16, fill=WH)
            box(px[0] + pw - 120, yy - 4, 110, f'{cc["stock"]:+.1f}', col=(170, 20, 20) if cc["stock"] < 0 else (0, 110, 50))
            d.text((px[0] + 10, yy + 22), f'TOPIX比 {cc["excess"]:+.1f}  {lab}  {r.get("type", "")}', font=f12, fill=GR)
            yy += 46
    elif not pending:
        d.text((px[0] + 10, yy + 10), "本日の該当なし", font=f16, fill=GR)
    for r in pending[:max(0, 3 - len(first[:3]))]:
        d.text((px[0] + 10, yy), f'{r["name"]} {r["code"]}', font=f16, fill=GR)
        d.text((px[0] + 10, yy + 22), f'{r["date"][5:].replace("-", "/")}引け後公表 → 翌営業日が初日（騰落率は未計測）', font=f12, fill=GR)
        yy += 46
    # P2 本日の最大下落
    panel(px[1], top_y, px[1] + pw, top_y + top_h, "本日 最大下落", "Biggest Drop (vs prev. close)")
    tracked = [r for r in rows if r.get("last") and r["last"].get("chg1d") is not None and r["last"]["days"] <= 30 and r["last"]["date"] == mday]
    if tracked:
        w_ = min(tracked, key=lambda r: r["last"]["chg1d"])
        d.text((px[1] + 10, top_y + 34), f'{w_["name"]} {w_["code"]}', font=f19, fill=WH)
        d.text((px[1] + 10, top_y + 58), f'{w_["date"][5:].replace("-", "/")}公表 {w_.get("type", "")}', font=f12, fill=GR)
        box(px[1] + 10, top_y + 76, 150, f'{w_["last"]["chg1d"]:+.1f}', col=(170, 20, 20) if w_["last"]["chg1d"] < 0 else (0, 110, 50), fnt=f28, h=42)
        d.text((px[1] + 170, top_y + 80), "前日比", font=f12, fill=GR)
        d.text((px[1] + 170, top_y + 98), f'公表前比 {w_["last"]["stock"]:+.1f}', font=f14, fill=c(w_["last"]["stock"]))
        d.text((px[1] + 10, top_y + 126), f'追跡中 {len(tracked)}銘柄（公表30営業日以内）', font=f12, fill=GR)
    # P3 全事案 中央値
    panel(px[2], top_y, px[2] + pw, top_y + top_h, "全事案 中央値", "Median excess vs TOPIX")
    g = summ.get("全事案", {})
    yy = top_y + 36
    for n in HORIZONS[:5]:
        cc = g.get(n)
        d.text((px[2] + 10, yy), f"{n}営業日後", font=f14, fill=GR)
        if cc:
            d.text((px[2] + 110, yy), f'{cc["med"]:+.1f}', font=f16, fill=c(cc["med"]))
            d.text((px[2] + 175, yy + 2), f'N={cc["n"]}  下落率{cc["neg"]:.0f}%', font=f12, fill=GR)
        else:
            d.text((px[2] + 110, yy), "—", font=f16, fill=GR)
        yy += 24

    # ---- 下段: 事案ボード
    by0 = top_y + top_h + 12
    panel(20, by0, W - 20, H - 60, "事案一覧（公表日順）", "Incidents since Sep 2026  —  1 / 5 / 10 days, latest   公表: 前=寄り前 中=場中 後=引け後 休=休場日 ?=時刻不明(引け後扱い)")
    cols = [("公表", 32), ("銘柄", 100), ("コード", 330), ("種別", 400), ("1日", 520), ("5日", 620), ("10日", 720), ("直近", 820), ("日目", 905), ("対象", 960)]
    yy = by0 + 32
    for lab, x in cols:
        d.text((x, yy), lab, font=f12, fill=GR)
    yy += 18
    # 行の高さは26〜30pxで可変: 残り高さに収まる限り詰めて、端数が「空行」に見えないようにする
    avail = H - 60 - yy - 6
    maxrows = max(1, avail // 26)
    rh = min(30, avail // max(1, min(len(rows), maxrows)))
    for i, r in enumerate(rows[:maxrows]):
        hi = (r.get("day0") == mday)
        col_name = AMB if hi else ((150, 200, 255) if not r.get("ret") else WH)   # 黄=本日が初日、薄青=本日公表
        d.text((32, yy), r["date"][5:].replace("-", "/"), font=f14, fill=col_name)
        d.text((76, yy + 3), "?" if r.get("time_unknown") else {"pre": "前", "intra": "中", "after": "後", "closed": "休"}.get(r.get("timing"), "?"), font=f12, fill=GR)
        d.text((100, yy), r["name"][:14], font=f14, fill=col_name)
        d.text((330, yy), r["code"], font=f14, fill=GR)
        d.text((400, yy), r.get("type", "")[:6], font=f12, fill=GR)
        for n, x in ((1, 520), (5, 620), (10, 720)):
            cc = r["ret"].get(n)
            if cc:
                d.text((x, yy), f'{cc["stock"]:+.1f}', font=f16, fill=c(cc["stock"]))
                if cc.get("excess") is not None:
                    d.text((x + 52, yy + 3), f'{cc["excess"]:+.1f}', font=f12, fill=GR)
            else:
                d.text((x, yy), "—", font=f16, fill=(70, 85, 115))
        L = r.get("last")
        if L:
            d.text((820, yy), f'{L["stock"]:+.1f}', font=f16, fill=c(L["stock"]))
            d.text((905, yy + 2), f'{L["days"]}', font=f14, fill=GR)
        elif not r.get("ret"):
            sd = r.get("same_day")
            if sd and sd.get("stock") is not None:
                d.text((820, yy), f'{sd["stock"]:+.1f}', font=f16, fill=c(sd["stock"]))
                d.text((872, yy + 3), "当日", font=f12, fill=GR)
            else:
                d.text((820, yy + 2), "本日公表", font=f12, fill=GR)
            d.text((905, yy + 2), "0", font=f14, fill=GR)
        d.text((960, yy + 2), (r.get("scale") or "")[:18], font=f12, fill=GR)
        yy += rh
        d.line([(28, yy - 5), (W - 28, yy - 5)], fill=(24, 36, 60))

    # ---- フッター: Xの投稿数（為替パネル風）
    fy = H - 50
    d.rectangle([20, fy, W - 20, H - 12], fill=PANEL, outline=LINE)
    d.text((32, fy + 6), "X 投稿数（直近7日 / 前3週平均）", font=f12, fill=GR)
    x = 230
    for w in (words or [])[:3]:
        if not w.get("ratio"):
            continue
        d.text((x, fy + 4), w["label"], font=f14, fill=WH)
        d.text((x, fy + 22), f'{w["last7"]:,}件', font=f12, fill=GR)
        d.text((x + 110, fy + 8), f'×{w["ratio"]:.1f}', font=f19, fill=RD if w["ratio"] >= 2 else GN)
        x += 215
    d.text((W - 290, fy + 14), "ichikon77.github.io/minervini/roei.html", font=f12, fill=GR)
    out = os.path.join(DRAFTS_DIR, f"roei_card_{today:%Y%m%d}.png")
    im.save(out)
    log(f"速報カード: {out}")
    outs = [out]
    # 1枚目に収まらなかった事案は2枚目（一覧のみ）に
    rest = rows[maxrows:]
    if rest:
        im2 = Image.new("RGB", (W, H), BG)
        d2 = ImageDraw.Draw(im2)
        d2.text((20, 10), TITLE, font=f28, fill=(255, 255, 255))
        d2.text((20 + d2.textlength(TITLE, font=f28) + 14, 20), "Incidents (continued)", font=f14, fill=(190, 210, 240))
        d2.text((W - 360, 14), f"{mday} 大引け時点  数字＝公表前終値比の騰落率 %", font=f14, fill=(220, 230, 245))
        d2.rectangle([20, 56, W - 20, H - 12], fill=PANEL, outline=LINE)
        d2.rectangle([20, 56, W - 20, 82], fill=BAR)
        d2.text((30, 60), "事案一覧（続き）", font=f16, fill=WH)
        yy2 = 90
        for lab, x in cols:
            d2.text((x, yy2), lab, font=f12, fill=GR)
        yy2 += 18
        for r in rest[: (H - 12 - yy2) // 30]:
            d2.text((32, yy2), r["date"][5:].replace("-", "/"), font=f14, fill=WH)
            d2.text((76, yy2 + 3), "?" if r.get("time_unknown") else {"pre": "前", "intra": "中", "after": "後", "closed": "休"}.get(r.get("timing"), "?"), font=f12, fill=GR)
            d2.text((100, yy2), r["name"][:14], font=f14, fill=WH)
            d2.text((330, yy2), r["code"], font=f14, fill=GR)
            d2.text((400, yy2), r.get("type", "")[:6], font=f12, fill=GR)
            for n, x in ((1, 520), (5, 620), (10, 720)):
                cc = r["ret"].get(n)
                if cc:
                    d2.text((x, yy2), f'{cc["stock"]:+.1f}', font=f16, fill=c(cc["stock"]))
                    if cc.get("excess") is not None:
                        d2.text((x + 52, yy2 + 3), f'{cc["excess"]:+.1f}', font=f12, fill=GR)
                else:
                    d2.text((x, yy2), "—", font=f16, fill=(70, 85, 115))
            L = r.get("last")
            if L:
                d2.text((820, yy2), f'{L["stock"]:+.1f}', font=f16, fill=c(L["stock"]))
                d2.text((905, yy2 + 2), f'{L["days"]}', font=f14, fill=GR)
            elif not r.get("ret"):
                sd = r.get("same_day")
                if sd and sd.get("stock") is not None:
                    d2.text((820, yy2), f'{sd["stock"]:+.1f}', font=f16, fill=c(sd["stock"]))
                    d2.text((872, yy2 + 3), "当日", font=f12, fill=GR)
                else:
                    d2.text((820, yy2 + 2), "本日公表", font=f12, fill=GR)
                d2.text((905, yy2 + 2), "0", font=f14, fill=GR)
            d2.text((960, yy2 + 2), (r.get("scale") or "")[:18], font=f12, fill=GR)
            yy2 += 30
            d2.line([(28, yy2 - 5), (W - 28, yy2 - 5)], fill=(24, 36, 60))
        out2 = os.path.join(DRAFTS_DIR, f"roei_card_{today:%Y%m%d}_2.png")
        im2.save(out2)
        log(f"速報カード（2枚目）: {out2}")
        outs.append(out2)
    return outs


# -----------------------------------------
# push
# -----------------------------------------
def push_to_github():
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML, "roei_screen.py", "roei_run.bat", "roei_incidents.json"], check=True)
    result = subprocess.run(["git", "-C", SCRIPT_DIR, "commit", "-m", "update roei report " + today], capture_output=True)
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
            log("  Done: https://ichikon77.github.io/minervini/roei.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


def main():
    log("情報漏洩銘柄検証 開始")
    inc = load_incidents()
    try:
        import roei_scan
        roei_scan.set_logger(log)
        meta = json.load(open(INCIDENTS_JSON, encoding="utf-8"))
        added, _ = roei_scan.scan(inc, meta.get("_aliases", {}), meta.get("_ignore", []))
        if added:
            inc.extend(added)
            inc.sort(key=lambda r: (r["date"], r["code"]))
            save_incidents(inc)
            log(f"  新規事案を自動追加: {len(added)} 件（roei_incidents.json に書き込み済み。auto=true）")
    except Exception as e:
        log(f"  新規事案スキャン失敗（既存リストで続行）: {e}")
    fill_auto_times(inc)
    start = (datetime.date.fromisoformat(min(r["date"] for r in inc)) - datetime.timedelta(days=20)).isoformat() if inc else "2026-08-01"
    closes = None
    for attempt in range(1, 4):
        try:
            closes = fetch_closes([r["code"] for r in inc], start)
            if BENCH in closes and closes[BENCH].dropna().shape[0] >= 5:
                break
            log(f"  株価取得が空（{attempt}/3）→ 30秒後に再試行")
        except Exception as e:
            log(f"  株価取得エラー（{attempt}/3）: {e}")
        time.sleep(30)
    if closes is None or BENCH not in closes or closes[BENCH].dropna().shape[0] < 5:
        log("エラー: 株価を取得できないため、ページ・投稿用データは更新しません（前回のまま）")
        sys.exit(1)
    compute(inc, closes)
    summ = summarize(inc)
    for r in inc:
        if r.get("ok") and r.get("ret"):
            r1 = r["ret"].get(1)
            log(f"  {r['date']} {r['name']}({r['code']}) 1日後 {r1['stock']:+.1f}% (超過 {r1['excess']:+.1f}%)" if r1 else f"  {r['date']} {r['name']}: 未到達")
        else:
            log(f"  {r['date']} {r['name']}({r['code']}): {r.get('err')}")
    words = word_stats()
    generate_html(inc, summ, words)
    # 夕方の自動投稿（roei_post.py）に渡す数値
    try:
        last_dates = [r["last"]["date"] for r in inc if r.get("last")]
        market_day = max(last_dates) if last_dates else None
        json.dump({
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "market_day": market_day,
            "incidents": [{k: r.get(k) for k in ("date", "time", "time_auto", "timing", "time_unknown", "time_basis", "same_day", "auto", "code", "name", "service", "type", "scale", "day0", "ret", "last", "ok", "err")}
                          for r in inc],
            "summary": {g: {str(n): v for n, v in d.items()} for g, d in summ.items()},
            "words": [{k: w.get(k) for k in ("label", "last7", "prev7", "ratio", "peak_day", "peak")} for w in words],
        }, open(os.path.join(SCRIPT_DIR, "roei_post.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    except Exception as e:
        log(f"投稿用JSONの出力失敗: {e}")
    if "--card" in sys.argv:
        try:
            make_card(inc, summ, words)
        except Exception as e:
            log(f"速報カード生成失敗: {e}")
    if "--nopush" in sys.argv:
        log("push スキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
