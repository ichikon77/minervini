# -*- coding: utf-8 -*-
"""
半導体ゾンビ復活レポート — 戦士の夕方投稿（平日 18:00、semi_screen.py の直後に semi_run.bat から呼ばれる）

書き出しは固定「かぶチワワよ。今日も戦士として、半導体ゾンビと戦ってきたぞ。」（2026-10-25 ユーザー指定）。
中身は比喩を使わず「株価」「信用倍率」「X投稿数」の素の言葉で書く（知らない人が読んでも分かるように、ユーザー指摘 10/25）。
項目は毎日固定: ゾンビの数 → 株価が高値からいちばん落ちている銘柄 → 今日沈んだ3 → 浮いた2 → 信用倍率が平常比で膨らんだ/しぼんだ1ずつ →
復活度の最上位 →（あれば）今日 X で話題の銘柄 → 締め。売買の言葉は使わない（kabuchiwa_post.FORBIDDEN_WORDS で検査）。

投稿のオン/オフは x_config.json の "semi_post_enabled"（既定 false＝下書きのみ）。--enable/--disable/--status。
テスト: python semi_post.py --dry-run [--force] [--no-render] [--date YYYY-MM-DD]。やり直し: --force --repost
"""
import os
import sys
import json
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import kabuchiwa_post as kp   # 認証・投稿・禁止語・休場判定・カード描画を共用

DATA_JSON = os.path.join(SCRIPT_DIR, "semi_post.json")
DRAFTS_DIR = os.path.join(SCRIPT_DIR, "drafts")
POSTED_JSON = os.path.join(SCRIPT_DIR, "semi_posted.json")   # {日付: 投稿時刻} 二重投稿ガード（gitignore）
DECK_URL_SHORT = "ichikon77.github.io/minervini"
WD = "月火水木金土日"
OPENING = "かぶチワワよ。今日も戦士として、半導体ゾンビと戦ってきたぞ。"
CLOSING = "半導体ゾンビ全40体の詳細は 分析デッキの「半導体ゾンビ」で確認できるぞ。"   # compose() では n を埋めて生成


def log(msg):
    kp.LOG_TXT = os.path.join(SCRIPT_DIR, "semi_post_log.txt")
    kp.log(msg)


def pct(v, nd=1):
    return "-" if v is None else f"{v:+.{nd}f}%"


def mmdd(s):
    d = datetime.date.fromisoformat(s)
    return f"{d.month}/{d.day}"


# -----------------------------------------
# 投稿文
# -----------------------------------------
def compose(d, today=None):
    """2026-10-25 ユーザー指定フォーマット（短く・需給が主役・締めは戦士の一言）。
    「チャンス」は禁止語なので「波」に置き換え。倍率の高低は市場の慣用で「需給が悪化/改善」と言う（中身の買残/売残はカードで見せる）。
    株価の日付（asof）が今日でないとき（休日に --force で出す等）は「今日」を「10/23（金）」に置き換える"""
    s, by = d["summary"], {r["code"]: r for r in d["rows"]}
    asof = datetime.date.fromisoformat(s["asof"])
    day = "今日" if (today is None or asof == today) else f"{asof.month}/{asof.day}（{WD[asof.weekday()]}）"
    lines = [OPENING]
    zombies = [r for r in d["rows"] if r["zombie"]] or d["rows"]
    worst = min(zombies, key=lambda r: r["d1"])
    if worst["d1"] < 0:
        lines.append(f"{day}、いちばん株価を下げたゾンビは {worst['name']} 前日比{worst['d1']:+.1f}%（高値から {worst['dd']:+.0f}%）。")
    else:
        best = max(zombies, key=lambda r: r["d1"])
        lines.append(f"{day}は ゾンビが 1体も 下げなかった。いちばん上げたのは {best['name']} 前日比{best['d1']:+.1f}%（高値から {best['dd']:+.0f}%）。")
    heavy = [by[c] for c in s["heavy"][:3]]
    light = [by[c] for c in s["light"][:3]]
    if heavy:
        lines.append("制度信用倍率が ふだん（50営業日の中央値）より 高く、需給が悪化しているのは "
                     + "、".join(f"{r['name']} {r['ratio']:.1f}倍（ふだんの {r['exp50']:.1f}倍）" for r in heavy) + "。")
    if light:
        lines.append("逆に ふだんより 低く、需給が改善しているのは "
                     + "、".join(f"{r['name']} {r['ratio']:.1f}倍（ふだんの {r['exp50']:.1f}倍）" for r in light) + "。")
    # 話題度の行（「今日 Xで 騒がれているのは …」）は 2026-10-26 ユーザー指示で投稿文から外した。
    # 話題度そのものは semi.html の列・カードには残る。復活させるなら BUZZ_LINE=True
    if BUZZ_LINE and s.get("buzz_top"):
        b = by[s["buzz_top"][0]]
        lines.append(f"{day} Xで 騒がれているのは {b['name']}（投稿 {b['buzz_today']:,}件、ふだんの {b['buzz_spike']:.1f}倍）。株価 {b['d1']:+.1f}%、制度信用倍率 {b['ratio']:.1f}倍。")
    lines.append("狂暴化したゾンビにおてつきしたり、つぎの半導体の波に乗り遅れたりしないためにも、毎日の数値確認を怠らないようにしておけよ。")
    lines.append(f"半導体ゾンビ全{s['n']}体の詳細は 分析デッキの「半導体ゾンビ」で確認できるぞ。")
    return "\n".join(lines)


