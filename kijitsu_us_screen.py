# -*- coding: utf-8 -*-
"""
下落スクリーナー US版（需給サイクル・下落銘柄スクリーナー、旧称:信用期日スクリーナーUS版） → kijitsu_us.html → GitHub Pages公開

日本版（kijitsu_screen.py）のUS移植版。ただし前提が異なる:
  日本の制度信用には6ヶ月の「期日」があり、高値から半年で信用買いの決済が一巡して
  需給が軽くなる、という時計が働く。USのmargin loanには返済期限がなく、この時計は
  構造的に存在しない。

バックテスト（2026-08、S&P500全503銘柄フルスキャン・10年・経過日数×1-6ヶ月先行リターン検証）:
  - USには日本の「180日」に相当する単一の変曲点は無い
  - 実測波形は谷が2つある（210-239日=平均勝率59.1%、450-479日=55.9%が最弱）、
    山も2つある（0-89日=64-65%、240-299日・360-419日=63%台）という非単調な形
  - → 経過日数の連続した「トンネル」構造ではなく、バケツ単位で期待値の高低を
    非連続に判定する方式に変更（zone_class）。480日以降はN(観測数)が900件未満まで
    急減するため「データ薄」として色を付けない

日本版との構造的な違い:
  日本の制度信用には6ヶ月の「期日」があり、高値から半年で信用買いの決済が一巡して
  需給が軽くなる、という時計が働く。USのmargin loanには返済期限がなく、この時計は
  構造的に存在しない。US側のゾーン分けは「期日」の概念ではなく、実測された
  経過日数×保有期間マトリクスの値そのものに基づく。

需給列について（日本版との重要な違い）:
  日本版の信用倍率（買い残÷売り残）は「低下=需給改善=ブリッシュ」と読める。
  USのShort Interest（空売り比率）はこの読み方が通用しない。実証研究（Asquith/Pathak/Ritter,
  Diether/Lee/Werner等）では、空売りをする側は総じて情報優位な投資家であることが多く、
  Short Interestが高い銘柄はその後もアンダーパフォームしやすい傾向が確認されている。
  つまり「Short Interestが高い→いずれ買い戻されて上がる」は平均的には成立しない。
  ショートスクイーズ（踏み上げ）自体は実在するが、それは「極端に高い比率」×「高いdays to cover」×
  「ポジティブなカタリスト」が同時に揃った特殊イベントであり、系統的な需給改善シグナルではない。
  → このスクリーナーではShort Interestを「弱気サインの目安」として表示する
    （高いまま=警戒継続、大きく下がってきた=売り方が撤退し始めた可能性、程度の弱い解釈）。
    日本版の「倍率低下=ブリッシュ」のロジックをそのまま反転コピーしないこと。

スクリーニング対象: S&P500構成銘柄・「大きな下落区間」内で基準高値から15%以上下落中

基準高値・下落区間の定義は日本版と同一ロジック（市場非依存の部分のみ移植）:
  - 下落区間 = 日足5MAが200MAを下抜け(DC)〜再上抜け(GC)まで。
    10営業日未満の一時的な上抜けはダマシとして同一区間の継続扱い
  - 基準高値 = DC直前の最後のスイング高値（ジグザグ5%閾値で確定したピボット高値）
  - 5MAが200MAより上にいる銘柄は「下落区間にいない」ので対象外

表示: 基準高値からの経過日数（US実測ゾーン色分け）/ 経過日数目安 / 最安値からの位置 /
      Short Interest（%of float・直近2回分・弱気サインとして表示）/
      過去10年の同様エピソードの癖（回数・中央値日数・深さ）

データ: yfinance（株価・Short Interest）。
過去の癖統計は kijitsu_us_kuse_cache.json に週次キャッシュ（7日超で自動リビルド）。

実行: 毎日（kijitsu_us_run.bat）。--test で50銘柄のみ・push無し。
"""

import os
import sys
import json
import time
import io
import subprocess
import datetime

import requests
import yfinance as yf
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KUSE_CACHE = os.path.join(SCRIPT_DIR, "kijitsu_us_kuse_cache.json")
SI_CACHE = os.path.join(SCRIPT_DIR, "kijitsu_us_si_cache.json")
NAME_CACHE = os.path.join(SCRIPT_DIR, "kijitsu_us_name_cache.json")
REPORT_HTML = "kijitsu_us.html"

DD_THRESHOLD = -15.0   # スクリーニング: 基準高値からの下落率
EP_THRESHOLD = 0.15    # 過去エピソード抽出の下落閾値
CHUNK = 100
KUSE_MAX_AGE_DAYS = 7   # 癖キャッシュのリビルド間隔
SI_MAX_AGE_DAYS = 3     # Short Interestキャッシュのリビルド間隔（yfinance側もFINRA準拠で月2回更新程度）
NAME_MAX_AGE_DAYS = 30  # 銘柄名キャッシュのリビルド間隔（社名はほぼ変わらないので長め）
ZZ_TH = 0.05            # 基準高値検出のジグザグ閾値（5%）
WHIPSAW_DAYS = 10       # GC後この営業日数未満で再DCならダマシ（同一下落区間の継続）

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def progress(label, i, total, t0):
    if i == 0:
        return
    elapsed = time.time() - t0
    eta = elapsed / i * (total - i)
    log(f"  {label} {i}/{total} ({i/total*100:.0f}%) 経過{elapsed/60:.0f}分 残り約{eta/60:.0f}分")


