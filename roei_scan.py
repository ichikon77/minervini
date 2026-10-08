# -*- coding: utf-8 -*-
"""
情報漏洩銘柄検証: 新規事案の自動検出（roei_screen.py から毎回呼ばれる）

  1. Googleニュース検索RSSで直近2日の「不正アクセス／情報漏えい／個人情報流出」見出しを集める
  2. 見出しに上場企業名（JPX一覧 data_j.xlsx の銘柄名を短縮したもの）か、
     roei_incidents.json の "_aliases"（佐川急便→9143 のような子会社・サービス名の対応表）が含まれるものを拾う
  3. 既存事案（同じコードが14日以内にある）を除き、roei_candidates.json に候補として保存
  4. 同じ日に 2つ以上の媒体が報じている候補だけ roei_incidents.json に自動追加（"auto": true、時刻は ?）
     → 表・カード・投稿では「自動検出」と明示。見出しだけの情報なので note に要確認と書く。
       誤検出なら JSON からその行を消し、必要なら "_ignore" に見出し語を足す。

精度より安全側に寄せている（単独報道・短い社名は候補に留めて追加しない）。
"""

import os
import re
import io
import json
import html
import datetime
import unicodedata
import urllib.parse
import urllib.request
import email.utils

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INCIDENTS_JSON = os.path.join(SCRIPT_DIR, "roei_incidents.json")
CANDIDATES_JSON = os.path.join(SCRIPT_DIR, "roei_candidates.json")
JPX_CACHE = os.path.join(SCRIPT_DIR, "roei_jpx_names.json")
JPX_URLS = [
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx",
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls",
]
GNEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"
QUERIES = [
    "不正アクセス 個人情報",
    "情報漏えい 発表",
    "個人情報 流出 可能性",
    "サイバー攻撃 個人情報 漏えい",
    "ランサムウェア 個人情報",
    # 2026-10-08追加: IDCフロンティア(ソフトバンク子会社)のクラウド攻撃は見出しに「個人情報/漏えい」が無く取りこぼした
    "不正アクセス 障害",
    "ランサムウェア攻撃",
]
INCIDENT_WORDS = ("不正アクセス", "漏えい", "漏洩", "流出", "ランサム", "サイバー攻撃", "紛失")
# 社名の短縮で削る語
STRIP_WORDS = ("ホールディングス", "ホールディング", "グループ", "株式会社", "ＨＤ", "HD", "・", "（株）", "(株)")
# 短縮後にこれだけになる社名は一般語なので使わない
GENERIC = {"日本", "東京", "大阪", "東日本", "西日本", "中部", "九州", "北海道", "関西", "電力", "銀行", "証券", "製薬", "工業", "商事", "物産", "不動産", "建設", "鉄道", "電鉄", "化学", "食品", "製作所", "情報", "システム", "ネット", "サービス", "三菱", "三井", "住友", "日立", "ソニー", "トヨタ",
           # 事故語・一般語の一部になる社名（「アクセス」は不正アクセスに必ず含まれる）
           "アクセス", "セキュリティ", "ネットワーク", "データ", "ニュース", "ソフトウェア", "コミュニケーションズ", "ジャパン", "インターネット", "メディア", "テクノロジー", "テクノロジーズ", "ソリューション", "ソリューションズ", "システムズ", "デジタル", "クラウド", "モバイル", "プラス", "ワン", "ライフ", "ケア", "パートナーズ", "キャピタル", "リート", "投資法人", "ファンド", "総研", "リサーチ", "マーケティング", "コンサルティング", "エージェント", "ポイント", "カード", "ペイ", "アプリ", "ゲームズ", "スタジオ", "ラボ", "ファクトリー", "ワークス", "ギフト", "ポート", "サポート", "オンライン", "ストア", "モール", "マート", "ホテル", "ドライブ", "メール", "ID", "アイ", "エス", "ケー",
           # 報道機関名（見出しの出典表記に出るため）
           "テレビ朝日", "日本テレビ", "TBS", "フジテレビ", "テレビ東京", "日経", "日本経済新聞", "朝日新聞", "読売新聞", "毎日新聞", "産経新聞", "中日新聞", "時事通信", "共同通信", "NHK", "ヤフー", "LINEヤフー", "スマートニュース", "朝日放送", "テレビ大阪", "RKB", "FBS", "KBC", "TVQ", "ABC", "ANN", "JNN", "FNN", "NNN"}
# 転載サイト（別媒体として数えない）
AGGREGATORS = ("Yahoo!ニュース", "Infoseek", "ライブドアニュース", "dメニューニュース", "ｄメニューニュース", "smartnews", "excite", "エキサイト", "goo", "ニコニコニュース", "msn", "au Web", "BIGLOBE", "mixi", "UQライフ", "Rakuten Infoseek")

