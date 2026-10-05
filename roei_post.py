# -*- coding: utf-8 -*-
"""
情報漏洩銘柄 株価ウォッチ（夕方のX自動投稿）

roei_screen.py が書いた roei_post.json を読み、ニュース番組の体で
  1. 今日が「事故発表後 1営業日目」の銘柄の騰落
  2. 本日いちばん下げた追跡中銘柄（前日終値比）
  3. 本日追加された新規事案（明日が初日）
  4. Xで今いちばん投稿が増えている語
を1本にまとめ、速報カード画像（drafts/roei_card_YYYYMMDD.png）を添えて投稿する。

投稿のオン/オフは x_config.json の "roei_post_enabled"（既定 false＝下書きのみ）。
  python roei_post.py --enable / --disable / --status
  python roei_post.py --dry-run   （下書きだけ）  --force（休場日チェック無視）
王様の朝の投稿（kabuchiwa_post.py）と同じ認証・同じ禁止語チェックを使う。
"""

import os
import sys
import json
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import kabuchiwa_post as kp   # 認証・投稿・禁止語・休場判定を共用

DATA_JSON = os.path.join(SCRIPT_DIR, "roei_post.json")
DRAFTS_DIR = os.path.join(SCRIPT_DIR, "drafts")
HASHTAGS = "#情報漏洩 #不正アクセス #サイバー攻撃 #個人情報流出"
DECK_URL = "https://ichikon77.github.io/minervini/roei.html"


def log(msg):
    kp.LOG_TXT = os.path.join(SCRIPT_DIR, "roei_post_log.txt")
    kp.log(msg)


def pct(v, d=1):
    return f"{v:+.{d}f}%"


def compose(d, today):
    inc = [r for r in d["incidents"] if r.get("ok") and r.get("ret")]
    mday = d.get("market_day") or today.isoformat()
    md = datetime.date.fromisoformat(mday)
    head = f"{md.month}/{md.day}（{'月火水木金土日'[md.weekday()]}）の「情報漏洩銘柄」株価ウォッチのお時間です。"

    # 1) 今日が1営業日目（day0 == market_day）
    first = [r for r in inc if r.get("day0") == mday and r["ret"].get("1")]
    lines = []
    if first:
        lines.append("事故の公表後、本日が最初の取引日となった銘柄の騰落率は、")
        for r in first:
            c = r["ret"]["1"]
            ex = f"（TOPIX比 {pct(c['excess'])}）" if c.get("excess") is not None else ""
            lines.append(f"・{r['name']}（{r['code']}）{pct(c['stock'])}{ex}　{r.get('service', '')}")
    # 2) 本日の最大下落（追跡中=30営業日以内）
    tracked = [r for r in inc if r.get("last") and r["last"].get("chg1d") is not None and r["last"]["days"] <= 30 and r["last"]["date"] == mday]
    worst = min(tracked, key=lambda r: r["last"]["chg1d"]) if tracked else None
    if worst and worst["last"]["chg1d"] < 0:
        wd = datetime.date.fromisoformat(worst["date"])
        lines.append(f"本日、特に下げた情報漏洩銘柄は、{wd.month}/{wd.day}に公表した{worst['name']}（{worst['code']}）で、前日比 {pct(worst['last']['chg1d'])}（公表前比 {pct(worst['last']['stock'])}、{worst['last']['days']}日目）でした。")
    elif tracked:
        best = max(tracked, key=lambda r: r["last"]["chg1d"])
        lines.append(f"本日は追跡中{len(tracked)}銘柄に目立った下げはなく、最も戻したのは{best['name']}（{best['code']}）の前日比 {pct(best['last']['chg1d'])}でした。")
    # 3) 本日追加（公表日=今日、または day0 がまだ来ていない）
    # 今日公表で、まだ市場が反応していないもの（引け後公表 or 反応日未到来）。今日が初日のものは上で報告済み
    first_codes = {r["code"] for r in first}
    new = [r for r in d["incidents"] if r.get("date") == today.isoformat() and r["code"] not in first_codes and r.get("day0") != mday]
    pending = [r for r in d["incidents"] if r.get("ok") and not r.get("ret") and r.get("err") == "反応日がまだ来ていない"]
    adds = new or pending
    if adds:
        names = "、".join(f"{r['name']}（{r['code']}・{r.get('type', '')}）" for r in adds)
        lines.append(f"本日公表の新規銘柄は {names} です。明日、公表後初日の結果をお伝えします。")
    # 4) 全事案の中央値
    g = d.get("summary", {}).get("全事案", {})
    c1, c5 = g.get("1"), g.get("5")
    if c1:
        s = f"9月以降の{c1['n']}件の中央値は、公表翌日 {pct(c1['med'])}（TOPIX比）"
        if c5:
            s += f"、5営業日後 {pct(c5['med'])}（N={c5['n']}）"
        lines.append(s + "。")
    # 5) Xの語
    w = (d.get("words") or [None])[0]
    if w and w.get("ratio"):
        lines.append(f"Xで今週いちばん投稿が増えている語は「{w['label']}」、直近7日で{w['last7']:,}件（前3週平均の{w['ratio']:.1f}倍）です。")
    body = "\n".join([head, ""] + lines + ["", "買え売れは申しません。観測と答え合わせのみ。", HASHTAGS])
    return body, bool(first or adds or worst)


