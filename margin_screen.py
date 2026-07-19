# -*- coding: utf-8 -*-
"""
銘柄別 制度信用倍率 検索ページ → HTML出力 → GitHub Pages公開

JPX「銘柄別信用取引週末残高」PDF（毎週第3営業日頃公表、サイトには直近5週分）
から全銘柄の制度信用の売残・買残をパースし、margin_all_history.json に週次で
蓄積する。margin.html は証券コードを入力して過去の制度信用倍率の推移を
ブラウザ内検索（JS）で表示する。カンマ区切りで複数銘柄の比較も可能。

- 倍率 = 制度信用買残 ÷ 制度信用売残
- 1未満 = 売り方過多（踏み上げ期待・水色） / 20以上 = 信用買い過熱（赤）
- データはJSONを margin.html と同時にpush（GitHub Pagesから fetch で読む）
"""

import os
import re
import sys
import json
import time
import tempfile
import subprocess
import datetime

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

JPX_MARGIN_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"
JPX_BASE = "https://www.jpx.co.jp"
HISTORY_JSON = os.path.join(SCRIPT_DIR, "margin_all_history.json")
REPORT_HTML = "margin.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# JPX PDF パース
# -----------------------------------------
def list_pdfs():
    """(日付ISO, URL) のリスト（古い順）"""
    r = requests.get(JPX_MARGIN_PAGE, headers=HEADERS, timeout=30)
    r.raise_for_status()
    links = re.findall(r'href="([^"]*syumatsu(\d{8})\d{2}\.pdf)"', r.text)
    out = {}
    for path, ymd in links:
        d = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        out[d] = JPX_BASE + path
    return sorted(out.items())


def parse_pdf(url):
    """全銘柄をパースして {code: [name, sell, buy]} を返す"""
    import pdfplumber

    r = requests.get(url, headers=HEADERS, timeout=90)
    r.raise_for_status()
    tmp = os.path.join(tempfile.gettempdir(), "_jpx_margin_all_tmp.pdf")
    with open(tmp, "wb") as f:
        f.write(r.content)

    def parse_line(line):
        """(code, name, toks) を返す。通常行はISIN基準、ETF交錯行はフォールバック"""
        nospace = line.replace(" ", "")
        m = re.search(r"JP[A-Z0-9]{10}", nospace)
        if m:
            pre = nospace[:nospace.find(m.group(0))]
            mc = re.search(r"(\d{4})0$", pre)
            if mc:
                code = mc.group(1)
                name = re.sub(r"^[AB]?", "", pre[:mc.start()])
                name = re.sub(r"(普通株式|受益証券|優先株式|外国株).*$", "", name)
                seg = line[line.find("JP"):].replace("▲ ", "-").replace("▲", "-")
                return code, name, re.findall(r"-?[\d,]+", seg[12:])
        # ETF行は「受益証券」「新証券コード」の文字が銘柄名と交錯してISINが壊れることがある
        if re.search(r"受.{0,8}益|投.{0,8}信", line) and "券" in line:
            anchor = line.rfind("券")
            head = nospace[:nospace.rfind("券") + 1]
            digits = "".join(re.findall(r"\d", head))
            if len(digits) >= 5 and digits[4] == "0":
                code = digits[:4]
                nm = re.sub(r"[A-Za-z0-9]", "", head)
                nm = re.sub(r"^[AB]?", "", nm)
                nm = re.sub(r"(受益証券|連動型|上場投信|受益|証券|投信).*$", "", nm)
                nm = re.sub(r"[・、]$", "", nm)
                seg = line[anchor + 1:].replace("▲ ", "-").replace("▲", "-")
                return code, nm, re.findall(r"-?[\d,]+", seg)
        return None, None, None

    out = {}
    with pdfplumber.open(tmp) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            for line in txt.splitlines():
                code, name, toks = parse_line(line)
                if not code or not toks or len(toks) < 12:
                    continue
                try:
                    sell = int(toks[6].replace(",", ""))
                    buy = int(toks[10].replace(",", ""))
                except ValueError:
                    continue
                out[code] = [name, sell, buy]
    try:
        os.remove(tmp)
    except OSError:
        pass
    return out


# -----------------------------------------
# 履歴（JSON）
# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        with open(HISTORY_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {"names": {}, "weeks": {}}


def save_history(hist):
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))


