# -*- coding: utf-8 -*-
"""
グローバル資金フロー（テーマ別リターン・ヒートマップ） → HTML出力 → GitHub Pages公開

データ源: Yahoo Finance (yfinance)
各資産クラス・テーマETFの騰落率を 5/10/15営業日・1ヶ月(21)・3ヶ月(63)・年初来 で計算し、
「世界のマネーがどこへ向かっているか」をヒートマップ表示する。

- 毎日実行（米国市場の引け後、朝8:20）
- 1ヶ月リターンで降順ソート
- プラス=緑の濃淡、マイナス=赤の濃淡
- 履歴保存は不要（毎回yfinanceから1年分取得して計算）
"""

import os
import sys
import json
import time
import subprocess
import datetime

import yfinance as yf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_HTML = "flow.html"

# (ticker, 表示名, グループ)
# 資産クラスはこの記載順で固定表示（株式: 米国→日本→アジア → 債券 → 現物資産 → 通貨 → REIT）
# テーマは3営業日リターンの降順で表示
THEMES = [
    # 資産クラス: 株式(米国)
    ("^GSPC",    "S&P500",           "資産クラス"),
    ("^DJI",     "NYダウ",            "資産クラス"),
    ("^IXIC",    "NASDAQ",           "資産クラス"),
    # 株式(日本)
    ("^N225",    "日経平均",          "資産クラス"),
    ("1306.T",   "TOPIX",            "資産クラス"),
    ("2516.T",   "東証グロース250",     "資産クラス"),
    # 株式(アジア・新興国)
    ("MCHI",     "中国株",            "資産クラス"),
    ("INDA",     "インド株",          "資産クラス"),
    ("VNM",      "ベトナム株",         "資産クラス"),
    ("EEM",      "新興国株",          "資産クラス"),
    # 債券
    ("TLT",      "米長期債",          "資産クラス"),
    # 現物資産・暗号資産
    ("GLD",      "金",               "資産クラス"),
    ("BTC-USD",  "ビットコイン",       "資産クラス"),
    # 通貨・不動産
    ("DX-Y.NYB", "ドル指数",          "資産クラス"),
    ("VNQ",      "REIT(米不動産)",    "資産クラス"),
    # テーマ・セクター（表示は3営業日降順）
    ("MAGS",     "マグニフィセント7",   "テーマ"),
    ("SMH",      "半導体",            "テーマ"),
    ("XLK",      "テック",            "テーマ"),
    ("IGV",      "ソフトウェア/SaaS",  "テーマ"),
    ("ITA",      "防衛",             "テーマ"),
    ("XLE",      "エネルギー(石油)",    "テーマ"),
    ("XLF",      "金融",             "テーマ"),
    ("XLV",      "ヘルスケア",         "テーマ"),
    ("XLU",      "公益(守り)",        "テーマ"),
    ("GDX",      "金鉱株",            "テーマ"),
    ("URA",      "ウラン・原子力",      "テーマ"),
    ("ARKX",     "宇宙",             "テーマ"),
    ("QTUM",     "量子コンピューター",   "テーマ"),
    ("JETS",     "航空",             "テーマ"),
    ("IYT",      "鉄道・運輸",         "テーマ"),
    ("PEJ",      "エンタメ・レジャー",   "テーマ"),
    ("ESPO",     "ゲーム・eスポーツ",   "テーマ"),
    ("XRT",      "小売",              "テーマ"),
    ("SLX",      "鉄鋼",              "テーマ"),
    ("XLP",      "生活必需品(食品)",    "テーマ"),
    ("CARZ",     "自動車",            "テーマ"),
    ("XLB",      "素材・化学",         "テーマ"),
    ("XLI",      "資本財(機械)",       "テーマ"),
    # テーマ（日本）: TOPIX-17業種ETF + 特別枠（半導体）
    ("2644.T",   "半導体（日本）",      "テーマ（日本）"),
    ("1617.T",   "食品",             "テーマ（日本）"),
    ("1618.T",   "エネルギー資源",     "テーマ（日本）"),
    ("1619.T",   "建設・資材",         "テーマ（日本）"),
    ("1620.T",   "素材・化学",         "テーマ（日本）"),
    ("1621.T",   "医薬品",            "テーマ（日本）"),
    ("1622.T",   "自動車・輸送機",     "テーマ（日本）"),
    ("1623.T",   "鉄鋼・非鉄",         "テーマ（日本）"),
    ("1624.T",   "機械",             "テーマ（日本）"),
    ("1625.T",   "電機・精密",         "テーマ（日本）"),
    ("1626.T",   "情報通信・サービス",  "テーマ（日本）"),
    ("1627.T",   "電力・ガス",         "テーマ（日本）"),
    ("1629.T",   "商社・卸売",         "テーマ（日本）"),
    ("1630.T",   "小売（日本）",       "テーマ（日本）"),
    ("1631.T",   "銀行",             "テーマ（日本）"),
    ("1632.T",   "金融（除く銀行）",    "テーマ（日本）"),
    ("1633.T",   "不動産（日本）",     "テーマ（日本）"),
]