def chg_s(r):
    parts = []
    if r.get("buy10") is not None:
        parts.append(f"買残 {r['buy10']:+d}%")
    if r.get("sell10") is not None:
        parts.append(f"売残 {r['sell10']:+d}%")
    return "、10営業日で " + "・".join(parts) if parts else ""


# -----------------------------------------
# カード（1200×675）: 左に戦士＋ウィンドウ、右に「洞窟の深さ」横棒（高値からの下落率 上位12）
# -----------------------------------------
# カードの銘柄名は 9 文字まで（列幅 140px・16px）。10 文字以上だけ短縮
BUZZ_LINE = False   # 投稿文に「今日 Xで 騒がれているのは…」の行を入れるか（2026-10-26 オフ）
CARD_SHORT = {"さくらインターネット": "さくらネット", "テクセンドフォトマスク": "テクセンドFM", "ハーモニックドライブ": "ハーモニック"}   # 名前欄は9文字まで

WARRIOR_PIXELS = """
.....MMMMMM.....
....MMMMMMMM....
....MRRMMRRM....
....MMMMMMMM....
....SSSSSSSS....
....SKSSSSKS....
....SSSSSSSS....
.....SSSSSS.....
...RRRRRRRRRR.W.
..RRMMMMMMMMRRW.
..RRMMMMMMMMRRW.
..SSMMMMMMMMSSW.
..SSMMMMMMMMSSW.
....RRRRRRRR..W.
....BBBB.BBBB.W.
....BBBB.BBBB.W.
....BBBB.BBBB...
...KKKKK.KKKKK..
""".strip("\n").split("\n")
WARRIOR_PALETTE = {"M": "#9ca3af", "R": "#dc2626", "S": "#f5d0a9", "K": "#111111", "B": "#1e3a8a", "W": "#e5e7eb"}


def warrior_svg(scale=9):
    rects = []
    for y, row in enumerate(WARRIOR_PIXELS):
        for x, ch in enumerate(row):
            if ch in WARRIOR_PALETTE:
                rects.append(f'<rect x="{x*scale}" y="{y*scale}" width="{scale}" height="{scale}" fill="{WARRIOR_PALETTE[ch]}"/>')
    w, h = len(WARRIOR_PIXELS[0]) * scale, len(WARRIOR_PIXELS) * scale
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" shape-rendering="crispEdges" xmlns="http://www.w3.org/2000/svg">{"".join(rects)}</svg>'


