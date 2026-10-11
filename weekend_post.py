# -*- coding: utf-8 -*-
"""
かぶチワワ 週末の王様投稿（2本）

  weekly  : 土曜 8:00  「王様の週間成績表」  yorimae_history.json の1週間分を採点して答え合わせ
  preview : 日曜 18:30 「来週のフィールド予告」calendar_screen のイベントから来週分（休場日も）を告げる
  kazami  : 日曜 10:00 「航海士の風見表」  kazami_screen.py の結果（日経上昇日に順風/逆風の船 Top10）を航海士が届ける

使い方:
  python weekend_post.py weekly  [--dry-run] [--force] [--date YYYY-MM-DD] [--no-render]
  python weekend_post.py preview [--dry-run] [--force] [--date YYYY-MM-DD] [--no-render]
  python weekend_post.py kazami  [--dry-run] [--force] [--no-render]        （先に kazami_screen.py を実行）
  python weekend_post.py --enable / --disable / --status     （x_config.json の weekend_post_enabled）
    --force   : 曜日チェック（weekly=土曜のみ・preview=日曜のみ）と二重投稿ガードを無視
    --dry-run : 下書き（文とカード）だけ作って投稿しない
    --repost  : --force と併用で、同じ週に2回目を投稿する

投稿文・カードの描画・X API は kabuchiwa_post.py（朝の王様）を流用。
採点の定義は yorimae_screen.py の答え合わせ表（①〜⑬）と同じ:
  問い1 寄りの方向  : ⑤夜間先物ギャップ（|⑤|≥0.3%で上/下、未満は横）と ⑥実際の寄りギャップの向き。横と言った日は |⑥|<0.5% なら的中
  問い2 夜の風      : ⑩判定（乖離⑨の±0.5%）→ ⑬判定。「理論通り→理論通り」「追い風/向かい風→残った」を的中、
                      「埋まった」「日中に発生」「反転」を外れ
  ADRの流れ         : その朝 ADRギャップ±0.5%以上だった銘柄のうち、日中（寄→引）が同じ向きだった率
外れた日も消さずに並べる（固定ポストの約束）。
"""

import os
import sys
import json
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import kabuchiwa_post as kp  # noqa: E402

HISTORY_JSON = os.path.join(SCRIPT_DIR, "yorimae_history.json")
POSTED_JSON = os.path.join(SCRIPT_DIR, "weekend_posted.json")   # {"weekly:2026-10-10": "投稿時刻", ...} 二重投稿ガード（gitignore）
LOG_TXT = os.path.join(SCRIPT_DIR, "weekend_post_log.txt")
DECK_URL_SHORT = "ichikon77.github.io/minervini"
DEV_TH = 0.5   # yorimae_screen.DEVIATION_TH と同じ（乖離の判定しきい値 %）
GAP_TH = 0.3   # kabuchiwa_post.gap_phrase の「上/下」と「ほぼ横」の境
FLAT_HIT = 0.5  # 「ほぼ横」と言った日に、実際の寄りが±この%以内なら的中
ADR_TH = 0.5    # ADRギャップがこの%以上動いた銘柄だけ採点

WD = ["月", "火", "水", "木", "金", "土", "日"]

# yorimae_screen.ADR_LIST と同じ15銘柄（import すると yfinance を引くので写し）
ADR_NAMES = {"7203": "トヨタ", "6758": "ソニーG", "8306": "三菱UFJ", "8316": "三井住友FG", "8411": "みずほFG", "7267": "ホンダ",
             "4502": "武田薬品", "8604": "野村HD", "8591": "オリックス", "7974": "任天堂", "9984": "ソフトバンクG", "6501": "日立",
             "8035": "東京エレクトロン", "9983": "ファーストリテ", "6301": "コマツ"}

# 東証休場日の名前（jp_market_holidays.py は日付のみ）。無ければ「祝日」
HOLIDAY_NAMES = {
    "01-01": "元日", "01-02": "年始休業", "01-03": "年始休業", "12-31": "大納会翌日・年末休業",
    "02-11": "建国記念の日", "02-23": "天皇誕生日", "04-29": "昭和の日",
    "05-03": "憲法記念日", "05-04": "みどりの日", "05-05": "こどもの日", "05-06": "振替休日",
    "08-11": "山の日", "11-03": "文化の日", "11-23": "勤労感謝の日",
    "2026-01-12": "成人の日", "2026-03-20": "春分の日", "2026-07-20": "海の日", "2026-09-21": "敬老の日",
    "2026-09-22": "国民の休日", "2026-09-23": "秋分の日", "2026-10-12": "スポーツの日",
    "2027-01-11": "成人の日", "2027-03-21": "春分の日", "2027-03-22": "振替休日", "2027-07-19": "海の日",
    "2027-09-20": "敬老の日", "2027-09-23": "秋分の日", "2027-10-11": "スポーツの日",
}


def log(msg):
    kp.LOG_TXT = LOG_TXT
    kp.log(msg)


def holiday_name(d):
    return HOLIDAY_NAMES.get(d.isoformat()) or HOLIDAY_NAMES.get(d.strftime("%m-%d")) or "祝日"


def is_holiday(d):
    try:
        from jp_market_holidays import is_market_holiday
        return is_market_holiday(d)
    except Exception:
        return d.weekday() >= 5


def pct(v, digits=2):
    return f"{v:+.{digits}f}%"


def mmdd(d):
    return f"{d.month}/{d.day}"


# =========================================================
# weekly: 王様の週間成績表
# =========================================================
def week_monday(today):
    """採点対象の週の月曜。土日なら同じ週の月曜、平日（--force テスト）なら今週の月曜"""
    return today - datetime.timedelta(days=today.weekday())


def classify(dev):
    if dev is None:
        return None
    if dev >= DEV_TH:
        return "buy"
    if dev <= -DEV_TH:
        return "sell"
    return "neutral"