_log = print


def set_logger(fn):
    global _log
    _log = fn


def norm(s):
    return unicodedata.normalize("NFKC", s or "").replace("　", " ").strip()


def short_name(name):
    n = norm(name)
    for w in STRIP_WORDS:
        n = n.replace(w, "")
    n = n.strip(" -")
    return n


def load_jpx_names():
    """{code: 銘柄名}（株式のみ）。週1回更新、取れなければキャッシュ"""
    today = datetime.date.today().isoformat()
    cache = None
    if os.path.exists(JPX_CACHE):
        try:
            cache = json.load(open(JPX_CACHE, encoding="utf-8"))
            if (datetime.date.today() - datetime.date.fromisoformat(cache.get("_updated", "2000-01-01"))).days < 7:
                return cache["names"]
        except Exception:
            cache = None
    try:
        import pandas as pd
        content = None
        for u in JPX_URLS:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                b = urllib.request.urlopen(req, timeout=30).read()
                if len(b) > 10000:
                    content = b
                    break
            except Exception:
                continue
        if content is None:
            raise RuntimeError("JPX一覧を取得できません")
        try:
            df = pd.read_excel(io.BytesIO(content), engine="openpyxl")
        except Exception:
            df = pd.read_excel(io.BytesIO(content), engine="xlrd")
        df.columns = [str(c).strip() for c in df.columns]
        mcol = next(c for c in df.columns if "市場" in c)
        ccol = next(c for c in df.columns if "コード" in c)
        ncol = next(c for c in df.columns if "銘柄名" in c)
        df = df[df[mcol].astype(str).str.contains("プライム|スタンダード|グロース", na=False, regex=True)]
        names = {}
        for code, name in zip(df[ccol].astype(str), df[ncol].astype(str)):
            code = code.strip()
            if re.fullmatch(r"\d{4}|\d{3}[A-Z]|\d{2}[A-Z]\d|\d[A-Z]\d{2}", code):
                names[code] = norm(name)
        json.dump({"_updated": today, "names": names}, open(JPX_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        _log(f"  JPX上場銘柄名 {len(names)} 件を更新")
        return names
    except Exception as e:
        _log(f"  JPX一覧の更新失敗（キャッシュ使用）: {e}")
        return cache["names"] if cache else {}


def _fetch_text_deadline(url, deadline_sec):
    """urlopen をデーモンスレッドで実行し、deadline_sec 秒で見切る（roei_screen.py と同じ。2026-10-08 のハング対策）"""
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


def fetch_news(days=2):
    """直近days日の見出し一覧 [{title, src, dt(JST), link}]（重複見出しは除く）"""
    since = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))) - datetime.timedelta(days=days)
    seen = set()
    out = []
    for q in QUERIES:
        url = GNEWS_RSS.format(q=urllib.parse.quote(f"{q} when:{days}d"))
        try:
            xml = _fetch_text_deadline(url, 25)
        except Exception as e:
            _log(f"  ニュース取得失敗 [{q}]: {e}")
            continue
        for m in re.finditer(r"<item><title>(.*?)</title><link>(.*?)</link>.*?<pubDate>([^<]+)</pubDate>.*?<source url=\"[^\"]*\">(.*?)</source>", xml, re.S):
            title = html.unescape(m.group(1))
            src = html.unescape(m.group(4))
            # 見出し末尾の「 - 媒体名」を落とす
            title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
            try:
                dt = email.utils.parsedate_to_datetime(m.group(3)).astimezone(datetime.timezone(datetime.timedelta(hours=9)))
            except Exception:
                continue
            if dt < since:
                continue
            key = norm(title)
            if key in seen:
                continue
            seen.add(key)
            out.append({"title": title, "src": src, "dt": dt, "link": m.group(2)})
    return out


def build_matchers(jpx_names, aliases):
    """[(検索語, code, 由来)] 長い語から順に"""
    ms = []
    for alias, code in (aliases or {}).items():
        ms.append((norm(alias), str(code), "alias"))
    for code, name in jpx_names.items():
        sn = short_name(name)
        if len(sn) >= 3 and sn not in GENERIC:
            ms.append((sn, code, "name"))
    ms.sort(key=lambda x: -len(x[0]))
    return ms


def guess_type(title):
    if "ランサム" in title:
        return "ランサム"
    if "委託先" in title or "委託" in title:
        return "委託先"
    if "紛失" in title or "誤送信" in title or "誤って" in title:
        return "紛失"
    if "設定不備" in title or "設定ミス" in title or "閲覧可能" in title:
        return "設定不備"
    if "元社員" in title or "従業員が" in title or "持ち出し" in title:
        return "内部不正"
    return "不正アクセス"