# -----------------------------------------
# HTML出力（検索はブラウザ内JS）
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>銘柄別 制度信用倍率 - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 20px 0 8px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  .searchbox {{ display: flex; gap: 10px; margin-bottom: 6px; flex-wrap: wrap; }}
  .searchbox input {{
    background: #1e293b; border: 1px solid #334155; color: #f8fafc;
    padding: 9px 14px; border-radius: 8px; font-size: 1rem; width: 300px;
  }}
  .searchbox input:focus {{ outline: none; border-color: #3b82f6; }}
  .searchbox button {{
    background: #1e40af; border: 1px solid #3b82f6; color: #bfdbfe;
    padding: 9px 22px; border-radius: 8px; font-size: 1rem; cursor: pointer; font-weight: 700;
  }}
  .searchbox button:hover {{ background: #1d4ed8; }}
  .hint {{ font-size: 0.78rem; color: #64748b; margin-bottom: 18px; }}
  .err {{ color: #f87171; font-size: 0.9rem; margin: 10px 0; }}
  table {{ border-collapse: collapse; font-size: 0.88rem; margin-bottom: 8px; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 9px 14px;
    text-align: right; font-weight: 600; white-space: nowrap;
  }}
  thead th:first-child {{ text-align: left; }}
  td {{
    padding: 8px 14px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{ text-align: left; color: #94a3b8; }}
  tr:hover td {{ background: #16213a; }}
  td.low {{ background: rgba(56,189,248,0.28); color: #7dd3fc; font-weight: bold; }}
  td.high {{ background: rgba(220,38,38,0.28); color: #fca5a5; font-weight: bold; }}
  td.ratio {{ font-weight: 700; color: #f8fafc; }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 14px; line-height: 1.8; max-width: 900px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
</style>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="minervini_report_v2.html">米国株 (Minervini)</a>
    <a href="haitou.html">日本株 (配当)</a>
    <a href="jpminervini.html">日本株 (Minervini)</a>
    <a href="saitei.html">裁定取引</a>
    <a href="totan.html">日銀利上げ確率</a>
    <a href="fedwatch.html">FRB利上げ確率</a>
    <a href="kinri.html">金利と為替</a>
    <a href="vix.html">VIX温度計</a>
    <a href="daikin.html">売買代金</a>
    <a href="shinyou.html">信用評価率</a>
    <a href="margin.html" class="active">銘柄別信用倍率</a>
    <a href="shutai.html">投資主体別</a>
    <a href="gaikoku.html">海外投資家</a>
    <a href="touraku.html">騰落レシオ</a>
    <a href="karauri.html">空売り比率</a>
    <a href="riron.html">日経理論株価</a>
    <a href="spriron.html">SP500理論株価</a>
    <a href="flow.html">資金フロー</a>
    <a href="calendar.html">イベント予定</a>
    <a href="map.html">デッキの見方</a>
  </nav>
  <h1>銘柄別 制度信用倍率 検索</h1>
  <p class="subtitle">最終更新: {updated} | 出所: JPX 銘柄別信用取引週末残高（週次） | 収録: {n_codes}銘柄 × {n_weeks}週分</p>
  <div class="searchbox">
    <input type="text" id="codes" placeholder="証券コード（例: 5411 または 5411,7203,1570）"
           onkeydown="if(event.key==='Enter')search()">
    <button onclick="search()">検索</button>
  </div>
  <p class="hint">カンマ区切りで複数銘柄を同時比較できます。倍率 = 制度信用買残 ÷ 制度信用売残。<span style="color:#7dd3fc">1未満 = 売り方過多（踏み上げ期待）</span> / <span style="color:#fca5a5">20以上 = 信用買い過熱</span></p>
  <div id="result"></div>
  <p class="note">
    ・JPX「銘柄別信用取引週末残高」（毎週金曜申込時点、翌週第3営業日頃公表）から制度信用の残高を毎週自動で蓄積。<br>
    ・収録開始は2026-06-12分から。以降、毎週自動で積み上がっていく。<br>
    ・単位は株（ETFは口）。<a href="https://www.jpx.co.jp/markets/statistics-equities/margin/05.html" style="color:#60a5fa">JPX 銘柄別信用取引週末残高</a>
  </p>
  <p class="updated">最終更新: {updated}</p>
<script>
let DATA = null;

async function loadData() {{
  if (DATA) return DATA;
  const res = await fetch('margin_all_history.json');
  DATA = await res.json();
  return DATA;
}}

function fmt(n) {{ return n.toLocaleString('ja-JP'); }}

async function search() {{
  const raw = document.getElementById('codes').value.trim();
  const out = document.getElementById('result');
  if (!raw) {{ out.innerHTML = ''; return; }}
  out.innerHTML = '<p class="hint">読み込み中...</p>';
  let data;
  try {{
    data = await loadData();
  }} catch (e) {{
    out.innerHTML = '<p class="err">データの読み込みに失敗しました</p>';
    return;
  }}
  const codes = raw.split(/[,、\\s]+/).map(c => c.trim()).filter(c => c);
  const weeks = Object.keys(data.weeks).sort().reverse();  // 新しい順
  let html = '';
  for (const code of codes) {{
    const name = data.names[code];
    if (!name) {{
      html += '<p class="err">' + code + ': データがありません（制度信用の対象外か、コード誤り）</p>';
      continue;
    }}
    html += '<h2>' + code + ' ' + name + '</h2>';
    html += '<table><thead><tr><th>週（申込日）</th><th>制度買残</th><th>制度売残</th><th>制度信用倍率</th><th>前週比</th></tr></thead><tbody>';
    let prev = null;
    const rows = [];
    for (const w of weeks) {{
      const rec = data.weeks[w][code];
      if (!rec) {{ rows.push({{w: w, none: true}}); continue; }}
      const sell = rec[0], buy = rec[1];
      const ratio = sell > 0 ? buy / sell : null;
      rows.push({{w: w, sell: sell, buy: buy, ratio: ratio}});
    }}
    // 前週比は古い順で計算してから新しい順で表示
    for (let i = rows.length - 1; i >= 0; i--) {{
      if (rows[i].none || rows[i].ratio === null) continue;
      let p = null;
      for (let j = i + 1; j < rows.length; j++) {{
        if (!rows[j].none && rows[j].ratio !== null) {{ p = rows[j].ratio; break; }}
      }}
      rows[i].chg = (p !== null) ? rows[i].ratio - p : null;
    }}
    for (const r of rows) {{
      if (r.none) {{
        html += '<tr><td>' + r.w.replace(/-/g, '/') + '</td><td>-</td><td>-</td><td>-</td><td>-</td></tr>';
        continue;
      }}
      let cls = 'ratio';
      if (r.ratio !== null && r.ratio < 1) cls = 'low';
      else if (r.ratio !== null && r.ratio >= 20) cls = 'high';
      const ratioStr = r.ratio !== null ? r.ratio.toFixed(2) + 'x' : '-';
      let chgStr = '-';
      if (r.chg !== null && r.chg !== undefined) {{
        const sign = r.chg > 0 ? '+' : '';
        chgStr = '<span class="' + (r.chg > 0 ? 'pos' : 'neg') + '">' + sign + r.chg.toFixed(2) + '</span>';
      }}
      html += '<tr><td>' + r.w.replace(/-/g, '/') + '</td><td>' + fmt(r.buy) + '</td><td>'
            + fmt(r.sell) + '</td><td class="' + cls + '">' + ratioStr + '</td><td>' + chgStr + '</td></tr>';
    }}
    html += '</tbody></table>';
  }}
  out.innerHTML = html;
}}
</script>
</body>
</html>
"""


def generate_html(hist):
    weeks = sorted(hist["weeks"])
    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_codes=len(hist["names"]),
        n_weeks=len(weeks),
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
                    "margin_all_history.json", ".gitignore",
                    "margin_screen.py", "margin_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update margin report " + today],
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
            log("  Done: https://ichikon77.github.io/minervini/margin.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    log("銘柄別制度信用倍率 チェック開始")

    hist = load_history()

    try:
        pdfs = list_pdfs()
    except Exception as e:
        log(f"エラー: JPXページの取得に失敗しました: {e}")
        sys.exit(1)
    log(f"JPXサイト上のPDF: {len(pdfs)}週分 ({pdfs[0][0]} ～ {pdfs[-1][0]})")

    added = 0
    for d, url in pdfs:
        if d in hist["weeks"]:
            continue
        log(f"  {d} 分をパース中...")
        try:
            data = parse_pdf(url)
        except Exception as e:
            log(f"  {d}: パース失敗 {e}")
            continue
        if len(data) < 3000:
            log(f"  {d}: 銘柄数が少なすぎるためスキップ（{len(data)}）")
            continue
        hist["weeks"][d] = {c: [v[1], v[2]] for c, v in data.items()}
        for c, v in data.items():
            hist["names"][c] = v[0]
        added += 1
        save_history(hist)  # 週ごとに保存（途中で落ちても再開できる）
        log(f"  {d}: {len(data)}銘柄を追加・保存")

    if added:
        log(f"履歴: {len(hist['weeks'])}週分 / {len(hist['names'])}銘柄")
    else:
        log("新しい週はありませんでした")

    if not hist["weeks"]:
        log("データがないためHTMLは生成しません")
        return

    generate_html(hist)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
