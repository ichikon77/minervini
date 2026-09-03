# -*- coding: utf-8 -*-
"""
還流ウォッチ（構造的な円キャリー解消の監視） → kanryu.html → GitHub Pages公開

「本邦の生保・年金・銀行が海外資産を国内に還流させる動き」はCFTCのCOT（投機筋）には出ない。
その代わりに以下の公式統計・市場データで監視する:
  ① 財務省「対外及び対内証券売買契約等の状況」週次CSV
       居住者による対外証券投資（中長期債）のネット + 4週合計 + 13週合計（+ 対内: 海外勢のJGB/日本株）
  ② 同 月次「投資家部門別対外証券投資」CSV
       中長期債を 銀行(銀行勘定)/信託勘定(年金等)/生保/損保/投信/金商 に分解。誰が売っているかを見る
  ③ ヘッジ後利回り差
       US10Y −（US3M − JP1Y）≒ ヘッジ付き米国債の円ベース利回り。これが JP10Y を下回るほど
       「外債を売って円債に乗り換える」経済合理性が強い（30年も同様）
  ④ 超長期国債（20/30/40年）入札の強弱
       応募倍率とテール（最高利回り−平均利回り）。生保が超長期を買っているか＝還流の受け皿が動いているか
  ⑤ 市場の逆行シグナル
       「米10年金利↑ なのに 円高」= 金利差で説明できない円買い（日本勢の米国債売り→円転の疑い）

データ源:
  - 財務省 対外及び対内証券売買契約等の状況 (week.csv / monthb2.csv / monthb3.csv, cp932)
  - 財務省 国債金利情報CSV（JP1Y/10Y/20Y/30Y）
  - 財務省 入札カレンダー（YYMM.htm）→ 入札結果ページ（resulYYYYMMDD.htm）
  - yfinance: ^IRX(米3ヶ月TB) ^TNX(US10Y) ^TYX(US30Y) USDJPY=X
履歴: kanryu_history.json（入札結果のキャッシュ。財務省CSVは全期間入っているので蓄積不要）
"""

import os
import re
import sys
import json
import time
import html as htmlmod
import subprocess
import datetime

import requests
import pandas as pd
import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_HTML = "kanryu.html"
HISTORY_JSON = os.path.join(SCRIPT_DIR, "kanryu_history.json")

MOF_SEC_BASE = "https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/"
MOF_WEEK_CSV = MOF_SEC_BASE + "week.csv"
MOF_MONTH_EQ_CSV = MOF_SEC_BASE + "monthb2.csv"   # 投資家部門別（株式・投資ファンド持分）
MOF_MONTH_LT_CSV = MOF_SEC_BASE + "monthb3.csv"   # 投資家部門別（中長期債）
MOF_JGB_ALL_URL = "https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv"
MOF_JGB_CUR_URL = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
MOF_AUCTION_CAL = "https://www.mof.go.jp/jgbs/auction/calendar/{yy}{mm}.htm"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

WEEK_ROWS_SHOW = 60          # 週次表に出す週数（スクロール枠）
MONTH_ROWS_SHOW = 36         # 月次部門別表に出す月数
PCTL_START = "2014-01-01"    # パーセンタイル計算の起点（2014/1から投資ファンドの計上方法が変わったため）
AUCTION_START = (2024, 1)    # 入札結果を遡る起点（年, 月）
AUCTION_TENORS = ("20年", "30年", "40年")
AUCTION_ROWS_SHOW = 40

# ヘッジ後利回り差の判定閾値（%）: ヘッジ後US − JGB
HEDGE_STRONG = -1.0   # これ以下 = 円債が圧倒的に有利（還流誘因 強）
HEDGE_WEAK = 0.0      # これ以下 = 円債が有利（還流誘因 あり）

# 市場の逆行シグナル閾値
SIG_US10Y_BPS = 5.0   # 米10年が+5bps以上 かつ
SIG_USDJPY_PCT = -0.5  # ドル円が-0.5%以上の円高 → 逆行


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fetch_bytes(url, retries=3):
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=40)
            r.raise_for_status()
            return r.content
        except Exception as e:  # noqa
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"取得失敗: {url} ({last})")


