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

■ 2026-11-03 の方針変更（ユーザー判断）
  平日22:00の毎日投稿は廃止（10/28〜11/2 の中央値 31imp・4週でフォロー0）。roei_run.bat は従来どおり毎日走るが、
  この main() は DAILY_RETIRED=True のため --force が無いと投稿しない（大きな事案の日に手で出す逃げ道は残す:
  python roei_post.py --force [--lead CODE]）。
  代わりに **日曜 18:30 に「週まとめ」1本**: python roei_post.py --weekly（roei_week_run.bat → タスク RoeiWeekly）。
  今週の新規公表（件数順）／公表後初日の騰落（中央値・下げた社数・最大下落）／今週公表で公表前比が最悪・最良／
  全事案中央値（1・5・10営業日）／来週 初日を迎える銘柄、をニュース体で。カードは roei_screen.py --week-card の
  drafts/roei_week_YYYYMMDD.png。二重投稿ガードのキーは roei_posted.json の "week:月曜日付"。
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
POSTED_JSON = os.path.join(SCRIPT_DIR, "roei_posted.json")   # {日付: 投稿時刻} 二重投稿ガード用（gitignore対象）
CLOSING = "不正アクセス、サイバー攻撃、個人情報流出には十分にお気をつけください。"   # ハッシュタグは使わない（Xは不要と公言・多用は減点）。検索語は本文に自然に含める
DECK_URL = "https://ichikon77.github.io/minervini/roei.html"
DAILY_RETIRED = True   # 2026-11-03: 平日の毎日投稿を止める（--force で手動投稿は可）。週まとめ（--weekly）に集約
WD = "月火水木金土日"


def log(msg):
    kp.LOG_TXT = os.path.join(SCRIPT_DIR, "roei_post_log.txt")
    kp.log(msg)


def pct(v, d=1):
    return f"{v:+.{d}f}%"


def nm(r):
    """銘柄名（コード）。公表前後に決算発表が重なる銘柄には「※決算」を添える（騰落が漏洩だけの反応ではないことを示す、2026-11-03）"""
    if r.get("earn"):
        y, m, d = r["earn"].split("-")
        tag = f"※決算{int(m)}/{int(d)}"
    else:
        tag = ""
    return f"{r['name']}（{r['code']}{tag}）"


