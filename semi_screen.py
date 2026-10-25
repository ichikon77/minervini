# -*- coding: utf-8 -*-
"""
半導体ゾンビの洞窟（半導体・AI関連40銘柄の毎営業日ウォッチ） → semi.html → GitHub Pages公開 → semi_post.py が戦士のX投稿

考え方（2026-10-25 ユーザー発案）:
  2026年の半導体バブルで買われ、6〜7月の高値から −30〜65% に落ち込んだ銘柄群を「半導体ゾンビ」と呼び、
  毎営業日、洞窟の深さ（1年高値からの下落率）・復活度・需給（信用倍率と膨張率）・アナリスト目標との乖離・X の話題度を記録する。
  Xでは話題性の高い銘柄だけを戦士がピックアップして報告する（semi_post.py）。
  売買の判断材料ではなく観測と答え合わせ。公開ページに載せるのは一般概念のみ。

用語:
  半導体ゾンビ = 1年高値から ZOMBIE_DD(−30%) 以上 下げている銘柄
  復活度       = (終値 − 高値後の安値) ÷ (1年高値 − 高値後の安値)。安値は1年高値をつけた日以降の最安値。0%=安値のまま、100%=高値奪還。50%超で「復活の兆し」
  膨張率(N)    = その日の信用倍率 ÷ その日を含む直近N営業日の倍率中央値（margin.html と同じ定義。週次期間は5営業日に展開）
  話題度       = 当日の X 投稿数 ÷ 直近20日の中央値（sns_screen と同じ Yahoo リアルタイム検索の件数。当日は集計途中の暫定値）

データ源（すべて手元のデッキと共用）:
  株価 yfinance（配当調整済み）／需給 margin_all_history.json（JPX 日次、T+1 公表）／
  アナリスト margin_fundamentals.json（時価総額3,000億以上）＋不足分は yfinance info（週1回キャッシュ semi_rating_cache.json）／
  話題度 sns_screen.fetch_word_counts

実行: 平日 18:00（semi_run.bat → 続けて semi_post.py が投稿）。--nopush / --test（話題度を取らない）。
"""
import os
import re
import sys
import json
import time
import datetime
import statistics
import subprocess

import pandas as pd
import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

REPORT_HTML = "semi.html"
POST_JSON = os.path.join(SCRIPT_DIR, "semi_post.json")          # semi_post.py に渡す（gitignore）
HISTORY_JSON = os.path.join(SCRIPT_DIR, "semi_history.json")    # 日ごとの全銘柄スナップショット（答え合わせ用、push）
RATING_CACHE = os.path.join(SCRIPT_DIR, "semi_rating_cache.json")  # yfinance のアナリスト目標（週1更新、gitignore）
MARGIN_HISTORY = os.path.join(SCRIPT_DIR, "margin_all_history.json")
FUNDAMENTALS = os.path.join(SCRIPT_DIR, "margin_fundamentals.json")
LOG_TXT = os.path.join(SCRIPT_DIR, "semi_log.txt")

ZOMBIE_DD = -30.0        # 1年高値からこの%以下で「半導体ゾンビ」
REVIVAL_TH = 50.0        # 復活度がこの%超で「復活の兆し」
TREND_SHORT, TREND_LONG, TREND_MIN = 10, 50, 6
DAILY_START = "2026-09-25"   # margin_all_history の日次化開始日（それ以前は週次）
RATING_MAX_AGE_DAYS = 7
BUZZ_SLEEP = 0.7
BUZZ_MIN_POSTS = 30      # 話題と呼ぶ最低投稿数（当日）
BUZZ_MIN_SPIKE = 1.5     # 話題と呼ぶ最低倍率（当日÷20日中央値、経過時間で按分）