def score_day(rec):
    """1日分の記録を採点して辞書で返す（yorimae_screen.day_values と同じ定義）"""
    prev, fut_gap, o, c, theo, dev = (rec.get("prev_close"), rec.get("gap"), rec.get("open"),
                                      rec.get("close"), rec.get("theo"), rec.get("dev"))
    out = {"gap": fut_gap, "g6": None, "pred": None, "q1": None, "cls9": classify(dev), "d12": None, "cls12": None,
           "k13": None, "q2": None, "adr_n": 0, "adr_follow": 0, "close_chg": None}
    if prev and o:
        out["g6"] = (o / prev - 1) * 100
    if prev and c:
        out["close_chg"] = (c / prev - 1) * 100
    if fut_gap is not None:
        out["pred"] = "上" if fut_gap >= GAP_TH else ("下" if fut_gap <= -GAP_TH else "横")
    if out["g6"] is not None and out["pred"]:
        if out["pred"] == "横":
            out["q1"] = abs(out["g6"]) < FLAT_HIT
        else:
            out["q1"] = (out["g6"] > 0) == (fut_gap > 0)
    if theo is not None and prev and c:
        out["d12"] = (c / prev - 1) * 100 - theo
        out["cls12"] = classify(out["d12"])
    if out["cls9"] and out["cls12"]:
        red9, red12 = out["cls9"] != "neutral", out["cls12"] != "neutral"
        if red9 and not red12:
            out["k13"] = "filled"
        elif red9 and red12:
            out["k13"] = "remained" if out["cls12"] == out["cls9"] else "reversed"
        elif not red9 and red12:
            out["k13"] = "emerged"
        else:
            out["k13"] = "theory"
        out["q2"] = out["k13"] in ("theory", "remained")
    out["adr_rows"] = []   # (code, ADRギャップ%, 日中%, 同方向か)
    for code, a in (rec.get("adr") or {}).items():
        g, ao, ac = a.get("gap"), a.get("o"), a.get("c")
        if g is None or not ao or not ac or abs(g) < ADR_TH:
            continue
        out["adr_n"] += 1
        intr = (ac / ao - 1) * 100
        follow = intr != 0 and (intr > 0) == (g > 0)
        if follow:
            out["adr_follow"] += 1
        out["adr_rows"].append((str(code), g, intr, follow))
    return out


def adr_habits(rows):
    """週を通して「くせ」が出た銘柄: (name, n, follow, best_day) を くせ強（追随率最大）と くせ逆（追随率最小）で返す。3日以上動いた銘柄だけ"""
    by = {}
    for r in rows:
        for code, g, intr, follow in r.get("adr_rows") or []:
            by.setdefault(code, []).append((r["date"], g, intr, follow))
    cands = [(code, v) for code, v in by.items() if len(v) >= 3]
    if not cands:
        return None, None
    def rate(v):
        return sum(1 for x in v if x[3]) / len(v)
    strong = max(cands, key=lambda cv: (rate(cv[1]), len(cv[1])))
    weak = min(cands, key=lambda cv: (rate(cv[1]), -len(cv[1])))
    def pack(code, v, want_follow):
        xs = [x for x in v if x[3] == want_follow] or v
        best = max(xs, key=lambda x: abs(x[1]))   # ADRの動きが最も大きかった日を例に出す
        return {"name": kp.short_name(ADR_NAMES.get(code, code)), "n": len(v), "follow": sum(1 for x in v if x[3]),
                "day": best[0], "gap": best[1], "intr": best[2], "dir": "上" if sum(1 for x in v if x[1] > 0) * 2 >= len(v) else "下"}
    st = pack(*strong, True) if rate(strong[1]) >= 0.75 else None
    wk = pack(*weak, False) if rate(weak[1]) <= 0.25 and weak[0] != strong[0] else None
    return st, wk


def score_week(days, monday):
    rows = []
    for i in range(5):
        d = monday + datetime.timedelta(days=i)
        rec = days.get(d.isoformat())
        row = {"date": d, "holiday": is_holiday(d), "rec": rec}
        if rec and rec.get("open") and rec.get("close"):
            row.update(score_day(rec))
        rows.append(row)
    return rows


WIND = {"buy": "追い風", "sell": "向かい風", "neutral": "理論通り"}
K13_JP = {"theory": "終日 理論通り", "remained": "引けまで 残った", "filled": "引けまでに 消えた",
          "emerged": "場中に 発生", "reversed": "逆向きに 反転"}


def compose_weekly(rows, monday):
    scored = [r for r in rows if r.get("q1") is not None]
    q1_hit = sum(1 for r in scored if r["q1"])
    q2_rows = [r for r in rows if r.get("q2") is not None]
    q2_hit = sum(1 for r in q2_rows if r["q2"])
    adr_n = sum(r.get("adr_n", 0) for r in rows)
    adr_f = sum(r.get("adr_follow", 0) for r in rows)
    fri = monday + datetime.timedelta(days=4)

    lines = [f"おお かぶチワワよ、今週（{mmdd(monday)}〜{mmdd(fri)}）の わしのおつげの 答え合わせじゃ。"]
    # 問い1: 寄りの位置（先物どおりか）＋ 先物と実寄りのズレ⑦（向きが合うのは当たり前なので、ズレの大きさを主役にする。2026-10-11）
    if scored:
        miss = [r for r in scored if not r["q1"]]
        errs = [(abs(r["g6"] - r["gap"]), r) for r in scored if r.get("g6") is not None and r.get("gap") is not None]
        s = f"寄りの位置は 先物どおりが {q1_hit}/{len(scored)}日。"
        if miss:
            s += "、".join(f"{WD[r['date'].weekday()]}曜は「{r['pred']}」と言って 実際は {pct(r['g6'])}" for r in miss) + "。"
        if errs:
            avg = sum(e for e, _ in errs) / len(errs)
            emax, rmax = max(errs, key=lambda x: x[0])
            s += f"先物と実際の寄りの ズレは 平均{avg:.1f}%、最大は {WD[rmax['date'].weekday()]}曜の{emax:.1f}%（先物{pct(rmax['gap'])}→寄り{pct(rmax['g6'])}）。"
        lines.append(s)
    # 問い2: 「夜に吹いた日本株だけの風（米国株で説明できない乖離）が引けまで残ったか」の内訳。王様が風向きを予言したわけではないので勝敗では書かない（2026-10-11）
    if q2_rows:
        def days(rs):
            return "・".join(WD[r["date"].weekday()] for r in rs) + "曜"
        red = [r for r in q2_rows if r["cls9"] != "neutral"]
        blue = [r for r in q2_rows if r["cls9"] == "neutral"]
        s = ""
        if red:
            rem = [r for r in red if r["k13"] == "remained"]
            fil = [r for r in red if r["k13"] == "filled"]
            rev = [r for r in red if r["k13"] == "reversed"]
            s += f"夜のうちに 米国株では 説明のつかぬ 日本株だけの風が 吹いたのは {days(red)}の{len(red)}日。"
            if rem:
                s += "引けまで 残ったのは " + "、".join(f"{WD[r['date'].weekday()]}曜の{WIND[r['cls9']]}" for r in rem) + ("だけ。" if len(rem) < len(red) else "。すべて 本物じゃった。")
            def by_wind(rs):   # 同じ風向きの曜日をまとめる（火・金曜の向かい風）
                g = {}
                for r in rs:
                    g.setdefault(r["cls9"], []).append(r)
                return "、".join(f"{days(v)}の{WIND[c]}" for c, v in g.items())
            if fil:
                s += by_wind(fil) + "は 場が開くと 消えた。"
            if rev:
                s += by_wind(rev) + "は 場中に 逆向きに 変わった。"
        if blue:
            em = [r for r in blue if r["k13"] == "emerged"]
            th = [r for r in blue if r["k13"] == "theory"]
            if em:
                winds = "・".join(sorted({WIND[r["cls12"]] for r in em}))
                s += f"夜は 米国どおりだった {days(em)}は、場中に 日本だけの{winds}が 吹いた。"
            if th:
                s += f"{days(th)}は 終日 米国どおり。"
        lines.append(s)
    # ADR: ADRギャップ（前日終値比）の向きに、寄り付きのあと日中（寄→引）も動いたか。くせの強い/逆の銘柄も1つずつ
    if adr_n:
        lines.append(f"ADRが 0.5%以上 動いた のべ{adr_n}銘柄のうち、寄り付きのあと 日中も ADRと同じ向きに 動いたのは {adr_f}（{adr_f / adr_n * 100:.0f}%）。")
        st, wk = adr_habits(rows)
        hab = []
        if st:
            hab.append(f"ADRのくせすご銘柄は {st['name']}。ADRが{st['dir']}げた{st['n']}日のうち{st['follow']}日は 日中も 同じ向き（{WD[st['day'].weekday()]}曜は ADR{pct(st['gap'], 1)}のあと 日中{pct(st['intr'], 1)}）")
        if wk:
            hab.append(f"逆に {wk['name']}は ADRが{wk['dir']}げた{wk['n']}日のうち{wk['n'] - wk['follow']}日で 日中は 反対に 動いた（{WD[wk['day'].weekday()]}曜は ADR{pct(wk['gap'], 1)}のあと 日中{pct(wk['intr'], 1)}）")
        if hab:
            lines.append("。".join(hab) + "。")
    # 本文にURLは入れない（リンク付き投稿は表示が絞られる。朝の王様・夜の情報漏洩と同じ方針。URLはカードのフッターに）
    lines.append("外れたおつげも 消さぬ。全部 巻物（分析デッキの寄り前チェック）に 残してある。")
    return "\n".join(lines)


