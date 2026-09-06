# -*- coding: utf-8 -*-
"""
かぶチワワ X自動投稿（朝の偵察 = 王様がフィールドの様子を告げる）

yorimae_screen.py の直後に走り、今朝の数値（yorimae_post.json）と
イベント予定（calendar_screen.build_events）から
  1. 王様のセリフ（投稿文）を組み立てる
  2. 1200×675 の専用カード画像を HTML→PNG で描く（Edge/Chrome ヘッドレス、なければ Playwright）
  3. drafts/YYYY-MM-DD/ に下書き（テキスト・HTML・PNG）を保存する
  4. x_config.json の post_enabled が true なら X に投稿し、返信でデッキのURLを付ける
を行う。

■ 言葉のルール（金商法・RPGの誠実さの両方の観点）
  - 観測だけを述べる。「買う」「売る」「狙う」「仕込む」「利確」「損切り」など
    売買を促す/宣言する動詞は使わない（FORBIDDEN_WORDS で機械的にチェックし、含まれていたら投稿を止める）
  - 王様は「フィールドの様子」を告げるだけで、かぶチワワ（=飼い主）の行動は決めない

■ 使い方
  python kabuchiwa_post.py                 通常運転（土日・祝日は自動スキップ。post_enabled に従う）
  python kabuchiwa_post.py --dry-run       投稿しない（下書きだけ作る）
  python kabuchiwa_post.py --force         土日祝チェックを無視して実行（動作確認用）
  python kabuchiwa_post.py --no-render     画像を作らない（文章の確認だけ）
  python kabuchiwa_post.py --data PATH     yorimae_post.json 以外のデータで試す

■ 設定 x_config.json（gitignore済み。x_config.example.json をコピーして作る）
  {
    "post_enabled": false,          ← true にすると実際に投稿する
    "reply_with_link": true,        ← 本文に URL を入れず、返信にデッキURLを付ける（URL入り本文はXで伸びにくいため）
    "premium": false,               ← X Premium 加入後 true にすると長文（最大 4000 文字）を許可
    "hashtags": "#寄り前 #日経平均",
    "consumer_key": "...", "consumer_secret": "...",
    "access_token": "...", "access_token_secret": "..."
  }
"""

import os
import re
import sys
import json
import glob
import shutil
import datetime
import subprocess
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

DATA_JSON = os.path.join(SCRIPT_DIR, "yorimae_post.json")
CONFIG_JSON = os.path.join(SCRIPT_DIR, "x_config.json")
DRAFTS_DIR = os.path.join(SCRIPT_DIR, "drafts")
LOG_TXT = os.path.join(SCRIPT_DIR, "kabuchiwa_post_log.txt")

DECK_URL = "https://ichikon77.github.io/minervini/yorimae.html"
DECK_URL_SHORT = "ichikon77.github.io/minervini"
CARD_W, CARD_H = 1200, 675

# 投稿文に入っていたら投稿を止める語（売買の宣言・推奨に読める語）
FORBIDDEN_WORDS = [
    "買い", "買う", "買え", "買お", "売り", "売る", "売れ", "売ろ", "狙", "仕込", "利確", "損切",
    "エントリー", "ロング", "ショート", "推奨", "おすすめ", "オススメ", "チャンス", "買場", "買い場", "売り場",
    "全力", "インしろ", "ポジ", "ホールド", "ナンピン",
]

# X の文字数カウント（CJK等は2、ラテン系は1）
MAX_WEIGHTED_FREE = 280
MAX_WEIGHTED_PREMIUM = 8000   # Premium の上限は 25,000 だが、カードと釣り合う長さに抑える


def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_TXT, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# -----------------------------------------
# 設定・データ
# -----------------------------------------
def load_config():
    cfg = {"post_enabled": False, "reply_with_link": True, "premium": False,
           "hashtags": "#寄り前 #日経平均"}
    if os.path.exists(CONFIG_JSON):
        try:
            cfg.update(json.load(open(CONFIG_JSON, encoding="utf-8")))
        except Exception as e:
            log(f"x_config.json の読み込み失敗（投稿はオフ扱い）: {e}")
    return cfg