# 分類（2026-10-25 整理表 drafts/semi_universe_20261025.md）。表示名は投稿で読める長さに
CATEGORIES = [
    ("A", "メモリ"), ("B", "AI投資・持株"), ("C", "製造装置・前工程"), ("D", "後工程・テスト"), ("E", "材料・基板"),
    ("F", "受動部品"), ("G", "電線・光ファイバー"), ("H", "水・薬液・ガス"), ("I", "半導体メーカー・ファブレス"), ("J", "AIクラウド・電力・FA"),
]
# code: (表示名, 分類, X検索語)  ※検索語は一般語と混ざるものに " 株" を付ける
UNIVERSE = {
    "285A": ("キオクシア", "A", "キオクシア"),
    "9984": ("ソフトバンクG", "B", "ソフトバンクG"),
    "8035": ("東京エレクトロン", "C", "東京エレクトロン"),
    "7735": ("SCREEN", "C", "SCREEN 株"),
    "6525": ("KOKUSAI", "C", "KOKUSAI"),
    "6920": ("レーザーテック", "C", "レーザーテック"),
    "6323": ("ローツェ", "C", "ローツェ"),
    "6590": ("芝浦メカトロニクス", "C", "芝浦メカトロニクス"),
    "6361": ("荏原", "C", "荏原"),
    "6857": ("アドバンテスト", "D", "アドバンテスト"),
    "6146": ("ディスコ", "D", "ディスコ 株"),
    "7729": ("東京精密", "D", "東京精密"),
    "6315": ("TOWA", "D", "TOWA 株"),
    "6871": ("日本マイクロニクス", "D", "日本マイクロニクス"),
    "4063": ("信越化学", "E", "信越化学"),
    "3436": ("SUMCO", "E", "SUMCO"),
    "4004": ("レゾナック", "E", "レゾナック"),
    "4186": ("東京応化", "E", "東京応化"),
    "5384": ("フジミ", "E", "フジミインコーポレーテッド"),
    "4062": ("イビデン", "E", "イビデン"),
    "3110": ("日東紡", "E", "日東紡"),
    "6264": ("マルマエ", "E", "マルマエ"),
    "6976": ("太陽誘電", "F", "太陽誘電"),
    "6981": ("村田製作所", "F", "村田製作所"),
    "6762": ("TDK", "F", "TDK 株"),
    "5803": ("フジクラ", "G", "フジクラ"),
    "5801": ("古河電工", "G", "古河電工"),
    "5802": ("住友電工", "G", "住友電工"),
    "5805": ("SWCC", "G", "SWCC"),
    "6254": ("野村マイクロ", "H", "野村マイクロ"),
    "6368": ("オルガノ", "H", "オルガノ"),
    "6370": ("栗田工業", "H", "栗田工業"),
    "4091": ("日本酸素", "H", "日本酸素"),
    "6723": ("ルネサス", "I", "ルネサス"),
    "6526": ("ソシオネクスト", "I", "ソシオネクスト"),
    "6963": ("ローム", "I", "ローム 株"),
    "6875": ("メガチップス", "I", "メガチップス"),
    "3778": ("さくらインターネット", "J", "さくらインターネット"),
    "7011": ("三菱重工", "J", "三菱重工 株"),
    "6506": ("安川電機", "J", "安川電機"),
}


def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_TXT, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# -----------------------------------------
# 株価
# -----------------------------------------
def fetch_prices(codes):
    tickers = [c + ".T" for c in codes]
    data = yf.download(tickers, period="1y", auto_adjust=True, progress=False, group_by="ticker", threads=True)
    out = {}
    for c in codes:
        try:
            s = data[c + ".T"]["Close"].dropna()
        except Exception:
            continue
        if len(s) < 60:
            continue
        out[c] = s
    nk = yf.download("^N225", period="1y", auto_adjust=True, progress=False)["Close"]
    if isinstance(nk, pd.DataFrame):
        nk = nk.iloc[:, 0]
    return out, nk.dropna()


def price_stats(s):
    last, prev = float(s.iloc[-1]), float(s.iloc[-2])
    hi_i = s.idxmax()
    hi = float(s.max())
    # 安値は「1年高値をつけた日以降の最安値」（2026-10-25 ユーザー指定）。1年安値だと高値より前の上昇途中の値を拾ってしまう
    after = s[s.index >= hi_i]
    lo_i, lo = after.idxmin(), float(after.min())
    asof = s.index[-1]
    y0 = s[s.index >= f"{asof.year}-01-01"]
    rec = (last - lo) / (hi - lo) * 100 if hi > lo else None
    return dict(
        last=round(last, 1), d1=round((last / prev - 1) * 100, 2),
        hi=round(hi, 1), hi_date=str(hi_i.date()), dd=round((last / hi - 1) * 100, 1), hi_days=int((asof - hi_i).days),
        lo=round(lo, 1), lo_date=str(lo_i.date()), up=round((last / lo - 1) * 100, 1), lo_days=int((asof - lo_i).days),
        ytd=round((last / float(y0.iloc[0]) - 1) * 100, 1) if len(y0) else None,
        rec=round(rec, 1) if rec is not None else None, asof=str(asof.date()),
    )


# -----------------------------------------
# 需給（margin.html と同じ定義）
# -----------------------------------------
def business_series(rows):
    """rows=[(date, [制度売残, 制度買残, 一般売残, 一般買残]), ...] 古い順 → 営業日ごとの (date, 倍率, 総買残, 総売残) 列。
    週次行（DAILY_START より前）は申込日（金曜）の値をその週の月〜金に展開（margin.html の buildBusinessDays と同じ）"""
    out = []
    for d, r in rows:
        sell, buy = r[0], r[1]          # 制度信用のみ（margin.html の制度信用倍率・膨張率と同じ定義。一般信用は含めない）
        ratio = buy / sell if sell else None
        thin = (sell < 10000) or (sell * 50 < buy)   # 売り残僅少（margin.html と同じ）: その日の倍率は暴れるので「ふだん」の中央値に入れない
        if d >= DAILY_START:
            out.append((d, ratio, buy, sell, thin))
            continue
        end = datetime.date.fromisoformat(d)
        for k in range(end.weekday(), -1, -1):
            x = end - datetime.timedelta(days=k)
            if x.weekday() < 5:
                out.append((x.isoformat(), ratio, buy, sell, thin))
    return out