WEEKLY_CARD = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=DotGothic16&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ width: {W}px; height: {H}px; overflow: hidden; }}
  body {{ background: #0f172a; color: #e2e8f0; font-family: 'DotGothic16', 'Yu Gothic UI', 'Meiryo', sans-serif; position: relative; }}
  .grid {{ position: absolute; inset: 0; opacity: 0.35;
    background-image: linear-gradient(#1e293b 1px, transparent 1px), linear-gradient(90deg, #1e293b 1px, transparent 1px); background-size: 30px 30px; }}
  .head {{ position: absolute; left: 36px; top: 24px; right: 36px; display: flex; align-items: center; gap: 14px; }}
  .head .title {{ font-size: 30px; color: #f8fafc; letter-spacing: 1px; }}
  .head .title small {{ font-size: 18px; color: #94a3b8; margin-left: 14px; }}
  .head .date {{ margin-left: auto; font-size: 22px; color: #cbd5e1; }}
  .left {{ position: absolute; left: 36px; top: 96px; width: 380px; bottom: 60px; }}
  .king {{ position: absolute; left: 20px; top: 8px; }}
  .window {{ position: absolute; left: 0; top: 190px; right: 0; background: #000; border: 4px solid #fff; border-radius: 6px; outline: 3px solid #000;
    padding: 14px 18px; font-size: 19px; line-height: 1.5; color: #fff; }}
  .window::before {{ content: "＊「"; }}
  .window .who {{ position: absolute; top: -20px; left: 18px; background: #000; padding: 0 10px; font-size: 18px; color: #fde68a; }}
  .right {{ position: absolute; left: 450px; top: 96px; right: 36px; bottom: 60px; }}
  .scores {{ display: flex; gap: 12px; }}
  .scores > div {{ flex: 1; background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 12px 16px; }}
  .scores .label {{ font-size: 16px; color: #94a3b8; }}
  .scores .v {{ font-size: 44px; color: #f8fafc; line-height: 1.1; margin-top: 4px; font-variant-numeric: tabular-nums; }}
  .scores .v small {{ font-size: 20px; color: #94a3b8; margin-left: 6px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 17px; font-variant-numeric: tabular-nums; }}
  th {{ color: #94a3b8; font-weight: normal; font-size: 14px; text-align: left; padding: 4px 6px; border-bottom: 1px solid #334155; }}
  td {{ padding: 7px 6px; border-bottom: 1px solid #1e293b; white-space: nowrap; }}
  td.day {{ color: #fde68a; }}
  .ok {{ color: #4ade80; }} .ng {{ color: #f87171; }} .mute {{ color: #64748b; }}
  .pos {{ color: #4ade80; }} .neg {{ color: #f87171; }}
  .foot {{ position: absolute; left: 36px; right: 36px; bottom: 18px; display: flex; font-size: 17px; color: #64748b; }}
  .foot span:last-child {{ margin-left: auto; }}
</style></head>
<body>
<div class="grid"></div>
<div class="head">{chiwawa}<div class="title">かぶチワワ 王様の週間成績表<small>答え合わせ</small></div><div class="date">{range_s}</div></div>
<div class="left">
  <div class="king">{king}</div>
  <div class="window"><span class="who">王様</span>{speech}</div>
</div>
<div class="right">
  <div class="scores">
    <div><div class="label">寄りの位置 先物どおり</div><div class="v">{q1_s}</div></div>
    <div><div class="label">夜の日本株だけの風が残った</div><div class="v">{q2_s}</div></div>
    <div><div class="label">ADRの向きに 寄り後も動いた</div><div class="v">{adr_s}</div></div>
  </div>
  <table>
    <thead><tr><th>日</th><th>先物6:00</th><th>予告</th><th>実際の寄り</th><th></th><th>夜の風⑩</th><th>引け⑬</th><th></th><th>終値</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
<div class="foot"><span>{deck}/yorimae.html　採点の定義は巻物の答え合わせ表（①〜⑬）と同じ。外れた日も消さない</span><span>@kabuchiwa</span></div>
</body></html>
"""


def weekly_card_html(rows, monday, speech):
    fri = monday + datetime.timedelta(days=4)
    scored = [r for r in rows if r.get("q1") is not None]
    q2_rows = [r for r in rows if r.get("q2") is not None]
    adr_n = sum(r.get("adr_n", 0) for r in rows)
    adr_f = sum(r.get("adr_follow", 0) for r in rows)
    mark = lambda b: '<span class="mute">—</span>' if b is None else ('<span class="ok">○</span>' if b else '<span class="ng">×</span>')
    cls = lambda v: "pos" if (v or 0) > 0 else ("neg" if (v or 0) < 0 else "")
    trs = []
    for r in rows:
        d = r["date"]
        day = f'<td class="day">{mmdd(d)}（{WD[d.weekday()]}）</td>'
        if r.get("holiday"):
            trs.append(f'<tr>{day}<td class="mute" colspan="8">休場（{holiday_name(d)}）</td></tr>')
            continue
        if r.get("q1") is None and r.get("q2") is None:
            trs.append(f'<tr>{day}<td class="mute" colspan="8">記録なし</td></tr>')
            continue
        wind = WIND.get(r.get("cls9"), "—")
        k13 = K13_JP.get(r.get("k13"), "—")
        trs.append(
            f'<tr>{day}<td class="{cls(r.get("gap"))}">{pct(r["gap"]) if r.get("gap") is not None else "—"}</td>'
            f'<td>{r.get("pred") or "—"}</td><td class="{cls(r.get("g6"))}">{pct(r["g6"]) if r.get("g6") is not None else "—"}</td><td>{mark(r.get("q1"))}</td>'
            f'<td>{wind}</td><td>{k13}</td><td>{mark(r.get("q2"))}</td>'
            f'<td class="{cls(r.get("close_chg"))}">{pct(r["close_chg"]) if r.get("close_chg") is not None else "—"}</td></tr>')
    q1_s = f'{sum(1 for r in scored if r["q1"])}<small>/ {len(scored)}</small>' if scored else "—"
    q2_s = f'{sum(1 for r in q2_rows if r["q2"])}<small>/ {len(q2_rows)}</small>' if q2_rows else "—"
    adr_s = f'{adr_f}<small>/ {adr_n}（{adr_f / adr_n * 100:.0f}%）</small>' if adr_n else "—"
    return WEEKLY_CARD.format(W=kp.CARD_W, H=kp.CARD_H, chiwawa=kp.CHIWAWA_SVG, king=kp.king_svg(9),
                              range_s=f"{monday.isoformat().replace('-', '/')} 〜 {fri.strftime('%m/%d')}",
                              speech=speech, q1_s=q1_s, q2_s=q2_s, adr_s=adr_s, rows_html="".join(trs), deck=DECK_URL_SHORT)


def weekly_speech(rows):
    scored = [r for r in rows if r.get("q1") is not None]
    q2_rows = [r for r in rows if r.get("q2") is not None]
    q1 = sum(1 for r in scored if r["q1"])
    q2 = sum(1 for r in q2_rows if r["q2"])
    s = f"今週の おつげの 答え合わせじゃ。寄りの位置は 先物どおりが {q1}/{len(scored)}日。夜に吹いた 日本株だけの風が 引けまで 残ったのは {q2}/{len(q2_rows)}日。"
    if q2_rows and q2 <= len(q2_rows) // 2:
        s += "<br>夜の風は 見えても、場中の風は 朝には 見えぬ。それが 分かっただけでも 収穫じゃ。"
    elif scored and q1 == len(scored):
        s += "<br>先物は 嘘を つかなかった週じゃ。"
    s += "<br>外れも 消さぬ。それが わしの 流儀じゃ。」"
    return s


# =========================================================
# kazami: 航海士の風見表（kazami_screen.py の kazami_post.json を読む）
# =========================================================
KAZAMI_JSON = os.path.join(SCRIPT_DIR, "kazami_post.json")

# 航海士（16×18 ピクセル）。N=紺の帽子と上着、W=帽子の白帯、S=肌、K=線、G=金ボタン、T=望遠鏡
NAVI_PIXELS = """
....NNNNNNNN....
...NNNNNNNNNN...
...NWWWWWWWWN...
...KSSSSSSSSK...
...KSKSSSSKSK...
...KSSSSSSSSK...
....SSSSSSSS....
.....SSSSSS.....
...NNNNNNNNNN...
..NNNGNNNNGNNNTT
..NNNNNNNNNNNTT.
..NNSNNNNNNSTT..
..NNSNNNNNNSNN..
..NNNNNNNNNNNN..
...NNNNNNNNNN...
...NNNN..NNNN...
...NNNN..NNNN...
...KKKK..KKKK...
""".strip("\n").split("\n")
NAVI_PALETTE = {"N": "#1d4ed8", "W": "#f8fafc", "K": "#111111", "S": "#f5d0a9", "G": "#facc15", "T": "#92400e"}


def navi_svg(scale=9):
    rects = []
    for y, row in enumerate(NAVI_PIXELS):
        for x, ch in enumerate(row):
            if ch in NAVI_PALETTE:
                rects.append(f'<rect x="{x*scale}" y="{y*scale}" width="{scale}" height="{scale}" fill="{NAVI_PALETTE[ch]}"/>')
    w = len(NAVI_PIXELS[0]) * scale
    h = len(NAVI_PIXELS) * scale
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" shape-rendering="crispEdges" '
            f'xmlns="http://www.w3.org/2000/svg">{"".join(rects)}</svg>')


def ship_name(name, maxlen=9):
    """全角→半角、ホールディングス等を落として短く"""
    import unicodedata
    n = unicodedata.normalize("NFKC", name)
    for w in ("・ホールディングス", "ホールディングス", "グループ", "ＨＤ", "HD", "・", "株式会社"):
        n = n.replace(w, "")
    n = kp.short_name(n)
    return n[:maxlen]


def compose_kazami(d):
    m = d["main"]
    st, en = datetime.date.fromisoformat(m["start"]), datetime.date.fromisoformat(m["end"])
    lines = [f"かぶチワワ殿、航海士だ。この3か月（{mmdd(st)}〜{mmdd(en)}）の 風見表を 届けに来た。",
             f"日経平均は 上昇{m['n_up']}日・下落{m['n_dn']}日で {m['nk_ret']:+.1f}%。チェックした{d['universe']}隻の船のうち、中央値となる船は、日経が上がった{m['n_up']}日のうち {round(d['median']['hit'] / 100 * m['n_up'])}日 いっしょに上がり、株価は 3か月で {d['median']['ret']:+.1f}% だった。",
             "【順風の船】日経が上がった日に いっしょに上がった日数（率）｜株価の3か月騰落"]
    for r in d["fair"][:10]:
        lines.append(f"{r['rank']}. {ship_name(r['name'])}{'★' if r['star'] else ''} {r['same']}/{r['n_up']}日（{r['hit']:.0f}%）｜株価 {r['ret']:+.0f}%")
    lines.append("【逆風の船】日経が上がった日に 逆に下がった日数（率）｜株価の3か月騰落")
    for r in d["head"][:10]:
        lines.append(f"{r['rank']}. {ship_name(r['name'])}{'★' if r['star'] else ''} {r['opp']}/{r['n_up']}日（{r['opp_rate']:.0f}%）｜株価 {r['ret']:+.0f}%")
    # 締め: 「読めぬ」で投げない（ユーザー指摘 10/11）。検証値（3か月Top10の先行き21営業日の同方向率 72.9% vs 中央値 63.1%、92時点）を根拠に「くせは続きやすい」とだけ言う。約束はしない
    lines.append("★は 6か月でも 同じ側にいた船。来週の風を 約束は できぬ。だが 過去2年の記録では、順風の船は その後1か月も 日経が上がった日の 73%で いっしょに上がった（ふつうの船は 63%）。くせは すぐには 変わらぬ。風を 読みながら、安全な 航海を！")
    lines.append("30隻までの表は 巻物（分析デッキの風見表）に。")
    return "\n".join(lines)


def kazami_speech(d):
    m = d["main"]
    f0, h0 = d["fair"][0], d["head"][0]
    s = f"この3か月の 風見表だ。日経は {m['nk_ret']:+.1f}%、上昇{m['n_up']}日・下落{m['n_dn']}日。"
    s += f"<br>順風の筆頭は {ship_name(f0['name'])}、日経が上がった{f0['n_up']}日のうち {f0['same']}日 いっしょに上がった。"
    s += f"<br>逆風の筆頭は {ship_name(h0['name'])}、{h0['opp']}日は 逆に下がった。"
    s += "<br>風を 読みながら、安全な 航海を！」"
    return s


KAZAMI_CARD = """<!DOCTYPE html>
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
  .left {{ position: absolute; left: 36px; top: 96px; width: 330px; bottom: 60px; }}
  .navi {{ position: absolute; left: 20px; top: 8px; }}
  .window {{ position: absolute; left: 0; top: 190px; right: 0; background: #000; border: 4px solid #fff; border-radius: 6px; outline: 3px solid #000;
    padding: 14px 16px; font-size: 18px; line-height: 1.5; color: #fff; }}
  .window::before {{ content: "＊「"; }}
  .window .who {{ position: absolute; top: -20px; left: 18px; background: #000; padding: 0 10px; font-size: 18px; color: #93c5fd; }}
  .right {{ position: absolute; left: 400px; top: 96px; right: 36px; bottom: 60px; }}
  .strip {{ position: absolute; left: 0; right: 0; top: 0; height: 30px; font-size: 16px; color: #cbd5e1; white-space: nowrap; overflow: hidden; }}
  .strip b {{ color: #f8fafc; font-weight: normal; }}
  .col {{ position: absolute; top: 38px; bottom: 0; width: 375px; background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 10px 12px; overflow: hidden; }}
  .col.fair {{ left: 0; }} .col.adverse {{ right: 0; }}
  .col h3 {{ font-size: 20px; font-weight: normal; white-space: nowrap; overflow: hidden; line-height: 1.2; }}
  .col .sub {{ font-size: 12px; color: #94a3b8; margin: 1px 0 4px; white-space: nowrap; overflow: hidden; }}
  .col.fair h3 {{ color: #86efac; }} .col.adverse h3 {{ color: #fca5a5; }}
  table {{ width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 17px; line-height: 1.25; font-variant-numeric: tabular-nums; }}
  td {{ padding: 5px 2px; border-bottom: 1px solid #0f172a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  td.rk {{ color: #64748b; }}
  td.nm {{ color: #f8fafc; font-size: 17px; }}
  td.ct {{ color: #cbd5e1; text-align: right; font-size: 17px; }}
  td.rt {{ text-align: right; font-size: 19px; }}
  .fair td.rt {{ color: #86efac; }} .adverse td.rt {{ color: #fca5a5; }}
  td.r3 {{ text-align: right; color: #94a3b8; font-size: 15px; }}
  .pos {{ color: #4ade80 !important; }} .neg {{ color: #f87171 !important; }}
  .star {{ color: #fde68a; }}
  .foot {{ position: absolute; left: 36px; right: 36px; bottom: 18px; display: flex; font-size: 16px; color: #64748b; white-space: nowrap; overflow: hidden; }}
  .foot span:last-child {{ margin-left: auto; padding-left: 12px; }}
</style></head>
<body>
<div class="grid"></div>
<div class="head">{chiwawa}<div class="title">かぶチワワ 航海士の風見表<small>日経が上がった日に どう動いた船か</small></div><div class="date">{range_s}</div></div>
<div class="left">
  <div class="navi">{navi}</div>
  <div class="window"><span class="who">航海士</span>{speech}</div>
</div>
<div class="right">
  <div class="strip">日経 <b>{nk_ret:+.1f}%</b>（上昇<b>{n_up}</b>日・下落<b>{n_dn}</b>日）｜{universe}隻の中央値の船: 同方向 <b>{med_days}/{n_up}日</b>・株価 <b>{med_ret:+.1f}%</b></div>
  <div class="col fair"><h3>順風の船</h3><div class="sub">日経上昇日に いっしょに上がった日数（率）｜株価の3か月</div>
    <table><colgroup><col style="width:26px"><col><col style="width:58px"><col style="width:50px"><col style="width:56px"></colgroup>{fair_rows}</table></div>
  <div class="col adverse"><h3>逆風の船</h3><div class="sub">日経上昇日に 逆に下がった日数（率）｜株価の3か月</div>
    <table><colgroup><col style="width:26px"><col><col style="width:58px"><col style="width:50px"><col style="width:56px"></colgroup>{head_rows}</table></div>
</div>
<div class="foot"><span>{deck}/kazami.html　★＝6か月でも同じ側の船　対象: 時価総額1,000億以上（営業赤字も含む）　毎週日曜更新</span><span>@kabuchiwa</span></div>
</body></html>
"""


def kazami_card_html(d, speech):
    m = d["main"]
    def rows(side):
        out = []
        for r in d[side][:10]:
            cnt = r["same"] if side == "fair" else r["opp"]
            rate = r["hit"] if side == "fair" else r["opp_rate"]
            star = '<span class="star">★</span>' if r["star"] else ""
            rc = "pos" if (r["ret"] or 0) > 0 else ("neg" if (r["ret"] or 0) < 0 else "")
            out.append(f'<tr><td class="rk">{r["rank"]}</td><td class="nm">{ship_name(r["name"], 9)}{star}</td><td class="ct">{cnt}/{r["n_up"]}</td>'
                       f'<td class="rt">{rate:.0f}%</td><td class="r3 {rc}">{r["ret"]:+.0f}%</td></tr>')
        return "".join(out)
    st, en = datetime.date.fromisoformat(m["start"]), datetime.date.fromisoformat(m["end"])
    return KAZAMI_CARD.format(W=kp.CARD_W, H=kp.CARD_H, chiwawa=kp.CHIWAWA_SVG, navi=navi_svg(9),
                              range_s=f"{st.strftime('%Y/%m/%d')} 〜 {en.strftime('%m/%d')}",
                              speech=speech, fair_rows=rows("fair"), head_rows=rows("head"), deck=DECK_URL_SHORT,
                              universe=d["universe"], med=d["median"]["hit"], med_ret=d["median"]["ret"],
                              med_days=round(d["median"]["hit"] / 100 * m["n_up"]),
                              nk_ret=m["nk_ret"], n_up=m["n_up"], n_dn=m["n_dn"])



# =========================================================
# preview: 来週のフィールド予告
# =========================================================
def next_monday(today):
    """来週の月曜（日曜に走る前提。平日/土曜の --force なら次の月曜）"""
    return today + datetime.timedelta(days=(7 - today.weekday()) % 7 or 7)


def short_event(name, note):
    n = name.replace("⚡ ", "")
    if "FOMC" in n:
        return "FOMC（結果は翌朝）", True
    if "日銀" in n:
        return "日銀会合（昼ごろ・会見15:30）", True
    if "メジャーSQ" in n:
        return "メジャーSQ", True
    if "SQ" in n:
        return "SQ", True
    if "トリプルウィッチング" in n:
        return "米トリプルウィッチング", True
    if "雇用統計" in n:
        return "米雇用統計 21:30", True
    if "CPI" in n:
        return "米CPI 21:30", True
    if "休場" in n:
        return "米国市場 休場", False
    if "決算" in n:
        return n, True
    return n, False


def week_events(monday):
    """来週 月〜金 の {date: [(表示名, 強調, 補足)]}。東証休場日は先頭に入れる"""
    out = {monday + datetime.timedelta(days=i): [] for i in range(5)}
    for d in out:
        if is_holiday(d):
            out[d].append((f"東証 休場（{holiday_name(d)}）", False, "王様の偵察も 休み"))
    try:
        import calendar_screen
        ev = calendar_screen.build_events(monday)
    except Exception as e:
        log(f"calendar_screen の読み込み失敗（イベント無しで続行）: {e}")
        ev = []
    for d, flag, name, note, _link, major in ev:
        if d in out:
            label, strong = short_event(name, note)
            if label.startswith("米CPI"):
                # カード用の短い材料（前回値・FF先物）。本文側は cpi_phrase() で別に組む
                ctx = cpi_context(d)
                if ctx and "last" in ctx:
                    note = f"前回 {ctx['last']:+.1f}%（{int(ctx['last_month'][5:7])}月分）"
                    if ctx.get("fed"):
                        m, v = ctx["fed"][-1]
                        note += f"・FF先物 {m[5:]}月{'利上げ' if v >= 0 else '利下げ'}{abs(v):.0f}%"
                _CPI_CTX_CACHE[d] = ctx
            out[d].append((label, strong or bool(major), note or ""))
    return out


_CPI_CTX_CACHE = {}


def _fetch_json_deadline(url, data=None, headers=None, deadline_sec=25):
    """urlopen をデーモンスレッドで実行し壁時計で見切る（roei と同じ考え方）"""
    import threading
    import urllib.request
    box = {}

    def _run():
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": "Mozilla/5.0"})
            box["data"] = json.loads(urllib.request.urlopen(req, timeout=15).read())
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


def cpi_context(cpi_date):
    """米CPIの材料: 前回・前々回の前年比（BLS CUSR0000SA0、cpi_screen と同じ系列）と FF先物の利上げ織り込み（fedwatch_history.json）。
       取れなければ None（予告はCPIの行だけになる）"""
    out = {}
    try:
        payload = json.dumps({"seriesid": ["CUSR0000SA0"], "startyear": str(cpi_date.year - 2), "endyear": str(cpi_date.year)}).encode()
        data = _fetch_json_deadline("https://api.bls.gov/publicAPI/v2/timeseries/data/", data=payload,
                                    headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        idx = {}
        for r in data["Results"]["series"][0]["data"]:
            if r["period"].startswith("M"):
                try:
                    idx[f"{r['year']}-{r['period'][1:]}"] = float(r["value"])
                except ValueError:
                    pass
        yoy = {}
        for k, v in idx.items():
            y, m = int(k[:4]), int(k[5:7])
            pv = idx.get(f"{y - 1}-{m:02d}")
            if pv:
                yoy[k] = (v / pv - 1) * 100
        ks = sorted(yoy)
        if len(ks) >= 2:
            out["last_month"], out["last"], out["prev_month"], out["prev"] = ks[-1], yoy[ks[-1]], ks[-2], yoy[ks[-2]]
            out["high12"] = max(yoy[k] for k in ks[-12:])
            out["low12"] = min(yoy[k] for k in ks[-12:])
    except Exception as e:
        log(f"  CPIの取得失敗（材料なしで続行）: {e}")
    try:
        fw = json.load(open(os.path.join(SCRIPT_DIR, "fedwatch_history.json"), encoding="utf-8"))
        latest = fw[max(fw)]
        # 次の2会合（キー 'YYYY/MM'、値 = +25bp利上げの織り込み% / マイナスは利下げ）
        meets = sorted(latest)
        out["fed"] = [(m, latest[m]) for m in meets[:2]]
    except Exception as e:
        log(f"  FF先物の読み込み失敗: {e}")
    return out or None


def cpi_phrase(ctx):
    """王様口調でCPIの見どころ1〜2文。方向の予想はせず「どちらに振れると何が動くか」だけ"""
    if not ctx:
        return ""
    parts = []
    if "last" in ctx:
        lm = int(ctx["last_month"][5:7])
        pm = int(ctx["prev_month"][5:7])
        trend = "上向き" if ctx["last"] > ctx["prev"] + 0.05 else ("下向き" if ctx["last"] < ctx["prev"] - 0.05 else "横ばい")
        parts.append(f"前回（{lm}月分）の 物価は 前年比 {ctx['last']:+.1f}%、その前（{pm}月分）が {ctx['prev']:+.1f}% で {trend}。この1年の幅は {ctx['low12']:+.1f}〜{ctx['high12']:+.1f}%")
    if ctx.get("fed"):
        def word(v):
            return f"利上げ{v:.0f}%" if v >= 0 else f"利下げ{-v:.0f}%"
        parts.append("FF先物の 織り込みは " + "、".join(f"{m[5:]}月会合 {word(v)}" for m, v in ctx["fed"]))
        v0 = ctx["fed"][0][1]
        if v0 >= 0:
            parts.append("物価が 上に振れれば 利上げが 近づき、下に振れれば 遠のく。金利と ドル円が まず 動く")
        else:
            parts.append("物価が 下に振れれば 利下げが 近づき、上に振れれば 遠のく。金利と ドル円が まず 動く")
    return "。".join(parts) + "。"


def compose_preview(evs, monday):
    fri = monday + datetime.timedelta(days=4)
    lines = [f"おお かぶチワワよ、来週（{mmdd(monday)}〜{mmdd(fri)}）の フィールドの予告じゃ。"]
    quiet = []
    for d in sorted(evs):
        items = evs[d]
        w = WD[d.weekday()]
        if not items:
            quiet.append(f"{w}")
            continue
        hol = [i for i in items if i[0].startswith("東証 休場")]
        rest = [i for i in items if not i[0].startswith("東証 休場")]
        if hol:
            lines.append(f"{w}曜は 東証が 休場（{holiday_name(d)}）。わしの偵察も 休みじゃ。")
        if rest:
            bosses = [i[0] for i in rest if i[1]]
            infos = [i[0] for i in rest if not i[1]]
            s = f"{w}曜は "
            if bosses:
                s += "、".join(bosses) + " が あらわれる"
            if infos:
                s += ("。" if bosses else "") + "、".join(infos) + " が ある"
            lines.append(s + "。")
            if any(b.startswith("米CPI") for b in bosses):   # 月で一番の大物。前回値とFF先物の織り込みを添える（2026-10-11 ユーザー要望）
                cp = cpi_phrase(_CPI_CTX_CACHE.get(d) or cpi_context(d))
                if cp:
                    lines.append(cp)
    if quiet:
        lines.append("・".join(quiet) + "曜は 大物の予定なし。米国の夜の動きが そのまま 寄りに 出る日じゃ。")
    lines.append("細かい日程は 巻物（分析デッキのイベントカレンダー）に。月曜の朝も 7:15に 告げに来る。")
    return "\n".join(lines)


PREVIEW_CARD = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=DotGothic16&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ width: {W}px; height: {H}px; overflow: hidden; }}
  body {{ background: #0f172a; color: #e2e8f0; font-family: 'DotGothic16', 'Yu Gothic UI', 'Meiryo', sans-serif; position: relative; }}
  .grid {{ position: absolute; inset: 0; opacity: 0.35;
    background-image: linear-gradient(#1e293b 1px, transparent 1px), linear-gradient(90deg, #1e293b 1px, transparent 1px); background-size: 30px 30px; }}
  .head {{ position: absolute; left: 36px; top: 24px; right: 36px; display: flex; align-items: center; gap: 14px; }}
  .head .title {{ font-size: 30px; color: #f8fafc; letter-spacing: 1px; }}
  .head .title small {{ font-size: 18px; color: #94a3b8; margin-left: 14px; }}
  .head .date {{ margin-left: auto; font-size: 22px; color: #cbd5e1; }}
  .left {{ position: absolute; left: 36px; top: 96px; width: 340px; bottom: 60px; }}
  .king {{ position: absolute; left: 20px; top: 8px; }}
  .window {{ position: absolute; left: 0; top: 190px; right: 0; background: #000; border: 4px solid #fff; border-radius: 6px; outline: 3px solid #000;
    padding: 14px 18px; font-size: 19px; line-height: 1.5; color: #fff; }}
  .window::before {{ content: "＊「"; }}
  .window .who {{ position: absolute; top: -20px; left: 18px; background: #000; padding: 0 10px; font-size: 18px; color: #fde68a; }}
  .right {{ position: absolute; left: 410px; top: 96px; right: 36px; bottom: 60px; display: flex; gap: 10px; }}
  .col {{ flex: 1; background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 12px 12px; display: flex; flex-direction: column; }}
  .col.holiday {{ background: #111827; border-style: dashed; }}
  .col .d {{ font-size: 22px; color: #f8fafc; }}
  .col .d small {{ font-size: 16px; color: #94a3b8; margin-left: 6px; }}
  .ev {{ margin-top: 10px; font-size: 17px; line-height: 1.35; }}
  .ev.boss {{ color: #fde68a; }}
  .ev.boss::before {{ content: "▶ "; }}
  .ev.info {{ color: #cbd5e1; }}
  .ev.hol {{ color: #94a3b8; }}
  .ev .note {{ display: block; font-size: 13px; color: #64748b; margin-top: 2px; white-space: normal; }}
  .quiet {{ margin-top: auto; font-size: 15px; color: #475569; }}
  .foot {{ position: absolute; left: 36px; right: 36px; bottom: 18px; display: flex; font-size: 17px; color: #64748b; }}
  .foot span:last-child {{ margin-left: auto; }}
</style></head>
<body>
<div class="grid"></div>
<div class="head">{chiwawa}<div class="title">かぶチワワ 来週のフィールド予告<small>王様のおつげ</small></div><div class="date">{range_s}</div></div>
<div class="left">
  <div class="king">{king}</div>
  <div class="window"><span class="who">王様</span>{speech}</div>
</div>
<div class="right">{cols}</div>
<div class="foot"><span>{deck}/calendar.html　FOMC・日銀・SQ・米指標・決算・指数イベント　▶＝大物</span><span>@kabuchiwa</span></div>
</body></html>
"""


def preview_card_html(evs, monday, speech):
    fri = monday + datetime.timedelta(days=4)
    cols = []
    for d in sorted(evs):
        items = evs[d]
        hol = any(i[0].startswith("東証 休場") for i in items)
        body = []
        for label, strong, note in items:
            if label.startswith("東証 休場"):
                body.append(f'<div class="ev hol">{label}</div>')
            else:
                nt = f'<span class="note">{note[:40] if label.startswith("米CPI") else note[:28]}</span>' if note else ""
                body.append(f'<div class="ev {"boss" if strong else "info"}">{label}{nt}</div>')
        if not body:
            body.append('<div class="quiet">大物の予定なし</div>')
        cols.append(f'<div class="col{" holiday" if hol else ""}"><div class="d">{mmdd(d)}<small>{WD[d.weekday()]}</small></div>{"".join(body)}</div>')
    return PREVIEW_CARD.format(W=kp.CARD_W, H=kp.CARD_H, chiwawa=kp.CHIWAWA_SVG, king=kp.king_svg(9),
                               range_s=f"{monday.isoformat().replace('-', '/')} 〜 {fri.strftime('%m/%d')}",
                               speech=speech, cols="".join(cols), deck=DECK_URL_SHORT)


def preview_speech(evs):
    """ウィンドウのセリフ。大物は括弧（銘柄名など）を落とし、同じ大物は曜日をまとめる（火・水曜の米大手銀行 決算）"""
    import re as _re
    hols = [d for d in sorted(evs) if any(i[0].startswith("東証 休場") for i in evs[d])]
    grouped = {}
    for d in sorted(evs):
        for i in evs[d]:
            if i[1]:
                short = _re.sub(r"（.*?）", "", i[0]).strip()
                grouped.setdefault(short, []).append(WD[d.weekday()])
    s = "来週の フィールドじゃ。"
    if hols:
        s += "<br>" + "・".join(WD[d.weekday()] for d in hols) + "曜は 東証が 休み。"
    if grouped:
        s += "<br>大物は " + "、".join(f"{'・'.join(wds)}曜の{name}" for name, wds in list(grouped.items())[:3]) + "。"
    else:
        s += "<br>大物の予定は ない。静かな週ほど 米国の夜が 寄りを 決める。"
    s += "<br>月曜の朝、7:15に また 告げに来る。」"
    return s


# =========================================================
# 共通: 下書き・カード・投稿
# =========================================================
def run(mode, args):
    dry_run = "--dry-run" in args
    force = "--force" in args
    no_render = "--no-render" in args
    today = datetime.date.today()
    if "--date" in args:
        today = datetime.date.fromisoformat(args[args.index("--date") + 1])
    log(f"かぶチワワ 週末投稿（{mode}） 開始")

    want_wd = 5 if mode == "weekly" else 6   # weekly=土曜, preview/kazami=日曜
    if not force and today.weekday() != want_wd:
        log(f"{today}（{WD[today.weekday()]}）は実行曜日（{WD[want_wd]}）ではない → スキップ（テストは --force）")
        return

    if mode == "weekly":
        if not os.path.exists(HISTORY_JSON):
            log("yorimae_history.json がありません")
            return
        days = json.load(open(HISTORY_JSON, encoding="utf-8")).get("days", {})
        monday = week_monday(today)
        rows = score_week(days, monday)
        if not any(r.get("q1") is not None for r in rows):
            log(f"{monday}の週に採点できる記録がありません → 投稿しません")
            return
        text = compose_weekly(rows, monday)
        html = weekly_card_html(rows, monday, weekly_speech(rows))
        key = f"weekly:{monday.isoformat()}"
    elif mode == "kazami":
        if not os.path.exists(KAZAMI_JSON):
            log("kazami_post.json がありません（kazami_screen.py を先に実行）")
            return
        d = json.load(open(KAZAMI_JSON, encoding="utf-8"))
        gen = datetime.datetime.fromisoformat(d["generated"])
        if (datetime.datetime.now() - gen).total_seconds() > 2 * 24 * 3600 and not force:
            log(f"kazami_post.json が古い（{gen:%m/%d %H:%M}）→ 投稿しません")
            return
        if len(d.get("fair") or []) < 10 or len(d.get("head") or []) < 10:
            log("順風/逆風の船が10隻に足りません → 投稿しません")
            return
        text = compose_kazami(d)
        html = kazami_card_html(d, kazami_speech(d))
        key = f"kazami:{d['asof']}"
    else:
        monday = next_monday(today)
        evs = week_events(monday)
        text = compose_preview(evs, monday)
        html = preview_card_html(evs, monday, preview_speech(evs))
        key = f"preview:{monday.isoformat()}"

    cfg = kp.load_config()
    out_dir = os.path.join(kp.DRAFTS_DIR, today.isoformat())
    os.makedirs(out_dir, exist_ok=True)
    txt_path = os.path.join(out_dir, f"weekend_{mode}.txt")
    html_path = os.path.join(out_dir, f"weekend_{mode}.html")
    png_path = os.path.join(out_dir, f"weekend_{mode}.png")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"投稿文（{kp.weighted_len(text)}）:\n{text}")

    rendered = False
    if not no_render:
        rendered = kp.render_card(html_path, png_path)
        log(f"カード画像: {'OK ' + png_path if rendered else '生成できず（画像なし）'}")

    hits = kp.check_forbidden(text)
    if hits:
        log(f"⚠ 禁止語を検出したため投稿しません: {hits}")
        return
    if dry_run or not cfg.get("weekend_post_enabled"):
        log("下書きのみ（weekend_post_enabled=false または --dry-run）")
        return
    posted = {}
    if os.path.exists(POSTED_JSON):
        try:
            posted = json.load(open(POSTED_JSON, encoding="utf-8"))
        except Exception:
            posted = {}
    if posted.get(key) and not (force and "--repost" in args):
        log(f"この週は既に投稿済み（{posted[key]}）→ スキップ。やり直すなら --force --repost")
        return
    try:
        kp.post_to_x(cfg, text, png_path if rendered else None, None)
        posted[key] = datetime.datetime.now().isoformat(timespec="seconds")
        posted = dict(sorted(posted.items())[-40:])
        json.dump(posted, open(POSTED_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"投稿失敗: {e}")
    log("完了")


def main():
    args = sys.argv[1:]
    if any(a in args for a in ("--enable", "--disable", "--status")):
        cfg = kp.load_config()
        if "--enable" in args:
            cfg["weekend_post_enabled"] = True
        if "--disable" in args:
            cfg["weekend_post_enabled"] = False
        cfg.pop("_comment", None)
        if "--status" not in args or len(args) > 1:
            json.dump(cfg, open(kp.CONFIG_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"週末投稿（土曜8:00 成績表・日曜10:00 風見表・日曜18:30 来週予告）: {'オン' if cfg.get('weekend_post_enabled') else 'オフ（下書きのみ）'}")
        return
    mode = next((a for a in args if a in ("weekly", "preview", "kazami")), None)
    if not mode:
        print(__doc__)
        return
    run(mode, args)


if __name__ == "__main__":
    main()