def load_data(path):
    if not os.path.exists(path):
        log(f"データがありません: {path}（yorimae_screen.py が先に走っている必要があります）")
        return None
    d = json.load(open(path, encoding="utf-8"))
    # 古いデータで投稿しない（yorimae が失敗した朝に前日の数値を流す事故の防止）
    try:
        gen = datetime.datetime.fromisoformat(d["generated"])
        if (datetime.datetime.now() - gen).total_seconds() > 6 * 3600:
            log(f"データが古すぎます（生成 {gen:%m/%d %H:%M}）→ 投稿スキップ")
            return None
    except Exception:
        pass
    return d


def is_market_holiday(today):
    """土日は休み。祝日は jpholiday があれば判定（無ければ土日のみ）。年末年始も休み"""
    if today.weekday() >= 5:
        return True
    if (today.month == 12 and today.day == 31) or (today.month == 1 and today.day <= 3):
        return True
    try:
        import jpholiday
        return bool(jpholiday.is_holiday(today))
    except Exception:
        return False


# -----------------------------------------
# X の文字数
# -----------------------------------------
def weighted_len(text):
    """X の weighted length。ラテン文字・記号など一部の範囲は1、それ以外（日本語・絵文字）は2"""
    n = 0
    for ch in text:
        o = ord(ch)
        if (0 <= o <= 0x10FF) or (0x2000 <= o <= 0x200D) or (0x2010 <= o <= 0x201F) or (0x2032 <= o <= 0x2037):
            n += 1
        else:
            n += 2
    return n


def fmt_signed(v, digits=2, unit="%"):
    if v is None:
        return "-"
    return f"{v:+.{digits}f}{unit}"


# -----------------------------------------
# 今日のイベント（calendar_screen の手動リストを流用）
# -----------------------------------------
def today_events(today):
    """(表示名, 種別) のリスト。今日のイベント + 「昨日の米国夜〜今朝」に結果が出たもの"""
    try:
        import calendar_screen
        ev = calendar_screen.build_events(today)
    except Exception as e:
        log(f"calendar_screen の読み込み失敗（イベント無しで続行）: {e}")
        return []
    out = []
    yesterday = today - datetime.timedelta(days=1)
    for d, flag, name, note, _link, major in ev:
        if d == yesterday:
            # 米国側で昨日=日本時間の今朝に結果が出たもの
            if "FOMC" in name:
                out.append(("けさ FOMCの結果が 出た", "us_morning"))
            elif "決算" in name and "🇰🇷" not in flag:
                out.append((f"けさ {name}が 出た", "us_morning"))
            continue
        if d != today:
            continue
        if "FOMC" in name:
            out.append(("FOMC（結果は明朝）", "boss"))
        elif "日銀" in name:
            out.append(("日銀会合（昼ごろ）", "boss"))
        elif "SQ" in name:
            out.append((("メジャーSQ" if "メジャー" in name else "SQ"), "boss"))
        elif "トリプルウィッチング" in name:
            out.append(("米トリプルウィッチング", "boss"))
        elif "雇用統計" in name:
            out.append(("米雇用統計 21:30", "boss"))
        elif "CPI" in name:
            out.append(("米CPI 21:30", "boss"))
        elif "休場" in name:
            out.append(("今夜 米国市場は 休み", "info"))
        elif "決算" in name and "🇰🇷" in flag:
            out.append((f"{name}（場中）", "boss"))
        elif "決算" in name:
            out.append((f"{name}（明朝）", "boss"))
        elif "水星逆行" in name:
            out.append((name, "info"))
        else:
            out.append((name, "boss" if major else "info"))
    return out


# -----------------------------------------
# 王様のセリフ（投稿文）
# -----------------------------------------
def build_judgement(d):
    """理論ギャップとの乖離の読みを (王様の一言, 状態ラベル, 色クラス) で返す"""
    gap, theo, dev = d.get("gap_pct"), d.get("theo_gap"), d.get("dev")
    th = d.get("dev_th", 0.5)
    if gap is None or theo is None or dev is None:
        return ("理論値との 答え合わせは できなかった", "判定不能", "mute")
    if abs(dev) <= th:
        return ("米国の動きで 説明がつく。日本固有の要因は みられぬ", "理論通り", "ok")
    if dev > 0:
        return (f"理論より {abs(dev):.2f}% 高い。夜のうちに 日本株に 追い風が 吹いたようじゃ", "日本買い方向", "warn")
    return (f"理論より {abs(dev):.2f}% 低い。夜のうちに 日本株に 向かい風が 吹いたようじゃ。用心せよ", "日本売り方向", "warn")


