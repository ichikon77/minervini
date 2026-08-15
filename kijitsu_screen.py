# -*- coding: utf-8 -*-
"""
信用期日スクリーナー（需給サイクル） → kijitsu.html → GitHub Pages公開

考え方（Sho_RAGウェビナー「銘柄選別方法」④高値から半年経過→需給改善期待）:
  高値から暴落すると信用買いの投げが重しになりズルズル下がるが、
  高値から6ヶ月で制度信用の期日が到来して決済が完了し、需給が軽くなって上がり始める。

バックテスト（2026-08-13、時価総額500億以上1,334銘柄・10年・5,680エピソード）:
  - 最安値到達日数の分布に180日ピンポイントの山はない（0-59日に38%集中）
  - ただし底打ちハザード率は150-179日の10.3%→180-209日で11.1%に反転上昇
  - 下落中の先行60日リターンは高値から120-179日が最悪（勝率49-50%）、
    180日通過後に改善（勝率52%）、330日以降でさらに改善（57%）
  → 「120-179日=需給最悪のトンネル / 180日通過=改善ゾーン」として色分け表示

スクリーニング対象: 時価総額500億円以上・「大きな下落区間」内で基準高値から15%以上下落中

基準高値の定義（2026-08-13変更、ユーザー発案）:
  単純な12ヶ月最高値ではなく「逃げ場がなくなった後の高値」を使う。
  例: 任天堂2025年は8/18が最高値だが、11/6の戻り高値で8/18組は逃げられた。
  本当に信用で捕まっているのは11/6以降に買った人 → 期日カウントの起点は11/6。
  - 下落区間 = 日足5MAが200MAを下抜け(DC)〜再上抜け(GC)まで。
    10営業日未満の一時的な上抜けはダマシとして同一区間の継続扱い
  - 基準高値 = DC直前の最後のスイング高値（ジグザグ5%閾値で確定したピボット高値）
  - 5MAが200MAより上にいる銘柄は「下落区間にいない」ので対象外

表示: 基準高値からの経過日数（ゾーン色分け）/ 期日目安日 / 最安値からの位置 /
      制度信用倍率の10週推移 / 過去10年の同様エピソードの癖（回数・中央値日数・深さ）
      ※過去の癖統計は従来の「高値-15%エピソード」定義のまま（銘柄の性格把握が目的）

データ: yfinance（株価）+ margin_all_history.json（週次信用残・margin_screen.py蓄積）
       + margin_mcap_cache.json（時価総額・margin_weekly.py更新）
過去の癖統計は kijitsu_kuse_cache.json に週次キャッシュ（7日超で自動リビルド）

実行: 毎日19:40（kijitsu_run.bat）。--test で50銘柄のみ・push無し。
"""

import os
import sys
import json
import time
import subprocess
import datetime

import yfinance as yf
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MARGIN_HISTORY = os.path.join(SCRIPT_DIR, "margin_all_history.json")
MCAP_CACHE = os.path.join(SCRIPT_DIR, "margin_mcap_cache.json")
KUSE_CACHE = os.path.join(SCRIPT_DIR, "kijitsu_kuse_cache.json")
FUNDAMENTALS = os.path.join(SCRIPT_DIR, "margin_fundamentals.json")  # 週次業績（margin_weekly.py）
REPORT_HTML = "kijitsu.html"

MIN_MCAP_OKU = 1000    # 時価総額の下限（億円）
DD_THRESHOLD = -15.0   # スクリーニング: 基準高値からの下落率
EP_THRESHOLD = 0.15    # 過去エピソード抽出の下落閾値（バックテストと同じ）
KIJITSU_DAYS = 183     # 制度信用期日の目安（6ヶ月）
CHUNK = 150
KUSE_MAX_AGE_DAYS = 7  # 癖キャッシュのリビルド間隔
ZZ_TH = 0.05           # 基準高値検出のジグザグ閾値（5%）
WHIPSAW_DAYS = 10      # GC後この営業日数未満で再DCならダマシ（同一下落区間の継続）


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def progress(label, i, total, t0):
    if i == 0:
        return
    elapsed = time.time() - t0
    eta = elapsed / i * (total - i)
    log(f"  {label} {i}/{total} ({i/total*100:.0f}%) 経過{elapsed/60:.0f}分 残り約{eta/60:.0f}分")