# -----------------------------------------
# 対象銘柄（S&P500構成）
# -----------------------------------------
def get_sp500_tickers():
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        html = requests.get(url, headers=HEADERS, timeout=15).text
        df = pd.read_html(io.StringIO(html))[0]
        return sorted(set(t.replace(".", "-") for t in df["Symbol"].tolist()))
    except Exception as e:
        log("S&P500取得エラー: " + str(e))
        return []


def load_universe():
    codes = get_sp500_tickers()
    log(f"S&P500ユニバース: {len(codes)}銘柄")
    return codes


# -----------------------------------------
# 過去エピソード（癖）の抽出（日本版と同一ロジック）
# -----------------------------------------
def extract_episodes(close, th=EP_THRESHOLD):
    """高値からth以上下落したエピソード（回復済みのみ）を返す。
    エピソード終了 = 高値更新（回復）"""
    eps = []
    peak = close.iloc[0]
    peak_i = 0
    in_ep = False
    trough = None
    trough_i = None
    for i in range(1, len(close)):
        p = close.iloc[i]
        if not in_ep:
            if p > peak:
                peak = p
                peak_i = i
            elif (peak - p) / peak >= th:
                in_ep = True
                trough = p
                trough_i = i
        else:
            if p < trough:
                trough = p
                trough_i = i
            if p > peak:
                eps.append({
                    "cal_days": (close.index[trough_i] - close.index[peak_i]).days,
                    "dd": round((trough / peak - 1) * 100, 1),
                })
                peak = p
                peak_i = i
                in_ep = False
    return eps


DEEP_DD = -30.0  # 「深い下落」の定義: 高値からこの%以上沈んだエピソード