def gap_phrase(d):
    fut, gap, yen = d.get("fut_last"), d.get("gap_pct"), d.get("gap_yen")
    if fut is None or gap is None:
        return "夜間先物は 霧の中で 見えぬ（取得失敗）"
    if gap >= 1.0:
        mood = "大きく 上で 寄りそうじゃ"
    elif gap >= 0.3:
        mood = "上で 寄りそうじゃ"
    elif gap <= -1.0:
        mood = "大きく 下で 寄りそうじゃ"
    elif gap <= -0.3:
        mood = "下で 寄りそうじゃ"
    else:
        mood = "ほぼ 横で 寄りそうじゃ"
    return f"日経先物は {fut:,.0f}円（{gap:+.2f}%・{yen:+,.0f}円）。{mood}"


def us_phrase(d):
    spx, ndx, fx, fxc = d.get("spx_ret"), d.get("ndx_ret"), d.get("fx_now"), d.get("fx_chg")
    parts = []
    if spx is not None:
        if abs(spx) >= 1.5:
            verb = "大きく うごいた" if spx > 0 else "大きく くずれた"
        elif abs(spx) >= 0.5:
            verb = "うごいた"
        else:
            verb = "しずかじゃった"
        parts.append(f"米国株群は {spx:+.2f}% {verb}")
    if fx is not None and fxc is not None:
        if fxc >= 0.5:
            parts.append(f"ドル円は {fx:.2f}円へ 円安に 走った")
        elif fxc <= -0.5:
            parts.append(f"ドル円は {fx:.2f}円へ 円高に 走った")
        else:
            parts.append(f"ドル円は {fx:.2f}円")
    return "。".join(parts)


def adr_phrase(d, n=2):
    adr = [r for r in d.get("adr") or [] if r.get("gap") is not None]
    if not adr:
        return ""
    ups = [r for r in adr if r["gap"] >= 0.5][:n]
    dns = [r for r in sorted(adr, key=lambda r: r["gap"]) if r["gap"] <= -0.5][:n]
    parts = []
    if ups:
        parts.append("ADRでは " + " ".join(f'{short_name(r["name"])}{r["gap"]:+.1f}%' for r in ups) + " が つよい")
    if dns:
        parts.append(" ".join(f'{short_name(r["name"])}{r["gap"]:+.1f}%' for r in dns) + " が よわい")
    return "。".join(parts)


def event_phrase(events):
    bosses = [e for e, k in events if k == "boss"]
    infos = [e for e, k in events if k in ("info", "us_morning")]
    parts = []
    if bosses:
        parts.append("今日は " + "、".join(bosses) + " が あらわれる")
    if infos:
        parts.append("。".join(infos))
    return "。".join(parts)


def compose_text(d, events, cfg):
    """優先順位つきの行を組み、文字数上限に収まるまで下位の行を落とす"""
    max_len = MAX_WEIGHTED_PREMIUM if cfg.get("premium") else MAX_WEIGHTED_FREE
    judge, _label, _cls = build_judgement(d)
    hashtags = (cfg.get("hashtags") or "").strip()

    # (優先度が高いほど残す, 行)
    lines = [
        (0, "おお かぶチワワよ、今朝の フィールドの様子じゃ。"),
        (1, gap_phrase(d) + "。"),
        (2, judge + "。"),
        (3, event_phrase(events) + "。" if events else ""),
        (4, us_phrase(d) + "。"),
        (5, adr_phrase(d) + "。" if adr_phrase(d) else ""),
        (6, hashtags),
    ]
    lines = [(p, s) for p, s in lines if s and s != "。"]

    def render(active):
        body = [s for p, s in lines if p in active]
        return "\n".join(body)

    active = set(p for p, _ in lines)
    text = render(active)
    # 上限（無料枠は日本語140字相当）を超えたら優先度の低い行から順に落とす。
    # ADR → 米株/ドル円 → ハッシュタグ → イベント → 判定 の順（先物ギャップと判定は最後まで残す）
    drop_order = [5, 4, 6, 3, 2]
    for p in drop_order:
        if weighted_len(text) <= max_len:
            break
        active.discard(p)
        text = render(active)
    if weighted_len(text) > max_len:
        # それでも長い場合は末尾を強制カット
        while weighted_len(text) > max_len - 1 and len(text) > 1:
            text = text[:-1]
        text += "…"
    return text