# 日本の特別枠バスケット（専用ETFがないテーマは個別株の等ウェイト平均で自前計算）
# (表示名, [構成銘柄], 表示ticker)
# 運輸・物流(1628)は特徴の違う4テーマ（航空/海運/鉄道/物流）に分解して表示
JP_BASKETS = [
    ("造船", ["7014.T", "7003.T", "6016.T"], "7014+7003+6016"),          # 名村造船・三井E&S・ジャパンエンジン
    ("重工・防衛", ["7011.T", "7012.T", "7013.T"], "7011+7012+7013"),     # 三菱重工・川重・IHI
    ("航空（日本）", ["9201.T", "9202.T"], "9201+9202"),                  # JAL・ANA
    ("海運", ["9101.T", "9104.T", "9107.T"], "9101+9104+9107"),          # 日本郵船・商船三井・川崎汽船
    ("鉄道", ["9020.T", "9021.T", "9022.T"], "9020+9021+9022"),          # JR東日本・JR西日本・JR東海
    ("物流", ["9064.T", "9143.T"], "9064+9143"),                         # ヤマトHD・SGHD
    ("ゲーム（日本）", ["7974.T", "7832.T", "9697.T", "9684.T", "9766.T"],
     "7974+7832+9697他"),                                               # 任天堂・バンナム・カプコン・スクエニ・コナミ
]