def to_num(x):
    """'1,234 ' / '-4,102' / '-' / '' → float or None"""
    if x is None:
        return None
    s = str(x).replace(",", "").replace('"', "").replace("△", "-").replace("▲", "-").strip()
    if s in ("", "-", "－", "―"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_csv_rows(raw):
    """cp932のCSVバイト列 → 行リスト（csvモジュールでクォート付きカンマ数値を処理）"""
    import csv
    import io
    txt = raw.decode("cp932", errors="replace")
    return list(csv.reader(io.StringIO(txt)))


# -----------------------------------------
# ① 週次: 対外及び対内証券売買契約等の状況
# -----------------------------------------
def fetch_weekly():
    """[{start, end, out_eq, out_lt, out_st, out_total, in_eq, in_lt}] 古い順（単位: 億円）"""
    rows = parse_csv_rows(fetch_bytes(MOF_WEEK_CSV))
    out = []
    pat = re.compile(r"^(\d{4})．\s*(\d{1,2})．\s*(\d{1,2})～\s*(\d{1,2})．\s*(\d{1,2})")
    for r in rows:
        if not r:
            continue
        m = pat.match(r[0].strip())
        if not m or len(r) < 22:
            continue
        y, m1, d1, m2, d2 = map(int, m.groups())
        y2 = y + 1 if m2 < m1 else y
        try:
            start = datetime.date(y, m1, d1)
            end = datetime.date(y2, m2, d2)
        except ValueError:
            continue
        # 列: 1取得株 2処分株 3net株 4取得中長 5処分中長 6net中長 7小計 8取得短 9処分短 10net短 11合計
        #     12取得株(対内) 13処分 14net 15取得中長 16処分 17net 18小計 19取得短 20処分 21net 22合計
        def net(a, b):
            va, vb = to_num(r[a]), to_num(r[b])
            return None if va is None or vb is None else va - vb   # ネットは自前で 取得−処分（+=取得超）
        rec = {
            "start": start, "end": end,
            "out_eq": net(1, 2), "out_lt": net(4, 5), "out_st": net(8, 9),
            "in_eq": net(12, 13), "in_lt": net(15, 16),
        }
        if rec["out_lt"] is None:
            continue
        rec["out_total"] = (rec["out_eq"] or 0) + (rec["out_lt"] or 0) + (rec["out_st"] or 0)
        out.append(rec)
    out.sort(key=lambda x: x["end"])
    if len(out) < 100:
        raise RuntimeError(f"週次CSVの行数が不足（{len(out)}）")
    # 4週・13週合計
    lt = [x["out_lt"] for x in out]
    for i, x in enumerate(out):
        x["lt_4w"] = sum(lt[max(0, i - 3):i + 1]) if i >= 3 else None
        x["lt_13w"] = sum(lt[max(0, i - 12):i + 1]) if i >= 12 else None
    log(f"  週次: {len(out)}週（最新 {out[-1]['start']}～{out[-1]['end']}）")
    return out


def percentile_rank(values, v):
    """v が values の中で下から何%か（0〜100）"""
    vals = [x for x in values if x is not None]
    if not vals or v is None:
        return None
    below = sum(1 for x in vals if x < v)
    return 100.0 * below / len(vals)


# -----------------------------------------
# ② 月次: 投資家部門別対外証券投資
# -----------------------------------------
# monthb*.csv の列（各3列 取得/処分/ネット の先頭index）
SECTOR_COLS = {
    "total": 3,     # 各部門計
    "bank": 18,     # 銀行等（銀行勘定）
    "trust": 30,    # 銀行等及び信託銀行（信託勘定）= 年金等
    "sec": 39,      # 金融商品取引業者
    "life": 42,     # 生命保険会社
    "nonlife": 45,  # 損害保険会社
    "itm": 48,      # 投資信託委託会社等
}
SECTOR_LABEL = {
    "total": "合計", "bank": "銀行(銀行勘定)", "trust": "信託勘定(年金等)",
    "sec": "証券会社", "life": "生保", "nonlife": "損保", "itm": "投信",
}


def parse_monthly_sector(raw):
    """{ 'YYYY-MM': {sector: net(億円)} } を返す"""
    rows = parse_csv_rows(raw)
    res = {}
    year = None
    mpat = re.compile(r"^(\d{1,2})月$")
    for r in rows:
        if len(r) < 52:
            continue
        c0 = r[0].strip()
        if re.match(r"^\d{4}$", c0):
            year = int(c0)
        elif c0 and not re.match(r"^[（(]", c0) and not mpat.match(r[1].strip() if len(r) > 1 else ""):
            # "2023年4月～2024年3月  2023FY" 等の年度行はスキップ
            pass
        m = mpat.match(r[1].strip().translate(str.maketrans("０１２３４５６７８９", "0123456789")))
        if not m or year is None:
            continue
        mon = int(m.group(1))
        rec = {}
        for k, idx in SECTOR_COLS.items():
            a, b = to_num(r[idx]), to_num(r[idx + 1])
            rec[k] = None if a is None or b is None else a - b
        if rec["total"] is None:
            continue
        res[f"{year}-{mon:02d}"] = rec
    return res


def fetch_monthly():
    lt = parse_monthly_sector(fetch_bytes(MOF_MONTH_LT_CSV))
    eq = parse_monthly_sector(fetch_bytes(MOF_MONTH_EQ_CSV))
    if len(lt) < 24:
        raise RuntimeError(f"月次部門別CSVの月数が不足（{len(lt)}）")
    log(f"  月次部門別: 中長期債 {len(lt)}ヶ月（最新 {max(lt)}）/ 株式 {len(eq)}ヶ月")
    return lt, eq


# -----------------------------------------
# ③ ヘッジ後利回り差
# -----------------------------------------
def wareki_to_date(s):
    m = re.match(r"([RHS])(\d+)\.(\d+)\.(\d+)", s.strip())
    if not m:
        return None
    base = {"R": 2018, "H": 1988, "S": 1925}[m.group(1)]
    try:
        return datetime.date(base + int(m.group(2)), int(m.group(3)), int(m.group(4)))
    except ValueError:
        return None


def fetch_jgb():
    """財務省CSV → DataFrame[JP1Y, JP10Y, JP20Y, JP30Y]（2019年〜）
    列: 0基準日 1=1年 2=2年 ... 10=10年 11=15年 12=20年 13=25年 14=30年 15=40年"""
    recs = {}
    for url in [MOF_JGB_ALL_URL, MOF_JGB_CUR_URL]:
        txt = fetch_bytes(url).decode("shift_jis", errors="replace")
        for line in txt.splitlines():
            cols = line.split(",")
            if len(cols) < 15:
                continue
            d = wareki_to_date(cols[0])
            if d is None or d.year < 2019:
                continue
            v = [to_num(cols[i]) for i in (1, 10, 12, 14)]
            if all(x is not None for x in v):
                recs[d] = v
    if not recs:
        raise RuntimeError("財務省CSVから金利データを取得できませんでした")
    idx = sorted(recs)
    df = pd.DataFrame([recs[d] for d in idx], index=pd.to_datetime(idx),
                      columns=["JP1Y", "JP10Y", "JP20Y", "JP30Y"])
    log(f"  JGB金利: {len(df)}日分（最新 {idx[-1]}）")
    return df


def fetch_us():
    tickers = ["^IRX", "^TNX", "^TYX", "USDJPY=X"]
    data = yf.download(tickers, period="5y", auto_adjust=False,
                       progress=False, group_by="ticker", threads=True)
    out = {}
    for t in tickers:
        close = data[t]["Close"].dropna()
        if len(close) < 200:
            raise RuntimeError(f"{t} のデータが不足（{len(close)}本）")
        close.index = close.index.tz_localize(None).normalize()
        out[t] = close
    log(f"  yfinance: {len(tickers)}銘柄（最新 {out['^TNX'].index[-1].date()}）")
    return out


def build_hedge_frame():
    jgb = fetch_jgb()
    us = fetch_us()
    df = pd.DataFrame({
        "US3M": us["^IRX"], "US10Y": us["^TNX"], "US30Y": us["^TYX"], "USDJPY": us["USDJPY=X"],
        "JP1Y": jgb["JP1Y"], "JP10Y": jgb["JP10Y"], "JP20Y": jgb["JP20Y"], "JP30Y": jgb["JP30Y"],
    }).sort_index().ffill().dropna()
    df["HEDGE"] = df["US3M"] - df["JP1Y"]              # ヘッジコスト（近似）
    df["US10Y_H"] = df["US10Y"] - df["HEDGE"]          # ヘッジ後US10Y
    df["US30Y_H"] = df["US30Y"] - df["HEDGE"]
    df["GAP10"] = df["US10Y_H"] - df["JP10Y"]          # マイナス = 円債が有利
    df["GAP30"] = df["US30Y_H"] - df["JP30Y"]
    return df


# -----------------------------------------
# ④ 超長期国債入札
# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        try:
            with open(HISTORY_JSON, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa
            pass
    return {"auctions": {}}


def save_history(h):
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=1)


def _strip_tags(s):
    s = re.sub(r"<script.*?</script>", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "|", s)
    s = htmlmod.unescape(s)
    s = re.sub(r"[\s　]+", " ", s)
    return s


def parse_auction_calendar(html_txt):
    """入札カレンダーHTML → [(日付テキスト, 銘柄名, 結果URL)] 超長期のみ"""
    body = html_txt[html_txt.find("skipmain"):]
    res = []
    for tr in re.findall(r"<tr.*?</tr>", body, flags=re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S)
        if len(cells) < 5:
            continue
        name = re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", cells[1]))).strip()
        if "利付国債" not in name or not any(t in name for t in AUCTION_TENORS):
            continue
        if "流動性供給" in name or "買入消却" in name:
            continue
        links = re.findall(r'href="([^"]+resul\d{8}\.htm)"', cells[4])
        if not links:
            continue
        res.append((name, links[0]))
    return res


def parse_auction_result(html_txt):
    """入札結果ページ → dict（応募額/募入決定額/最低価格/最高利回り/平均価格/平均利回り）"""
    t = _strip_tags(html_txt)

    def grab(label_regex, after_regex):
        m = re.search(label_regex + r"[^0-9０-９]{0,40}?" + after_regex, t)
        return m.group(1) if m else None

    def yen(label):
        v = grab(label, r"([0-9,，]+(?:兆[0-9,，]*)?)\s*億円")
        if v is None:
            return None
        v = v.replace("，", ",")
        if "兆" in v:
            a, b = v.split("兆")
            return to_num(a) * 10000 + (to_num(b) or 0)
        return to_num(v)

    def pct(label):
        v = grab(label, r"（?\s*([0-9.]+)\s*％")
        return to_num(v)

    def price(label):
        m = re.search(label + r"[^0-9]{0,20}?([0-9]+)円([0-9]+)銭", t)
        return None if not m else int(m.group(1)) + int(m.group(2)) / 100

    rec = {
        "bid": yen(r"応募額"),
        "accepted": yen(r"募入決定額"),
        "low_price": price(r"募入最低価格"),
        "high_yield": pct(r"募入最高利回り"),
        "avg_price": price(r"募入平均価格"),
        "avg_yield": pct(r"募入平均利回り"),
    }
    # 40年債はダッチ方式（利回り競争入札・単一利回り）: 「応募者利回り（募入最高利回り）」のみで平均はない
    if rec["avg_yield"] is None:
        v = pct(r"応募者利回り")
        if v is not None:
            rec["avg_yield"] = v
            rec["high_yield"] = v
            rec["dutch"] = True
    m = re.search(r"(\d{2,3})年利付国債（第(\d+)回）", t) or re.search(r"利付国庫債券（(\d{2,3})年）（第(\d+)回）", t)
    rec["tenor"] = (m.group(1) + "年") if m else None
    rec["series"] = int(m.group(2)) if m else None
    return rec


def update_auctions(hist):
    """入札カレンダーを巡回し、未取得の超長期入札結果を hist['auctions'][url] に追加"""
    auctions = hist.setdefault("auctions", {})
    today = datetime.date.today()
    months = []
    y, m = AUCTION_START
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    # 直近2ヶ月は毎回、それ以前は「その月の結果が全て取得済み」なら省略
    fetched_pages = 0
    for (yy, mm) in months:
        recent = (yy, mm) >= ((today.replace(day=1) - datetime.timedelta(days=1)).year,
                              (today.replace(day=1) - datetime.timedelta(days=1)).month)
        key = f"{yy}-{mm:02d}"
        if not recent and hist.get("months_done", {}).get(key):
            continue
        url = MOF_AUCTION_CAL.format(yy=str(yy)[2:], mm=f"{mm:02d}")
        try:
            page = fetch_bytes(url).decode("utf-8", errors="replace")
        except Exception as e:  # noqa
            log(f"  入札カレンダー取得失敗 {key}: {e}")
            continue
        fetched_pages += 1
        items = parse_auction_calendar(page)
        ok = True
        for name, rurl in items:
            if rurl in auctions:
                continue
            try:
                rec = parse_auction_result(fetch_bytes(rurl).decode("utf-8", errors="replace"))
            except Exception as e:  # noqa
                log(f"  入札結果取得失敗 {rurl}: {e}")
                ok = False
                continue
            if rec["avg_yield"] is None or rec["bid"] is None or rec["accepted"] is None:
                log(f"  入札結果パース不可（スキップ）: {rurl}")
                ok = False
                continue
            dm = re.search(r"resul(\d{4})(\d{2})(\d{2})", rurl)
            rec["date"] = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"
            rec["name"] = name
            rec["url"] = rurl
            auctions[rurl] = rec
            time.sleep(0.5)
        if ok and not recent:
            hist.setdefault("months_done", {})[key] = True
        time.sleep(0.3)
    log(f"  入札: カレンダー{fetched_pages}ページ巡回、累計{len(auctions)}件")
    return [auctions[k] for k in sorted(auctions, key=lambda k: auctions[k]["date"])]


# -----------------------------------------
# HTML部品
# -----------------------------------------
def fmt_oku(v, bold=False):
    """億円 → '+1.2兆' / '-8,240億' 表記（色付き）"""
    if v is None:
        return "<td class='muted'>—</td>"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    if abs(v) >= 10000:
        s = f"{v / 10000:+,.2f}兆"
    else:
        s = f"{v:+,.0f}億"
    b = " style='font-weight:700'" if bold else ""
    return f"<td class='{cls}'{b}>{s}</td>"


def pctl_bg(p, invert=False):
    """パーセンタイル → 背景色クラス。低い(処分超が大きい)ほど赤、高いほど緑"""
    if p is None:
        return ""
    if p <= 10:
        return " bg-red2"
    if p <= 25:
        return " bg-red1"
    if p >= 90:
        return " bg-green1"
    return ""


def build_weekly_html(weeks):
    base = [w for w in weeks if w["end"] >= datetime.date.fromisoformat(PCTL_START)]
    hist_lt = [w["out_lt"] for w in base]
    hist_4w = [w["lt_4w"] for w in base]
    hist_13w = [w["lt_13w"] for w in base]
    latest = weeks[-1]
    p_lt = percentile_rank(hist_lt, latest["out_lt"])
    p_4w = percentile_rank(hist_4w, latest["lt_4w"])
    p_13w = percentile_rank(hist_13w, latest["lt_13w"])

    # 連続処分超の週数
    streak = 0
    for w in reversed(weeks):
        if w["out_lt"] is not None and w["out_lt"] < 0:
            streak += 1
        else:
            break

    if p_4w is not None and p_4w <= 10:
        badge = "<span class='badge b-red'>還流シグナル 強（4週合計が2014年以降の下位10%）</span>"
    elif p_4w is not None and p_4w <= 25:
        badge = "<span class='badge b-org'>処分超 多め（4週合計が下位25%）</span>"
    elif p_4w is not None and p_4w >= 90:
        badge = "<span class='badge b-green'>対外債券 買い越し 強（上位10%）</span>"
    else:
        badge = "<span class='badge b-gray'>平常圏</span>"

    def pt(p):
        return "—" if p is None else f"{p:.0f}%"

    summary = (
        f"<div class='summary'>最新週 {latest['start']:%m/%d}～{latest['end']:%m/%d}: "
        f"対外中長期債ネット <b>{latest['out_lt'] / 10000:+.2f}兆円</b>（{pt(p_lt)}タイル） / "
        f"4週合計 <b>{(latest['lt_4w'] or 0) / 10000:+.2f}兆円</b>（{pt(p_4w)}タイル） / "
        f"13週合計 <b>{(latest['lt_13w'] or 0) / 10000:+.2f}兆円</b>（{pt(p_13w)}タイル） / "
        f"連続処分超 <b>{streak}週</b> {badge}</div>"
    )

    rows = []
    for w in reversed(weeks[-WEEK_ROWS_SHOW:]):
        p4 = percentile_rank(hist_4w, w["lt_4w"])
        p13 = percentile_rank(hist_13w, w["lt_13w"])
        rows.append(
            "<tr>"
            f"<td class='dt'>{w['start']:%Y/%m/%d}～{w['end']:%m/%d}</td>"
            + fmt_oku(w["out_lt"], bold=True)
            + fmt_oku(w["lt_4w"]).replace("<td class='", f"<td class='{pctl_bg(p4).strip()} ", 1)
            + f"<td class='muted'>{pt(p4)}</td>"
            + fmt_oku(w["lt_13w"]).replace("<td class='", f"<td class='{pctl_bg(p13).strip()} ", 1)
            + fmt_oku(w["out_eq"]) + fmt_oku(w["out_st"]) + fmt_oku(w["out_total"])
            + fmt_oku(w["in_lt"]) + fmt_oku(w["in_eq"])
            + "</tr>"
        )
    table = (
        "<div class='scroll'><table>"
        "<thead><tr><th>週</th><th>対外 中長期債</th><th>4週合計</th><th>%tile</th><th>13週合計</th>"
        "<th>対外 株式</th><th>対外 短期債</th><th>対外 合計</th>"
        "<th>対内 中長期債<br><span class='sub'>海外勢のJGB</span></th>"
        "<th>対内 株式<br><span class='sub'>海外勢の日本株</span></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
    return summary + table


def build_monthly_html(lt, eq):
    keys = sorted(lt)
    base_keys = [k for k in keys if k >= PCTL_START[:7]]
    sectors = ["total", "bank", "trust", "life", "nonlife", "itm", "sec"]
    hist = {s: [lt[k][s] for k in base_keys] for s in sectors}

    # 12ヶ月累計（合計・生保・信託）
    def roll12(sector, k):
        i = keys.index(k)
        if i < 11:
            return None
        vals = [lt[kk][sector] for kk in keys[i - 11:i + 1]]
        return None if any(v is None for v in vals) else sum(vals)

    latest = keys[-1]
    lrec = lt[latest]
    parts = []
    for s in ("life", "trust", "bank"):
        v = lrec.get(s)
        p = percentile_rank(hist[s], v)
        parts.append(f"{SECTOR_LABEL[s]} <b>{(v or 0) / 10000:+.2f}兆</b>（{'—' if p is None else f'{p:.0f}%'}タイル）")
    summary = (f"<div class='summary'>最新月 {latest.replace('-', '/')} の対外中長期債ネット: 合計 "
               f"<b>{lrec['total'] / 10000:+.2f}兆円</b> / " + " / ".join(parts)
               + f" / 生保12ヶ月累計 <b>{(roll12('life', latest) or 0) / 10000:+.2f}兆円</b></div>")

    rows = []
    for k in reversed(keys[-MONTH_ROWS_SHOW:]):
        r = lt[k]
        e = eq.get(k, {})
        cells = [f"<td class='dt'>{k.replace('-', '/')}</td>"]
        for s in sectors:
            p = percentile_rank(hist[s], r.get(s))
            cell = fmt_oku(r.get(s), bold=(s == "total"))
            cells.append(cell.replace("<td class='", f"<td class='{pctl_bg(p).strip()} ", 1))
        cells.append(fmt_oku(roll12("life", k)))
        cells.append(fmt_oku(roll12("total", k)))
        cells.append(fmt_oku(e.get("total")))
        cells.append(fmt_oku(e.get("itm")))
        rows.append("<tr>" + "".join(cells) + "</tr>")
    table = (
        "<div class='scroll'><table>"
        "<thead><tr><th>月</th>"
        + "".join(f"<th>{SECTOR_LABEL[s]}</th>" for s in sectors)
        + "<th>生保 12ヶ月累計</th><th>合計 12ヶ月累計</th>"
        "<th>株式 合計<br><span class='sub'>対外株式・投信</span></th><th>株式 投信<br><span class='sub'>個人のオルカン等</span></th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
    return summary + table


def build_hedge_html(df):
    cur = df.iloc[-1]
    offsets = [("今日", 0), ("1ヶ月前", 21), ("3ヶ月前", 63), ("6ヶ月前", 126), ("1年前", 252), ("2年前", 504)]

    def val(col, n):
        if len(df) <= n:
            return None
        return float(df[col].iloc[-1 - n])

    def cell(v, kind="pct", cls=""):
        if v is None:
            return "<td class='muted'>—</td>"
        return f"<td class='{cls}'>{v:+.2f}%</td>" if kind == "gap" else f"<td class='{cls}'>{v:.2f}%</td>"

    def gap_cls(v):
        if v is None:
            return ""
        if v <= HEDGE_STRONG:
            return "bg-red2"
        if v <= HEDGE_WEAK:
            return "bg-red1"
        return "bg-blue1"

    lines = [
        ("US10Y", "米10年金利（^TNX）", "US10Y", "pct"),
        ("US3M", "米3ヶ月TB（^IRX）", "US3M", "pct"),
        ("JP1Y", "日本1年国債（財務省）", "JP1Y", "pct"),
        ("ヘッジコスト", "US3M − JP1Y（3ヶ月ロールの近似）", "HEDGE", "pct"),
        ("ヘッジ後 US10Y", "US10Y − ヘッジコスト ＝ 円ベースで見た米10年債", "US10Y_H", "pct"),
        ("JP10Y", "日本10年国債", "JP10Y", "pct"),
        ("差（10年）", "ヘッジ後US10Y − JP10Y　マイナス＝円債が有利＝還流誘因", "GAP10", "gap"),
        ("US30Y", "米30年金利（^TYX）", "US30Y", "pct"),
        ("ヘッジ後 US30Y", "US30Y − ヘッジコスト", "US30Y_H", "pct"),
        ("JP30Y", "日本30年国債（生保の主戦場）", "JP30Y", "pct"),
        ("差（30年）", "ヘッジ後US30Y − JP30Y　マイナス＝円債が有利", "GAP30", "gap"),
    ]
    rows = []
    for name, desc, col, kind in lines:
        tds = []
        for _, n in offsets:
            v = val(col, n)
            tds.append(cell(v, kind, gap_cls(v) if kind == "gap" else ("now" if n == 0 else "")))
        rows.append(f"<tr class='{'gaprow' if kind == 'gap' else ''}'><td class='dt'>{name}</td>"
                    f"<td class='desc'>{desc}</td>{''.join(tds)}</tr>")

    g10, g30 = float(cur["GAP10"]), float(cur["GAP30"])
    if min(g10, g30) <= HEDGE_STRONG:
        badge = "<span class='badge b-red'>還流誘因 強</span>"
        msg = "ヘッジ付き米国債は円債に完敗。外債を売って円債へ乗り換える経済合理性が強い状態"
    elif min(g10, g30) <= HEDGE_WEAK:
        badge = "<span class='badge b-org'>還流誘因 あり</span>"
        msg = "ヘッジ付き米国債より円債の方が高利回り。乗り換えの動機はある"
    else:
        badge = "<span class='badge b-blue'>還流誘因 なし</span>"
        msg = "ヘッジしても米国債の方がまだ高利回り。金利面では海外に留まる方が合理的"
    summary = (f"<div class='summary'>ヘッジ後利回り差: 10年 <b>{g10:+.2f}%</b> / 30年 <b>{g30:+.2f}%</b>"
               f"（ヘッジコスト {float(cur['HEDGE']):.2f}%） {badge}　{msg}</div>")
    table = ("<div class='scroll'><table><thead><tr><th>系列</th><th>説明</th>"
             + "".join(f"<th>{l}</th>" for l, _ in offsets)
             + f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>")
    return summary + table


def build_signal_html(df):
    """米10年↑ & 円高 の逆行チェック（日次データから）"""
    periods = [("5営業日", 5), ("15営業日", 15), ("1ヶ月", 21), ("3ヶ月", 63)]
    rows = []
    flags = 0
    for label, n in periods:
        if len(df) <= n:
            continue
        d_us = (float(df["US10Y"].iloc[-1]) - float(df["US10Y"].iloc[-1 - n])) * 100
        d_fx = (float(df["USDJPY"].iloc[-1]) / float(df["USDJPY"].iloc[-1 - n]) - 1) * 100
        d_jp = (float(df["JP10Y"].iloc[-1]) - float(df["JP10Y"].iloc[-1 - n])) * 100
        d_diff = d_us - d_jp   # 日米10Y差の変化（マイナス=縮小）
        if d_us >= SIG_US10Y_BPS and d_fx <= SIG_USDJPY_PCT:
            if d_diff <= -SIG_US10Y_BPS:
                j, cls = (f"円高だが日米10Y差は縮小（{d_diff:+.1f}bps・日銀側主導）→ 金利差で説明可能", "j-mid")
            else:
                w = "拡大" if d_diff >= 0 else "ほぼ横ばい"
                j, cls = (f"逆行: 米金利↑・日米差は{w}（{d_diff:+.1f}bps）なのに円高 → 金利差で説明できない円買い＝還流疑い", "j-ng")
                flags += 1
        elif d_us <= -SIG_US10Y_BPS and d_fx >= -SIG_USDJPY_PCT:
            j, cls = "逆行: 米金利↓なのに円安", "j-mid"
        elif abs(d_us) < SIG_US10Y_BPS or abs(d_fx) < abs(SIG_USDJPY_PCT):
            j, cls = "微動のため判定なし", "j-none"
        else:
            j, cls = "理論通り（米金利と円は同方向）", "j-ok"
        rows.append(f"<tr><td class='dt'>{label}</td>"
                    f"<td class='{'pos' if d_us > 0 else 'neg'}'>{d_us:+.1f}bps</td>"
                    f"<td class='{'pos' if d_jp > 0 else 'neg'}'>{d_jp:+.1f}bps</td>"
                    f"<td class='{'pos' if d_fx > 0 else 'neg'}'>{d_fx:+.2f}%</td>"
                    f"<td class='{cls}' style='text-align:left'>{j}</td></tr>")
    badge = ("<span class='badge b-red'>逆行シグナル 点灯</span>" if flags
             else "<span class='badge b-gray'>逆行なし</span>")
    summary = f"<div class='summary'>米10年金利とドル円の整合性チェック {badge}</div>"
    table = ("<div class='scroll'><table><thead><tr><th>期間</th><th>US10Y 変化</th><th>JP10Y 変化</th>"
             "<th>ドル円 変化</th><th>判定</th></tr></thead>"
             f"<tbody>{''.join(rows)}</tbody></table></div>")
    return summary + table


def build_auction_html(aucs):
    if not aucs:
        return "<div class='summary'>入札データなし</div>"
    # テノール別の直近12回平均（倍率・テール）
    by_tenor = {}
    for a in aucs:
        a["ratio"] = a["bid"] / a["accepted"] if a["accepted"] else None
        if a.get("dutch") or a["high_yield"] is None or a["avg_yield"] is None:
            a["tail_bps"] = None   # ダッチ方式（40年）はテールの概念なし → 倍率のみで判定
        else:
            a["tail_bps"] = (a["high_yield"] - a["avg_yield"]) * 100
        by_tenor.setdefault(a["tenor"], []).append(a)

    def trailing_avg(lst, i, key, n=12):
        prev = [x[key] for x in lst[max(0, i - n):i] if x.get(key) is not None]
        return sum(prev) / len(prev) if prev else None

    for t, lst in by_tenor.items():
        for i, a in enumerate(lst):
            a["ratio_avg"] = trailing_avg(lst, i, "ratio")
            a["tail_avg"] = trailing_avg(lst, i, "tail_bps")
            ra, ta = a["ratio_avg"], a["tail_avg"]
            if a["ratio"] is None or ra is None:
                a["judge"], a["jcls"] = "—", ""
            elif a["ratio"] <= ra - 0.4 or (ta is not None and a["tail_bps"] is not None and a["tail_bps"] >= max(ta * 2, ta + 2)):
                a["judge"], a["jcls"] = "弱い（需要細る）", "j-ng"
            elif a["ratio"] >= ra + 0.3 and (a["tail_bps"] is None or ta is None or a["tail_bps"] <= ta):
                a["judge"], a["jcls"] = "強い（超長期に買い）", "j-ok"
            else:
                a["judge"], a["jcls"] = "平常", "j-none"

    latest3 = sorted(aucs, key=lambda a: a["date"], reverse=True)[:3]
    parts = []
    for a in latest3:
        parts.append(f"{a['date'][5:].replace('-', '/')} {a['tenor']} 倍率{a['ratio']:.2f}"
                     f"（12回平均{a['ratio_avg']:.2f}）" if a['ratio_avg'] else
                     f"{a['date'][5:].replace('-', '/')} {a['tenor']} 倍率{a['ratio']:.2f}")
    weak = sum(1 for a in latest3 if a["jcls"] == "j-ng")
    strong = sum(1 for a in latest3 if a["jcls"] == "j-ok")
    if weak >= 2:
        badge = "<span class='badge b-red'>超長期の受け皿 弱い</span>"
    elif strong >= 2:
        badge = "<span class='badge b-blue'>超長期に買い集まる（還流の受け皿が動いている）</span>"
    else:
        badge = "<span class='badge b-gray'>平常</span>"
    summary = f"<div class='summary'>直近の超長期入札: {' / '.join(parts)} {badge}</div>"

    rows = []
    for a in sorted(aucs, key=lambda a: a["date"], reverse=True)[:AUCTION_ROWS_SHOW]:
        def f(v, fmt):
            return "—" if v is None else format(v, fmt)
        rows.append(
            "<tr>"
            f"<td class='dt'><a href='{a['url']}' target='_blank' rel='noopener'>{a['date'].replace('-', '/')}</a></td>"
            f"<td>{a['tenor']}<span class='sub'> 第{a['series']}回</span></td>"
            f"<td>{f(a['avg_yield'], '.3f')}%{'<span class=sub> 単一</span>' if a.get('dutch') else ''}</td>"
            f"<td>{f(a['high_yield'], '.3f')}%</td>"
            f"<td>{'<span class=muted>—</span>' if a.get('dutch') else f(a['tail_bps'], '+.1f')}</td>"
            f"<td class='muted'>{f(a['tail_avg'], '.1f')}</td>"
            f"<td>{f(a['bid'] / 10000 if a['bid'] else None, '.2f')}兆</td>"
            f"<td>{f(a['accepted'] / 10000 if a['accepted'] else None, '.2f')}兆</td>"
            f"<td style='font-weight:700'>{f(a['ratio'], '.2f')}</td>"
            f"<td class='muted'>{f(a['ratio_avg'], '.2f')}</td>"
            f"<td class='{a['jcls']}' style='text-align:left'>{a['judge']}</td>"
            "</tr>"
        )
    table = ("<div class='scroll'><table><thead><tr><th>入札日</th><th>年限</th><th>平均利回り</th>"
             "<th>最高利回り</th><th>テール(bps)</th><th>12回平均</th><th>応募額</th><th>募入額</th>"
             "<th>応募倍率</th><th>12回平均</th><th>判定</th></tr></thead>"
             f"<tbody>{''.join(rows)}</tbody></table></div>")
    return summary + table


# -----------------------------------------
# HTML本体
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>還流ウォッチ - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #fbbf24; margin: 28px 0 8px; padding-top: 14px; border-top: 1px solid #1e293b; }}
  h2 .num {{ display:inline-block; background:#7c3aed; color:#fff; border-radius:50%; width:22px; height:22px;
            text-align:center; line-height:22px; font-size:0.8rem; margin-right:8px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .intro {{ max-width: 1100px; background: #1e293b; border: 1px solid #334155; border-radius: 8px;
           padding: 12px 16px; font-size: 0.85rem; line-height: 1.75; color: #cbd5e1; margin-bottom: 8px; }}
  .intro b {{ color: #fbbf24; }}
  .summary {{ max-width: 1100px; font-size: 0.88rem; line-height: 1.8; color: #cbd5e1; margin: 6px 0 10px; }}
  .summary b {{ color: #f8fafc; }}
  .badge {{ display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.78rem; font-weight: 700; margin-left: 6px; }}
  .b-red {{ background: rgba(239,68,68,0.3); color: #fca5a5; border: 1px solid #ef4444; }}
  .b-org {{ background: rgba(251,146,60,0.25); color: #fdba74; border: 1px solid #f97316; }}
  .b-green {{ background: rgba(34,197,94,0.22); color: #86efac; border: 1px solid #22c55e; }}
  .b-blue {{ background: rgba(59,130,246,0.25); color: #93c5fd; border: 1px solid #3b82f6; }}
  .b-gray {{ background: #334155; color: #cbd5e1; border: 1px solid #475569; }}
  .scroll {{ overflow: auto; max-width: 1100px; max-height: 440px; border: 1px solid #1e293b; border-radius: 6px; }}
  table {{ border-collapse: collapse; font-size: 0.84rem; min-width: 100%; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 8px 12px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child {{ text-align: left; }}
  .sub {{ color: #64748b; font-size: 0.72rem; font-weight: 400; }}
  td {{
    padding: 7px 12px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td.dt {{ text-align: left; font-weight: 600; color: #f1f5f9; }}
  td.dt a {{ color: #93c5fd; text-decoration: none; }}
  td.desc {{ color: #64748b; font-size: 0.76rem; text-align: left; white-space: normal; min-width: 240px; }}
  td.now {{ font-weight: 700; color: #f8fafc; }}
  td.muted {{ color: #64748b; }}
  tr:hover td {{ filter: brightness(1.15); }}
  tr.gaprow td {{ border-top: 1px solid #334155; font-weight: 700; }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
  td.bg-red2 {{ background: rgba(239,68,68,0.38); font-weight: 700; }}
  td.bg-red1 {{ background: rgba(239,68,68,0.18); }}
  td.bg-green1 {{ background: rgba(34,197,94,0.18); }}
  td.bg-blue1 {{ background: rgba(59,130,246,0.22); }}
  td.j-ok {{ background: rgba(59,130,246,0.28); }}
  td.j-mid {{ background: rgba(251,191,36,0.22); }}
  td.j-ng {{ background: rgba(239,68,68,0.34); font-weight: 700; }}
  td.j-none {{ color: #64748b; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 12px; line-height: 1.8; max-width: 1100px; }}
  .note b {{ color: #94a3b8; }}
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
    <a href="kanryu.html" class="active" style="border-color:#7c3aed">還流ウォッチ</a>
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
  <h1>還流ウォッチ（構造的な円キャリー解消の監視）</h1>
  <p class="subtitle">最終更新: {updated} | 出所: 財務省（対外及び対内証券売買契約等の状況・国債金利情報・入札結果）、Yahoo Finance | 単位: 億円・兆円、ネット＝取得−処分（＋＝買い越し、−＝売り越し）</p>
  <div class="intro">
    投機筋の円キャリー（<a href="kinri.html" style="color:#60a5fa">金利と為替</a>の燃料計＝CFTC円ショート）はV字で巻き戻るが、
    <b>本邦の生保・年金・銀行が海外資産を国内に還流させる「構造的な解消」はCOTには一切出ない</b>。
    こちらは半期の運用計画に沿った階段状の動きで、VIXは跳ねないのにドル円が戻らない・米長期金利が上がる、という形で現れる。
    その入口を5つの窓で監視する: ①週次フロー ②誰が売っているか ③乗り換えの経済合理性 ④超長期入札（受け皿） ⑤市場の逆行。
    ①〜④が揃って⑤が点灯したら本物。
  </div>

  <h2><span class="num">1</span>週次フロー: 居住者による対外証券投資（財務省・毎週木曜8:50公表、前週分）</h2>
{weekly}
  <p class="note">
    ・<b>対外 中長期債</b>が本命。数週連続の処分超（−）と、4週合計が2014年以降の<span style="color:#f87171">下位10%（濃赤）</span>に入ったら還流の入口。上位10%は薄緑。<br>
    ・対内（海外勢）の列は参考。日本勢が外債を売る局面で海外勢もJGBを売っていれば「JGB需給の綱引き」、海外勢が日本株を売っていれば<a href="gaikoku.html" style="color:#60a5fa">海外投資家</a>デッキと突き合わせ。<br>
    ・注意: <b>ヘッジ付き外債を売っても円買いは発生しない</b>（ヘッジ解消と相殺）。為替に効くのはオープン外債の売却とヘッジ比率の引き上げ。フローの符号と<a href="kinri.html" style="color:#60a5fa">ドル円</a>の動きは必ずセットで見る。
  </p>

  <h2><span class="num">2</span>誰が売っているか: 投資家部門別 対外中長期債ネット（財務省・月次、翌月8〜10日頃公表）</h2>
{monthly}
  <p class="note">
    ・<b>生保</b>＝ESR（経済価値ベースの新規制）で負債とのデュレーションマッチングを求められており、外債→超長期円債への乗り換えの主役。12ヶ月累計がマイナスに沈み続けるかを見る。<br>
    ・<b>信託勘定(年金等)</b>＝GPIF等の委託分を含む。GPIFは基本ポートフォリオ（4資産25%）固定なので、大きく動くとしたら企業年金・共済側。<br>
    ・<b>銀行(銀行勘定)</b>＝メガバンク・農林中金等の外債ポートフォリオ。2024年の農中の米国債・CLO売却が小さな予行演習。<br>
    ・<b>投信</b>＝個人のオルカン・S&P500積立。ここが買い続けている限り「個人の円売り」が生保の還流を一部相殺する。株式の投信列も併記。<br>
    ・セルの色は各部門の2014年以降の月次分布に対するパーセンタイル（下位10%濃赤・下位25%薄赤・上位10%薄緑）。
  </p>

  <h2><span class="num">3</span>乗り換えの経済合理性: ヘッジ後利回り差（ヘッジ付き米国債 vs 日本国債）</h2>
{hedge}
  <p class="note">
    ・ヘッジコストは <b>US3M − JP1Y</b> の近似（実務は3ヶ月フォワードのロール。日本側は3ヶ月TBが財務省CSVにないため1年債で代用、数十bps程度の誤差あり）。<br>
    ・<b>差がマイナス</b>＝ヘッジ付き米国債より円債の方が利回りが高い＝生保・銀行が外債を売って円債に乗り換える動機。<span style="color:#f87171">−1%以下は濃赤（誘因 強）</span>、0%以下は薄赤、プラスは青。<br>
    ・これは「動機」であって「実行」ではない。実行は①②のフローで確認する。動機が強いのにフローが出ない期間＝含み損で動けない状態（生保4社の国内債含み損13兆円超）。
  </p>

  <h2><span class="num">4</span>受け皿: 超長期国債（20/30/40年）入札の強弱（財務省・入札結果）</h2>
{auction}
  <p class="note">
    ・<b>テール</b>＝募入最高利回り − 募入平均利回り（bps）。大きいほど「安くしないと売れなかった」＝需要が弱い。<b>応募倍率</b>＝応募額÷募入決定額。高いほど需要が強い。<br>
    ・判定は同じ年限の直近12回平均との比較（倍率が−0.4以上低い、またはテールが平均の2倍以上→弱い。倍率が+0.3以上高くテールが平均以下→強い）。<br>
    ・還流が本格化すると生保の超長期買いで<span style="color:#93c5fd">30年・40年入札が強く</span>なる（①の処分超と同時に出たら本物）。逆に入札が弱いまま金利だけ上がる局面は「売りが先で買いが後」の無秩序期間。
  </p>

  <h2><span class="num">5</span>市場の逆行シグナル: 米10年金利↑ なのに 円高</h2>
{signal}
  <p class="note">
    ・教科書では米金利上昇＝ドル高円安。<span style="color:#f87171">米10年が+{sig_bps:.0f}bps以上上がっているのにドル円が{sig_pct:.1f}%以上の円高</span>なら、金利差で説明できない円買い＝日本勢の米国債売り→円転の疑い（赤）。①〜④のデータが出る前に市場が先に教えてくれる唯一の日次シグナル。<br>
    ・ただし<span style="color:#fbbf24">日本の10年金利が米国以上に上がって日米差が縮小している</span>なら、その円高は日銀利上げ織り込みで説明できる（黄）。赤は「米金利↑・日米差拡大・それでも円高」の三点セットのときだけ。<br>
    ・<a href="kinri.html" style="color:#60a5fa">金利と為替</a>デッキのドル円行が赤（逆行）になるのと同じ考え方。あちらは日米10Y差、こちらは米10Yそのものと突き合わせる。
  </p>
  <p class="updated">最終更新: {updated} | 次の確認: 週次は毎週木曜8:50、月次は翌月8〜10日、生保の運用計画は4月・10月、決算（ヘッジ比率）は5月・11月</p>
</body>
</html>
"""


def generate_html(weeks, lt, eq, hdf, aucs):
    now = datetime.datetime.now()
    html = HTML_TEMPLATE.format(
        updated=now.strftime("%Y-%m-%d %H:%M"),
        updated_date=now.strftime("%Y-%m-%d"),
        weekly=build_weekly_html(weeks),
        monthly=build_monthly_html(lt, eq),
        hedge=build_hedge_html(hdf),
        auction=build_auction_html(aucs),
        signal=build_signal_html(hdf),
        sig_bps=SIG_US10Y_BPS, sig_pct=SIG_USDJPY_PCT,
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    log(f"HTML出力: {path}")


def push_to_github():
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML, "kanryu_screen.py",
                    "kanryu_run.bat", "kanryu_history.json"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update kanryu report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/kanryu.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


def main():
    log("還流ウォッチ 開始")
    try:
        log("① 週次フロー取得")
        weeks = fetch_weekly()
        log("② 月次部門別取得")
        lt, eq = fetch_monthly()
        log("③ 金利データ取得")
        hdf = build_hedge_frame()
    except Exception as e:
        log(f"エラー: データの取得に失敗しました: {e}")
        sys.exit(1)

    log("④ 超長期入札結果の更新")
    hist = load_history()
    try:
        aucs = update_auctions(hist)
        save_history(hist)
    except Exception as e:
        log(f"  入札更新でエラー（キャッシュ分で継続）: {e}")
        aucs = [hist["auctions"][k] for k in sorted(hist.get("auctions", {}),
                                                     key=lambda k: hist["auctions"][k]["date"])]

    generate_html(weeks, lt, eq, hdf, aucs)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
