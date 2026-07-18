# -*- coding: utf-8 -*-
"""
菫｡逕ｨ隧穂ｾ｡謳咲寢邇・・菫｡逕ｨ蜿門ｼ墓ｮ矩ｫ・閾ｪ蜍戊ｨ倬鹸 竊・HTML蜃ｺ蜉・竊・GitHub Pages蜈ｬ髢・
繝・・繧ｿ貅・ https://nikkei225jp.com/data/sinyou.php
螳溘ョ繝ｼ繧ｿ縺ｯ繝壹・繧ｸ縺瑚ｪｭ縺ｿ霎ｼ繧 dailyweek2.json・・009蟷ｴ縺九ｉ縺ｮ騾ｱ谺｡繝・・繧ｿ・峨↓
蜈･縺｣縺ｦ縺・ｋ縺溘ａ縲√◎繧後ｒ逶ｴ謗･蜿門ｾ励＠縺ｦ繝代・繧ｹ縺吶ｋ縲・
- 騾ｱ谺｡繝・・繧ｿ・域ｯ朱ｱ驥第屆逕ｳ縺苓ｾｼ縺ｿ譎らせ縲∫ｿ碁ｱ轣ｫ譖憺・峩譁ｰ・・- 螻･豁ｴ縺ｯ shinyou_history.json 縺ｫ蜈ｨ譛滄俣闢・ｩ・- shinyou.html 繧堤函謌舌＠縺ｦ git push・井ｻ悶せ繧ｯ繝ｪ繝ｼ繝翫・縺ｨ蜷梧婿蠑擾ｼ・- 濶ｲ蛻・￠:
    螢ｲ繧頑ｮ矩≡鬘・> 800,000逋ｾ荳・・ 竊・螳牙ｿ・ｳｻ・育ｷ托ｼ・    雋ｷ縺・ｮ矩≡鬘・> 5,000,000逋ｾ荳・・ 竊・隴ｦ謌堤ｳｻ・郁ｵ､・・    菫｡逕ｨ隧穂ｾ｡邇・ > -2 螟ｩ莠募恟:隱ｿ謨ｴ隴ｦ謌抵ｼ医ヴ繝ｳ繧ｯ・・/ -9縲・10 隕∬ｭｦ謌抵ｼ医が繝ｬ繝ｳ繧ｸ・・/
                < -10 雜・ｭｦ謌抵ｼ郁ｵ､螟ｪ蟄暦ｼ・"""

import os
import re
import sys
import json
import time
import subprocess
import datetime

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

PAGE_URL = "https://nikkei225jp.com/data/sinyou.php"
DATA_URL = "https://nikkei225jp.com/_data/_nfsDATA/DAY/dailyweek2.json"

HISTORY_JSON = os.path.join(SCRIPT_DIR, "shinyou_history.json")
REPORT_HTML = "shinyou.html"

SELL_ALERT = 800_000      # 螢ｲ繧頑ｮ矩≡鬘搾ｼ育卆荳・・・・ 雜・∴縺溘ｉ螳牙ｿ・ｳｻ
BUY_ALERT = 5_000_000     # 雋ｷ縺・ｮ矩≡鬘搾ｼ育卆荳・・・・ 雜・∴縺溘ｉ隴ｦ謌堤ｳｻ

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": PAGE_URL,
}