def _kuse_stats(eps):
    """エピソードリスト → 癖統計。深い下落（DD-30%超）だけの中央値日数・中央値DDを別枠で持つ"""
    if not eps:
        return {"n": 0}
    days = sorted(e["cal_days"] for e in eps)
    dds = sorted(e["dd"] for e in eps)
    rec = {
        "n": len(eps),
        "med_days": days[len(days) // 2],
        "med_dd": dds[len(dds) // 2],
    }
    deep = [e for e in eps if e["dd"] <= DEEP_DD]
    rec["deep_n"] = len(deep)
    if deep:
        ddays = sorted(e["cal_days"] for e in deep)
        ddds = sorted(e["dd"] for e in deep)
        rec["deep_med_days"] = ddays[len(ddays) // 2]
        rec["deep_med_dd"] = ddds[len(ddds) // 2]
    return rec


def build_kuse_cache(codes):
    """過去10年のエピソード統計 {code: {n, med_days, med_dd, deep_n}} を構築"""
    log("癖キャッシュをリビルド中（10年ダウンロード）...")
    out = {"_updated": datetime.date.today().isoformat(), "stocks": {}}
    t0 = time.time()
    for ci in range(0, len(codes), CHUNK):
        chunk = codes[ci:ci + CHUNK]
        data = yf.download(chunk, period="10y", auto_adjust=True,
                           progress=False, group_by="ticker", threads=True)
        for c in chunk:
            try:
                close = data[c]["Close"].dropna()
            except Exception:
                continue
            if len(close) < 500:
                continue
            eps = extract_episodes(close)
            out["stocks"][c] = _kuse_stats(eps)
        progress("癖統計", min(ci + CHUNK, len(codes)), len(codes), t0)
    json.dump(out, open(KUSE_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    log(f"癖キャッシュ保存: {len(out['stocks'])}銘柄")
    return out


def load_kuse_cache(codes):
    if os.path.exists(KUSE_CACHE):
        try:
            cache = json.load(open(KUSE_CACHE, encoding="utf-8"))
            updated = datetime.date.fromisoformat(cache.get("_updated", "2000-01-01"))
            if (datetime.date.today() - updated).days <= KUSE_MAX_AGE_DAYS:
                return cache
            log(f"癖キャッシュが{(datetime.date.today() - updated).days}日前のためリビルド")
        except Exception:
            pass
    return build_kuse_cache(codes)


# -----------------------------------------
# Short Interest（弱気サインの目安）
# -----------------------------------------
def fetch_short_interest(codes):
    """yfinanceのTicker.info から shortPercentOfFloat 等を取得。
    重い（1銘柄ずつAPI呼び出し）ためキャッシュして数日おきに更新。
    直近2回分を保持し、前回比の増減で表示（増加=空売り継続で弱気継続、減少=撤退の兆し程度の弱い解釈）"""
    cache = {"_updated": datetime.date.today().isoformat(), "stocks": {}}
    if os.path.exists(SI_CACHE):
        try:
            old = json.load(open(SI_CACHE, encoding="utf-8"))
            updated = datetime.date.fromisoformat(old.get("_updated", "2000-01-01"))
            if (datetime.date.today() - updated).days <= SI_MAX_AGE_DAYS:
                return old
            cache["stocks"] = {c: v for c, v in old.get("stocks", {}).items() if "prev_pct" not in v}
            # 今回取得した値を「前回値」として引き継げるよう保持
            cache["_prev_stocks"] = old.get("stocks", {})
        except Exception:
            pass
    prev_stocks = cache.pop("_prev_stocks", {})
    log("Short Interestを取得中（銘柄ごとAPI呼び出し、時間がかかります）...")
    t0 = time.time()
    for i, c in enumerate(codes):
        try:
            info = yf.Ticker(c).info
            pct = info.get("shortPercentOfFloat")
            ratio = info.get("shortRatio")  # days to cover
            if pct is not None:
                rec = {"pct": round(pct * 100, 2), "days_to_cover": ratio}
                prev = prev_stocks.get(c, {})
                if "pct" in prev:
                    rec["prev_pct"] = prev["pct"]
                cache["stocks"][c] = rec
        except Exception:
            continue
        if (i + 1) % 50 == 0:
            progress("Short Interest", i + 1, len(codes), t0)
    json.dump(cache, open(SI_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    log(f"Short Interest取得: {len(cache['stocks'])}銘柄")
    return cache


# -----------------------------------------
# 銘柄名（ティッカー→社名）
# -----------------------------------------
def fetch_names(codes):
    """yfinanceのTicker.info から shortName を取得しキャッシュ。
    社名はほぼ変わらないため30日おきの更新で十分（Short Interestより長い間隔）。
    既存キャッシュにある銘柄は再取得しない（新規追加分だけ取得して差分更新）"""
    cache = {"_updated": datetime.date.today().isoformat(), "names": {}}
    if os.path.exists(NAME_CACHE):
        try:
            old = json.load(open(NAME_CACHE, encoding="utf-8"))
            updated = datetime.date.fromisoformat(old.get("_updated", "2000-01-01"))
            cache["names"] = old.get("names", {})
            if (datetime.date.today() - updated).days <= NAME_MAX_AGE_DAYS:
                missing = [c for c in codes if c not in cache["names"]]
                if not missing:
                    return cache
                codes_to_fetch = missing
            else:
                codes_to_fetch = codes
        except Exception:
            codes_to_fetch = codes
    else:
        codes_to_fetch = codes

    log(f"銘柄名を取得中（{len(codes_to_fetch)}銘柄、差分のみ）...")
    t0 = time.time()
    for i, c in enumerate(codes_to_fetch):
        try:
            info = yf.Ticker(c).info
            name = info.get("shortName") or info.get("longName")
            if name:
                cache["names"][c] = name
        except Exception:
            continue
        if (i + 1) % 50 == 0:
            progress("銘柄名", i + 1, len(codes_to_fetch), t0)
    cache["_updated"] = datetime.date.today().isoformat()
    json.dump(cache, open(NAME_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    log(f"銘柄名キャッシュ: {len(cache['names'])}銘柄")
    return cache


# -----------------------------------------
# 基準高値の検出（5MA×200MAの下落区間 + ジグザグ、日本版と同一ロジック）
# -----------------------------------------
def _last_swing_high(close, before):
    """before以前の最後のスイング高値 (日付, 価格) をジグザグZZ_THで返す"""
    s = close[close.index <= before]
    if len(s) < 5:
        return None
    last_high = None
    ext_p = s.iloc[0]
    ext_d = s.index[0]
    trend = None
    for d, p in s.items():
        if trend in (None, "up"):
            if p > ext_p:
                ext_p = p
                ext_d = d
            elif (ext_p - p) / ext_p >= ZZ_TH:
                last_high = (ext_d, float(ext_p))
                trend = "down"
                ext_p = p
                ext_d = d
        if trend == "down":
            if p < ext_p:
                ext_p = p
                ext_d = d
            elif (p - ext_p) / ext_p >= ZZ_TH:
                trend = "up"
                ext_p = p
                ext_d = d
    if trend in (None, "up") and (last_high is None or ext_d > last_high[0]):
        last_high = (ext_d, float(ext_p))
    return last_high


def find_downtrend_anchor(close):
    """現在「大きな下落区間」内にある場合、(DC日, 基準高値日, 基準高値) を返す。
    区間外（5MA>200MA）ならNone。ロジックは日本版kijitsu_screen.pyと同一（市場非依存）。"""
    ma5 = close.rolling(5).mean()
    ma200 = close.rolling(200).mean()
    diff = (ma5 - ma200).dropna()
    if len(diff) < 30 or diff.iloc[-1] >= 0:
        return None

    sign = (diff > 0).astype(int)
    i = len(sign) - 1
    dc_i = None
    while i > 0:
        if sign.iloc[i] == 0 and sign.iloc[i - 1] == 1:  # DC
            j = i - 1
            up_len = 0
            while j >= 0 and sign.iloc[j] == 1:
                up_len += 1
                j -= 1
            dc_i = i
            if up_len >= WHIPSAW_DAYS or j < 0:
                break
            k = j
            while k > 0 and not (sign.iloc[k] == 0 and sign.iloc[k - 1] == 1):
                k -= 1
            prev_dc_date = diff.index[k]
            dc_now = diff.index[dc_i]
            prev_anchor = _last_swing_high(close, prev_dc_date)
            between_high = close[(close.index >= prev_dc_date) & (close.index <= dc_now)].max()
            if prev_anchor is not None and between_high >= prev_anchor[1]:
                break
            i = k
        else:
            i -= 1
    if dc_i is None:
        return None
    dc_date = diff.index[dc_i]

    last_high = _last_swing_high(close, dc_date)
    if last_high is None:
        return None
    return dc_date, last_high[0], float(last_high[1])


# -----------------------------------------
# 現在のスクリーニング（2年日足）
# -----------------------------------------
def screen_current(codes, kuse, si, names):
    log("現在の下落状況をスクリーニング中（2年ダウンロード）...")
    rows = []
    t0 = time.time()
    today = pd.Timestamp.today().normalize()
    for ci in range(0, len(codes), CHUNK):
        chunk = codes[ci:ci + CHUNK]
        data = yf.download(chunk, period="2y", auto_adjust=True,
                           progress=False, group_by="ticker", threads=True)
        for c in chunk:
            try:
                close = data[c]["Close"].dropna()
            except Exception:
                continue
            if len(close) < 220:
                continue
            anchor = find_downtrend_anchor(close)
            if anchor is None:
                continue
            dc_date, peak_date, peak = anchor
            last = float(close.iloc[-1])
            dd = (last / peak - 1) * 100
            if dd > DD_THRESHOLD:
                continue
            after = close[close.index >= peak_date]
            trough = float(after.min())
            trough_date = after.idxmin()
            cal_days = int((today - peak_date.normalize()).days)
            rows.append({
                "code": c,
                "name": names.get("names", {}).get(c, ""),
                "price": last,
                "peak_date": peak_date.strftime("%Y-%m-%d"),
                "dc_date": dc_date.strftime("%Y-%m-%d"),
                "cal_days": cal_days,
                "dd": round(dd, 1),
                "trough_date": trough_date.strftime("%Y-%m-%d"),
                "trough_dd": round((trough / peak - 1) * 100, 1),
                "off_trough": round((last / trough - 1) * 100, 1),
                "kuse": kuse.get("stocks", {}).get(c, {}),
                "si": si.get("stocks", {}).get(c, {}),
            })
        progress("スクリーニング", min(ci + CHUNK, len(codes)), len(codes), t0)
    log(f"該当: {len(rows)}銘柄（下落区間内・基準高値-15%超）")
    return rows


# -----------------------------------------
# HTML
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>下落スクリーナー US版 - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .legend {{ display: flex; gap: 10px; margin-bottom: 14px; font-size: 0.78rem; color: #94a3b8; flex-wrap: wrap; align-items: center; }}
  .chip {{ padding: 2px 10px; border-radius: 10px; }}
  .zone-hot {{ background: rgba(34,197,94,0.25); color: #86efac; }}
  .zone-cold {{ background: rgba(220,38,38,0.3); color: #fecaca; }}
  .zone-neutral {{ background: rgba(100,116,139,0.3); color: #cbd5e1; }}
  .zone-thin {{ background: rgba(100,116,139,0.12); color: #64748b; }}
  .table-wrap {{ overflow: auto; max-height: calc(100vh - 250px); }}
  table {{ border-collapse: collapse; font-size: 0.8rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 8px 10px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:nth-child(-n+3) {{ text-align: left; }}
  td {{
    padding: 6px 10px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:nth-child(-n+3) {{ text-align: left; }}
  tr:hover td {{ background: #16213a; }}
  .neg {{ color: #f87171; }}
  .pos {{ color: #4ade80; }}
  .dim {{ color: #64748b; font-size: 0.74rem; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.9; max-width: 1150px; }}
  .evidence {{
    background: rgba(59,130,246,0.08); border: 1px solid rgba(59,130,246,0.3);
    border-radius: 8px; padding: 10px 14px; font-size: 0.8rem; line-height: 1.8;
    max-width: 1150px; margin-bottom: 16px;
  }}
  .evidence b {{ color: #93c5fd; }}
  .warn-box {{
    background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.3);
    border-radius: 8px; padding: 10px 14px; font-size: 0.8rem; line-height: 1.8;
    max-width: 1150px; margin-bottom: 16px;
  }}
  .warn-box b {{ color: #fca5a5; }}
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
    <a href="kijitsu_us.html" class="active" style="border-color:#db2777">下落日数(US)</a>
    <a href="fx_corr.html" style="border-color:#db2777">円安/円高相関</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>下落スクリーナー US版 — 下落銘柄の経過日数×需給</h1>
  <p class="subtitle">最終更新: {updated} | 対象: S&amp;P500構成銘柄・下落区間内（5MA&lt;200MA）で基準高値から{dd_th}%以上下落中（{n_hits}銘柄） | Short Interest: yfinance直近取得分</p>
  <div class="warn-box">
    <b>日本版との違い（重要）:</b>
    USのmargin loanには日本の制度信用のような「6ヶ月の期日」が存在せず、全員に一斉決済を強制する時計は構造的に無い。
    実際に検証（S&amp;P500全503銘柄フルスキャン・10年）したところ、<b>日本の180日に相当する単一の変曲点はUSには無い</b>。
    実測波形は非単調で、<b>2ヶ月時点の勝率×平均リターンで見たスコアが高いのは390-419日・420-449日・0-89日・240-299日</b>、
    <b>低いのは180-239日・300-329日</b>など、経過日数が連続的に改善する「トンネル」構造ではない。そのためこのページのゾーン分けは、
    <b>経過日数×保有期間マトリクスのバケツ別実測値（2ヶ月列の勝率×平均リターン）から「期待値高め/低め」を非連続に判定</b>する方式にしている
    （480日以降は観測数が900件未満まで急減するため判定材料不足として色を付けない）。<br>
    また需給列の<b>Short Interest（空売り比率）は、日本版の信用倍率と逆の意味</b>で見る必要がある。
    実証研究では空売りが多い銘柄はその後もアンダーパフォームしやすい傾向が確認されており、
    「空売りが多い→いずれ買い戻されて上がる」は平均的には成立しない。踏み上げは「高い比率×高いdays to cover×カタリスト」が
    揃った特殊イベントであり系統的な需給改善シグナルではないため、<b>Short Interestはここでは「弱気サインの目安」として表示</b>している。
  </div>
  <div class="evidence">
    <b>考え方と検証（S&amp;P500全503銘柄フルスキャン・10年・経過日数×先行リターン検証）:</b><br>
    ・<b>USには日本の「180日効果」に相当する単一の変曲点は無い</b>。<b>2ヶ月時点の勝率×平均リターンのスコアで見ると
    390-449日・0-89日・240-299日が高く、180-239日・300-329日が低い</b>という非単調な形<br>
    ・ゾーン色分けは下の「経過日数×保有期間マトリクス」の2ヶ月列（勝率×リターン）からバケツごとに機械的に判定（非連続）。
    詳細は表とその下の注記を参照<br>
    ・基準高値の定義・下落区間の検出ロジックは日本版と同一（市場非依存の部分のみ移植）。
    5MAが200MAを下抜け（DC）てから再上抜けするまでを1つの下落区間とし、
    基準高値=DC直前の最後のスイング高値（ジグザグ5%）とする<br>
    ・単独の売買根拠ではなく、他の条件と重ねる1要素として使う
  </div>
  <div class="legend">
    <span class="chip zone-hot">期待値高め（2ヶ月スコア4.0以上）</span>
    <span class="chip zone-cold">期待値低め（1.8未満）</span>
    <span class="chip zone-neutral">中間</span>
    <span class="chip zone-thin">データ薄（N&lt;1,000）</span>
    <span style="margin-left:10px">経過日数の長い順・下の検証マトリクスのバケツ別実測に基づく判定（スコア=2ヶ月勝率%/100×平均リターン%）</span>
  </div>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>ティッカー</th><th>銘柄名</th><th>現値</th>
        <th>基準高値日<br>(最後の逃げ場)</th><th>5MA×200MA<br>下抜け日</th><th>経過<br>日数</th>
        <th>高値比</th><th>最安値日</th><th>最安時<br>高値比</th><th>最安値比<br>(現在)</th>
        <th>Short Interest<br>(%of float)</th><th>Days to<br>Cover</th>
        <th>過去10年の深い下落(-30%超)の癖<br>(回数/中央値日数/中央値DD)</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・<b>基準高値</b> = 5MAが200MAを下抜ける直前の最後のスイング高値（ジグザグ5%で確定）。
    「最後の逃げ場」であり、ここより後に買った人が本当に捕まっている。<br>
    ・<b>経過日数</b> = 基準高値からの暦日数。ゾーン色は下の検証マトリクスの実測値からバケツ単位で機械判定（非連続）。<br>
    ・5MAが200MAを再上抜け（GC）した銘柄は「下落区間終了」として表から消える（10営業日未満の上抜けはダマシとして継続扱い）。<br>
    ・<b>最安値比</b> = 下落開始後の最安値からの上昇率。プラスが大きい=既に底から反発が進行。<br>
    ・<b>Short Interest（%of float）</b> = 空売り残高÷浮動株数（yfinance shortPercentOfFloat）。
    上述の通り「高いまま=警戒継続、大きく下がってきた=売り方が撤退し始めた可能性」程度の弱い解釈に留める。
    数字が無い銘柄はデータ未提供。<br>
    ・<b>Days to Cover</b> = 空売り残高÷平均日次出来高（shortRatio）。高いほど空売りの解消に時間がかかる=踏み上げが起きた場合の値動きが大きくなりうる。<br>
    ・<b>過去10年の深い下落の癖</b> = 高値から-30%超まで沈んだエピソードの回数・高値→最安値の中央値日数・中央値の深さ。
    浅い下落（-15〜30%）は数週間で片付くことが多く癖として別物のため、深い下落だけを表示（カッコ内は-15%超の全エピソード数）。
  </p>
{verification_section}
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""

# -----------------------------------------
# 検証マトリクス（フォワード検証セクション、日本版と同じ運用方針）
# -----------------------------------------
# US側の検証日。日本版と同じく、半年〜1年に1回程度、最新データで回し直して更新する想定。
VERIFICATION_DATE_US = "2026-08-16"

# S&P500構成「全503銘柄」フルスキャン・過去10年日足・kijitsu_us_screen.pyのユニバース/
# 基準高値ロジックと同一条件（観測日=5MA<200MAの下落区間内で基準高値-15%以上の営業日）。
# N列は2ヶ月列の観測数（日本版と同じ2ヶ月ベース表示）。480日以降はNが急減するため参考値として見る。
# 平均DD=そのバケツの基準高値からの平均下落率（日本版と同じ列）。
VERIFICATION_MATRIX_ROWS_US = [
    # (経過日数ラベル, 平均DD, 1ヶ月, 2ヶ月, 3ヶ月, 4ヶ月, 5ヶ月, 6ヶ月, N(2ヶ月), マーク)
    ("0-29日", "-21.3%", (60.9, 3.3), (61.7, 6.7), (66.9, 11.4), (67.0, 13.5), (66.5, 16.7), (68.2, 18.5), "14,436", ""),
    ("30-59日", "-22.9%", (59.2, 3.0), (63.0, 6.7), (65.9, 8.6), (66.0, 11.0), (66.3, 12.4), (67.5, 14.1), "29,632", ""),
    ("60-89日", "-24.0%", (62.2, 3.8), (64.9, 6.2), (65.8, 8.2), (64.7, 9.1), (66.0, 10.6), (64.8, 11.2), "30,240", ""),
    ("90-119日", "-25.0%", (61.4, 2.9), (62.5, 4.5), (61.7, 5.2), (62.8, 6.6), (61.4, 6.7), (64.2, 9.1), "27,328", ""),
    ("120-149日", "-26.3%", (58.6, 1.9), (59.3, 2.9), (61.3, 4.2), (58.4, 4.4), (61.4, 6.6), (65.4, 9.4), "22,553", ""),
    ("150-179日", "-27.7%", (56.7, 1.4), (59.3, 2.9), (56.9, 3.0), (60.0, 5.2), (64.2, 7.8), (64.3, 9.7), "19,354", ""),
    ("180-209日", "-29.4%", (58.6, 2.1), (56.6, 2.3), (60.0, 4.4), (65.2, 7.2), (65.0, 8.8), (66.3, 11.7), "17,084", ""),
    ("210-239日", "-31.0%", (51.2, 0.4), (55.6, 2.7), (61.3, 5.6), (60.7, 7.2), (61.5, 9.7), (64.3, 12.4), "14,314", "←USの谷"),
    ("240-269日", "-33.6%", (55.9, 2.7), (63.2, 5.9), (62.5, 7.2), (63.8, 9.7), (65.7, 12.0), (67.4, 14.0), "11,421", ""),
    ("270-299日", "-35.5%", (61.7, 4.2), (60.1, 5.2), (62.1, 6.6), (63.5, 9.1), (66.4, 10.6), (69.3, 13.9), "8,813", ""),
    ("300-329日", "-37.5%", (54.0, 1.3), (55.3, 2.0), (58.3, 3.9), (61.1, 5.4), (64.5, 8.7), (64.4, 9.5), "6,276", ""),
    ("330-359日", "-40.5%", (56.1, 1.5), (58.4, 3.2), (60.4, 5.1), (63.9, 9.0), (64.5, 9.1), (61.6, 8.9), "4,445", "←1年前後"),
    ("360-389日", "-43.2%", (57.2, 2.3), (59.9, 4.2), (66.8, 8.9), (66.0, 9.2), (64.9, 8.5), (64.7, 10.2), "3,303", ""),
    ("390-419日", "-45.0%", (58.0, 2.7), (67.6, 7.7), (68.4, 7.8), (63.6, 6.7), (58.1, 7.9), (63.3, 11.6), "2,515", ""),
    ("420-449日", "-47.3%", (65.5, 6.2), (65.6, 6.9), (62.7, 6.0), (58.0, 6.7), (59.9, 10.9), (62.9, 16.9), "1,953", ""),
    ("450-479日", "-49.0%", (57.8, 2.7), (58.0, 4.3), (48.3, 4.3), (54.8, 7.8), (57.7, 13.4), (58.8, 18.5), "1,303", ""),
    ("480-509日", "-50.5%", (55.0, 2.9), (48.3, 4.3), (53.4, 8.6), (59.5, 14.9), (57.9, 19.3), (57.5, 21.2), "899", "※N減少"),
    ("510-539日", "-52.7%", (45.3, -0.3), (50.5, 3.9), (63.0, 13.4), (59.9, 22.1), (63.1, 24.9), (59.0, 29.5), "602", "※N減少"),
    ("540-569日", "-54.7%", (63.1, 4.2), (66.6, 15.0), (58.7, 26.2), (61.7, 28.9), (64.3, 32.6), (61.9, 27.3), "464", "※N少"),
    ("570-599日", "-54.7%", (65.2, 10.1), (60.1, 22.1), (61.7, 24.9), (66.9, 32.9), (63.9, 27.1), (67.9, 33.5), "338", "※N少"),
    ("600-629日", "-54.0%", (54.9, 5.5), (57.3, 8.9), (70.7, 13.9), (67.6, 12.7), (71.1, 18.3), (70.7, 18.9), "246", "※N少"),
    ("630-659日", "-55.3%", (52.8, 5.1), (58.8, 9.3), (64.2, 9.1), (61.3, 12.9), (61.8, 17.2), (56.1, 17.9), "177", "※N少"),
    ("660-689日", "-57.1%", (67.0, 3.9), (72.7, 4.5), (71.8, 4.1), (68.2, 8.2), (46.4, 8.8), (66.4, 14.0), "110", "※N僅少"),
    ("690-719日", "-57.3%", (48.1, 1.4), (53.1, 0.9), (49.4, -0.9), (28.4, -3.2), (54.3, 2.6), (93.8, 13.7), "81", "※N僅少"),
    ("720日以上", "-61.6%", (59.6, 1.8), (60.7, 4.1), (66.0, 6.6), (73.7, 11.6), (84.6, 15.1), (83.5, 17.9), "285", "※N僅少"),
]


def _matrix_cell_us(pair):
    """(勝率, 平均リターン) → 色付きtd。緑=勝率58%+（濃緑62%+）、赤=55%未満（濃赤52%未満）
    US版は全体的に勝率が高いバケツが多いため日本版と同じ閾値をそのまま使う"""
    win, ret = pair
    if win >= 62:
        bg = "rgba(34,197,94,0.40)"
    elif win >= 58:
        bg = "rgba(34,197,94,0.22)"
    elif win < 52:
        bg = "rgba(239,68,68,0.35)"
    elif win < 55:
        bg = "rgba(239,68,68,0.16)"
    else:
        bg = "transparent"
    return (f'<td style="background:{bg}"><b>{win:.1f}%</b><br>'
            f'<span style="color:#94a3b8; font-size:0.72rem">{ret:+.1f}%</span></td>')


def build_verification_section_us():
    """S&P500全503銘柄フルスキャンの経過日数×保有期間マトリクス。
    半年〜1年に1回、最新データで再検証してVERIFICATION_DATE_USと表を更新する運用。"""
    rows_html = []
    for label, dd, m1, m2, m3, m4, m5, m6, n, mark in VERIFICATION_MATRIX_ROWS_US:
        mark_html = f' <span style="color:#fbbf24; font-size:0.72rem">{mark}</span>' if mark else ""
        rows_html.append(
            f'<tr><td style="text-align:left; color:#94a3b8">{label}{mark_html}</td>'
            f'<td style="color:#fca5a5; font-weight:600">{dd}</td>'
            f'{_matrix_cell_us(m1)}{_matrix_cell_us(m2)}{_matrix_cell_us(m3)}'
            f'{_matrix_cell_us(m4)}{_matrix_cell_us(m5)}{_matrix_cell_us(m6)}'
            f'<td style="color:#475569; font-size:0.72rem">{n}</td></tr>')

    return f"""
  <div class="evidence">
    <b>下落銘柄の経過日数×保有期間マトリクス — US・S&amp;P500全503銘柄フルスキャン（検証日 {VERIFICATION_DATE_US}）:</b><br>
    対象=S&amp;P500構成<b>全503銘柄</b>・過去10年日足（サンプル抽出ではなく全銘柄走査）。
    観測日=<b>5MA&lt;200MAの下落区間内</b>で<b>基準高値から-15%以上</b>の営業日（このスクリーナーの表示条件と同一）。
    平均DD=そのバケツの基準高値からの平均下落率（買える安さの目安）。各セル=その時点で買った場合のNヶ月後の<b>勝率%（太字）と平均リターン%</b>（絶対リターン・配当除く）。N列=2ヶ月列の観測数。
    緑=勝率58%以上（濃緑62%以上）、赤=勝率55%未満（濃赤52%未満）。
    <b>長期バケツ（480日以降、マーク付き）はNが900件未満まで下がり690日以降は100件前後のため、参考値として幅を持って見る</b>。
    <br><br>
    <div style="overflow:auto; max-width:1150px;">
    <table style="border-collapse:collapse; font-size:0.76rem; white-space:nowrap;">
      <thead><tr>
        <th style="text-align:left; padding:4px 8px; color:#94a3b8">経過日数</th>
        <th style="padding:4px 8px; color:#94a3b8">平均DD</th>
        <th style="padding:4px 8px; color:#94a3b8">1ヶ月</th>
        <th style="padding:4px 8px; color:#94a3b8">2ヶ月</th>
        <th style="padding:4px 8px; color:#94a3b8">3ヶ月</th>
        <th style="padding:4px 8px; color:#94a3b8">4ヶ月</th>
        <th style="padding:4px 8px; color:#94a3b8">5ヶ月</th>
        <th style="padding:4px 8px; color:#94a3b8">6ヶ月</th>
        <th style="padding:4px 8px; color:#94a3b8">N(2ヶ月)</th>
      </tr></thead>
      <tbody style="text-align:center;">
{"".join(f"        {r}" + chr(10) for r in rows_html)}      </tbody>
    </table>
    </div>
    <br>
    <b>読みどころ:</b> USは全域で日本より高勝率（S&amp;P500銘柄という質の高いユニバース+過去10年の米株の強さが背景）。
    <b>180日近辺に日本のような特異な谷は無く、210-239日の1ヶ月51.2%が最も低い谷</b>。300日以降でも300-329日にもう一段低めの谷があるが、
    総じて「経過日数が長いほど・保有期間が長いほど良い」がUSの基本形で、日本のような時間帯依存のトンネルは薄い。
    <b>510日以降の平均リターンが目立って高い（+13〜33%）区間があるが、観測数が少なく2020年コロナ暴落からの回復期の
    観測が混在している可能性があるため時期偏在の疑いが残る</b>。過信せず参考程度に。
    <br><br>
    <span style="color:#64748b; font-size:0.74rem">
    この検証は{VERIFICATION_DATE_US}時点のデータに基づく1回分のスナップショットで、市場構造は時間とともに変化する。
    <b>半年〜1年に1回を目安に、最新の10年日足データで同じ条件でフォワード検証を回し直し、この表・検証日を更新する運用</b>としている
    （固定の結論ではなく、定期的にアップデートされる前提のセクション。日本版kijitsu.htmlと同じ方針）。
    </span>
  </div>"""


HOT_TH = 4.0      # 2ヶ月スコア(勝率%/100×平均リターン%)がこれ以上なら「期待値高め」
COLD_TH = 1.8     # これ未満なら「期待値低め」
THIN_N = 1000     # N(2ヶ月)がこれ未満ならデータ不足として色を付けない


def _parse_bucket_label(label):
    """"150-179日" → (150,179) / "720日以上" → (720, 99999)"""
    if "以上" in label:
        lo = int(label.replace("日以上", ""))
        return lo, 99999
    lo, hi = label.replace("日", "").split("-")
    return int(lo), int(hi)


def zone_class(cal_days):
    """US実測ベースのゾーン分け。日本版のような「経過日数の連続したU字」ではなく、
    経過日数×保有期間マトリクス（VERIFICATION_MATRIX_ROWS_US）の実測値から、
    バケツ単位で「期待値が高い/低い」を非連続に判定する（谷が2つある実測波形をそのまま反映）。
    期待値スコア = 2ヶ月列の勝率%/100 × 平均リターン%（勝率とリターンの両方を反映）。
    N(2ヶ月列)が薄いバケツ(480日以降が中心)は判定材料不足として色を付けない。
    マトリクスを再検証して更新すれば、このゾーン判定も連動して自動更新される。"""
    for label, dd, m1, m2, m3, m4, m5, m6, n, mark in VERIFICATION_MATRIX_ROWS_US:
        lo, hi = _parse_bucket_label(label)
        if lo <= cal_days <= hi:
            n_val = int(n.replace(",", ""))
            if n_val < THIN_N:
                return "zone-thin", "データ薄"
            win2, ret2 = m2  # 2ヶ月列（勝率%, 平均リターン%）
            score = win2 / 100 * ret2
            if score >= HOT_TH:
                return "zone-hot", "期待値高め"
            if score < COLD_TH:
                return "zone-cold", "期待値低め"
            return "zone-neutral", "中間"
    return "zone-neutral", "中間"


def fmt_si_cell(si):
    """Short Interestセル。前回比増加=赤（弱気継続）、減少=薄緑（撤退の兆し程度）"""
    pct = si.get("pct")
    if pct is None:
        return '<span class="dim">-</span>'
    prev = si.get("prev_pct")
    color = ""
    if prev is not None:
        if pct > prev:
            color = ' class="neg"'
        elif pct < prev:
            color = ' style="color:#86efac"'  # 撤退の兆し程度の弱い緑（ブリッシュの断定はしない）
    return f'<span{color}>{pct:.1f}%</span>'


def generate_html(rows):
    rows.sort(key=lambda r: -r["cal_days"])

    trs = []
    for r in rows:
        zcls, zlabel = zone_class(r["cal_days"])
        kuse = r["kuse"]
        if kuse.get("deep_n"):
            kuse_s = (f'<b>{kuse["deep_n"]}回</b> / {kuse.get("deep_med_days", "-")}日 / {kuse.get("deep_med_dd", "-")}%'
                      f' <span class="dim">(全下落{kuse.get("n", 0)}回)</span>')
        elif kuse.get("n"):
            kuse_s = f'<span class="dim">深い下落なし (全下落{kuse["n"]}回 / {kuse.get("med_days", "-")}日 / {kuse.get("med_dd", "-")}%)</span>'
        else:
            kuse_s = '<span class="dim">-</span>'
        off = r["off_trough"]
        off_s = f'<span class="{"pos" if off > 0 else ""}">{off:+.1f}%</span>'
        si = r["si"]
        dtc = si.get("days_to_cover")
        dtc_s = f'{dtc:.1f}' if dtc is not None else '<span class="dim">-</span>'
        name_s = r.get("name", "") or '<span class="dim">-</span>'
        trs.append(
            f'      <tr><td>{r["code"]}</td>'
            f'<td>{name_s}</td>'
            f'<td>{r["price"]:,.2f}</td>'
            f'<td>{r["peak_date"]}</td>'
            f'<td class="dim">{r.get("dc_date", "-")}</td>'
            f'<td><span class="chip {zcls}">{r["cal_days"]}日</span></td>'
            f'<td class="neg">{r["dd"]:+.1f}%</td>'
            f'<td>{r["trough_date"]}</td>'
            f'<td class="neg">{r["trough_dd"]:+.1f}%</td>'
            f'<td>{off_s}</td>'
            f'<td>{fmt_si_cell(si)}</td>'
            f'<td>{dtc_s}</td>'
            f'<td style="text-align:left">{kuse_s}</td></tr>')

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        dd_th=int(abs(DD_THRESHOLD)),
        n_hits=len(rows),
        rows="\n".join(trs),
        verification_section=build_verification_section_us(),
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
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "kijitsu_us_screen.py", "kijitsu_us_run.bat",
                    "kijitsu_us_kuse_cache.json", "kijitsu_us_si_cache.json",
                    "kijitsu_us_name_cache.json",
                    ".gitignore"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update kijitsu US report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/kijitsu_us.html")
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
    log("下落スクリーナー US版 開始" + ("（テスト: 50銘柄）" if test else ""))

    codes = load_universe()
    if test:
        codes = codes[:50]

    kuse = load_kuse_cache(codes)
    si = fetch_short_interest(codes)
    names = fetch_names(codes)
    rows = screen_current(codes, kuse, si, names)

    if not rows:
        log("該当銘柄なし。HTMLは生成しません")
        return

    generate_html(rows)

    if "--nopush" in sys.argv or test:
        log("push スキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