def main():
    args = sys.argv[1:]
    if any(a in args for a in ("--enable", "--disable", "--status")):
        cfg = kp.load_config()
        if "--enable" in args:
            cfg["roei_post_enabled"] = True
        if "--disable" in args:
            cfg["roei_post_enabled"] = False
        cfg.pop("_comment", None)
        json.dump(cfg, open(kp.CONFIG_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"情報漏洩ウォッチの夕方投稿: {'オン（毎日16:30ごろ）' if cfg.get('roei_post_enabled') else 'オフ（下書きのみ）'}")
        return
    today = datetime.date.today()
    if "--date" in args:
        today = datetime.date.fromisoformat(args[args.index("--date") + 1])
    force = "--force" in args
    log("情報漏洩 株価ウォッチ投稿 開始")
    if not force and kp.is_market_holiday(today):
        log(f"{today} は休場日 → スキップ")
        return
    if not os.path.exists(DATA_JSON):
        log("roei_post.json がありません（roei_screen.py を先に実行）")
        return
    d = json.load(open(DATA_JSON, encoding="utf-8"))
    gen = datetime.datetime.fromisoformat(d["generated"])
    if (datetime.datetime.now() - gen).total_seconds() > 6 * 3600 and not force:
        log("データが古いので投稿しません")
        return
    text, worth = compose(d, today)
    os.makedirs(os.path.join(DRAFTS_DIR, today.isoformat()), exist_ok=True)
    with open(os.path.join(DRAFTS_DIR, today.isoformat(), "roei_post.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    log(f"投稿文（{kp.weighted_len(text)}）:\n{text}")
    hits = kp.check_forbidden(text.replace("買え売れは申しません", ""))
    if hits:
        log(f"⚠ 禁止語を検出したため投稿しません: {hits}")
        return
    cfg = kp.load_config()
    if "--dry-run" in args or not cfg.get("roei_post_enabled"):
        log("下書きのみ（roei_post_enabled=false または --dry-run）")
        return
    if not worth:
        log("今日は報告する動きがないため投稿しません")
        return
    png = os.path.join(DRAFTS_DIR, f"roei_card_{today:%Y%m%d}.png")
    try:
        kp.post_to_x(cfg, text, png if os.path.exists(png) else None, None)
    except Exception as e:
        log(f"投稿失敗: {e}")
    log("完了")


if __name__ == "__main__":
    main()