FIELDS = ["螢ｲ繧頑ｮ区椢謨ｰ", "螢ｲ繧頑ｮ矩≡鬘・, "螢ｲ繧頑ｮ句､牙喧", "雋ｷ縺・ｮ区椢謨ｰ", "雋ｷ縺・ｮ矩≡鬘・,
          "雋ｷ縺・ｮ句､牙喧", "菫｡逕ｨ蛟咲紫", "菫｡逕ｨ隧穂ｾ｡邇・]


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# 繝・・繧ｿ蜿門ｾ・# -----------------------------------------
def _fetch_requests(url, timeout):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text


def _fetch_curl(url, timeout):
    """requests縺・03縺ｧ蠑ｾ縺九ｌ繧句ｴ蜷医・繝輔か繝ｼ繝ｫ繝舌ャ繧ｯ・・url縺ｯ繝悶Λ繧ｦ繧ｶ縺ｫ霑代＞謖吝虚・・""
    cmd = ["curl", "-sL", "--max-time", str(timeout),
           "-A", HEADERS["User-Agent"],
           "-e", PAGE_URL,
           "-H", "Accept: */*",
           "-H", "Accept-Language: ja,en-US;q=0.9",
           "--compressed",
           url]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0 or not r.stdout or len(r.stdout) < 1000:
        raise RuntimeError(f"curl蜿門ｾ怜､ｱ謨・(rc={r.returncode}, len={len(r.stdout or '')})")
    if "403" in r.stdout[:200] and "Forbidden" in r.stdout[:500]:
        raise RuntimeError("curl: 403 Forbidden")
    return r.stdout


def _fetch_powershell(url, timeout):
    """Windows邏疲ｭ｣HTTP繧ｹ繧ｿ繝・け邨檎罰・医ヶ繝ｩ繧ｦ繧ｶ縺ｨ蜷檎ｭ峨↓謇ｱ繧上ｌ繧・☆縺・ｼ・""
    ps = (
        f"$ProgressPreference='SilentlyContinue'; "
        f"$r = Invoke-WebRequest -UseBasicParsing -Uri '{url}' -TimeoutSec {timeout} "
        f"-UserAgent '{HEADERS['User-Agent']}' "
        f"-Headers @{{'Referer'='{PAGE_URL}'; 'Accept-Language'='ja,en-US;q=0.9'}}; "
        f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        f"$r.Content"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore",
                       timeout=timeout + 30)
    if r.returncode != 0 or not r.stdout or len(r.stdout) < 1000:
        err = (r.stderr or "")[:200]
        raise RuntimeError(f"PowerShell蜿門ｾ怜､ｱ謨・(len={len(r.stdout or '')}) {err}")
    return r.stdout


def fetch_with_retry(url, tries=3, timeout=60, wait=45):
    last_err = None
    methods = [("requests", _fetch_requests), ("curl", _fetch_curl)]
    if os.name == "nt":
        methods.append(("powershell", _fetch_powershell))
    for i in range(1, tries + 1):
        for method, fn in methods:
            try:
                return fn(url, timeout)
            except Exception as e:
                last_err = e
                log(f"  蜿門ｾ怜､ｱ謨・[{method}] ({i}/{tries}): {e}")
        if i < tries:
            time.sleep(wait)
    raise last_err


def fetch_weekly_data():
    """dailyweek2.json 縺九ｉ (譌･莉露SO, {蛻・ 蛟､}) 縺ｮ繝ｪ繧ｹ繝医ｒ霑斐☆・域律莉俶・鬆・ｼ・
    JSON陦後・讒矩: [繧ｿ繧､繝繧ｹ繧ｿ繝ｳ繝洋s, 譌･邨悟ｹｳ蝮・ 蜃ｺ譚･鬮・,
                   螢ｲ繧頑ｮ区椢謨ｰ, 螢ｲ繧頑ｮ矩≡鬘・ 雋ｷ縺・ｮ区椢謨ｰ, 雋ｷ縺・ｮ矩≡鬘・
                   菫｡逕ｨ隧穂ｾ｡邇・ 菫｡逕ｨ蛟咲紫, ...謚戊ｳ・Κ髢蛻･繝・・繧ｿ...]
    菫｡逕ｨ繝・・繧ｿ縺後↑縺・｡鯉ｼ育ｩｺ譁・ｭ暦ｼ峨・繧ｹ繧ｭ繝・・
    """
    s = fetch_with_retry(DATA_URL)
    start = s.find('[')
    end = s.rfind(']')
    if start < 0 or end < 0:
        raise ValueError("繝・・繧ｿJSON縺ｮ蠖｢蠑上′諠ｳ螳壹→逡ｰ縺ｪ繧翫∪縺・)
    raw = json.loads(s[start:end + 1].replace('""', 'null'))

    out = []
    for row in raw:
        if len(row) < 9 or row[3] is None or row[3] == "":
            continue
        try:
            d = datetime.datetime.fromtimestamp(row[0] / 1000).date()
            rec = {
                "螢ｲ繧頑ｮ区椢謨ｰ": float(row[3]),
                "螢ｲ繧頑ｮ矩≡鬘・: float(row[4]),
                "雋ｷ縺・ｮ区椢謨ｰ": float(row[5]),
                "雋ｷ縺・ｮ矩≡鬘・: float(row[6]),
                "菫｡逕ｨ隧穂ｾ｡邇・: float(row[7]),
                "菫｡逕ｨ蛟咲紫": float(row[8]),
                "螢ｲ繧頑ｮ句､牙喧": None,  # 蠕後〒蜑埼ｱ豈斐°繧芽ｨ育ｮ・                "雋ｷ縺・ｮ句､牙喧": None,
            }
        except (TypeError, ValueError):
            continue
        out.append((d.isoformat(), rec))

    out.sort(key=lambda x: x[0])
    # 蜑埼ｱ豈費ｼ磯≡鬘阪・螟牙喧邇・ｼ峨ｒ險育ｮ・    for i in range(1, len(out)):
        prev = out[i - 1][1]
        cur = out[i][1]
        if prev["螢ｲ繧頑ｮ矩≡鬘・]:
            cur["螢ｲ繧頑ｮ句､牙喧"] = round((cur["螢ｲ繧頑ｮ矩≡鬘・] / prev["螢ｲ繧頑ｮ矩≡鬘・] - 1) * 100, 2)
        if prev["雋ｷ縺・ｮ矩≡鬘・]:
            cur["雋ｷ縺・ｮ句､牙喧"] = round((cur["雋ｷ縺・ｮ矩≡鬘・] / prev["雋ｷ縺・ｮ矩≡鬘・] - 1) * 100, 2)
    return out


# -----------------------------------------
# 螻･豁ｴ・・SON・・# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        with open(HISTORY_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(hist):
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)


# -----------------------------------------
# HTML蜃ｺ蜉・# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>菫｡逕ｨ隧穂ｾ｡謳咲寢邇・・菫｡逕ｨ谿・- {latest_date}</title>
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
  .legend {{ display: flex; gap: 14px; margin-bottom: 14px; font-size: 0.78rem; color: #94a3b8; flex-wrap: wrap; }}
  .chip {{ padding: 2px 10px; border-radius: 10px; }}
  .table-wrap {{
    overflow: auto;
    max-height: calc(100vh - 210px);
    max-width: 980px;
  }}
  table {{ border-collapse: collapse; font-size: 0.84rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 12px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child {{ text-align: left; }}
  td {{
    padding: 6px 12px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{ text-align: left; color: #94a3b8; }}
  tr:hover td {{ background: #16213a; }}
  .latest-row td {{ background: rgba(30,64,175,0.18); font-weight: bold; }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
  /* 螢ｲ繧頑ｮ矩≡鬘・80荳・ｶ・= 雋ｷ縺・綾縺玲悄蠕・ｼ育ｷ代ワ繧､繝ｩ繧､繝茨ｼ・*/
  td.sell-calm {{ background: rgba(34,197,94,0.22); color: #86efac; font-weight: bold; }}
  /* 雋ｷ縺・ｮ矩≡鬘・500荳・ｶ・= 隴ｦ謌堤ｳｻ・郁ｵ､繝上う繝ｩ繧､繝茨ｼ・*/
  td.buy-warn {{ background: rgba(220,38,38,0.25); color: #fca5a5; font-weight: bold; }}
  /* 菫｡逕ｨ隧穂ｾ｡邇・・繧｢繝ｩ繝ｼ繝・*/
  .rate-top {{ background: rgba(236,72,153,0.25); color: #f9a8d4; font-weight: bold; }}  /* > -2 螟ｩ莠募恟:隱ｿ謨ｴ隴ｦ謌・*/
  .rate-normal {{ color: #e2e8f0; }}
  .rate-caution {{ color: #fbbf24; font-weight: bold; }}       /* -9 縲・-10 隕∬ｭｦ謌・*/
  .rate-danger {{ background: rgba(220,38,38,0.3); color: #f87171; font-weight: bold; }} /* < -10 雜・ｭｦ謌・*/
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.8; }}
</style>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>縺九・繝√Ρ繝ｯ縺ｮ蛻・梵繝・ャ繧ｭ・・a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>・・/div>
  <nav class="nav">
    <a href="minervini_report_v2.html">邀ｳ蝗ｽ譬ｪ (Minervini)</a>
    <a href="haitou.html">譌･譛ｬ譬ｪ (驟榊ｽ・</a>
    <a href="jpminervini.html">譌･譛ｬ譬ｪ (Minervini)</a>
    <a href="saitei.html">陬∝ｮ壼叙蠑・/a>
    <a href="totan.html">譌･驫蛻ｩ荳翫￡遒ｺ邇・/a>
    <a href="daikin.html">螢ｲ雋ｷ莉｣驥・/a>
    <a href="shinyou.html" class="active">菫｡逕ｨ隧穂ｾ｡邇・/a>
  </nav>
  <h1>菫｡逕ｨ隧穂ｾ｡謳咲寢邇・・菫｡逕ｨ蜿門ｼ墓ｮ矩ｫ・/h1>
  <p class="subtitle">譛邨よ峩譁ｰ: {updated} | 蜃ｺ謇: nikkei225jp.com | 騾ｱ谺｡・磯≡譖懃筏霎ｼ譎らせ・・| 譫壽焚:蜊・ｪ 驥鷹｡・逋ｾ荳・・</p>
  <div class="legend">
    <span class="chip" style="background:rgba(34,197,94,0.22); color:#86efac">螢ｲ繧頑ｮ矩≡鬘・{sell_alert:,}雜・= 雋ｷ縺・綾縺玲悄蠕・/span>
    <span class="chip" style="background:rgba(220,38,38,0.25); color:#fca5a5">雋ｷ縺・ｮ矩≡鬘・{buy_alert:,}雜・= 隴ｦ謌・/span>
    <span>|</span>
    <span class="rate-top" style="padding:1px 8px">隧穂ｾ｡邇・&gt;-2 螟ｩ莠募恟:隱ｿ謨ｴ隴ｦ謌・/span>
    <span class="rate-caution">-9縲・10 隕∬ｭｦ謌・/span>
    <span class="rate-danger" style="padding:1px 8px">&lt;-10 雜・ｭｦ謌・/span>
  </div>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>譌･莉・/th>
        <th>螢ｲ繧頑ｮ区椢謨ｰ</th>
        <th>螢ｲ繧頑ｮ矩≡鬘・/th>
        <th>螢ｲ繧頑ｮ句､牙喧</th>
        <th>雋ｷ縺・ｮ区椢謨ｰ</th>
        <th>雋ｷ縺・ｮ矩≡鬘・/th>
        <th>雋ｷ縺・ｮ句､牙喧</th>
        <th>菫｡逕ｨ蛟咲紫</th>
        <th>菫｡逕ｨ隧穂ｾ｡邇・/th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    繝ｻ菫｡逕ｨ隧穂ｾ｡邇・= 菫｡逕ｨ雋ｷ縺・ｒ縺励※縺・ｋ莠ｺ縺溘■縺ｮ蟷ｳ蝮・性縺ｿ謳咲寢邇・ｼ・・峨ょ性縺ｿ謳阪′蟆上＆縺・ｼ・2雜・ｼ会ｼ晏､ｩ莠募恟縺ｧ隱ｿ謨ｴ隴ｦ謌偵・15%蜑ｲ繧後〒蠎募､蝨上→縺輔ｌ繧九・br>
    繝ｻ螢ｲ繧頑ｮ九′螟壹＞・・0荳・ｶ・ｼ会ｼ晏ｰ・擂縺ｮ雋ｷ縺・綾縺暦ｼ郁ｲｷ縺・悸蜉幢ｼ峨′遨阪∩荳翫′縺｣縺ｦ縺・ｋ迥ｶ諷九・br>
    繝ｻ<a href="{src_url}" style="color:#60a5fa">nikkei225jp.com 菫｡逕ｨ隧穂ｾ｡謳咲寢邇・/a>
  </p>
  <p class="updated">譛邨よ峩譁ｰ: {updated}</p>
</body>
</html>
"""


def fmt_int(v):
    return f"{v:,.0f}" if v is not None else "-"


def fmt_pct(v, signed=True):
    if v is None:
        return "-"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+.2f}%</span>' if signed else f"{v:.2f}"


def rate_class(v):
    if v is None:
        return "rate-normal"
    if v > -2:
        return "rate-top"
    if v < -10:
        return "rate-danger"
    if v <= -9:
        return "rate-caution"
    return "rate-normal"


def generate_html(hist):
    dates = sorted(hist.keys(), reverse=True)  # 譛譁ｰ縺御ｸ・    rows = []
    for i, d in enumerate(dates):
        r = hist[d]
        cls = ' class="latest-row"' if i == 0 else ""
        sell_cls = ' class="sell-calm"' if (r.get("螢ｲ繧頑ｮ矩≡鬘・) or 0) > SELL_ALERT else ""
        buy_cls = ' class="buy-warn"' if (r.get("雋ｷ縺・ｮ矩≡鬘・) or 0) > BUY_ALERT else ""
        rc = rate_class(r.get("菫｡逕ｨ隧穂ｾ｡邇・))
        rate_v = r.get("菫｡逕ｨ隧穂ｾ｡邇・)
        rate_s = f"{rate_v:+.2f}" if rate_v is not None else "-"
        bairitsu = r.get("菫｡逕ｨ蛟咲紫")
        rows.append(
            f'      <tr{cls}><td>{d.replace("-", "/")}</td>'
            f'<td>{fmt_int(r.get("螢ｲ繧頑ｮ区椢謨ｰ"))}</td>'
            f'<td{sell_cls}>{fmt_int(r.get("螢ｲ繧頑ｮ矩≡鬘・))}</td>'
            f'<td>{fmt_pct(r.get("螢ｲ繧頑ｮ句､牙喧"))}</td>'
            f'<td>{fmt_int(r.get("雋ｷ縺・ｮ区椢謨ｰ"))}</td>'
            f'<td{buy_cls}>{fmt_int(r.get("雋ｷ縺・ｮ矩≡鬘・))}</td>'
            f'<td>{fmt_pct(r.get("雋ｷ縺・ｮ句､牙喧"))}</td>'
            f'<td>{bairitsu if bairitsu is not None else "-"}</td>'
            f'<td class="{rc}">{rate_s}</td></tr>'
        )

    html = HTML_TEMPLATE.format(
        latest_date=dates[0].replace("-", "/") if dates else "-",
        rows="\n".join(rows),
        sell_alert=SELL_ALERT,
        buy_alert=BUY_ALERT,
        src_url=PAGE_URL,
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML蜃ｺ蜉・ {path}")


# -----------------------------------------
# GitHub Pages 閾ｪ蜍・push
# -----------------------------------------
def push_to_github():
    log("GitHub Pages 縺ｫ蜈ｬ髢倶ｸｭ...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    os.path.basename(HISTORY_JSON), ".gitignore",
                    "shinyou_screen.py", "shinyou_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update shinyou report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/shinyou.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("菫｡逕ｨ隧穂ｾ｡謳咲寢邇・繝√ぉ繝・け髢句ｧ・)

    hist = load_history()

    try:
        weekly = fetch_weekly_data()
    except Exception as e:
        log(f"繧ｨ繝ｩ繝ｼ: 繝・・繧ｿ縺ｮ蜿門ｾ励↓螟ｱ謨励＠縺ｾ縺励◆: {e}")
        sys.exit(1)

    log(f"繧ｵ繧､繝井ｸ翫・繝・・繧ｿ: {len(weekly)}騾ｱ蛻・({weekly[0][0]} ・・{weekly[-1][0]})")

    added = 0
    for d, rec in weekly:
        if d in hist:
            continue
        hist[d] = rec
        added += 1
        log(f"霑ｽ險・ {d}  隧穂ｾ｡邇・{rec['菫｡逕ｨ隧穂ｾ｡邇・]:+.2f}% 螢ｲ繧頑ｮ・{rec['螢ｲ繧頑ｮ矩≡鬘・]:,.0f} 雋ｷ縺・ｮ・{rec['雋ｷ縺・ｮ矩≡鬘・]:,.0f}")

    if added:
        save_history(hist)
        log(f"螻･豁ｴ菫晏ｭ・ {len(hist)}騾ｱ蛻・)
    else:
        log("譁ｰ縺励＞繝・・繧ｿ縺ｯ縺ゅｊ縺ｾ縺帙ｓ縺ｧ縺励◆")

    if not hist:
        log("繝・・繧ｿ縺後↑縺・◆繧？TML縺ｯ逕滓・縺励∪縺帙ｓ")
        return

    generate_html(hist)

    if "--nopush" in sys.argv:
        log("--nopush 謖・ｮ壹・縺溘ａ git push 縺ｯ繧ｹ繧ｭ繝・・")
    else:
        push_to_github()

    log("螳御ｺ・)


if __name__ == "__main__":
    main()