PERIODS = [("3営業日", 3), ("5営業日", 5), ("10営業日", 10), ("15営業日", 15),
           ("1ヶ月", 21), ("3ヶ月", 63)]  # + 年初来は別計算


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# リターン計算
# -----------------------------------------
def _calc_returns(close):
    """終値Seriesから各期間の騰落率dictを返す（データ不足はNone）"""
    out = {}
    for label, n in PERIODS:
        if len(close) > n:
            out[label] = round((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100, 2)
        else:
            out[label] = None
    year = close.index[-1].year
    ytd_base = close[close.index.year < year]
    out["年初来"] = round((close.iloc[-1] / ytd_base.iloc[-1] - 1) * 100, 2) if len(ytd_base) else None
    return out


def fetch_returns():
    basket_tickers = [t for _, members, _ in JP_BASKETS for t in members]
    tickers = [t for t, _, _ in THEMES] + basket_tickers
    data = yf.download(tickers, period="2y", auto_adjust=True,
                       progress=False, group_by="ticker", threads=True)

    rows = []
    for t, name, group in THEMES:
        try:
            close = data[t]["Close"].dropna()
        except Exception:
            log(f"  {name}({t}): データなし、スキップ")
            continue
        if len(close) < 70:
            log(f"  {name}({t}): データ不足、スキップ")
            continue

        rec = {"ticker": t, "name": name, "group": group,
               "last_date": close.index[-1].strftime("%Y-%m-%d"),
               "price": float(close.iloc[-1])}
        rec.update(_calc_returns(close))
        rows.append(rec)

    # 特別枠バスケット: 構成銘柄の騰落率の等ウェイト平均
    for name, members, disp in JP_BASKETS:
        member_rets = []
        last_date = None
        for t in members:
            try:
                close = data[t]["Close"].dropna()
            except Exception:
                log(f"  {name}構成 {t}: データなし、スキップ")
                continue
            if len(close) < 70:
                continue
            member_rets.append(_calc_returns(close))
            last_date = close.index[-1].strftime("%Y-%m-%d")
        if not member_rets:
            log(f"  {name}: 構成銘柄が全滅、スキップ")
            continue
        rec = {"ticker": disp, "name": name, "group": "テーマ（日本）",
               "last_date": last_date, "price": None}
        for c in [label for label, _ in PERIODS] + ["年初来"]:
            vals = [m[c] for m in member_rets if m[c] is not None]
            rec[c] = round(sum(vals) / len(vals), 2) if vals else None
        rows.append(rec)
    return rows


# -----------------------------------------
# HTML出力
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>グローバル資金フロー - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 22px 0 10px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .table-wrap {{ overflow-x: auto; max-width: 980px; }}
  table {{ border-collapse: collapse; font-size: 0.86rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 12px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child {{ text-align: left; }}
  td {{
    padding: 7px 12px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{ text-align: left; font-weight: 600; }}
  td.tk {{ color: #64748b; font-size: 0.78rem; text-align: left; }}
  tr:hover td {{ filter: brightness(1.25); }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.8; }}
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
    <a href="flow.html" class="active" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
    <a href="insider.html" style="border-color:#db2777">インサイダー売買</a>
    <a href="margin.html" style="border-color:#db2777">銘柄別信用倍率</a>
    <a href="buffett.html" style="border-color:#db2777">バフェット</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>グローバル資金フロー</h1>
  <p class="subtitle">最終更新: {updated} | 出所: Yahoo Finance | 各テーマETF/指数の騰落率(%) | 資産クラス=固定順 / テーマ=3営業日リターン降順</p>
{sections}
  <p class="note">
    ・世界のマネーがどの資産・テーマに向かっているかを騰落率で観察する。緑=資金流入（上昇）、赤=資金流出（下落）。<br>
    ・<b>対指数相対（デフォルト表示）</b> = テーマの騰落から指数（米国=S&amp;P500、日本=日経平均）の騰落を差し引いた相対力。
    緑=指数より強い、赤=指数より弱い。指数が下げていても相対が緑なら「資金が逃げ込んでいる」業種、
    指数が上げているのに赤なら「置いていかれている」業種。トップダウンの業種絞り込みはこちらで見る。<br>
    ・5/10/15営業日の並びで「加速中か失速中か」が分かる（右肩上がりの緑=ローテーション初動、5営業日だけ赤=直近で変調）。<br>
    ・1ヶ月=21営業日、3ヶ月=63営業日。年初来は前年末終値比。米国テーマETFは米国上場のため為替の影響を含む。<br>
    ・テーマ（日本）はTOPIX-17業種ETF（1617〜1633）+ 半導体（2644 グローバルX半導体）。
    運輸・物流(1628)は特徴の違う4テーマに分解: 航空（JAL・ANA）/ 海運（郵船・商船三井・川崎汽船）/ 鉄道（JR東・JR西・JR東海）/ 物流（ヤマト・SGHD）。
    これらと造船（名村造船・三井E&S・ジャパンエンジン）・重工・防衛（三菱重工・川重・IHI）・
    ゲーム（任天堂・バンダイナムコ・カプコン・スクエニ・コナミ）は専用ETFがないため個別株の等ウェイト平均で自前計算。<br>
    ・米国→日本の対応の目安: 半導体↔半導体、テック/ソフトウェア↔情報通信・サービス、防衛↔重工・防衛、エネルギー(石油)↔エネルギー資源、
    金融↔銀行/金融、ヘルスケア↔医薬品、公益↔電力・ガス、航空↔航空、鉄道・運輸↔鉄道/物流、小売↔小売・商社、ゲーム・eスポーツ↔ゲーム、
    鉄鋼↔鉄鋼・非鉄、生活必需品(食品)↔食品、自動車↔自動車・輸送機、素材・化学↔素材・化学、資本財(機械)↔機械。同じテーマが両国で緑なら世界的な資金流入。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""

COLS = ["3営業日", "5営業日", "10営業日", "15営業日", "1ヶ月", "3ヶ月", "年初来"]


def cell(v):
    if v is None:
        return "<td>-</td>"
    # 緑/赤の濃淡: ±15%で最大濃度
    alpha = min(0.6, abs(v) / 15 * 0.6)
    color = "34,197,94" if v > 0 else "239,68,68"
    style = f' style="background:rgba({color},{alpha:.2f})"' if abs(v) >= 0.05 else ""
    return f"<td{style}>{v:+.2f}%</td>"


def cell_rel(v):
    """相対力セル（対指数）: 絶対リターンより振れが小さいので±8%で最大濃度"""
    if v is None:
        return "<td>-</td>"
    alpha = min(0.6, abs(v) / 8 * 0.6)
    color = "34,197,94" if v > 0 else "239,68,68"
    style = f' style="background:rgba({color},{alpha:.2f})"' if abs(v) >= 0.05 else ""
    return f"<td{style}>{v:+.2f}%</td>"


def rel_value(v, vb):
    """対指数の相対リターン: (1+テーマ)/(1+指数)-1"""
    if v is None or vb is None:
        return None
    return round(((1 + v / 100) / (1 + vb / 100) - 1) * 100, 2)


def build_ratio_html(by_ticker):
    """倍率ダッシュボード（NS/NT/NG/TG）: 日米・市場の優位性をトップダウンで確認"""
    pairs = [
        ("NS倍率", "^N225", "^GSPC", "日経÷S&P500", "上昇=日本株優位 / 下落=米国株優位"),
        ("NT倍率", "^N225", "1306.T", "日経÷TOPIX", "上昇=日経寄与の大型株優位 / 下落=バリュー・高配当優位"),
        ("NG倍率", "^N225", "2516.T", "日経÷グロース250", "下落=中小型グロース優位（夏枯れ・年末に下がりやすい）"),
        ("TG倍率", "1306.T", "2516.T", "TOPIX÷グロース250", "下落=中小型グロース優位"),
    ]
    body = []
    for name, num_tk, den_tk, desc, yomi in pairs:
        num, den = by_ticker.get(num_tk), by_ticker.get(den_tk)
        if not num or not den:
            continue
        cur = num["price"] / den["price"]
        tds = "".join(cell_rel(rel_value(num.get(c), den.get(c))) for c in COLS)
        body.append(f'      <tr><td>{name}</td><td class="tk">{desc}</td>'
                    f'<td style="font-weight:700">{cur:,.2f}</td>{tds}</tr>')
        body.append(f'      <tr><td colspan="10" style="color:#64748b; font-size:0.74rem; '
                    f'text-align:left; padding:0 12px 7px">└ {yomi}</td></tr>')
    header = "".join(f"<th>{c}</th>" for c in COLS)
    return (f'  <h2>倍率ダッシュボード — 日米・市場の優位性（トップダウンの入口）</h2>\n'
            f'  <div class="table-wrap">\n  <table>\n'
            f'    <thead><tr><th>倍率</th><th style="text-align:left">定義</th><th>現在値</th>{header}</tr></thead>\n'
            f'    <tbody>\n' + "\n".join(body) + '\n    </tbody>\n  </table>\n  </div>\n'
            f'  <p style="font-size:0.76rem; color:#64748b; margin:6px 0 0;">'
            f'変化率は分子÷分母の倍率の騰落（緑=分子優位へ、赤=分母優位へ）。'
            f'「日本株か米国株か → 日経かTOPIXかグロースか」を先に決めてから下のテーマに進む。</p>')


def generate_html(rows):
    theme_order = [t for t, _, _ in THEMES]  # 定義順
    by_ticker = {r["ticker"]: r for r in rows}
    header = "".join(f"<th>{c}</th>" for c in COLS)

    def table_html(grp_vals, cls, hidden=False):
        body = []
        for r, vals, cellfn in grp_vals:
            tds = "".join(cellfn(vals.get(c)) for c in COLS)
            body.append(f'      <tr><td>{r["name"]}</td><td class="tk">{r["ticker"]}</td>{tds}</tr>')
        style = ' style="display:none"' if hidden else ""
        return (f'  <div class="table-wrap {cls}"{style}>\n  <table>\n'
                f'    <thead><tr><th>テーマ</th><th style="text-align:left">ticker</th>{header}</tr></thead>\n'
                f'    <tbody>\n' + "\n".join(body) + '\n    </tbody>\n  </table>\n  </div>')

    sections = []

    # 資産クラス（定義順・絶対リターンのみ・トグル対象外）
    grp = sorted([r for r in rows if r["group"] == "資産クラス"],
                 key=lambda r: theme_order.index(r["ticker"]))
    sections.append('  <h2>資産クラス</h2>\n'
                    + table_html([(r, r, cell) for r in grp], "always-view"))

    # 倍率ダッシュボード
    sections.append(build_ratio_html(by_ticker))

    # 表示切り替えボタン（テーマ2表に効く。デフォルト=対指数相対）
    sections.append(
        '  <div style="margin:22px 0 2px; display:flex; gap:10px; align-items:center;">\n'
        '    <span style="font-size:0.8rem; color:#94a3b8">テーマの表示:</span>\n'
        '    <button id="btn-rel" onclick="setMode(true)" style="background:#1e40af; color:#bfdbfe; '
        'border:1px solid #3b82f6; border-radius:6px; padding:4px 14px; cursor:pointer; font-size:0.8rem">対指数相対（vs S&amp;P500 / vs 日経）</button>\n'
        '    <button id="btn-abs" onclick="setMode(false)" style="background:#1e293b; color:#60a5fa; '
        'border:1px solid #334155; border-radius:6px; padding:4px 14px; cursor:pointer; font-size:0.8rem">絶対リターン</button>\n'
        '  </div>')

    # テーマ2表: 絶対リターン + 対指数相対（トグル）
    for group, title, bench_tk, bench_label in [
            ("テーマ", "テーマ（米国）", "^GSPC", "S&P500"),
            ("テーマ（日本）", "テーマ（日本）", "^N225", "日経平均")]:
        grp = [r for r in rows if r["group"] == group]
        bench = by_ticker.get(bench_tk, {})
        abs_grp = sorted(grp, key=lambda r: (r.get("3営業日") is None, -(r.get("3営業日") or 0)))
        rel_vals = {r["ticker"]: {c: rel_value(r.get(c), bench.get(c)) for c in COLS} for r in grp}
        rel_grp = sorted(grp, key=lambda r: (rel_vals[r["ticker"]]["3営業日"] is None,
                                             -(rel_vals[r["ticker"]]["3営業日"] or 0)))
        sections.append(
            f'  <h2>{title} <span style="font-size:0.72rem; color:#64748b" class="rel-view">'
            f'（対{bench_label}の相対力 — TradingViewの「業種÷NI225」と同じ思想）</span></h2>\n'
            + table_html([(r, rel_vals[r["ticker"]], cell_rel) for r in rel_grp], "rel-view")
            + "\n"
            + table_html([(r, r, cell) for r in abs_grp], "abs-view", hidden=True))

    # トグル用スクリプト（デフォルト=相対。sections経由なので中括弧OK）
    sections.append("""  <script>
  function setMode(rel) {
    document.querySelectorAll('.abs-view').forEach(function(e){ e.style.display = rel ? 'none' : ''; });
    document.querySelectorAll('.rel-view').forEach(function(e){ e.style.display = rel ? '' : 'none'; });
    var on = 'background:#1e40af; color:#bfdbfe; border:1px solid #3b82f6; border-radius:6px; padding:4px 14px; cursor:pointer; font-size:0.8rem';
    var off = 'background:#1e293b; color:#60a5fa; border:1px solid #334155; border-radius:6px; padding:4px 14px; cursor:pointer; font-size:0.8rem';
    document.getElementById('btn-abs').style.cssText = rel ? off : on;
    document.getElementById('btn-rel').style.cssText = rel ? on : off;
  }
  </script>""")

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        sections="\n".join(sections),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")


# -----------------------------------------
# GitHub Pages 自動 push
# -----------------------------------------
def push_to_github():
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "flow_screen.py", "flow_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update flow report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/flow.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("グローバル資金フロー チェック開始")

    try:
        rows = fetch_returns()
    except Exception as e:
        log(f"エラー: データの取得に失敗しました: {e}")
        sys.exit(1)

    if len(rows) < 25:
        log(f"エラー: 取得テーマが少なすぎます（{len(rows)}件）")
        sys.exit(1)

    log(f"取得: {len(rows)}テーマ（基準日 {rows[0]['last_date']}）")

    generate_html(rows)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