def check_forbidden(text):
    hits = [w for w in FORBIDDEN_WORDS if w in text]
    return hits


# -----------------------------------------
# カード画像（HTML → PNG）
# -----------------------------------------
KING_PIXELS = """
....YY.YY.YY....
....YYYYYYYY....
....YBYYYYBY....
....YYYYYYYY....
...KSSSSSSSSK...
...KSKSSSSKSK...
...KSSSSSSSSK...
...KWSSSSSSWK...
...KWWWWWWWWK...
....WWWWWWWW....
...RRRWWWWRRR...
..RRRRRWWRRRRR..
..RRRRRRRRRRRR..
..RRSRRRRRRSRR..
..RRSRRRRRRSRR..
..RRRRRRRRRRRR..
...RRRRRRRRRR...
...KKKKKKKKKK...
""".strip("\n").split("\n")

KING_PALETTE = {"Y": "#facc15", "B": "#3b82f6", "K": "#111111", "S": "#f5d0a9",
                "W": "#f8fafc", "R": "#dc2626"}


def king_svg(scale=9):
    rects = []
    for y, row in enumerate(KING_PIXELS):
        for x, ch in enumerate(row):
            if ch in KING_PALETTE:
                rects.append(f'<rect x="{x*scale}" y="{y*scale}" width="{scale}" height="{scale}" fill="{KING_PALETTE[ch]}"/>')
    w = len(KING_PIXELS[0]) * scale
    h = len(KING_PIXELS) * scale
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" shape-rendering="crispEdges" '
            f'xmlns="http://www.w3.org/2000/svg">{"".join(rects)}</svg>')


# デッキ共通のチワワロゴ（yorimae.html ヘッダと同じSVG）
CHIWAWA_SVG = ('<svg width="44" height="50" viewBox="0 0 32 36"><polygon points="3,1 13,9 2,15" fill="#262626"/>'
               '<polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/>'
               '<polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/>'
               '<ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/>'
               '<circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/>'
               '<circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/>'
               '<circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/>'
               '<ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/>'
               '<path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/>'
               '<path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>')

WEEKDAYS_JP = ["月", "火", "水", "木", "金", "土", "日"]