def compose(d, today, lead_code=None):
    """lead_code: 冒頭1行（見出し）にする事案の証券コードを手で指定（--lead 9449）。
       話題度の自動選択が見出しの略称（「GMO、…」等）で数え漏れたときの逃げ道（2026-10-27 GMOインターネットグループ）"""
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
            lines.append(f"{nm(r)}{pct(sd['stock'])}（TOPIX比 {pct(sd['excess'])}）{q}{'。' if i == len(same) - 1 else '、'}")
    # 2) 今日が「1営業日後」（day0 == market_day）
    first = [r for r in inc if r.get("day0") == mday and r["ret"].get("1")]
    if first:
        lines.append("公表後、初めて終値がついた銘柄（1営業日後）の騰落率は、")
        for i, r in enumerate(first):
            c = r["ret"]["1"]
            ex = f"（TOPIX比 {pct(c['excess'])}）" if c.get("excess") is not None else ""
            lines.append(f"{nm(r)}{pct(c['stock'])}{ex}{'。' if i == len(first) - 1 else '、'}")
    # 3) 本日の最大下落（追跡中=30営業日以内）
    tracked = [r for r in inc if r.get("last") and r["last"].get("chg1d") is not None and r["last"]["days"] <= 30 and r["last"]["date"] == mday]
    worst = min(tracked, key=lambda r: r["last"]["chg1d"]) if tracked else None
    if worst and worst["last"]["chg1d"] < 0:
        wd = datetime.date.fromisoformat(worst["date"])
        lines.append(f"本日、特に下げた情報漏洩銘柄は、{wd.month}/{wd.day}に公表した{nm(worst)}で、前日比 {pct(worst['last']['chg1d'])}（公表前比 {pct(worst['last']['stock'])}、{worst['last']['days']}日目）でした。")
    elif tracked:
        best = max(tracked, key=lambda r: r["last"]["chg1d"])
        lines.append(f"本日は追跡中{len(tracked)}銘柄に目立った下げはなく、最も戻したのは{nm(best)}の前日比 {pct(best['last']['chg1d'])}でした。")
    # 4) 本日引け後に公表（翌営業日がまだ来ていない）新規
    pending = [r for r in d["incidents"] if r.get("ok") and not r.get("ret") and r.get("err") == "翌営業日がまだ来ていない"
               and not (r.get("same_day") and r["same_day"].get("date") == mday)]
    adds = pending
    # 5) 冒頭1行: その日いちばん話題（直近24hの見出し数 buzz が最大、同点なら前日比の絶対値が大きい方）の事案を見出しにする
    #    （2026-10-10 ユーザー判断: 10/7の1042impは「話題の楽天」に数字がついたから。話題度を主、数字を従に。
    #     話題の事案にまだ終値がなければ「新規漏洩銘柄に A、B が追加。」の形。全事案 buzz=0 の静かな日は冒頭なし）
    lead = None
    hot = [r for r in d["incidents"] if r.get("ok") and (r.get("buzz") or 0) > 0]
    if lead_code:
        forced = [r for r in d["incidents"] if r.get("ok") and r.get("code") == lead_code]
        if forced:
            hot = [max(forced, key=lambda r: r["date"])]   # 同じコードが複数あれば最新の事案
            hot[0]["buzz"] = max(hot[0].get("buzz") or 0, 1)
        else:
            log(f"--lead {lead_code}: 該当する事案が無いので自動選択に戻します")
    if hot:
        def _chg(r):
            if r.get("last") and r["last"].get("chg1d") is not None and r["last"]["date"] == mday:
                return abs(r["last"]["chg1d"])
            if r.get("same_day") and r["same_day"].get("date") == mday and r["same_day"].get("stock") is not None:
                return abs(r["same_day"]["stock"])
            return 0
        top = max(hot, key=lambda r: (r["buzz"], _chg(r)))
        if top in adds:
            hot_adds = sorted(adds, key=lambda r: -(r.get("buzz") or 0))   # 話題度の高い順（先頭が見出し）
            names = "、".join(f"{r['name']}（{r['code']}）" for r in hot_adds[:3]) + (f" ほか{len(adds) - 3}社" if len(adds) > 3 else "")
            lead = f"新規漏洩銘柄に {names} が追加。"
        elif top in same:
            sd = top["same_day"]
            lead = f"{nm(top)}、公表当日の引けまでに前日比 {pct(sd['stock'])}（TOPIX比 {pct(sd['excess'])}）。"
        elif top.get("day0") == mday and top.get("ret", {}).get("1"):
            c = top["ret"]["1"]
            ex = f"（TOPIX比 {pct(c['excess'])}）" if c.get("excess") is not None else ""
            lead = f"{nm(top)}、公表後初日は {pct(c['stock'])}{ex}。"
        elif top.get("last") and top["last"].get("chg1d") is not None and top["last"]["date"] == mday:
            L = top["last"]
            ms = (top.get("ret") or {}).get(str(L["days"]))   # 5/10/15/30日目の節目なら TOPIX比も添える
            ex = f"、TOPIX比 {pct(ms['excess'])}" if ms and ms.get("excess") is not None else ""
            lead = f"{nm(top)}、公表{L['days']}日目で前日比 {pct(L['chg1d'])}（公表前比 {pct(L['stock'])}{ex}）。"
    if adds and not (lead and lead.startswith("新規漏洩銘柄に")):
        # 「報道より自動検出」は読者に不要な内部事情なので投稿文には出さない（2026-10-07 ユーザー判断。表・カードの「自動検出」タグは据え置き）
        names = "、".join(f"{r['name']}（{r['code']}・{r.get('type', '')}）" for r in adds)
        lines.append(f"本日引け後の公表など、まだ終値がついていない新規銘柄は {names} です。")
    body = "\n".join(([lead] if lead else []) + [head] + lines + [CLOSING])
    return body, bool(first or same or adds or worst)