CARD = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=DotGothic16&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ width: {W}px; height: {H}px; overflow: hidden; }}
  body {{ background: #0f172a; color: #e2e8f0; font-family: 'DotGothic16', 'Yu Gothic UI', 'Meiryo', sans-serif; position: relative; }}
  .grid {{ position: absolute; inset: 0; opacity: 0.35;
    background-image: linear-gradient(#1e293b 1px, transparent 1px), linear-gradient(90deg, #1e293b 1px, transparent 1px); background-size: 30px 30px; }}
  .head {{ position: absolute; left: 36px; top: 24px; right: 36px; height: 44px; display: flex; align-items: center; gap: 14px; white-space: nowrap; overflow: hidden; }}
  .head .title {{ font-size: 30px; color: #f8fafc; letter-spacing: 1px; }}
  .head .title small {{ font-size: 17px; color: #94a3b8; margin-left: 14px; }}
  .head .date {{ margin-left: auto; font-size: 20px; color: #cbd5e1; }}
  .left {{ position: absolute; left: 36px; top: 96px; width: 300px; bottom: 60px; }}
  .warrior {{ position: absolute; left: 10px; top: 8px; }}
  .window {{ position: absolute; left: 0; top: 190px; right: 0; background: #000; border: 4px solid #fff; border-radius: 6px; outline: 3px solid #000;
    padding: 12px 14px; font-size: 17px; line-height: 1.45; color: #fff; }}
  .window::before {{ content: "＊「"; }}
  .window .who {{ position: absolute; top: -20px; left: 18px; background: #000; padding: 0 10px; font-size: 18px; color: #fca5a5; }}
  .right {{ position: absolute; left: 364px; top: 96px; right: 36px; bottom: 56px; }}
  .defs {{ position: absolute; left: 0; right: 0; bottom: 0; font-size: 12px; color: #64748b; white-space: nowrap; overflow: hidden; }}
  .strip {{ position: absolute; left: 0; right: 0; top: 0; height: 30px; font-size: 16px; color: #cbd5e1; white-space: nowrap; overflow: hidden; }}
  .strip b {{ color: #f8fafc; font-weight: normal; }}
  .col {{ position: absolute; top: 38px; bottom: 24px; width: 392px; background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 10px 12px; overflow: hidden; }}
  .col.heavy {{ left: 0; }} .col.light {{ right: 0; }}
  .col h3 {{ font-size: 20px; font-weight: normal; white-space: nowrap; overflow: hidden; line-height: 1.2; }}
  .col .sub {{ font-size: 12px; color: #94a3b8; margin: 1px 0 4px; white-space: nowrap; overflow: hidden; }}
  .col.heavy h3 {{ color: #fca5a5; }} .col.light h3 {{ color: #86efac; }}
  table {{ width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 17px; line-height: 1.25; font-variant-numeric: tabular-nums; }}
  td {{ padding: 6px 2px; border-bottom: 1px solid #0f172a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  td.nm {{ color: #f8fafc; font-size: 16px; letter-spacing: -0.5px; }}
  td.rt {{ text-align: right; font-size: 20px; }}
  .heavy td.rt {{ color: #fca5a5; }} .light td.rt {{ color: #86efac; }}
  td.ex {{ text-align: right; font-size: 18px; color: #fde68a; }}
  td.r3 {{ text-align: right; color: #94a3b8; font-size: 15px; }}
  td.hd {{ color: #94a3b8; font-size: 12px; border-bottom: none; padding: 0 2px 2px; }}
  td.hd.r {{ text-align: right; }}
  .pos {{ color: #4ade80 !important; }} .neg {{ color: #f87171 !important; }}
  .foot {{ position: absolute; left: 36px; right: 36px; bottom: 14px; display: flex; align-items: baseline; font-size: 15px; color: #64748b; white-space: nowrap; overflow: hidden; }}
  .foot .url {{ font-size: 19px; color: #93c5fd; }}
  .foot .url b {{ color: #f8fafc; font-weight: normal; margin-right: 10px; }}
  .foot span:last-child {{ margin-left: auto; padding-left: 12px; }}
</style></head>
<body>
<div class="grid"></div>
<div class="head">{chiwawa}<div class="title">かぶチワワ 半導体ゾンビの洞窟<small>高値から落ちた銘柄の 信用倍率は いま重いか 軽いか</small></div><div class="date">{date_s}</div></div>
<div class="left">
  <div class="warrior">{warrior}</div>
  <div class="window"><span class="who">戦士</span>{speech}</div>
</div>
<div class="right">
  <div class="strip">{strip}</div>
  <div class="col heavy"><h3>信用倍率が ふだんより 高い</h3><div class="sub">制度信用倍率｜ふだん（50営業日の中央値）との比｜買残・売残の10営業日比</div>
    <table><colgroup><col><col style="width:56px"><col style="width:64px"><col style="width:54px"><col style="width:54px"></colgroup>
    <tr><td class="hd">銘柄</td><td class="hd r">倍率</td><td class="hd r">ふだん比</td><td class="hd r">買残10日</td><td class="hd r">売残10日</td></tr>{heavy_rows}</table></div>
  <div class="col light"><h3>信用倍率が ふだんより 低い</h3><div class="sub">制度信用倍率｜ふだん（50営業日の中央値）との比｜買残・売残の10営業日比</div>
    <table><colgroup><col><col style="width:56px"><col style="width:64px"><col style="width:54px"><col style="width:54px"></colgroup>
    <tr><td class="hd">銘柄</td><td class="hd r">倍率</td><td class="hd r">ふだん比</td><td class="hd r">買残10日</td><td class="hd r">売残10日</td></tr>{light_rows}</table></div>
  <div class="defs">半導体ゾンビ＝1年高値から−30%以上の銘柄　制度信用倍率＝制度買残÷制度売残　ふだん比＝直近50営業日の倍率中央値との比（売り残僅少の日は除く）　観測の記録であり判断材料ではない</div>
</div>
<div class="foot"><span class="url"><b>かぶチワワの分析デッキ「半導体ゾンビ」</b>https://{deck}/semi.html</span><span>需給: JPX {margin_date} 分　@kabuchiwa</span></div>
</body></html>
"""


def card_speech(d):
    s, by = d["summary"], {r["code"]: r for r in d["rows"]}
    zombies = [r for r in d["rows"] if r["zombie"]] or d["rows"]
    worst = min(zombies, key=lambda r: r["d1"])
    t = f"半導体ゾンビは {s['n_zombie']}/{s['n']}体。いちばん 下げたのは {worst['name']} {worst['d1']:+.1f}%（高値から {worst['dd']:+.0f}%）。"
    if s["heavy"]:
        h = by[s["heavy"][0]]
        t += f"<br>需給が 悪化しているのは {h['name']}（制度信用倍率 {h['ratio']:.1f}倍、ふだんの {h['exp50']:.1f}倍）。"
    if s["light"]:
        l = by[s["light"][0]]
        t += f"改善しているのは {l['name']}（ふだんの {l['exp50']:.1f}倍）。"
    t += "<br>毎日の 数値確認を 怠るな。」"
    return t


def card_html(d, today, top=6):
    s, by = d["summary"], {r["code"]: r for r in d["rows"]}
    ranked = [r for r in d["rows"] if r.get("exp50") is not None and r.get("ratio") is not None and not r.get("thin")]
    heavy = sorted(ranked, key=lambda r: -r["exp50"])[:top]
    light = sorted(ranked, key=lambda r: r["exp50"])[:top]

    def rows(lst):
        out = []
        for r in lst:
            b10 = "-" if r.get("buy10") is None else f"{r['buy10']:+d}%"
            s10 = "-" if r.get("sell10") is None else f"{r['sell10']:+d}%"
            out.append(f'<tr><td class="nm">{CARD_SHORT.get(r["name"], r["name"])}</td><td class="rt">{r["ratio"]:.1f}</td><td class="ex">{r["exp50"]:.1f}倍</td>'
                       f'<td class="r3">{b10}</td><td class="r3">{s10}</td></tr>')
        return "".join(out)
    import statistics
    med_ratio = statistics.median([r["ratio"] for r in ranked]) if ranked else 0
    med_exp = statistics.median([r["exp50"] for r in ranked]) if ranked else 0
    nk_cls = "pos" if s["nk_d1"] > 0 else "neg"
    strip = (f'日経 <b class="{nk_cls}">{s["nk_d1"]:+.2f}%</b>｜{s["n"]}銘柄: 今日 上げ <b>{sum(1 for r in d["rows"] if r["d1"] > 0)}</b>・下げ <b>{sum(1 for r in d["rows"] if r["d1"] < 0)}</b>・'
             f'安値更新 <b>{s.get("n_bottom", 0)}</b>｜制度信用倍率 中央値 <b>{med_ratio:.1f}倍</b>・ふだん比 <b>{med_exp:.2f}倍</b>')
    return CARD.format(W=kp.CARD_W, H=kp.CARD_H, chiwawa=kp.CHIWAWA_SVG, warrior=warrior_svg(9), speech=card_speech(d),
                       date_s=f"{today:%Y/%m/%d}（{WD[today.weekday()]}）", strip=strip, heavy_rows=rows(heavy), light_rows=rows(light),
                       deck=DECK_URL_SHORT, margin_date=mmdd(s["margin_date"]) if s.get("margin_date") else "-")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    args = sys.argv[1:]
    if any(a in args for a in ("--enable", "--disable", "--status")):
        cfg = kp.load_config()
        if "--enable" in args:
            cfg["semi_post_enabled"] = True
        if "--disable" in args:
            cfg["semi_post_enabled"] = False
        cfg.pop("_comment", None)
        json.dump(cfg, open(kp.CONFIG_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"半導体ゾンビ 戦士の夕方投稿: {'オン（平日 18:00・休場日は自動スキップ）' if cfg.get('semi_post_enabled') else 'オフ（下書きのみ）'}")
        return
    today = datetime.date.today()
    if "--date" in args:
        today = datetime.date.fromisoformat(args[args.index("--date") + 1])
    force = "--force" in args
    dry_run = "--dry-run" in args
    log("半導体ゾンビ 戦士の投稿 開始")
    if not force and kp.is_market_holiday(today):
        log(f"{today} は休場日 → スキップ")
        return
    if not os.path.exists(DATA_JSON):
        log("semi_post.json がありません（semi_screen.py を先に実行）")
        return
    d = json.load(open(DATA_JSON, encoding="utf-8"))
    gen = datetime.datetime.fromisoformat(d["generated"])
    if not force:
        if (datetime.datetime.now() - gen).total_seconds() > 6 * 3600:
            log(f"データが古い（{gen:%m/%d %H:%M}）→ 投稿しません")
            return
        if d["summary"]["asof"] != today.isoformat():
            log(f"株価が今日の終値ではない（{d['summary']['asof']}）→ 投稿しません（yfinance の遅延なら semi_run.bat を再実行）")
            return
    text = compose(d, today)
    out_dir = os.path.join(DRAFTS_DIR, today.isoformat())
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "semi_post.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    log(f"投稿文（{kp.weighted_len(text)}）:\n{text}")
    hits = kp.check_forbidden(text)
    if hits:
        log(f"⚠ 禁止語を検出したため投稿しません: {hits}")
        return
    png_path = None
    if "--no-render" not in args:
        html_path = os.path.join(out_dir, "semi_card.html")
        png_path = os.path.join(out_dir, "semi_card.png")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(card_html(d, datetime.date.fromisoformat(d["summary"]["asof"])))
        if kp.render_card(html_path, png_path):
            log(f"カード画像: OK {png_path}")
        else:
            log("カード画像: 生成できず（画像なしで続行）")
            png_path = None
    cfg = kp.load_config()
    if dry_run or not cfg.get("semi_post_enabled"):
        log("下書きのみ（semi_post_enabled=false または --dry-run）")
        return
    posted = {}
    if os.path.exists(POSTED_JSON):
        try:
            posted = json.load(open(POSTED_JSON, encoding="utf-8"))
        except Exception:
            posted = {}
    if posted.get(today.isoformat()) and not ("--repost" in args and force):
        log(f"本日は既に投稿済み（{posted[today.isoformat()]}）→ スキップ。やり直すなら --force --repost")
        return
    try:
        kp.post_to_x(cfg, text, png_path, None)
        posted[today.isoformat()] = datetime.datetime.now().isoformat(timespec="seconds")
        posted = {k: v for k, v in sorted(posted.items())[-60:]}
        json.dump(posted, open(POSTED_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"投稿失敗: {e}")
    log("完了")


if __name__ == "__main__":
    main()
