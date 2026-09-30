# -*- coding: utf-8 -*-
"""
JPX 銘柄別信用取引残高 の共通パーサ（margin / haitou / jpminervini / shinyou で共用）

2026-09-25申込分から JPX が「銘柄別信用取引週末残高」(週次, syumatsuYYYYMMDD00.pdf, 05.html)
を廃止し「銘柄別信用取引残高」(日次・16:00目安掲載, YYYYMMDD_mtall.pdf, 01.html) に変更した。
このモジュールは新旧どちらの PDF でも同じ dict を返す。

  list_pdfs()            -> [(date_iso, url, fmt), ...] 古い順。fmt は "daily" / "weekly"
  download_pdf(url)      -> 一時ファイルのパス
  parse_pdf_file(path)   -> {code: {"name", "std_sell", "std_buy", "gen_sell", "gen_buy"}}
  parse_pdf(url)         -> download + parse + 一時ファイル削除
  latest_from_history()  -> margin_all_history.json の最新日付分を (date, {code: [std_sell, std_buy, gen_sell, gen_buy]}) で返す
                            （haitou / jpminervini が100秒のPDFパースを毎回やらずに済むようにするための近道）

新フォーマットの行構造（1銘柄2行。株数行だけ使う）:
  B サカタのタネ 普通株式 プライム 貸 13770 JP3315000004株数 Shs. 売残 前日比 上場比% 買残 前日比 上場比% 一般売 前日比 制度売 前日比 一般買 前日比 制度買 前日比
  ▲ は減少。新証券コードは5桁（13770→1377, 137A0→137A）。上場比は ETF だと "*"。
  同じ4桁コードで「種類株式」行（ANA第1回社債型種類株式など）が出るので「普通株式」「受益証券」行を優先する。
"""