def compose_weekly(d, today):
    """日曜 18:30 の週まとめ（2026-11-03 新設）。集計は roei_screen.week_stats と共用"""
    import roei_screen as rs
    ws = rs.week_stats(d["incidents"], today)
    mon, fri = ws["monday"], ws["friday"]
    ret1 = lambda r: (r.get("ret") or {}).get("1") or (r.get("ret") or {}).get(1)
    lines = []
    # 見出し: 今週いちばん件数の大きい新規事案（話題性の代わり）
    new = ws["new"]
    if new:
        top = new[0]
        sc = f"の{top['scale']}" if top.get("scale") and not str(top["scale"]).startswith("（") else ""
        lines.append(f"今週は {top['name']}（{top['code']}）{sc}をはじめ、{len(new)}社が情報漏洩を公表。")
    lines.append(f"{mon.month}/{mon.day}（月）〜{fri.month}/{fri.day}（金）の「情報漏洩銘柄」株価ウォッチ、週まとめのお時間です。")
    # 1) 今週の新規公表（件数順、最大6社）
    if new:
        names = "、".join(f"{r['name']}（{r['code']}" + (f"・{r['scale']}" if r.get("scale") and not str(r["scale"]).startswith("（") else "") + "）" for r in new[:6])
        more = f" ほか{len(new) - 6}社" if len(new) > 6 else ""
        lines.append(f"今週 新たに公表したのは {names}{more} です。")
    # 2) 今週 初日を迎えた銘柄の騰落（下げた順）
    first = ws["first"]
    if first:
        n = len(first)
        lines.append(f"今週、公表後 初めて終値がついた {n}社の騰落率は、中央値 {pct(ws['first_med'])}"
                     + (f"（TOPIX比 {pct(ws['first_med_ex'])}）" if ws["first_med_ex"] is not None else "") + f"、下げたのは {ws['first_neg']}社でした。")
        def fmt_r(r):
            c = ret1(r)
            ex = f"（TOPIX比 {pct(c['excess'])}）" if c.get("excess") is not None else ""
            return f"{nm(r)}{pct(c['stock'])}{ex}"
        neg = [r for r in first if ret1(r)["stock"] < 0][:4]
        pos = [r for r in reversed(first) if ret1(r)["stock"] > 0][:2]
        if neg:
            lines.append("下げたのは " + "、".join(fmt_r(r) for r in neg) + ("" if len(neg) == ws["first_neg"] else f" ほか{ws['first_neg'] - len(neg)}社") + "。")
        if pos:
            lines.append("上げたのは " + "、".join(fmt_r(r) for r in pos) + ("" if len(pos) == n - ws["first_neg"] else f" ほか{n - ws['first_neg'] - len(pos)}社") + "。")
    # 3) 今週公表の銘柄で、公表前比が最悪・最良（直近終値）
    w, b = ws["worst"], ws["best"]
    n_earn = ws.get("n_earn", 0)
    if w and w["last"]["stock"] < 0:
        lines.append(f"今週公表の銘柄で、公表前からの下げがいちばん大きいのは {nm(w)}{pct(w['last']['stock'])}（{w['last']['days']}日目）。"
                     + (f"いちばん戻しているのは {nm(b)}{pct(b['last']['stock'])}。" if b and b is not w and b["last"]["stock"] > 0 else "")
                     + (f"（決算発表が重なる{n_earn}社は除く）" if n_earn else ""))
    # 4) 全事案中央値
    g = d.get("summary", {}).get("全事案", {})
    med = []
    for k in ("1", "5", "10"):
        cc = g.get(k)
        if cc:
            med.append(f"{k}営業日後 {pct(cc['med'])}（N={cc['n']}・下落率{cc['neg']:.0f}%）")
    if med:
        lines.append("9月からの全事案の TOPIX比 中央値は、" + "、".join(med) + "。")
    # 5) 来週 初日を迎える銘柄
    pend = ws["pending"]
    if pend:
        names = "、".join(f"{r['name']}（{r['code']}・{r.get('type', '')}）" for r in pend[:6]) + (f" ほか{len(pend) - 6}社" if len(pend) > 6 else "")
        lines.append(f"来週、公表後 初めて終値がつくのは {names} です。")
    lines.append(CLOSING)
    return "\n".join(lines), bool(new or first)