def guess_scale(title):
    m = re.search(r"(最大)?約?([\d,.]+(?:万|億)?[\d,.]*)\s*(件|人分|人|アカウント|名分|名)", norm(title))
    return (m.group(0) if m else "").strip()


def scan(inc, aliases, ignore_words, today=None):
    """候補の検出と自動追加。戻り値: (追加した事案のリスト, 候補リスト)"""
    today = today or datetime.date.today()
    jpx = load_jpx_names()
    matchers = build_matchers(jpx, aliases)
    news = fetch_news(days=2)
    _log(f"  ニュース見出し {len(news)} 件を走査（上場社名 {len(jpx)}・別名 {len(aliases or {})}）")
    recent = {}   # code -> 最新の事案日
    for r in inc:
        recent[r["code"]] = max(recent.get(r["code"], "0000"), r["date"])
    cands = {}    # (code, date) -> {...}
    for n in news:
        t = norm(n["title"])
        if not any(w in t for w in INCIDENT_WORDS):
            continue
        if any(w and w in t for w in (ignore_words or [])):
            continue
        hit = next(((kw, code, how) for kw, code, how in matchers if kw in t), None)
        if not hit:
            continue
        kw, code, how = hit
        d = n["dt"].date().isoformat()
        last = recent.get(code)
        if last and (datetime.date.fromisoformat(d) - datetime.date.fromisoformat(last)).days <= 14:
            continue   # 既に登録済み事案の続報
        c = cands.setdefault((code, d), {"code": code, "date": d, "kw": kw, "how": how, "name": jpx.get(code, kw),
                                          "titles": [], "srcs": set(), "first": n["dt"], "link": n["link"]})
        c["titles"].append(f"{n['dt']:%H:%M} {n['src']}: {n['title']}")
        c["srcs"].add("(転載)" if any(a.lower() in n["src"].lower() for a in AGGREGATORS) else n["src"])
        if n["dt"] < c["first"]:
            c["first"] = n["dt"]
            c["link"] = n["link"]
    added = []
    cand_out = []
    for (code, d), c in sorted(cands.items()):
        rec = {"code": code, "date": d, "name": c["name"], "kw": c["kw"], "matched_by": c["how"],
               "sources": len(c["srcs"]), "first_report": c["first"].strftime("%Y-%m-%d %H:%M"), "titles": c["titles"][:8]}
        n_src = len([x for x in c["srcs"] if x != "(転載)"]) or 1
        rec["sources"] = n_src
        strong = n_src >= 2 and (c["how"] == "alias" or len(c["kw"]) >= 4)
        rec["auto_added"] = bool(strong)
        cand_out.append(rec)
        if strong:
            t0 = sorted(c["titles"])[0].split(": ", 1)[-1]   # 時刻順で最も早い見出し
            added.append({
                "date": d, "time": "?", "kw": c["kw"], "code": code, "name": short_name(c["name"]) or c["name"],
                "service": t0[:60], "type": guess_type(" ".join(c["titles"])), "scale": guess_scale(" ".join(c["titles"])) or "（見出しに件数なし）",
                "note": f"自動検出（報道{n_src}媒体・初出{c['first']:%m/%d %H:%M}）・要確認。誤検出なら行を削除", "url": c["link"], "auto": True,
            })
            _log(f"  ★ 自動追加: {d} {c['name']}({code}) {n_src}媒体 初出{c['first']:%H:%M}  {t0[:50]}")
        else:
            _log(f"  候補（追加せず）: {d} {c['name']}({code}) {n_src}媒体 [{c['how']}:{c['kw']}]  {c['titles'][0][:70]}")
    try:
        json.dump({"_updated": datetime.datetime.now().isoformat(timespec="seconds"),
                   "_説明": "roei_scan.py が見つけた新規事案の候補。auto_added=true は roei_incidents.json に自動追加済み。false は媒体数不足などで保留（手で追加するなら roei_incidents.json に1行書く）",
                   "candidates": cand_out}, open(CANDIDATES_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        _log(f"  候補JSONの保存失敗: {e}")
    return added, cand_out


if __name__ == "__main__":
    d = json.load(open(INCIDENTS_JSON, encoding="utf-8"))
    added, cands = scan(d["incidents"], d.get("_aliases", {}), d.get("_ignore", []))
    print(json.dumps(cands, ensure_ascii=False, indent=1))
    print("追加対象:", [(a["date"], a["name"], a["code"]) for a in added])