import os
import re
import json
import tempfile

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JPX_BASE = "https://www.jpx.co.jp"
JPX_MARGIN_PAGES = [
    "https://www.jpx.co.jp/markets/statistics-equities/margin/01.html",  # 2026-09-25〜 銘柄別信用取引残高（日次）
    "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html",  # 旧: 銘柄別信用取引週末残高
]
HISTORY_JSON = os.path.join(SCRIPT_DIR, "margin_all_history.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

# 新フォーマットの株数行の数値列（14個）
_NEW_FIELDS = ["sell", "sell_chg", "sell_ratio", "buy", "buy_chg", "buy_ratio",
               "gen_sell", "gen_sell_chg", "std_sell", "std_sell_chg",
               "gen_buy", "gen_buy_chg", "std_buy", "std_buy_chg"]


# -----------------------------------------
# リンク一覧
# -----------------------------------------
def list_pdfs():
    """[(date_iso, url, fmt)] を古い順で返す。同じ日付が両形式にあれば daily を優先"""
    out = {}
    for page in JPX_MARGIN_PAGES:
        try:
            r = requests.get(page, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception:
            continue
        for path, ymd in re.findall(r'href="([^"]*?/(\d{8})_mtall\.pdf)"', r.text):
            d = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
            out[d] = (JPX_BASE + path, "daily")
        for path, ymd in re.findall(r'href="([^"]*syumatsu(\d{8})\d{2}\.pdf)"', r.text):
            d = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
            out.setdefault(d, (JPX_BASE + path, "weekly"))
        if out:
            break  # 01.html で取れたら 05.html は見ない
    return [(d, u, f) for d, (u, f) in sorted(out.items())]


def download_pdf(url, name="_jpx_margin_shared_tmp.pdf"):
    r = requests.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    tmp = os.path.join(tempfile.gettempdir(), name)
    with open(tmp, "wb") as f:
        f.write(r.content)
    return tmp


# -----------------------------------------
# パース
# -----------------------------------------
def _nums(s):
    """'1,000 ▲ 500 0.1% * 300' -> [1000, -500, 0.1, None, 300]"""
    out = []
    for t in s.replace("▲ ", "▲").split():
        if t in ("*", "-"):
            out.append(None)
            continue
        neg = t.startswith("▲")
        t = t.lstrip("▲").replace(",", "").rstrip("%")
        try:
            v = float(t) if "." in t else int(t)
        except ValueError:
            continue
        out.append(-v if neg else v)
    return out


def _clean_name_new(head):
    """'B サカタのタネ 普通株式 プライム 貸' -> 'サカタのタネ'

    ETF行や長い社名は列が交錯して 'キオクシアホールディングス普通株プ式ライム貸' のように壊れる。
    ここではベストエフォートで削るだけなので、呼び出し側は既知の銘柄名（margin_all_history.json の names）を優先すること。
    """
    s = re.sub(r"^[AJKBMCTF]\s+", "", head.strip())
    s = re.split(r"\s*(普通株式|受益証券|優先株式|外国株|種類株式|投信等|出資証券|投資証券|普通株)", s)[0]
    s = re.sub(r"(プライム|スタンダード|グロース|プロマーケット|貸|制|他)$", "", s.replace(" ", ""))
    s = re.sub(r"\s+", "", s)
    # 列交錯の後始末: 半角英数字（コード/ISIN/株数Shs.の残骸）が混ざる、または「投信等」「普通株式」の
    # 文字がバラけて混ざっている場合は、最初に混ざった文字の手前で切って「…」を付ける
    garbled = bool(re.search(r"[0-9A-Za-z]", s)) or ("等" in s) or ("普" in s and "普通株式" not in head)
    if garbled and len(s) > 2:
        m = re.search(r"[0-9A-Za-z.投信等受益証券貸制普通株式]", s[1:])
        if m:
            s = s[:m.start() + 1] + "…"
    return s or head.strip()


def _code_from_garbled(text):
    """交錯した行から新証券コード(4桁+0)を復元する。
    JPXのPDFで銘柄名中の数字は全角なので、半角数字はコードかISINしか無い。
    'Fun1d3570 JP3047780006' -> 半角数字列 '13570' + ISIN -> 1357。
    ISINより前に5桁揃わないケースもあるので、ISIN(JP/US+10桁)を先に取り除く"""
    text = re.sub(r"[A-Z]{2}[0-9A-Z]{10}", " ", text)
    m = re.search(r"([0-9][0-9A-Z]{3})0(?![0-9A-Z])", text.replace(" ", ""))
    digits = "".join(re.findall(r"[0-9]", text))
    if len(digits) >= 5 and digits[4] == "0":
        return digits[:4]
    if m:
        return m.group(1)
    return None


def _parse_new_line(line, next_line=""):
    """株数行をパース。ETF行は銘柄名と「投信等/受益証券/貸」の列が交錯してコードが壊れるので、
    直後の金額行（'15700 JP3047460005金額 Val.' と綺麗に出る）からコードを取る"""
    m = re.search(r"([0-9][0-9A-Z]{3}0) ([A-Z]{2}[0-9A-Z]{10})株数 Shs\. (.*)$", line)
    if m:
        code = m.group(1)[:4]
        head = line[:m.start()]
        rest = m.group(3)
    else:
        # 交錯がひどいと '株受数益 証Sh券s.' のように「株数 Shs.」自体が壊れるので、
        # 行末から数値トークン（数字/▲/*/％）を拾って14個以上あれば数値部とみなす
        if "金額" in line or "Val." in line:
            return None
        if "金額" not in next_line:
            return None
        toks = line.split()
        k = len(toks)
        while k > 0 and re.fullmatch(r"▲?[\d,]+(\.\d+)?%?|▲|\*", toks[k - 1]):
            k -= 1
        rest = " ".join(toks[k:])
        head = " ".join(toks[:k])
        code = _code_from_garbled(head) or _code_from_garbled(next_line.split("金額")[0])  # 英語行は "Nikkei 225" など半角数字を含むので和文行を先に
        if not code:
            return None
    nums = _nums(rest)
    if len(nums) < 14:
        return None
    d = dict(zip(_NEW_FIELDS, nums[:14]))
    common = ("普通株式" in head) or ("受益" in head) or ("投信" in head)
    return code, {
        "name": _clean_name_new(head),
        "std_sell": int(d["std_sell"] or 0), "std_buy": int(d["std_buy"] or 0),
        "gen_sell": int(d["gen_sell"] or 0), "gen_buy": int(d["gen_buy"] or 0),
    }, common


def _parse_old_line(line):
    """旧フォーマット（margin_screen.py 旧 parse_line を移植）"""
    nospace = line.replace(" ", "")
    m = re.search(r"JP[A-Z0-9]{10}", nospace)
    code = name = toks = None
    if m:
        pre = nospace[:nospace.find(m.group(0))]
        mc = re.search(r"(\d{4})0$", pre)
        if mc:
            code = mc.group(1)
            name = re.sub(r"^[AB]?", "", pre[:mc.start()])
            name = re.sub(r"(普通株式|受益証券|優先株式|外国株).*$", "", name)
            seg = line[line.find("JP"):].replace("▲ ", "-").replace("▲", "-")
            toks = re.findall(r"-?[\d,]+", seg[12:])
    if code is None and re.search(r"受.{0,8}益|投.{0,8}信", line) and "券" in line:
        anchor = line.rfind("券")
        head = nospace[:nospace.rfind("券") + 1]
        digits = "".join(re.findall(r"\d", head))
        if len(digits) >= 5 and digits[4] == "0":
            code = digits[:4]
            nm = re.sub(r"[A-Za-z0-9]", "", head)
            nm = re.sub(r"^[AB]?", "", nm)
            nm = re.sub(r"(受益証券|連動型|上場投信|受益|証券|投信).*$", "", nm)
            name = re.sub(r"[・、]$", "", nm)
            seg = line[anchor + 1:].replace("▲ ", "-").replace("▲", "-")
            toks = re.findall(r"-?[\d,]+", seg)
    if not code or not toks or len(toks) < 12:
        return None
    try:
        ints = [int(t.replace(",", "")) for t in toks[:12]]
    except ValueError:
        return None
    # 売残計,前週比,買残計,前週比,一般売,前週比,制度売,前週比,一般買,前週比,制度買,前週比
    return code, {"name": name, "std_sell": ints[6], "std_buy": ints[10],
                  "gen_sell": ints[4], "gen_buy": ints[8]}, True


def parse_pdf_file(path):
    """新旧自動判定。{code: {"name","std_sell","std_buy","gen_sell","gen_buy"}}"""
    import pdfplumber
    out = {}
    with pdfplumber.open(path) as pdf:
        first = pdf.pages[0].extract_text() or ""
        is_new = "Shs." in first or "銘柄別信用取引残高" in first.replace(" ", "")
        lines = []
        for page in pdf.pages:
            lines.extend((page.extract_text() or "").splitlines())
        if True:
            for i, line in enumerate(lines):
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                rec = _parse_new_line(line, nxt) if is_new else _parse_old_line(line)
                if not rec:
                    continue
                code, d, common = rec
                if code in out and not common:
                    continue  # 種類株式などは普通株式を上書きしない
                out[code] = d
    return out


def parse_pdf(url):
    tmp = download_pdf(url)
    try:
        return parse_pdf_file(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# -----------------------------------------
# margin_all_history.json からの近道
# -----------------------------------------
def latest_from_history(history_json=HISTORY_JSON, max_age_days=10):
    """(date_iso, {code: [std_sell, std_buy, gen_sell?, gen_buy?]}, names) を返す。無ければ (None, {}, {})

    margin_screen.py（毎日08:55）が蓄積したデータを再利用する。max_age_days より古ければ無効扱い。
    """
    import datetime
    if not os.path.exists(history_json):
        return None, {}, {}
    try:
        with open(history_json, encoding="utf-8") as f:
            hist = json.load(f)
        dates = sorted(hist.get("weeks", {}))
        if not dates:
            return None, {}, {}
        d = dates[-1]
        age = (datetime.date.today() - datetime.date.fromisoformat(d)).days
        if age > max_age_days:
            return None, {}, {}
        return d, hist["weeks"][d], hist.get("names", {})
    except Exception:
        return None, {}, {}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        data = parse_pdf_file(sys.argv[1])
        print(len(data), "銘柄")
        for c in ("8267", "9202", "1570", "285A", "1301"):
            print(c, data.get(c))
    else:
        for d, u, f in list_pdfs():
            print(d, f, u)