CARD_TEMPLATE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=DotGothic16&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ width: {W}px; height: {H}px; overflow: hidden; }}
  body {{
    background: #0f172a; color: #e2e8f0;
    font-family: 'DotGothic16', 'Yu Gothic UI', 'Meiryo', 'Segoe UI', sans-serif;
    position: relative;
  }}
  .grid {{ position: absolute; inset: 0; opacity: 0.35;
    background-image: linear-gradient(#1e293b 1px, transparent 1px), linear-gradient(90deg, #1e293b 1px, transparent 1px);
    background-size: 30px 30px; }}
  .head {{ position: absolute; left: 36px; top: 24px; right: 36px; display: flex; align-items: center; gap: 14px; }}
  .head .title {{ font-size: 30px; color: #f8fafc; letter-spacing: 1px; }}
  .head .title small {{ font-size: 18px; color: #94a3b8; margin-left: 14px; }}
  .head .date {{ margin-left: auto; font-size: 22px; color: #cbd5e1; }}
  .left {{ position: absolute; left: 36px; top: 96px; width: 420px; bottom: 60px; }}
  .king {{ position: absolute; left: 20px; top: 8px; }}
  .window {{
    position: absolute; left: 0; top: 190px; right: 0;
    background: #000; border: 4px solid #fff; border-radius: 6px; outline: 3px solid #000;
    padding: 18px 22px 20px; font-size: 22px; line-height: 1.55; color: #fff;
  }}
  .window::before {{ content: "＊「"; color: #fff; }}
  .window .who {{ position: absolute; top: -20px; left: 18px; background: #000; padding: 0 10px; font-size: 18px; color: #fde68a; }}
  .right {{ position: absolute; left: 490px; top: 96px; right: 36px; bottom: 60px; }}
  .big {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 16px 22px; }}
  .big .label {{ font-size: 17px; color: #94a3b8; }}
  .big .value {{ font-size: 52px; color: #f8fafc; line-height: 1.1; margin-top: 2px; font-variant-numeric: tabular-nums; }}
  .big .value span {{ font-size: 34px; margin-left: 14px; }}
  .judge {{ margin-top: 12px; display: flex; align-items: center; gap: 12px; font-size: 20px; }}
  .badge {{ padding: 4px 14px; border-radius: 999px; font-size: 19px; }}
  .badge.ok {{ background: rgba(59,130,246,0.2); color: #93c5fd; border: 1px solid #3b82f6; }}
  .badge.warn {{ background: rgba(248,113,113,0.18); color: #fca5a5; border: 1px solid #f87171; }}
  .badge.mute {{ background: #1e293b; color: #94a3b8; border: 1px solid #475569; }}
  .mini {{ display: flex; gap: 12px; margin-top: 14px; }}
  .mini > div {{ flex: 1; background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 10px 14px; }}
  .mini .label {{ font-size: 15px; color: #94a3b8; }}
  .mini .v {{ font-size: 27px; margin-top: 2px; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .adr {{ margin-top: 14px; font-size: 20px; line-height: 1.7; white-space: nowrap; }}
  .adr b {{ color: #94a3b8; font-weight: normal; margin-right: 8px; }}
  .boss {{ margin-top: 10px; font-size: 20px; color: #fde68a; line-height: 1.6; }}
  .boss .info {{ color: #cbd5e1; }}
  .pos {{ color: #4ade80; }} .neg {{ color: #f87171; }} .flat {{ color: #e2e8f0; }}
  .foot {{ position: absolute; left: 36px; right: 36px; bottom: 18px; display: flex; font-size: 17px; color: #64748b; }}
  .foot span:last-child {{ margin-left: auto; }}
</style></head>
<body>
<div class="grid"></div>
<div class="head">{chiwawa}<div class="title">かぶチワワ 寄り前偵察<small>王様のおつげ</small></div><div class="date">{date_s}</div></div>
<div class="left">
  <div class="king">{king}</div>
  <div class="window"><span class="who">王様</span>{speech}</div>
</div>
<div class="right">
  <div class="big">
    <div class="label">CME日経先物（夜間）→ 今朝の寄り付き目安　／　前日終値 {n225_prev}</div>
    <div class="value">{fut_s}<span class="{gap_cls}">{gap_s}</span></div>
    <div class="judge">理論 {theo_s} ／ 乖離 {dev_s} <span class="badge {judge_cls}">{judge_label}</span></div>
  </div>
  <div class="mini">
    <div><div class="label">S&amp;P500</div><div class="v {spx_cls}">{spx_s}</div></div>
    <div><div class="label">NASDAQ</div><div class="v {ndx_cls}">{ndx_s}</div></div>
    <div><div class="label">ドル円</div><div class="v">{fx_s} <span style="font-size:18px" class="{fxc_cls}">{fxc_s}</span></div></div>
  </div>
  <div class="adr">{adr_up}{adr_dn}</div>
  <div class="boss">{boss_s}</div>
</div>
<div class="foot"><span>{deck_url}/yorimae.html　毎朝7:15 自動更新（ADR15銘柄・答え合わせ履歴はデッキで）</span><span>@kabuchiwa</span></div>
</body></html>
"""


def _cls(v):
    if v is None:
        return "flat"
    return "pos" if v > 0 else ("neg" if v < 0 else "flat")


# 投稿文・カードで使う短い銘柄名（yorimae の表示名 → 株クラで通じる略称）
SHORT_NAMES = {
    "東京エレクトロン": "東エレク", "ソフトバンクG": "SBG", "三井住友FG": "三井住友",
    "みずほFG": "みずほ", "ファーストリテ": "ファストリ", "武田薬品": "武田", "野村HD": "野村",
}


def short_name(name):
    return SHORT_NAMES.get(name, name)


def build_card_html(d, events, today):
    judge_text, judge_label, judge_cls = build_judgement(d)
    fut, gap, yen = d.get("fut_last"), d.get("gap_pct"), d.get("gap_yen")
    adr = [r for r in d.get("adr") or [] if r.get("gap") is not None]
    ups = [r for r in adr if r["gap"] >= 0.3][:4]
    dns = [r for r in sorted(adr, key=lambda r: r["gap"]) if r["gap"] <= -0.3][:4]

    def adr_line(label, rows):
        if not rows:
            return ""
        s = "　".join(f'{short_name(r["name"])} <span class="{_cls(r["gap"])}">{r["gap"]:+.1f}%</span>' for r in rows)
        return f"<div><b>{label}</b>{s}</div>"

    bosses = [e for e, k in events if k == "boss"]
    infos = [e for e, k in events if k != "boss"]
    boss_s = ""
    if bosses:
        boss_s = "今日のボス： " + "、".join(bosses)
    if infos:
        boss_s += f'<div class="info">{"・".join(infos)}</div>'

    # 王様の短いセリフ（カード内は3行程度）
    speech = ("日経先物は " + gap_phrase(d).split("。")[-1]) if fut is not None else "夜間先物は 霧の中じゃ"
    speech = f"{speech}。{judge_text}。"

    return CARD_TEMPLATE.format(
        W=CARD_W, H=CARD_H, chiwawa=CHIWAWA_SVG, king=king_svg(),
        date_s=f"{today:%Y-%m-%d}（{WEEKDAYS_JP[today.weekday()]}）",
        speech=speech,
        n225_prev=f'{d["n225_prev"]:,.0f}円' if d.get("n225_prev") else "-",
        fut_s=f"{fut:,.0f}円" if fut is not None else "取得失敗",
        gap_cls=_cls(gap), gap_s=(f"{gap:+.2f}%（{yen:+,.0f}円）" if gap is not None else ""),
        theo_s=fmt_signed(d.get("theo_gap")), dev_s=fmt_signed(d.get("dev")),
        judge_cls=judge_cls, judge_label=judge_label,
        spx_cls=_cls(d.get("spx_ret")), spx_s=fmt_signed(d.get("spx_ret")),
        ndx_cls=_cls(d.get("ndx_ret")), ndx_s=fmt_signed(d.get("ndx_ret")),
        fx_s=(f'{d["fx_now"]:.2f}円' if d.get("fx_now") else "-"),
        fxc_cls=_cls(d.get("fx_chg")), fxc_s=fmt_signed(d.get("fx_chg")),
        adr_up=adr_line("ADR つよい", ups), adr_dn=adr_line("ADR よわい", dns),
        boss_s=boss_s, deck_url=DECK_URL_SHORT,
    )


def _find_browser():
    cands = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def render_card(html_path, png_path):
    """HTML → PNG。Edge/Chrome のヘッドレス（追加インストール不要）を優先、無ければ Playwright"""
    exe = _find_browser()
    if exe:
        # 別プロファイルを使う: 通常の Edge が起動中でも新規プロセスとして動かすため
        profile = os.path.join(tempfile.gettempdir(), "kabuchiwa_headless_profile")
        cmd = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
               "--disable-extensions", "--mute-audio",
               f"--user-data-dir={profile}",
               f"--window-size={CARD_W},{CARD_H}",
               "--virtual-time-budget=5000",        # Webフォントの読み込みを待つ
               f"--screenshot={png_path}",
               "file:///" + html_path.replace("\\", "/")]
        try:
            subprocess.run(cmd, capture_output=True, timeout=90)
            if os.path.exists(png_path) and os.path.getsize(png_path) > 1000:
                _crop(png_path)
                return True
            log(f"ブラウザのスクリーンショットが出ませんでした: {exe}")
        except Exception as e:
            log(f"ブラウザ起動失敗 {exe}: {e}")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": CARD_W, "height": CARD_H})
            page.goto("file:///" + html_path.replace("\\", "/"))
            page.wait_for_timeout(1500)
            page.screenshot(path=png_path)
            b.close()
        return os.path.exists(png_path)
    except Exception as e:
        log(f"Playwright も使えません: {e}")
    return False


def _crop(png_path):
    """ウィンドウ枠の分だけ大きく撮れた場合に 1200×675 へ切り詰める"""
    try:
        from PIL import Image
        im = Image.open(png_path)
        if im.size != (CARD_W, CARD_H):
            im = im.crop((0, 0, min(im.width, CARD_W), min(im.height, CARD_H)))
            im.save(png_path)
    except Exception:
        pass


# -----------------------------------------
# X 投稿
# -----------------------------------------
def upload_media_v2(ck, cs, at, ats, path):
    """X API v2 のチャンクアップロード。OAuth1.0a ユーザー認証（tweepy 同梱の requests_oauthlib を使う）。
    initialize(JSON) → append(multipart) → finalize の3段。画像は1チャンクで足りる（上限5MB/segment）"""
    import requests
    from requests_oauthlib import OAuth1
    auth = OAuth1(ck, cs, at, ats)
    base = "https://api.x.com/2/media/upload"
    data = open(path, "rb").read()
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    r = requests.post(f"{base}/initialize", auth=auth, timeout=60,
                      json={"media_type": mime, "total_bytes": len(data), "media_category": "tweet_image"})
    r.raise_for_status()
    media_id = r.json()["data"]["id"]
    chunk = 4 * 1024 * 1024
    for i in range(0, len(data), chunk):
        r = requests.post(f"{base}/{media_id}/append", auth=auth, timeout=120,
                          data={"segment_index": str(i // chunk)},
                          files={"media": ("card.png", data[i:i + chunk], mime)})
        r.raise_for_status()
    r = requests.post(f"{base}/{media_id}/finalize", auth=auth, timeout=60)
    r.raise_for_status()
    return media_id


def post_to_x(cfg, text, png_path, reply_text):
    import tweepy
    ck, cs = cfg["consumer_key"], cfg["consumer_secret"]
    at, ats = cfg["access_token"], cfg["access_token_secret"]
    client = tweepy.Client(consumer_key=ck, consumer_secret=cs,
                           access_token=at, access_token_secret=ats)
    media_ids = None
    if png_path and os.path.exists(png_path):
        media_id = None
        # v2 のメディアアップロード（2026年現行。initialize→append→finalize）→ 失敗時は旧 v1.1 を試す
        try:
            media_id = upload_media_v2(ck, cs, at, ats, png_path)
        except Exception as e:
            log(f"  v2 media upload 失敗: {e}")
            try:
                api = tweepy.API(tweepy.OAuth1UserHandler(ck, cs, at, ats))
                media_id = api.media_upload(filename=png_path).media_id
            except Exception as e2:
                log(f"  v1.1 media_upload も失敗: {e2}")
        if media_id:
            media_ids = [str(media_id)]
        else:
            log("  画像なしで投稿します")
    resp = client.create_tweet(text=text, media_ids=media_ids)
    tid = resp.data["id"]
    log(f"  投稿完了: https://x.com/kabuchiwa/status/{tid}")
    if reply_text:
        try:
            client.create_tweet(text=reply_text, in_reply_to_tweet_id=tid)
            log("  返信（デッキURL）完了")
        except Exception as e:
            log(f"  返信の投稿失敗: {e}")
    return tid


# -----------------------------------------
# main
# -----------------------------------------
def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args
    no_render = "--no-render" in args
    data_path = DATA_JSON
    if "--data" in args:
        data_path = args[args.index("--data") + 1]
    today = datetime.date.today()
    if "--date" in args:
        today = datetime.date.fromisoformat(args[args.index("--date") + 1])

    if any(a in args for a in ("--enable", "--disable", "--link-on", "--link-off", "--status")):
        # 投稿のオン/オフ・URL返信のオン/オフをコマンドで切り替える（x_config.json を手で編集しなくていいように）
        cfg = load_config()
        if "--enable" in args:
            cfg["post_enabled"] = True
        if "--disable" in args:
            cfg["post_enabled"] = False
        if "--link-on" in args:
            cfg["reply_with_link"] = True
        if "--link-off" in args:
            cfg["reply_with_link"] = False
        cfg.pop("_comment", None)
        if "--status" not in args or len(args) > 1:
            json.dump(cfg, open(CONFIG_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"投稿: {'オン（毎朝7:15に自動投稿）' if cfg.get('post_enabled') else 'オフ（下書きのみ）'}")
        print(f"デッキURLの返信: {'付ける（1件 $0.20）' if cfg.get('reply_with_link', True) else '付けない'}")
        print(f"Premium長文: {'オン' if cfg.get('premium') else 'オフ（140字相当に収める）'}")
        return

    if "--setup" in args:
        # 対話式に4つのキーを聞いて x_config.json を書く（JSONを手で編集しなくていいように）
        cfg = load_config()
        print("X Developer Console の「キーとトークン」で表示された値を、順に貼り付けて Enter を押してください。")
        print("（右クリックで貼り付け。空のまま Enter すると今の値を残します）\n")
        prompts = [
            ("consumer_key", "1/4  API Key（コンシューマーキーの1つ目）"),
            ("consumer_secret", "2/4  API Key Secret（コンシューマーキーの2つ目）"),
            ("access_token", "3/4  Access Token（アクセストークンの1つ目）"),
            ("access_token_secret", "4/4  Access Token Secret（アクセストークンの2つ目）"),
        ]
        for key, label in prompts:
            v = input(f"{label}: ").strip().strip('"').strip("'")
            if v:
                cfg[key] = v
        for k in ("_comment",):
            cfg.pop(k, None)
        json.dump(cfg, open(CONFIG_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n保存しました: {CONFIG_JSON}（post_enabled={cfg.get('post_enabled')}）")
        print("続けて疎通確認をします...\n")
        args.append("--check")

    if "--check" in args:
        # キーの疎通確認だけ（投稿しない）。x_config.json のキーで誰としてログインできるかを表示
        cfg = load_config()
        missing = [k for k in ("consumer_key", "consumer_secret", "access_token", "access_token_secret")
                   if not cfg.get(k) or "developer.x.com" in str(cfg.get(k)) or "同 " in str(cfg.get(k))]
        if missing:
            print(f"x_config.json に未入力のキーがあります: {missing}")
            return
        try:
            import tweepy
        except ImportError:
            print("tweepy が入っていません → pip install tweepy")
            return
        try:
            client = tweepy.Client(consumer_key=cfg["consumer_key"], consumer_secret=cfg["consumer_secret"],
                                   access_token=cfg["access_token"], access_token_secret=cfg["access_token_secret"])
            me = client.get_me()
            print(f"OK: @{me.data.username}（{me.data.name}）として認証できました。")
            print("次は x_config.json の post_enabled を true にすれば、翌朝7:15から投稿されます。")
        except Exception as e:
            print(f"認証失敗: {e}")
            print("→ App permissions が Read and write になっているか、Access Token を権限変更の後に生成し直したかを確認してください。")
        return

    log("かぶチワワ 朝の偵察投稿 開始")
    if not force and is_market_holiday(today):
        log(f"{today} は休場日 → スキップ")
        return

    cfg = load_config()
    d = load_data(data_path)
    if d is None:
        return

    events = today_events(today)
    text = compose_text(d, events, cfg)
    hits = check_forbidden(text)

    out_dir = os.path.join(DRAFTS_DIR, today.isoformat())
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "yorimae_post.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    log(f"投稿文（{weighted_len(text)}/{MAX_WEIGHTED_PREMIUM if cfg.get('premium') else MAX_WEIGHTED_FREE}）:\n" + text)

    html_path = os.path.join(out_dir, "yorimae_card.html")
    png_path = os.path.join(out_dir, "yorimae_card.png")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build_card_html(d, events, today))
    rendered = False
    if not no_render:
        rendered = render_card(html_path, png_path)
        log(f"カード画像: {'OK ' + png_path if rendered else '生成できず（画像なし）'}")

    if hits:
        log(f"⚠ 禁止語を検出したため投稿しません: {hits}")
        return
    if dry_run or not cfg.get("post_enabled"):
        log("下書きのみ（post_enabled=false または --dry-run）")
        return
    if not all(cfg.get(k) for k in ("consumer_key", "consumer_secret", "access_token", "access_token_secret")):
        log("x_config.json に API キーが揃っていないため投稿しません")
        return

    try:
        import tweepy  # noqa: F401
    except ImportError:
        log("tweepy が入っていません → pip install tweepy を実行してください（今回は下書きのみ）")
        return

    reply = None
    if cfg.get("reply_with_link", True):
        reply = f"偵察の詳細（ADR15銘柄・理論ギャップの答え合わせ履歴）はこちらの巻物に。\n{DECK_URL}"
    try:
        post_to_x(cfg, text, png_path if rendered else None, reply)
    except Exception as e:
        log(f"投稿失敗: {e}")

    # 下書きは30日分だけ残す
    for p in sorted(glob.glob(os.path.join(DRAFTS_DIR, "20*")))[:-30]:
        shutil.rmtree(p, ignore_errors=True)
    log("完了")


if __name__ == "__main__":
    main()
