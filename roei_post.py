# -*- coding: utf-8 -*-
"""
情報漏洩銘柄 株価ウォッチ（夕方のX自動投稿）

roei_screen.py が書いた roei_post.json を読み、ニュース番組の体で
  1. 本日場中に公表した銘柄の、引けまでの反応（当日）
  2. 今日が「公表後 1営業日後」の銘柄の騰落
  3. 本日いちばん下げた追跡中銘柄（前日終値比）
  4. 本日引け後に公表された新規事案（数字なし・翌営業日が1営業日後）
（全事案中央値・Xの投稿数は本文に入れない。空行なしの詰めた形式＝ユーザー指定 10/5）
を1本にまとめ、「不正アクセス、サイバー攻撃、個人情報流出には十分にお気をつけください。」で締め、
速報カード画像（drafts/roei_card_YYYYMMDD.png）を添えて投稿する。ハッシュタグは付けない（検索語は締めの文に含む）。
Xの投稿数はカード画像のフッターにだけ残す。

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
CLOSING = "不正アクセス、サイバー攻撃、個人情報流出には十分にお気をつけください。"   # ハッシュタグは使わない（Xは不要と公言・多用は減点）。検索語は本文に自然に含める
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

    lines = []
    # 1) 本日場中に公表 → 引けまでの反応（当日）
    same = [r for r in d["incidents"] if r.get("same_day") and r["same_day"].get("date") == mday and r["same_day"].get("excess") is not None]
    if same:
        lines.append("本日場中に公表した銘柄の、引けまでの騰落率は、")
        for i, r in enumerate(same):
            sd = r["same_day"]
            q = f"（{r['time_basis']}から場中と推定）" if str(r.get("time_basis", "")).startswith("報道初出") else ""
            lines.append(f"{r['name']}（{r['code']}）{pct(sd['stock'])}（TOPIX比 {pct(sd['excess'])}）{q}{'。' if i == len(same) - 1 else '、'}")
    # 2) 今日が「1営業日後」（day0 == market_day）
    first = [r for r in inc if r.get("day0") == mday and r["ret"].get("1")]
    if first:
        lines.append("公表後、初めて終値がついた銘柄（1営業日後）の騰落率は、")
        for i, r in enumerate(first):
            c = r["ret"]["1"]
            ex = f"（TOPIX比 {pct(c['excess'])}）" if c.get("excess") is not None else ""
            lines.append(f"{r['name']}（{r['code']}）{pct(c['stock'])}{ex}{'。' if i == len(first) - 1 else '、'}")
    # 3) 本日の最大下落（追跡中=30営業日以内）
    tracked = [r for r in inc if r.get("last") and r["last"].get("chg1d") is not None and r["last"]["days"] <= 30 and r["last"]["date"] == mday]
    worst = min(tracked, key=lambda r: r["last"]["chg1d"]) if tracked else None
    if worst and worst["last"]["chg1d"] < 0:
        wd = datetime.date.fromisoformat(worst["date"])
        lines.append(f"本日、特に下げた情報漏洩銘柄は、{wd.month}/{wd.day}に公表した{worst['name']}（{worst['code']}）で、前日比 {pct(worst['last']['chg1d'])}（公表前比 {pct(worst['last']['stock'])}、{worst['last']['days']}日目）でした。")
    elif tracked:
        best = max(tracked, key=lambda r: r["last"]["chg1d"])
        lines.append(f"本日は追跡中{len(tracked)}銘柄に目立った下げはなく、最も戻したのは{best['name']}（{best['code']}）の前日比 {pct(best['last']['chg1d'])}でした。")
    # 4) 本日引け後に公表（翌営業日がまだ来ていない）新規
    pending = [r for r in d["incidents"] if r.get("ok") and not r.get("ret") and r.get("err") == "翌営業日がまだ来ていない"
               and not (r.get("same_day") and r["same_day"].get("date") == mday)]
    adds = pending
    if adds:
        names = "、".join(f"{r['name']}（{r['code']}・{r.get('type', '')}{'・報道より自動検出' if r.get('auto') else ''}）" for r in adds)
        lines.append(f"本日引け後の公表など、まだ終値がついていない新規銘柄は {names} です。")
    body = "\n".join([head] + lines + [CLOSING])
    return body, bool(first or same or adds or worst)


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
        print(f"情報漏洩ウォッチの夕方投稿: {'オン（月〜金 20:00ごろ・休場日は自動スキップ）' if cfg.get('roei_post_enabled') else 'オフ（下書きのみ）'}")
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
    hits = kp.check_forbidden(text)
    if hits:
        log(f"⚠ 禁止語を検出したため投稿しません: {hits}")
        return
    pngs = [os.path.join(DRAFTS_DIR, f"roei_card_{today:%Y%m%d}.png")]   # 1枚だけ添付（2枚は視認性が悪い＝ユーザー判断 10/6。_2.png は作るが付けない）
    pngs = [p for p in pngs if os.path.exists(p)]
    log(f"  添付画像 {len(pngs)} 枚: {[os.path.basename(p) for p in pngs]}")
    cfg = kp.load_config()
    if "--dry-run" in args or not cfg.get("roei_post_enabled"):
        log("下書きのみ（roei_post_enabled=false または --dry-run）")
        return
    if not worth:
        log("今日は報告する動きがないため投稿しません")
        return
    try:
        kp.post_to_x(cfg, text, pngs, None)
    except Exception as e:
        log(f"投稿失敗: {e}")
    log("完了")


if __name__ == "__main__":
    main()