def margin_stats(hist, code):
    W = hist["weeks"]
    rows = sorted((d, W[d][code]) for d in W if code in W[d])
    if not rows:
        return None
    bd = business_series(rows)
    d, ratio, buy, sell, thin = bd[-1]
    # 当日が売り残僅少なら倍率は参考外（重い/軽いの順位から外す）。「ふだん」の中央値も僅少日を除いて取る
    # （2026-10-25 レゾナック: 8〜9月に売残が極端に少なく倍率が暴れていたため、中央値が実態より高く「ふだんの0.2倍」と出た）
    out = dict(margin_date=d, ratio=round(ratio, 1) if ratio else None, buy=buy, sell=sell, thin=thin)
    for N, tag in ((TREND_SHORT, "10"), (TREND_LONG, "50")):
        out["exp" + tag] = None
        if len(bd) >= N and ratio is not None and not thin:
            win = [v[1] for v in bd[-N:] if v[1] is not None and not v[4]]
            if len(win) >= TREND_MIN:
                m = statistics.median(win)
                out["exp" + tag] = round(ratio / m, 2) if m > 0 else None
    # 買残・売残の10営業日比（10営業日前の行と比較。分割をまたぐと歪むので、買残が2倍以上/半分以下に跳んでいれば伏せる）
    out["buy10"] = out["sell10"] = None
    if len(bd) > TREND_SHORT:
        b0, s0 = bd[-1 - TREND_SHORT][2], bd[-1 - TREND_SHORT][3]
        if b0 > 0 and 0.5 < buy / b0 < 2.0:
            out["buy10"] = round((buy / b0 - 1) * 100)
        if s0 > 0 and b0 > 0 and 0.5 < buy / b0 < 2.0:   # 分割判定は買残の跳びで行う（売残は分割時に k の5〜8割しか増えず判定に向かない）
            out["sell10"] = round((sell / s0 - 1) * 100)
    return out