def run_weekly(args, today):
    force = "--force" in args
    log("情報漏洩 週まとめ投稿 開始")
    if not force and today.weekday() != 6:
        log(f"{today}（{WD[today.weekday()]}）は日曜ではない → スキップ（テストは --force）")
        return
    if not os.path.exists(DATA_JSON):
        log("roei_post.json がありません（roei_screen.py を先に実行）")
        return
    d = json.load(open(DATA_JSON, encoding="utf-8"))
    gen = datetime.datetime.fromisoformat(d["generated"])
    if (datetime.datetime.now() - gen).total_seconds() > 6 * 3600 and not force:
        log("データが古いので投稿しません")
        return
    text, worth = compose_weekly(d, today)
    os.makedirs(os.path.join(DRAFTS_DIR, today.isoformat()), exist_ok=True)
    with open(os.path.join(DRAFTS_DIR, today.isoformat(), "roei_week.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    log(f"投稿文（{kp.weighted_len(text)}）:\n{text}")
    hits = kp.check_forbidden(text)
    if hits:
        log(f"⚠ 禁止語を検出したため投稿しません: {hits}")
        return
    pngs = [p for p in [os.path.join(DRAFTS_DIR, f"roei_week_{today:%Y%m%d}.png")] if os.path.exists(p)]
    log(f"  添付画像 {len(pngs)} 枚: {[os.path.basename(p) for p in pngs]}")
    cfg = kp.load_config()
    if "--dry-run" in args or not cfg.get("roei_post_enabled"):
        log("下書きのみ（roei_post_enabled=false または --dry-run）")
        return
    if not worth:
        log("今週は新規も初日もないため投稿しません")
        return
    import roei_screen as rs
    key = f"week:{rs.week_range(today)[0].isoformat()}"
    posted = {}
    if os.path.exists(POSTED_JSON):
        try:
            posted = json.load(open(POSTED_JSON, encoding="utf-8"))
        except Exception:
            posted = {}
    if posted.get(key) and not ("--repost" in args and force):
        log(f"この週は既に投稿済み（{posted[key]}）→ スキップ。やり直すなら --force --repost")
        return
    try:
        kp.post_to_x(cfg, text, pngs, None)
        posted[key] = datetime.datetime.now().isoformat(timespec="seconds")
        posted = {k: v for k, v in sorted(posted.items())[-60:]}
        json.dump(posted, open(POSTED_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"投稿失敗: {e}")
    log("完了")


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
        print(f"情報漏洩ウォッチの投稿: {'オン（日曜 18:30 週まとめ。平日の毎日投稿は 2026-11-03 廃止、--force で手動のみ）' if cfg.get('roei_post_enabled') else 'オフ（下書きのみ）'}")
        return
    today = datetime.date.today()
    if "--date" in args:
        today = datetime.date.fromisoformat(args[args.index("--date") + 1])
    force = "--force" in args
    if "--weekly" in args:
        run_weekly(args, today)
        return
    log("情報漏洩 株価ウォッチ投稿 開始")
    if DAILY_RETIRED and not force:
        log("平日の毎日投稿は 2026-11-03 に廃止 → 投稿しません（週まとめは日曜 18:30 の --weekly。大きな事案の日に手で出すなら --force [--lead CODE]）")
        return
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
    lead_code = args[args.index("--lead") + 1] if "--lead" in args and args.index("--lead") + 1 < len(args) else None
    text, worth = compose(d, today, lead_code=lead_code)
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
    # 二重投稿ガード（2026-10-08: 定時実行がハング→手動で止めて再実行、の場面で2回出るのを防ぐ）。--force --repost で無視
    posted = {}
    if os.path.exists(POSTED_JSON):
        try:
            posted = json.load(open(POSTED_JSON, encoding="utf-8"))
        except Exception:
            posted = {}
    if posted.get(today.isoformat()) and not ("--repost" in args and force):
        log(f"本日は既に投稿済み（{posted[today.isoformat()]}）→ 二重投稿を防ぐためスキップ。やり直すなら --force --repost")
        return
    try:
        kp.post_to_x(cfg, text, pngs, None)
        posted[today.isoformat()] = datetime.datetime.now().isoformat(timespec="seconds")
        posted = {k: v for k, v in sorted(posted.items())[-60:]}   # 直近60日分だけ保持
        json.dump(posted, open(POSTED_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"投稿失敗: {e}")
    log("完了")


if __name__ == "__main__":
    main()