# -----------------------------------------
# 対象銘柄
# -----------------------------------------
def load_universe():
    with open(MARGIN_HISTORY, encoding="utf-8") as f:
        h = json.load(f)
    names = h["names"]
    weeks = h.get("weeks", {})
    caps = {}
    if os.path.exists(MCAP_CACHE):
        caps = json.load(open(MCAP_CACHE, encoding="utf-8"))
    codes = sorted(c for c in names if (caps.get(c) or 0) >= MIN_MCAP_OKU)

    # 赤字フィルタ: 直近四半期の営業利益が赤字の銘柄を除外（margin_fundamentals.jsonより）
    # 営業利益がNoneの銘柄（銀行・投資会社等、かぶたんに営業利益概念がない）は
    # 除外せず残す（判定不能を落とすと大型銘柄が消えすぎるため）
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
    return codes, names, caps, weeks


# -----------------------------------------
# 過去エピソード（癖）の抽出
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
    """エピソードリスト → 癖統計。全体の回数に加え、深い下落（DD-30%超）だけの
    中央値日数・中央値DDを別枠で持つ（ユーザーが見たいのは深い下落の癖のため）"""
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
        data = yf.download([c + ".T" for c in chunk], period="10y", auto_adjust=True,
                           progress=False, group_by="ticker", threads=True)
        for c in chunk:
            try:
                close = data[c + ".T"]["Close"].dropna()
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
# 基準高値の検出（5MA×200MAの下落区間 + ジグザグ）
# -----------------------------------------
def _last_swing_high(close, before):
    """before以前の最後のスイング高値 (日付, 価格) をジグザグZZ_THで返す。
    未確定の山（上昇トレンド中にbeforeへ到達）も候補として採用"""
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
    区間外（5MA>200MA）ならNone。

    下落区間 = 5MAが200MAを下抜け(DC)〜再上抜け(GC)。
    GC後WHIPSAW_DAYS営業日未満で再DCした場合は原則ダマシとして同一区間の継続。
    ただし短い上抜けでも、その間に株価が「前区間の高値以上」まで戻していた場合は
    逃げ場が実際に発生したので、ダマシではなく区間リセットとして扱う
    （例: コーエーテクモ2025年11月 — GCは2〜5営業日だが11/12に8月高値を更新。
      8月に買った人も逃げられたので、基準高値は11月の戻り高値になるべき）。
    基準高値 = DC直前の最後のスイング高値（ジグザグZZ_THで確定したピボット高値）"""
    ma5 = close.rolling(5).mean()
    ma200 = close.rolling(200).mean()
    diff = (ma5 - ma200).dropna()
    if len(diff) < 30 or diff.iloc[-1] >= 0:
        return None  # 現在は下落区間にいない

    sign = (diff > 0).astype(int)
    # 直近から遡って「区間の始まりのDC」を探す（ダマシGCはスキップ）
    i = len(sign) - 1
    dc_i = None
    while i > 0:
        if sign.iloc[i] == 0 and sign.iloc[i - 1] == 1:  # DC
            # このDCの前の「上抜け期間」の長さを数える
            j = i - 1
            up_len = 0
            while j >= 0 and sign.iloc[j] == 1:
                up_len += 1
                j -= 1
            dc_i = i
            if up_len >= WHIPSAW_DAYS or j < 0:
                break  # 本物の上昇期間の後のDC = 区間の始まり
            # 短い上抜け（ダマシ候補）でも、前のDC〜今のDCの間に価格が
            # 「前の区間の基準高値」以上まで戻していれば逃げ場が発生している
            # → ダマシではなく区間リセット
            # 比較対象は前区間の基準高値（前DC直前のスイング高値）であって
            # 全期間の大天井ではない（大天井と比べると戻りが常に届かずダマシ扱いになる）
            k = j
            while k > 0 and not (sign.iloc[k] == 0 and sign.iloc[k - 1] == 1):
                k -= 1
            prev_dc_date = diff.index[k]
            dc_now = diff.index[dc_i]
            prev_anchor = _last_swing_high(close, prev_dc_date)
            between_high = close[(close.index >= prev_dc_date) & (close.index <= dc_now)].max()
            if prev_anchor is not None and between_high >= prev_anchor[1]:
                break  # 前区間の基準高値まで戻った=捕まった人に逃げ場があった → 区間リセット
            i = k  # 本当のダマシ → さらに前のDCへ遡る
        else:
            i -= 1
    if dc_i is None:
        return None
    dc_date = diff.index[dc_i]

    # 基準高値 = DC以前の最後のスイング高値
    last_high = _last_swing_high(close, dc_date)
    if last_high is None:
        return None
    return dc_date, last_high[0], float(last_high[1])


# -----------------------------------------
# 現在のスクリーニング（2年日足）
# -----------------------------------------
def screen_current(codes, names, caps, kuse):
    log("現在の下落状況をスクリーニング中（2年ダウンロード）...")
    rows = []
    t0 = time.time()
    today = pd.Timestamp.today().normalize()
    for ci in range(0, len(codes), CHUNK):
        chunk = codes[ci:ci + CHUNK]
        data = yf.download([c + ".T" for c in chunk], period="2y", auto_adjust=True,
                           progress=False, group_by="ticker", threads=True)
        for c in chunk:
            try:
                close = data[c + ".T"]["Close"].dropna()
            except Exception:
                continue
            if len(close) < 220:
                continue
            anchor = find_downtrend_anchor(close)
            if anchor is None:
                continue  # 下落区間にいない
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
                "name": names.get(c, ""),
                "mcap": caps.get(c),
                "price": last,
                "peak_date": peak_date.strftime("%Y-%m-%d"),
                "dc_date": dc_date.strftime("%Y-%m-%d"),
                "cal_days": cal_days,
                "dd": round(dd, 1),
                "trough_date": trough_date.strftime("%Y-%m-%d"),
                "trough_dd": round((trough / peak - 1) * 100, 1),
                "off_trough": round((last / trough - 1) * 100, 1),
                "kijitsu_date": (peak_date + pd.Timedelta(days=KIJITSU_DAYS)).strftime("%Y-%m-%d"),
                "kijitsu_in": KIJITSU_DAYS - cal_days,  # マイナス=通過済み
                "kuse": kuse.get("stocks", {}).get(c, {}),
            })
        progress("スクリーニング", min(ci + CHUNK, len(codes)), len(codes), t0)
    log(f"該当: {len(rows)}銘柄（下落区間内・基準高値-15%超）")
    return rows


# -----------------------------------------
# 信用倍率の10週推移
# -----------------------------------------
def margin_series(weeks, code, n=10):
    """[(週, 倍率), ...] 古い順（最大n週）"""
    out = []
    for w in sorted(weeks.keys())[-n:]:
        v = weeks[w].get(code)
        if v and v[0]:
            out.append((w, round(v[1] / v[0], 2)))
        else:
            out.append((w, None))
    return out


# -----------------------------------------
# HTML
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>信用期日スクリーナー - {updated_date}</title>
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
  .zone-early {{ background: rgba(100,116,139,0.3); color: #cbd5e1; }}
  .zone-tunnel {{ background: rgba(220,38,38,0.3); color: #fecaca; }}
  .zone-passed {{ background: rgba(34,197,94,0.25); color: #86efac; }}
  .zone-long {{ background: rgba(59,130,246,0.25); color: #93c5fd; }}
  .table-wrap {{ overflow: auto; max-height: calc(100vh - 250px); }}
  table {{ border-collapse: collapse; font-size: 0.8rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 8px 10px;
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
    <a href="buffett.html" style="border-color:#db2777">バフェット</a>
    <a href="cramer.html" style="border-color:#db2777">クレイマー</a>
    <a href="kijitsu.html" class="active" style="border-color:#db2777">信用期日</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>信用期日スクリーナー — 高値から半年の需給サイクル</h1>
  <p class="subtitle">最終更新: {updated} | 対象: 時価総額{min_mcap}億円以上・直近四半期の営業黒字・下落区間内（5MA&lt;200MA）で基準高値から{dd_th}%以上下落中（{n_hits}銘柄） | 週次信用残: {margin_week}時点</p>
  <div class="evidence">
    <b>考え方と検証（2026-08-13、1,334銘柄・10年・5,680エピソード）:</b>
    高値から暴落すると信用買いの投げが重しになりズルズル下がるが、高値から6ヶ月で制度信用の期日が到来して需給が軽くなる、という説を検証。<br>
    ・下落中の銘柄のその後60日リターンは<b>高値から120〜179日が最悪期</b>（勝率49〜50%）、<b>180日通過後に改善</b>（勝率52%）、330日以降でさらに改善（57%）<br>
    ・底打ち率も150-179日の10.3%→180-209日で11.1%に反転上昇。ただし「180日=スイッチ」ではなく「トンネルを抜けると徐々に軽くなる」効果（差は小さい）<br>
    ・単独の売買根拠ではなく、需給（信用倍率の低下）・過去の癖・テクニカルと重ねる1要素として使う<br>
    <b>基準高値の定義:</b> 単純な12ヶ月最高値ではなく「逃げ場がなくなった後の高値」を使う。
    下落区間=日足5MAが200MAを下抜け（DC）てから再上抜けするまで（10営業日未満の上抜けはダマシ扱い）。
    基準高値=DC直前の最後のスイング高値（ジグザグ5%）。それより前の高値で買った人は途中の戻りで逃げられたが、
    基準高値以降に信用で買った人が本当に捕まっている＝期日カウントの起点。
  </div>
  <div class="legend">
    <span class="chip zone-early">下落初期（〜119日）</span>
    <span class="chip zone-tunnel">需給最悪期（120〜179日）</span>
    <span class="chip zone-passed">期日通過（180〜359日）</span>
    <span class="chip zone-long">長期低迷（360日〜）</span>
    <span style="margin-left:10px">経過日数の長い順。信用倍率は週次10週推移（右端が最新、低下=需給改善）</span>
  </div>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>コード</th><th>銘柄</th><th>時価総額<br>(億円)</th><th>現値</th>
        <th>基準高値日<br>(最後の逃げ場)</th><th>5MA×200MA<br>下抜け日</th><th>経過<br>日数</th><th>期日目安<br>(高値+6ヶ月)</th>
        <th>高値比</th><th>最安値日</th><th>最安時<br>高値比</th><th>最安値比<br>(現在)</th>
        <th>信用倍率 10週推移（古→新）</th>
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
    「最後の逃げ場」であり、ここより後に信用で買った人が本当に捕まっている。<br>
    ・<b>経過日数</b> = 基準高値からの暦日数。制度信用（6ヶ月期日）の決済サイクルに対する現在位置。<br>
    ・<b>期日目安</b> = 基準高値日+183日。この日以降、高値圏で信用買いした玉の期日決済が一巡する。<br>
    ・5MAが200MAを再上抜け（GC）した銘柄は「下落区間終了」として表から消える（10営業日未満の上抜けはダマシとして継続扱い）。<br>
    ・<b>最安値比</b> = 下落開始後の最安値からの上昇率。プラスが大きい=既に底から反発が進行。<br>
    ・<b>信用倍率</b> = 買い残÷売り残（週次・<a href="margin.html" style="color:#60a5fa">銘柄チェッカー</a>と同データ）。
    下落中に倍率が下がっていく=投げ・期日決済で買い残が減り需給が軽くなっている。<br>
    ・<b>過去10年の深い下落の癖</b> = 高値から-30%超まで沈んだエピソードの回数・高値→最安値の中央値日数・中央値の深さ。
    ソフトバンクGのように大型下落を繰り返す銘柄は「半年前後で底」の癖が読める。
    浅い下落（-15〜30%）は数週間で片付くことが多く癖として別物のため、深い下落だけを表示（カッコ内は-15%超の全エピソード数）。<br>
    ・<b>営業赤字フィルタ</b> = 直近四半期の営業利益が赤字の銘柄は除外（<a href="margin.html" style="color:#60a5fa">銘柄チェッカー</a>の週次業績データより）。
    業績の裏付けなく下がっている銘柄は需給が改善しても戻りが弱いため。
    営業利益の概念がない銀行・投資会社等は判定不能のため残している。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def zone_class(cal_days):
    if cal_days < 120:
        return "zone-early", "下落初期"
    if cal_days < 180:
        return "zone-tunnel", "需給最悪期"
    if cal_days < 360:
        return "zone-passed", "期日通過"
    return "zone-long", "長期低迷"


def fmt_margin_cells(series):
    """10週の倍率をテキストで（最新は太字・前週比で色）"""
    parts = []
    prev = None
    for _, v in series:
        if v is None:
            parts.append('<span class="dim">-</span>')
        else:
            color = ""
            if prev is not None:
                color = ' style="color:#4ade80"' if v < prev else (' style="color:#f87171"' if v > prev else "")
            parts.append(f"<span{color}>{v:.1f}</span>")
            prev = v
    if parts:
        parts[-1] = f"<b>{parts[-1]}</b>"
    return " ".join(parts)


def generate_html(rows, weeks):
    rows.sort(key=lambda r: -r["cal_days"])
    week_labels = sorted(weeks.keys())
    latest_week = week_labels[-1] if week_labels else "-"

    trs = []
    for r in rows:
        zcls, zlabel = zone_class(r["cal_days"])
        kuse = r["kuse"]
        if kuse.get("deep_n"):
            # 深い下落（-30%超）の癖をメインに表示
            kuse_s = (f'<b>{kuse["deep_n"]}回</b> / {kuse.get("deep_med_days", "-")}日 / {kuse.get("deep_med_dd", "-")}%'
                      f' <span class="dim">(全下落{kuse.get("n", 0)}回)</span>')
        elif kuse.get("n"):
            kuse_s = f'<span class="dim">深い下落なし (全下落{kuse["n"]}回 / {kuse.get("med_days", "-")}日 / {kuse.get("med_dd", "-")}%)</span>'
        else:
            kuse_s = '<span class="dim">-</span>'
        kj = r["kijitsu_in"]
        kj_s = (f'{r["kijitsu_date"]} <span class="dim">あと{kj}日</span>' if kj > 0
                else f'{r["kijitsu_date"]} <span class="pos">通過</span>')
        off = r["off_trough"]
        off_s = f'<span class="{"pos" if off > 0 else ""}">{off:+.1f}%</span>'
        trs.append(
            f'      <tr><td>{r["code"]}</td><td>{r["name"]}</td>'
            f'<td>{r["mcap"]:,}</td>'
            f'<td>{r["price"]:,.0f}</td>'
            f'<td>{r["peak_date"]}</td>'
            f'<td class="dim">{r.get("dc_date", "-")}</td>'
            f'<td><span class="chip {zcls}">{r["cal_days"]}日</span></td>'
            f'<td>{kj_s}</td>'
            f'<td class="neg">{r["dd"]:+.1f}%</td>'
            f'<td>{r["trough_date"]}</td>'
            f'<td class="neg">{r["trough_dd"]:+.1f}%</td>'
            f'<td>{off_s}</td>'
            f'<td style="text-align:left; font-size:0.74rem">{fmt_margin_cells(margin_series(weeks, r["code"]))}</td>'
            f'<td style="text-align:left">{kuse_s}</td></tr>')

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        min_mcap=MIN_MCAP_OKU,
        dd_th=int(abs(DD_THRESHOLD)),
        n_hits=len(rows),
        margin_week=latest_week,
        rows="\n".join(trs),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")

    # TradingView用リスト（Minerviniと同形式・表と同じ順=経過日数の長い順）
    tv_path = os.path.join(SCRIPT_DIR, "txt", "Japan Kijitsu.txt")
    os.makedirs(os.path.dirname(tv_path), exist_ok=True)
    with open(tv_path, "w", encoding="utf-8") as f:
        f.write("\n".join(r["code"] + "\t," for r in rows) + "\n")
    log(f"TradingViewリスト出力: {tv_path}（{len(rows)}銘柄）")


# -----------------------------------------
# push
# -----------------------------------------
def push_to_github():
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "kijitsu_screen.py", "kijitsu_run.bat", "kijitsu_kuse_cache.json",
                    ".gitignore"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update kijitsu report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/kijitsu.html")
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
    log("信用期日スクリーナー 開始" + ("（テスト: 50銘柄）" if test else ""))

    codes, names, caps, weeks = load_universe()
    log(f"対象ユニバース: {len(codes)}銘柄（時価総額{MIN_MCAP_OKU}億以上）")
    if test:
        codes = codes[:50]

    kuse = load_kuse_cache(codes)
    rows = screen_current(codes, names, caps, kuse)

    if not rows:
        log("該当銘柄なし。HTMLは生成しません")
        return

    generate_html(rows, weeks)

    if "--nopush" in sys.argv or test:
        log("push スキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
