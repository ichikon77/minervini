# -*- coding: utf-8 -*-
"""
東京証券取引所の休業日判定（yorimae_screen.py / kabuchiwa_post.py 共用）

- 土日
- 国民の祝日（振替休日・国民の休日を含む）
- 年末年始（12/31, 1/1〜1/3）
jpholiday が入っていればそれを優先し、無ければ下の表で判定する。
表は毎年12月ごろに翌々年分を追記する（内閣府「国民の祝日について」参照）。
"""
import datetime

# 年ごとの祝日（振替休日・国民の休日を含む）。土日と重なる日も入れてあるが判定上は無害
_HOLIDAYS = {
    2026: [
        "01-01", "01-12", "02-11", "02-23", "03-20", "04-29", "05-03", "05-04", "05-05", "05-06",
        "07-20", "08-11", "09-21", "09-22", "09-23", "10-12", "11-03", "11-23",
    ],
    2027: [
        "01-01", "01-11", "02-11", "02-23", "03-21", "03-22", "04-29", "05-03", "05-04", "05-05",
        "07-19", "08-11", "09-20", "09-23", "10-11", "11-03", "11-23",
    ],
}


def is_market_holiday(d):
    """東証が休みなら True"""
    if d.weekday() >= 5:
        return True
    if (d.month == 12 and d.day == 31) or (d.month == 1 and d.day <= 3):
        return True
    try:
        import jpholiday
        if jpholiday.is_holiday(d):
            return True
    except Exception:
        pass
    return d.strftime("%m-%d") in _HOLIDAYS.get(d.year, [])


def next_business_day(d):
    """d 以降（d を含む）で最初の営業日"""
    while is_market_holiday(d):
        d += datetime.timedelta(days=1)
    return d


def prev_business_day(d):
    """d より前で最後の営業日"""
    d -= datetime.timedelta(days=1)
    while is_market_holiday(d):
        d -= datetime.timedelta(days=1)
    return d


if __name__ == "__main__":
    import sys
    for s in sys.argv[1:] or ["2026-09-18", "2026-09-19", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]:
        d = datetime.date.fromisoformat(s)
        print(s, "休場" if is_market_holiday(d) else "営業日", "→ 次の営業日", next_business_day(d))