# -----------------------------------------
# アナリスト目標（margin_fundamentals 優先、無ければ yfinance info を週1キャッシュ）
# -----------------------------------------
def load_ratings(codes):
    ratings = {}
    try:
        fund = json.load(open(FUNDAMENTALS, encoding="utf-8")).get("stocks", {})
        for c in codes:
            r = (fund.get(c) or {}).get("rating")
            if r and r.get("target"):
                ratings[c] = dict(target=r["target"], mean=r.get("mean"), n=r.get("n"), src="weekly")
    except Exception as e:
        log(f"  margin_fundamentals 読めず: {e}")
    cache = {}
    if os.path.exists(RATING_CACHE):
        try:
            cache = json.load(open(RATING_CACHE, encoding="utf-8"))
        except Exception:
            cache = {}
    today = datetime.date.today()
    missing = [c for c in codes if c not in ratings]
    stale = [c for c in missing if not cache.get(c) or (today - datetime.date.fromisoformat(cache[c]["asof"])).days >= RATING_MAX_AGE_DAYS]
    if stale and "--test" not in sys.argv:
        log(f"  yfinance でアナリスト目標を取得: {len(stale)}銘柄")
        for c in stale:
            try:
                i = yf.Ticker(c + ".T").info
                cache[c] = dict(asof=today.isoformat(), target=i.get("targetMeanPrice"), mean=i.get("recommendationMean"),
                                n=i.get("numberOfAnalystOpinions"))
            except Exception as e:
                log(f"    {c}: {e}")
            time.sleep(0.5)
        json.dump(cache, open(RATING_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for c in missing:
        v = cache.get(c)
        if v and v.get("target"):
            ratings[c] = dict(target=v["target"], mean=v.get("mean"), n=v.get("n"), src="yf")
    return ratings


# -----------------------------------------
# 話題度（X 投稿数、Yahoo リアルタイム検索）
# -----------------------------------------
def fetch_buzz(codes):
    try:
        import sns_screen
    except Exception as e:
        log(f"  sns_screen を import できず話題度なし: {e}")
        return {}
    out = {}
    today = datetime.date.today().isoformat()
    for c in codes:
        word = UNIVERSE[c][2]
        try:
            counts = sns_screen.fetch_word_counts(word)
        except Exception as e:
            log(f"    話題度 {word}: {e}")
            continue
        days = sorted(counts)
        if not days:
            continue
        last_day = days[-1]
        past = [counts[d] for d in days[:-1]][-20:]
        med = statistics.median(past) if past else None
        partial = (last_day == today)
        # 当日は集計途中なので、中央値を経過時間で按分して比べる（18:00 実行なら ×0.75。X の投稿は夜に偏るので倍率はやや高めに出る）
        frac = min(1.0, max(0.25, (datetime.datetime.now().hour + datetime.datetime.now().minute / 60) / 24)) if partial else 1.0
        spike = round(counts[last_day] / (med * frac), 1) if med else None
        out[c] = dict(day=last_day, today=counts[last_day], partial=partial, med20=med, spike=spike, total30=sum(counts.values()))
        time.sleep(BUZZ_SLEEP)
    return out


# -----------------------------------------
# 組み立て
# -----------------------------------------
def build(prices, nk, margins, ratings, buzz):
    rows = []
    for c, (name, cat, _) in UNIVERSE.items():
        if c not in prices:
            continue
        p = price_stats(prices[c])
        r = dict(code=c, name=name, cat=cat, **p)
        m = margins.get(c) or {}
        r.update(margin_date=m.get("margin_date"), ratio=m.get("ratio"), exp10=m.get("exp10"), exp50=m.get("exp50"),
                 buy10=m.get("buy10"), sell10=m.get("sell10"), thin=bool(m.get("thin")))
        a = ratings.get(c)
        r.update(target=a["target"] if a else None, tgt_gap=round((a["target"] / p["last"] - 1) * 100) if a and p["last"] else None,
                 rating=a.get("mean") if a else None, rating_n=a.get("n") if a else None, rating_src=a.get("src") if a else None)
        b = buzz.get(c) or {}
        r.update(buzz_today=b.get("today"), buzz_partial=b.get("partial"), buzz_med20=b.get("med20"), buzz_spike=b.get("spike"), buzz30=b.get("total30"))
        r["zombie"] = p["dd"] <= ZOMBIE_DD
        rows.append(r)
    rows.sort(key=lambda r: r["dd"])
    asof = rows[0]["asof"] if rows else None
    nk_last, nk_hi = float(nk.iloc[-1]), float(nk.max())
    summary = dict(
        asof=asof, n=len(rows), n_zombie=sum(1 for r in rows if r["zombie"]),
        n_bottom=sum(1 for r in rows if r["lo_days"] == 0),   # 今日が「高値のあとの最安値」の銘柄数
        nk_last=round(nk_last), nk_hi=round(nk_hi), nk_hi_date=str(nk.idxmax().date()), nk_dd=round((nk_last / nk_hi - 1) * 100, 1),
        nk_d1=round((nk_last / float(nk.iloc[-2]) - 1) * 100, 2),
        deepest=rows[0]["code"] if rows else None,
        top_up=[r["code"] for r in sorted(rows, key=lambda r: -r["d1"])[:3]],
        top_dn=[r["code"] for r in sorted(rows, key=lambda r: r["d1"])[:3]],
        heavy=[r["code"] for r in sorted([r for r in rows if r["exp50"] and not r["thin"]], key=lambda r: -r["exp50"])[:6]],
        light=[r["code"] for r in sorted([r for r in rows if r["exp50"] and not r["thin"]], key=lambda r: r["exp50"])[:6]],
        revival=[r["code"] for r in sorted([r for r in rows if r["rec"] is not None], key=lambda r: -r["rec"])[:3]],
        # 「話題」と呼ぶのは 当日 BUZZ_MIN_POSTS 件以上 かつ 中央値の BUZZ_MIN_SPIKE 倍以上 だけ（1件→0.5倍のような雑音を拾わない）
        buzz_top=[r["code"] for r in sorted([r for r in rows if r["buzz_spike"] and r["buzz_spike"] >= BUZZ_MIN_SPIKE and (r["buzz_today"] or 0) >= BUZZ_MIN_POSTS],
                                            key=lambda r: -r["buzz_spike"])[:3]],
        buzz_abs_top=[r["code"] for r in sorted([r for r in rows if r["buzz_today"] is not None], key=lambda r: -r["buzz_today"])[:3]],
        margin_date=next((r["margin_date"] for r in rows if r["margin_date"]), None),
    )
    return dict(generated=datetime.datetime.now().isoformat(timespec="seconds"), summary=summary, rows=rows,
                categories=CATEGORIES, zombie_dd=ZOMBIE_DD, revival_th=REVIVAL_TH)


# -----------------------------------------
# HTML
# -----------------------------------------
def nav_html():
    """map.html のナビを読んで同じ並び・色で生成（active=semi）。semi が未挿入なら roei の後に入れる"""
    try:
        src = open(os.path.join(SCRIPT_DIR, "map.html"), encoding="utf-8", errors="ignore").read()
        links = re.findall(r'<a href="([a-z_0-9]+\.html)"[^>]*border-color:(#[0-9a-fA-F]{6})[^>]*>([^<]+)</a>', src)
    except Exception:
        links = []
    if not links:
        links = [("map.html", "#94a3b8", "デッキの見方"), ("semi.html", "#db2777", "半導体ゾンビ")]
    if not any(h == "semi.html" for h, _, _ in links):
        out = []
        for h, col, lab in links:
            out.append((h, col, lab))
            if h == "roei.html":
                out.append(("semi.html", "#db2777", "半導体ゾンビ"))
        links = out
    out = []
    for h, col, lab in links:
        active = ' class="active"' if h == "semi.html" else ""
        out.append(f'    <a href="{h}"{active} style="border-color:{col}">{lab}</a>')
    return "\n".join(out)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>半導体ゾンビの洞窟 - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.1rem; margin: 24px 0 10px; color: #f1f5f9; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{ color: #60a5fa; text-decoration: none; background: #1e293b; padding: 5px 14px; border-radius: 6px; border: 1px solid #334155; }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .evidence {{ background: rgba(59,130,246,0.08); border: 1px solid rgba(59,130,246,0.3); border-radius: 8px; padding: 10px 14px; font-size: 0.8rem; line-height: 1.8; max-width: 1250px; margin-bottom: 16px; }}
  .evidence b {{ color: #93c5fd; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }}
  .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 10px 16px; min-width: 190px; }}
  .card .label {{ font-size: 0.75rem; color: #94a3b8; }}
  .card .v {{ font-size: 1.4rem; margin-top: 2px; font-variant-numeric: tabular-nums; }}
  .card .v small {{ font-size: 0.8rem; color: #94a3b8; margin-left: 6px; }}
  .filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 6px 0 12px; font-size: 0.8rem; }}
  .filters button {{ background: #1e293b; color: #cbd5e1; border: 1px solid #334155; border-radius: 6px; padding: 4px 10px; cursor: pointer; }}
  .filters button.on {{ background: #9d174d; border-color: #db2777; color: #fce7f3; }}
  table {{ border-collapse: collapse; font-size: 0.8rem; width: 100%; max-width: 1400px; }}
  thead th {{ background: #1e293b; color: #94a3b8; padding: 7px 7px; text-align: right; font-weight: 600; white-space: nowrap; position: sticky; top: 0; cursor: pointer; }}
  thead th:nth-child(-n+3) {{ text-align: left; }}
  thead th.sorted {{ color: #f9a8d4; }}
  td {{ padding: 6px 7px; border-bottom: 1px solid #1e293b; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
  td:nth-child(-n+3) {{ text-align: left; }}
  tr:hover td {{ background: #16213a; }}
  tr.hide {{ display: none; }}
  .pos {{ color: #4ade80; }} .neg {{ color: #f87171; }}
  .zombie {{ color: #f9a8d4; font-weight: 600; }}
  .dim {{ color: #64748b; font-size: 0.74rem; }}
  .rev {{ color: #fde68a; }}
  .buzz {{ color: #fb923c; }}
  .cat {{ color: #94a3b8; font-size: 0.74rem; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.9; max-width: 1250px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 20px; }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;">かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
{nav}
  </nav>
  <h1>半導体ゾンビの洞窟 — 2026年の半導体バブルで買われ、高値から落ちた{n}銘柄の毎日</h1>
  <p class="subtitle">最終更新: {updated} | 株価: {asof} 終値 | 需給: JPX {margin_date} 申込分 | 話題度: X 投稿数（Yahoo リアルタイム検索、当日は暫定） | 平日 18:00 更新・戦士が X で報告</p>

  <div class="evidence">
    <b>半導体ゾンビ</b>＝1年高値から <b>{zombie_dd:.0f}%</b> 以上 下げている銘柄（いま {n_zombie}/{n} 体）。日経平均は {nk_hi_date} の高値 {nk_hi:,} から {nk_dd:+.1f}% なのに対し、半導体・AI 関連は 6〜7月に揃って天井をつけ −30〜65%。指数は持ちこたえ、バブル銘柄だけが洞窟に沈んだ。<br>
    <b>復活度</b>＝(終値 − 安値) ÷ (1年高値 − 安値)。<b>安値は1年高値をつけた日以降の最安値</b>（高値より前の値は使わない）。0%＝安値のまま、100%＝高値奪還。<b>{revival_th:.0f}% 超で「復活の兆し」</b>。
    <b>膨張率</b>＝信用倍率 ÷ 直近N営業日の倍率中央値（10営業日＝直近2週間で急に動いたか、50営業日＝2〜3か月の平常比。<a href="margin.html" style="color:#93c5fd">銘柄チェッカー</a>と同じ定義だが、こちらは売り残僅少だった日を中央値から除く）。
    <b>目標乖離</b>＝アナリスト平均目標株価 ÷ 終値 − 1。目標は高値圏のまま残っていることが多く、乖離が大きい＝割安ではなく「アナリストがまだ追随していない」と読む。
    <b>話題度</b>＝当日の X 投稿数 ÷ 直近20日の中央値（当日は集計途中なので中央値を経過時間で按分）。当日30件以上かつ1.5倍以上を「話題」と呼ぶ。
  </div>

  <div class="cards">
    <div class="card"><div class="label">半導体ゾンビの数</div><div class="v zombie">{n_zombie}<small>/ {n} 体</small></div></div>
    <div class="card"><div class="label">今日 高値後の安値を更新</div><div class="v neg">{n_bottom}<small>/ {n} 体</small></div></div>
    <div class="card"><div class="label">最深部（高値からの下落）</div><div class="v neg">{deepest_name}<small>{deepest_dd:+.0f}%・{deepest_days}日目</small></div></div>
    <div class="card"><div class="label">今日 いちばん浮いた</div><div class="v pos">{up_name}<small>{up_d1:+.1f}%</small></div></div>
    <div class="card"><div class="label">今日 いちばん沈んだ</div><div class="v neg">{dn_name}<small>{dn_d1:+.1f}%</small></div></div>
    <div class="card"><div class="label">復活度 最上位（高値後の安値→高値）</div><div class="v rev">{rev_name}<small>{rev_rec:.0f}%</small></div></div>
    <div class="card"><div class="label">今日 X で話題（倍率）</div><div class="v buzz">{buzz_name}<small>{buzz_spike}</small></div></div>
    <div class="card"><div class="label">日経平均</div><div class="v {nk_cls}">{nk_d1:+.2f}%<small>{nk_last:,}</small></div></div>
  </div>

  <div class="filters" id="filters">
    <button class="on" data-cat="ALL">すべて</button>
{filter_buttons}
  </div>
  <table id="tbl"><thead><tr>
    <th data-k="code">code</th><th data-k="name">銘柄</th><th data-k="cat">分類</th>
    <th data-k="last">終値</th><th data-k="d1">前日比</th>
    <th data-k="dd" class="sorted">高値→DD%</th><th data-k="hi_days">高値から日</th><th data-k="up">高値後の安値→上昇%</th><th data-k="lo_days">安値から日</th><th data-k="rec">復活度</th><th data-k="ytd">YTD</th>
    <th data-k="ratio">制度信用倍率</th><th data-k="exp10">膨張率10</th><th data-k="exp50">膨張率50</th><th data-k="buy10">買残10日比</th><th data-k="sell10">売残10日比</th>
    <th data-k="tgt_gap">目標乖離</th><th data-k="rating">評価</th><th data-k="buzz_today">X投稿 当日</th><th data-k="buzz_spike">話題度</th><th data-k="buzz30">X投稿 30日</th>
  </tr></thead>
  <tbody>{rows}</tbody></table>

  <p class="note">
    ・これは<b>観測の記録</b>であり売買の判断材料ではない。下落率が大きい＝割安ではなく、復活度が高い＝続く保証もない。毎日の値は semi_history.json に蓄積し、後日「どの深さの銘柄がその後どうなったか」を答え合わせする。<br>
    ・株価は配当調整済み終値（yfinance）。株式分割（SBG 4分割 2025-12-29、フジクラ 6分割 2026-03-30、キオクシア・東京エレクトロン 2026-09-29 等）は調整済み。<br>
    ・需給は JPX「銘柄別信用取引残高」の日次データ（公表は翌営業日）の<b>制度信用</b>（一般信用は含めない。銘柄チェッカーの制度信用倍率と同じ）。信用倍率＝制度買残÷制度売残。※＝売り残僅少（1万株未満 or 買残の1/50未満）で倍率が暴れる銘柄、重い/軽いの順位から外す。倍率の上下は買残側と売残側の両方で起きるので、買残10日比・売残10日比を一緒に読む。買残・売残10日比は10営業日前との比較で、分割をまたいで2倍以上/半分以下に跳ぶ場合は伏せる。<br>
    ・アナリスト目標は 時価総額3,000億以上は margin_weekly（土曜更新）、それ未満は yfinance（週1更新）。評価は 1=強気買い〜5=売り。件数が少ない銘柄（n=1〜3）は参考程度。<br>
    ・話題度の検索語は銘柄ごとに調整（「ディスコ」「SCREEN」「TDK」「ローム」「TOWA」「三菱重工」は一般語と混ざるため「 株」を付加、「ソフトバンクG」は過小になりやすい）。当日分は集計途中の暫定値で翌日の実行で確定値に置き換わる。<br>
    ・対象40銘柄は 2026-10-25 に話題度・下落率・時価総額で選定した固定リスト（A〜J の分類）。入れ替えは手動。
  </p>
  <p class="updated">最終更新: {updated}</p>
<script>
const CATS = {cats_json};
document.querySelectorAll('#filters button').forEach(b => b.addEventListener('click', () => {{
  document.querySelectorAll('#filters button').forEach(x => x.classList.remove('on')); b.classList.add('on');
  const c = b.dataset.cat;
  document.querySelectorAll('#tbl tbody tr').forEach(tr => tr.classList.toggle('hide', c !== 'ALL' && tr.dataset.cat !== c));
}}));
let sortKey = 'dd', sortAsc = true;
document.querySelectorAll('#tbl thead th').forEach(th => th.addEventListener('click', () => {{
  const k = th.dataset.k;
  if (sortKey === k) sortAsc = !sortAsc; else {{ sortKey = k; sortAsc = !['name','cat','code'].includes(k) ? (k === 'dd' || k === 'exp50' ? true : false) : true; }}
  document.querySelectorAll('#tbl thead th').forEach(x => x.classList.remove('sorted')); th.classList.add('sorted');
  const tb = document.querySelector('#tbl tbody');
  const rows = Array.from(tb.querySelectorAll('tr'));
  rows.sort((a, b) => {{
    const va = a.dataset[k], vb = b.dataset[k];
    const na = parseFloat(va), nb = parseFloat(vb);
    if (isNaN(na) && isNaN(nb)) return (va || '').localeCompare(vb || '', 'ja');
    if (isNaN(na)) return 1; if (isNaN(nb)) return -1;
    return sortAsc ? na - nb : nb - na;
  }});
  rows.forEach(r => tb.appendChild(r));
}}));
</script>
</body>
</html>
"""


def _pct(v, nd=1, plus=True):
    if v is None:
        return "-"
    s = f"{v:+.{nd}f}%" if plus else f"{v:.{nd}f}%"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{s}</span>' if plus else s


def _x(v, nd=2, suffix="倍"):
    return "-" if v is None else f"{v:.{nd}f}{suffix}"


def rows_html(rows):
    catname = dict(CATEGORIES)
    out = []
    for r in rows:
        data = " ".join(f'data-{k}="{r.get(k) if r.get(k) is not None else ""}"' for k in
                        ("code", "name", "cat", "last", "d1", "dd", "hi_days", "up", "lo_days", "rec", "ytd", "ratio", "exp10", "exp50",
                         "buy10", "sell10", "tgt_gap", "rating", "buzz_today", "buzz_spike", "buzz30"))
        name = f'<span class="zombie">{r["name"]}</span>' if r["zombie"] else r["name"]
        rec = "-" if r["rec"] is None else (f'<span class="rev">{r["rec"]:.0f}%</span>' if r["rec"] > REVIVAL_TH else f'{r["rec"]:.0f}%')
        rating = "-" if r["rating"] is None else f'{r["rating"]:.2f}<span class="dim"> n={r["rating_n"] or "?"}</span>'
        buzz_t = "-" if r["buzz_today"] is None else f'{r["buzz_today"]:,}{"<span class=dim>*</span>" if r["buzz_partial"] else ""}'
        buzz30 = "-" if r["buzz30"] is None else f'{r["buzz30"]:,}'
        spike = "-" if r["buzz_spike"] is None else (f'<span class="buzz">{r["buzz_spike"]:.1f}倍</span>' if r["buzz_spike"] >= 2 else f'{r["buzz_spike"]:.1f}倍')
        out.append(
            f'<tr {data}><td>{r["code"]}</td><td>{name}</td><td class="cat">{r["cat"]} {catname[r["cat"]]}</td>'
            f'<td>{r["last"]:,.0f}</td><td>{_pct(r["d1"], 2)}</td>'
            f'<td>{_pct(r["dd"], 0)}<span class="dim"> {r["hi_date"][5:]}</span></td><td>{r["hi_days"]}</td>'
            f'<td>{_pct(r["up"], 0)}<span class="dim"> {r["lo_date"][5:]}</span></td><td>{r["lo_days"]}</td><td>{rec}</td><td>{_pct(r["ytd"], 0)}</td>'
            f'<td>{_x(r["ratio"], 1, "")}{"<span class=dim title=売り残僅少>※</span>" if r.get("thin") else ""}</td><td>{_x(r["exp10"])}</td><td>{_x(r["exp50"])}</td><td>{_pct(r["buy10"], 0)}</td><td>{_pct(r["sell10"], 0)}</td>'
            f'<td>{_pct(r["tgt_gap"], 0)}</td><td>{rating}</td><td>{buzz_t}</td><td>{spike}</td><td>{buzz30}</td></tr>')
    return "\n".join(out)


def generate_html(d):
    s = d["summary"]
    by = {r["code"]: r for r in d["rows"]}
    deep = by.get(s["deepest"]) or {}
    up = by.get(s["top_up"][0]) if s["top_up"] else {}
    dn = by.get(s["top_dn"][0]) if s["top_dn"] else {}
    rev = by.get(s["revival"][0]) if s["revival"] else {}
    bz = by.get(s["buzz_top"][0]) if s["buzz_top"] else None
    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(), updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), nav=nav_html(),
        n=s["n"], n_zombie=s["n_zombie"], n_bottom=s["n_bottom"], asof=s["asof"], margin_date=s["margin_date"] or "-",
        zombie_dd=abs(ZOMBIE_DD), revival_th=REVIVAL_TH, nk_hi=s["nk_hi"], nk_hi_date=s["nk_hi_date"], nk_dd=s["nk_dd"],
        nk_d1=s["nk_d1"], nk_last=s["nk_last"], nk_cls="pos" if s["nk_d1"] > 0 else "neg",
        deepest_name=deep.get("name", "-"), deepest_dd=deep.get("dd", 0), deepest_days=deep.get("hi_days", 0),
        up_name=up.get("name", "-"), up_d1=up.get("d1", 0), dn_name=dn.get("name", "-"), dn_d1=dn.get("d1", 0),
        rev_name=rev.get("name", "-"), rev_rec=rev.get("rec") or 0,
        buzz_name=bz["name"] if bz else "-", buzz_spike=f'{bz["buzz_spike"]:.1f}倍・{bz["buzz_today"]:,}件' if bz else "-",
        filter_buttons="\n".join(f'    <button data-cat="{k}">{k} {v}</button>' for k, v in CATEGORIES),
        rows=rows_html(d["rows"]), cats_json=json.dumps(dict(CATEGORIES), ensure_ascii=False))
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")


def save_history(d):
    hist = {}
    if os.path.exists(HISTORY_JSON):
        try:
            hist = json.load(open(HISTORY_JSON, encoding="utf-8"))
        except Exception:
            hist = {}
    keys = ("last", "d1", "dd", "hi_days", "rec", "ratio", "exp10", "exp50", "buy10", "sell10", "tgt_gap", "buzz_today", "buzz_spike")
    hist[d["summary"]["asof"]] = {r["code"]: {k: r.get(k) for k in keys} for r in d["rows"]}
    json.dump(hist, open(HISTORY_JSON, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"履歴保存: {len(hist)}日分")


def push_to_github():
    try:
        from git_lock_helper import wait_for_git_lock
        wait_for_git_lock(SCRIPT_DIR)
    except Exception:
        pass
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML, "semi_screen.py", "semi_post.py", "semi_history.json"], check=False)
    result = subprocess.run(["git", "-C", SCRIPT_DIR, "commit", "-m", "update semi report " + today], capture_output=True)
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
            log("  Done: https://ichikon77.github.io/minervini/semi.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


def main():
    log("半導体ゾンビの洞窟 開始")
    codes = list(UNIVERSE)
    prices, nk = fetch_prices(codes)
    log(f"  株価取得 {len(prices)}/{len(codes)}銘柄（{prices[codes[0]].index[-1].date() if codes[0] in prices else '?'} まで）")
    if len(prices) < len(codes) * 0.8 or len(nk) < 100:
        log("エラー: 株価が十分に取れませんでした → 更新しません")
        sys.exit(1)
    try:
        mh = json.load(open(MARGIN_HISTORY, encoding="utf-8"))
        margins = {c: margin_stats(mh, c) for c in codes}
        log(f"  需給: JPX {max(mh['weeks'])} 分まで")
    except Exception as e:
        log(f"  需給データ読めず: {e}")
        margins = {}
    ratings = load_ratings(codes)
    log(f"  アナリスト目標 {len(ratings)}銘柄（weekly {sum(1 for v in ratings.values() if v['src'] == 'weekly')}・yf {sum(1 for v in ratings.values() if v['src'] == 'yf')}）")
    buzz = {} if "--test" in sys.argv else fetch_buzz(codes)
    log(f"  話題度 {len(buzz)}銘柄")
    d = build(prices, nk, margins, ratings, buzz)
    s = d["summary"]
    by = {r["code"]: r for r in d["rows"]}
    def nd(codes):
        return "、".join(f"{by[c]['name']} {by[c]['d1']:+.1f}%" for c in codes)
    log(f"  ゾンビ {s['n_zombie']}/{s['n']}体  最深部 {by[s['deepest']]['name']} {by[s['deepest']]['dd']:+.0f}%  浮いた {nd(s['top_up'][:2])}  沈んだ {nd(s['top_dn'][:3])}")
    if s["buzz_top"]:
        log("  話題度: " + "、".join(f"{by[c]['name']} {by[c]['buzz_spike']}倍({by[c]['buzz_today']}件)" for c in s["buzz_top"]))
    json.dump(d, open(POST_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    generate_html(d)
    if "--test" not in sys.argv:
        save_history(d)
    if "--nopush" in sys.argv or "--test" in sys.argv:
        log("push スキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
