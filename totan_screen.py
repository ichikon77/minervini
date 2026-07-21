# -*- coding: utf-8 -*-
"""
東短リサーチ「マーケットから読み解く日銀利上げ確率」自動記録 → HTML出力 → GitHub Pages公開

https://www.tokyotanshi.co.jp/archives/15647
に毎日掲載される表画像（日銀会合OIS気配値・織り込み比率）をOCRで読み取り、
会合ごとの「政策変更織り込み比率(%)」を日次で蓄積する。

- 表は画像でしか提供されないため tesseract OCR で読み取る
- OIS気配値・対前会合差分・織り込み比率・累積回数の整合性を検算し、
  読み取りミスがあれば記録せずエラーログを残す（誤データ混入防止）
- 履歴は totan_history.json に蓄積、totan.html を生成して git push
- 必要環境: pip install requests pillow numpy
            + Tesseract OCR本体 (https://github.com/UB-Mannheim/tesseract/wiki)
"""

import os
import re
import sys
import json
import time
import subprocess
import datetime

import requests
from PIL import Image
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

PAGE_URL = "https://www.tokyotanshi.co.jp/archives/15647"

HISTORY_JSON = os.path.join(SCRIPT_DIR, "totan_history.json")
REPORT_HTML = "totan.html"

# Windowsでのtesseract.exeの標準インストール先（PATHにあればそのまま"tesseract"でOK）
TESSERACT_CANDIDATES = [
    "tesseract",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

RATE_STEP = 0.25  # 利上げ幅の仮定（東短の分析前提）
PCT_TOLERANCE = 2.5   # 比率検算の許容誤差(%ポイント)
CUM_TOLERANCE = 0.06  # 累積回数検算の許容誤差


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def find_tesseract():
    for cand in TESSERACT_CANDIDATES:
        try:
            subprocess.run([cand, "--version"], capture_output=True, timeout=10)
            return cand
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError(
        "tesseractが見つかりません。https://github.com/UB-Mannheim/tesseract/wiki "
        "からインストールしてください")


TESSERACT = None  # main()で設定


# -----------------------------------------
# ページから表画像を取得
# -----------------------------------------
def fetch_with_retry(url, tries=5, timeout=60, wait=120):
    """サイト不調(504等)に備え、失敗したら2分おきに最大5回リトライ"""
    last_err = None
    for i in range(1, tries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            log(f"  取得失敗 ({i}/{tries}): {e}")
            if i < tries:
                time.sleep(wait)
    raise last_err


def fetch_table_image():
    r = fetch_with_retry(PAGE_URL)
    # 本文中の /wp-content/uploads/ 配下のpng（1枚目が表、2枚目がグラフ）
    imgs = re.findall(r'<img[^>]+src="(https://www\.tokyotanshi\.co\.jp/wp-content/uploads/[^"]+\.png)"', r.text)
    if not imgs:
        raise ValueError("ページ内に表画像が見つかりません（ページ構造が変わった可能性）")
    url = imgs[0]
    # サムネイルサイズ表記（-1030x341など）を外してフル解像度を取得
    full_url = re.sub(r'-\d+x\d+(\.png)$', r'\1', url)
    for u in (full_url, url):
        try:
            resp = fetch_with_retry(u, tries=3)
        except Exception:
            continue
        if resp.status_code == 200:
            path = os.path.join(SCRIPT_DIR, "_totan_table.png")
            with open(path, "wb") as f:
                f.write(resp.content)
            return path
    raise ValueError("表画像のダウンロードに失敗しました")


# -----------------------------------------
# OCR
# -----------------------------------------
def load_grayscale(path):
    img = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.alpha_composite(bg, img).convert("L")


def detect_grid(img):
    """罫線（縦線・横線）の位置を検出"""
    a = np.array(img)
    h, w = a.shape
    dark = a < 100

    def group(xs, gap=3):
        out = []
        for x in xs:
            if out and x - out[-1][-1] <= gap:
                out[-1].append(x)
            else:
                out.append([x])
        return [int(np.mean(g)) for g in out]

    vlines = group([x for x in range(w) if dark[:, x].sum() / h > 0.5])
    hlines = group([y for y in range(h) if dark[y, :].sum() / w > 0.5])

    if len(vlines) < 5 or len(hlines) < 3:
        raise ValueError(f"表の罫線が検出できません (縦{len(vlines)}本, 横{len(hlines)}本)")
    return vlines, hlines


def ocr_region(img, box, whitelist=None, psm="7"):
    cell = img.crop(box)
    cell = cell.resize((cell.width * 4, cell.height * 4), Image.LANCZOS)
    cell = cell.point(lambda p: 0 if p < 150 else 255)
    tmp = os.path.join(SCRIPT_DIR, "_totan_cell.png")
    cell.save(tmp)
    cmd = [TESSERACT, tmp, "-", "--psm", psm]
    if whitelist:
        cmd += ["-c", f"tessedit_char_whitelist={whitelist}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip()


def parse_table(img_path):
    """表画像から (as_of日付, [(会合, OIS, 差分, 比率%, 累積回数), ...]) を抽出"""
    img = load_grayscale(img_path)
    vlines, hlines = detect_grid(img)

    rows = []
    for i in range(len(hlines) - 1):
        y0, y1 = hlines[i], hlines[i + 1]
        if y1 - y0 < 20:
            continue
        first = ocr_region(img, (vlines[0] + 3, y0 + 3, vlines[1] - 3, y1 - 3),
                           whitelist="0123456789/")
        m = re.search(r'(\d{4}/\d{2})', first)
        if not m:
            continue  # ヘッダ行など
        meeting = m.group(1)

        def cell(j, wl):
            return ocr_region(img, (vlines[j] + 3, y0 + 3, vlines[j + 1] - 3, y1 - 3), whitelist=wl)

        try:
            ois = float(cell(1, "0123456789.-"))
            diff = float(cell(2, "0123456789.-"))
            pct_txt = cell(3, "0123456789.%-")
            pct = float(pct_txt.replace("%", ""))
            freq = float(cell(4, "0123456789.-"))
        except ValueError as e:
            raise ValueError(f"{meeting} 行の数値が読み取れません: {e}")
        rows.append((meeting, ois, diff, pct, freq))

    if not rows:
        raise ValueError("表のデータ行が1行も読み取れませんでした")

    # 表の下の「2026/7/15 15:15」からas of日付を取得
    as_of = None
    footer = ocr_region(img, (0, hlines[-1], img.width // 2, img.height), psm="6")
    m = re.search(r'(\d{4})/(\d{1,2})/(\d{1,2})', footer)
    if m:
        try:
            as_of = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    if as_of is None:
        as_of = datetime.date.today()
        log("注意: 画像内の日付が読めなかったため本日日付で記録します")

    return as_of, rows


def validate(rows):
    """OIS気配値・差分・比率・累積回数の整合性チェック（OCR誤読の検出）"""
    prev_ois = None
    cum = 0.0
    for meeting, ois, diff, pct, freq in rows:
        # 差分 = OISの前会合との差
        if prev_ois is not None:
            calc = ois - prev_ois
            if abs(calc - diff) > 0.003:
                raise ValueError(
                    f"{meeting}: 差分の検算エラー ({ois}-{prev_ois}={calc:.4f} != {diff})")
        # 比率 = 差分 / 0.25
        calc_pct = diff / RATE_STEP * 100
        if abs(calc_pct - pct) > PCT_TOLERANCE:
            raise ValueError(
                f"{meeting}: 比率の検算エラー ({diff}/{RATE_STEP}={calc_pct:.1f}% != {pct}%)")
        # 累積回数
        cum += pct / 100
        if abs(cum - freq) > CUM_TOLERANCE:
            raise ValueError(
                f"{meeting}: 累積回数の検算エラー (累積{cum:.2f} != {freq})")
        prev_ois = ois
    return True


# -----------------------------------------
# 履歴（JSON）
# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        with open(HISTORY_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(hist):
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)


# -----------------------------------------
# HTML出力
# -----------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>日銀利上げ織り込み比率 - {latest_date}</title>
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
  .table-wrap {{
    overflow: auto;
    max-height: calc(100vh - 180px);
    max-width: 900px;
  }}
  table {{ border-collapse: collapse; font-size: 0.88rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 10px 14px;
    text-align: right; font-weight: 600;
    position: sticky; top: 0; white-space: nowrap; z-index: 2;
  }}
  thead th:first-child {{
    text-align: left; position: sticky; left: 0; z-index: 3;
  }}
  td {{
    padding: 8px 14px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{
    text-align: left; color: #94a3b8; position: sticky; left: 0;
    background: #0f172a; z-index: 1;
  }}
  tr:hover td {{ background: #16213a; }}
  tr:hover td:first-child {{ background: #16213a; }}
  .latest-row td {{ background: rgba(30,64,175,0.18); font-weight: bold; }}
  .hi {{ color: #f87171; }}
  .mid {{ color: #fbbf24; }}
  .lo {{ color: #94a3b8; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.8; }}
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
    <a href="totan.html" class="active" style="border-color:#7c3aed">日銀利上げ確率</a>
    <a href="kinri.html" style="border-color:#7c3aed">金利と為替</a>
    <a href="spriron.html" style="border-color:#7c3aed">SP500理論株価</a>
    <a href="riron.html" style="border-color:#7c3aed">日経理論株価</a>
    <a href="gaikoku.html" style="border-color:#2563eb">海外投資家</a>
    <a href="saitei.html" style="border-color:#2563eb">裁定取引</a>
    <a href="shutai.html" style="border-color:#2563eb">投資主体別</a>
    <a href="margin.html" style="border-color:#2563eb">銘柄別信用倍率</a>
    <a href="insider.html" style="border-color:#2563eb">インサイダー売買</a>
    <a href="buffett.html" style="border-color:#2563eb">バフェット</a>
    <a href="shinyou.html" style="border-color:#d97706">信用評価率</a>
    <a href="touraku.html" style="border-color:#d97706">騰落レシオ</a>
    <a href="karauri.html" style="border-color:#d97706">空売り比率</a>
    <a href="vix.html" style="border-color:#d97706">VIX温度計</a>
    <a href="flow.html" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>日銀会合ごとの利上げ織り込み比率</h1>
  <p class="subtitle">最終更新: {updated} | 出所: 東短リサーチ/東短ICAP（日銀会合OIS気配より） | 単位: %</p>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>日付</th>
{header_cells}
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・各日銀金融政策決定会合で0.25%の利上げが行われる確率の市場織り込み（日銀会合OIS気配から東短リサーチが試算）。<br>
    ・出所ページの表画像を毎日自動読み取りして蓄積。<br>
    ・<a href="{src_url}" style="color:#60a5fa">東短リサーチ 元ページ</a>
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def meeting_sort_key(mtg):
    y, m = mtg.split("/")
    return int(y) * 100 + int(m)


def fmt_pct(v):
    if v is None:
        return "-"
    cls = "hi" if v >= 50 else ("mid" if v >= 20 else "lo")
    s = f"{v:.0f}" if float(v) == int(v) else f"{v:.1f}"
    return f'<span class="{cls}">{s}%</span>'


def generate_html(hist):
    # 新しい会合ほど左に表示（降順）。新会合が出たら左端に追加される
    meetings = sorted({m for rec in hist.values() for m in rec}, key=meeting_sort_key, reverse=True)
    dates = sorted(hist.keys(), reverse=True)

    header_cells = "\n".join(f'        <th>{m}会合</th>' for m in meetings)

    rows = []
    for i, d in enumerate(dates):
        rec = hist[d]
        cls = ' class="latest-row"' if i == 0 else ""
        cells = "".join(f"<td>{fmt_pct(rec.get(m))}</td>" for m in meetings)
        rows.append(f'      <tr{cls}><td>{d.replace("-", "/")}</td>{cells}</tr>')

    html = HTML_TEMPLATE.format(
        latest_date=dates[0].replace("-", "/") if dates else "-",
        header_cells=header_cells,
        rows="\n".join(rows),
        src_url=PAGE_URL,
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}")


# -----------------------------------------
# GitHub Pages 自動 push（他スクリーナーと同方式）
# -----------------------------------------
def push_to_github():
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    os.path.basename(HISTORY_JSON), ".gitignore",
                    "totan_screen.py", "totan_run.bat"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update totan report " + today],
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
            subprocess.run(["git", "-C", SCRIPT_DIR, "push"],            check=True)
            log("  Done: https://ichikon77.github.io/minervini/totan.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def main():
    global TESSERACT
    log("東短 日銀利上げ織り込み比率 チェック開始")

    TESSERACT = find_tesseract()

    hist = load_history()

    try:
        img_path = fetch_table_image()
        as_of, rows = parse_table(img_path)
        validate(rows)
    except Exception as e:
        log(f"エラー: 表の取得/読み取りに失敗しました: {e}")
        if hist and "--nopush" not in sys.argv:
            # 読み取り失敗でも既存データでHTMLは再生成しない（変化がないため）
            pass
        sys.exit(1)

    key = as_of.isoformat()
    rec = {meeting: pct for meeting, _, _, pct, _ in rows}

    if hist.get(key) == rec:
        log(f"{key} は記録済み・変化なし")
    else:
        hist[key] = rec
        save_history(hist)
        summary = " / ".join(f"{m}:{p:.0f}%" for m, p in rec.items())
        log(f"記録: {key}  {summary}")

    generate_html(hist)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()

    log("完了")


if __name__ == "__main__":
    main()
