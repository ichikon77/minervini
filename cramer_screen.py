# -*- coding: utf-8 -*-
"""
ジム・クレイマー Lightning Round 検証 → cramer.html → GitHub Pages公開

データ源: usa-option.com（マカベェさんの日本語訳ブログ）の連番記事
  https://usa-option.com/lightning-round-NNNN/
  本文構造:「社名 (TICKER)」→ コメント の繰り返し + 「M/DのLightning Round」の放送日

やること:
  1. 記事から銘柄・コメント・放送日を抽出、コメントを3段階判定（強気/中立/弱気）
     - 判定は関西弁の口癖キーワードで、逆接（〜やが、あかん）に対応するため
       「最後に出現した極性語」を採用（日本語は結論が文末に来るため）
     - 判定不能は「?」として表示（隠さない）
  2. cramer_history.json に蓄積（2025年放送分以降、記事番号1185〜）
  3. 放送日から 1週間/2週間/1ヶ月/2ヶ月/3ヶ月/6ヶ月後の騰落をyfinanceで計算
  4. 冒頭に集計表: 全期間/直近1年 × 強気/中立/弱気 × 各期間の的中率
     （強気の的中=上昇、弱気の的中=下落）+ 平均リターン

実行: 毎日08:50（cramer_run.bat）。新記事を自動検出して追記。
初回バックフィルは --backfill N で記事N件ずつ取得（Windowsなら --backfill 400 で一括）
"""

import os
import re
import sys
import json
import time
import subprocess
import datetime
import urllib.request

import yfinance as yf
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_JSON = os.path.join(SCRIPT_DIR, "cramer_history.json")
REPORT_HTML = "cramer.html"

BASE = "https://usa-option.com/lightning-round-{}/"
FIRST_ARTICLE = 1185       # 2025-01-09投稿（放送2024-12-11）から。放送2025-01-01以降を採用
MIN_BROADCAST = "2025-01-01"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}

PERIODS = [("1週間", 7), ("2週間", 14), ("1ヶ月", 30), ("2ヶ月", 61), ("3ヶ月", 91), ("6ヶ月", 183)]

# 3段階判定キーワード（最後に出現した極性語で判定）
BULL_WORDS = ["買うべき", "買いや", "買い増す", "買いたい", "買っても", "素晴らしい",
              "強く推", "推してもいい", "オススメ", "おすすめ", "気に入っとる", "大好きや",
              "好きやで", "割安", "良い銘柄", "いい銘柄", "最高の", "勝者や", "本物や",
              "持つべき", "保有すべき", "上がると思う", "持っておくべき", "持っとくべき",
              "業績が大きく伸びる",
              "いいと思うで", "ええと思うで", "良いと思うで", "強く支持", "支持しとる",
              "支持し続けたい", "支持し続けるで", "支持するで", "信頼しとる", "信頼を失ったことはない",
              "手放してほしくない", "売ってほしくない", "消化中なだけ",
              "優良な", "優良や", "応援しとる", "イチオシ", "好調や", "絶好調",
              "買い始めるには良い", "買うには良い", "良い水準", "いい水準", "ええ水準",
              "買うのに適した", "買うに適した",
              "買いの好機", "買う好機", "絶好の買い場",
              "良い決算", "いい決算", "ええ決算", "好決算", "決算が良かった", "決算は良かった",
              "買い時", "買い場や", "よくやっとる", "うまくやっとる", "見事や",
              "今買う時や", "今が買う時", "今こそ買う", "買う時やと思う",
              "市場を支配する", "支配することになる", "王者や", "チャンピオンや", "ナンバーワンや",
              "強気やで", "強気なんや", "買い始めるべき", "買うべきやろう",
              "BuyBuyBuy", "Buy Buy Buy", "バイバイバイ", "株買おう", "株買うで",
              "買おうと言う", "買え、と言う",
              "ええで。", "ええ銘柄", "ええ会社", "ええ株",
              "保有しても構わない", "持っても構わない", "持っとっても構わない", "保有してええ",
              "勝ち組銘柄", "勝ち組や", "上昇を続けられる", "上昇し続ける", "まだ上がる",
              "ホットな銘柄", "利益をもたらす", "上昇するところまで持ち続け",
              "そのまま持ち続けるべき", "持ち続けるべきや", "保有し続けるべき", "ホールドすべき",
              "保有し続けるつもり", "持ち続けるつもり", "優良企業",
              "Own it, don't trade it", "Own it, don’t trade it",
              "持ち続けてほしい", "保有し続けてほしい", "さらに上がるやろう", "上がり続けとる",
              "ホールドしてほしい", "持っとるんやったらホールド",
              "持ち続けることを強く勧める", "持ち続けることを勧める", "保有し続けることを勧める",
              "もっと上がる", "はるかに価値がある", "もっと価値がある",
              "わいは買い手", "買い手やで", "ずっと高い価値がある", "高い価値があると思う",
              "勢いづき始めている", "勢いづいとる", "本当に嬉しいで", "株価の動きが本当に嬉しい",
              "銘柄を推奨する", "この株を推奨する", "推奨するで", "良い会社やと思っとる",
              "とても良い会社", "うまくいっている分野",
              "評価の高い会社", "良い仕事をしている", "素晴らしい仕事をしとる",
              "いい会社だと思う", "いい会社やと思う", "良い会社だと思う", "評価している人は多い",
              "良い株だと思う", "良い株やと思う", "いい株だと思う", "いい株やと思う",
              "ええくらい良い株", "一本コーナーをやってもええくらい",
              "ブル派が正しいと思う", "強気派が正しいと思う", "を信じとるで", "を信じとる。",
              "上昇余地がある", "まだ上昇余地", "黒字化も果たす", "黒字化すると思う",
              "大きな銘柄の一つになる", "まだまだ上に行ける", "まだ上に行ける",
              "かなり上に行ける", "上に行けると思う", "必要なタイプの株",
              "熱い案件", "上がるかもしれん", "保有する価値はある", "持つ価値はある",
              "素晴らしい投機銘柄", "良い投機銘柄", "ええ投機銘柄", "優れた投機銘柄",
              "上がっていくとは思う", "上がっていくと思う", "上がると見とる",
              "好きになるで", "好きになっとる", "気に入っている", "この株が好きや",
              "本気で推しとる", "本気で推す", "本当に良いと思っとる", "本当に、本当に良い",
              "は買いということ", "は買いや", "買いやで",
              "さらに上がると思っとる", "さらに上がると思う", "大きな保有銘柄",
              "状況は良いに決まっとる", "良いに決まっとる", "夢中なんや", "持っているべき",
              "ここまで持ち続けた", "持ち続けた人たちには敬意", "敬意を表したい",
              "ついに戻ってきた", "復活した"]

# 目標株価の言及（「400まで行くと思う」等）はbull扱い
TARGET_PRICE_PAT = re.compile(r'(?:\d[\d,\.]*\s*(?:ドル)?まで(?:行く|いく|上がる)と思う)')
BEAR_WORDS = ["売りや", "売るべき", "売った方", "あかんで", "あかんのや", "持ちたくない",
              "買いたくない",
              # 「良い決算」(bull)の否定形。長い語が勝つルールでbullを上書きする
              "良い決算じゃなかった", "良い決算ではなかった", "いい決算じゃなかった",
              "ええ決算じゃなかった", "良い決算やなかった", "決算は良くなかった",
              "決算が良くなかった", "悪い決算", "ひどい決算", "決算がひどかった", "決算を外した",
              # 「近づかんほうがええで」等。「ええで。」(bull)と重なるが長い語が勝つルールでbearが優先される
              "近づかんほうがええ", "近づかん方がええ", "近づかないほうがいい", "近づかない方がいい",
              "近づかんとこう", "近寄らんほうがええ", "近寄らん方がええ", "近づくべきではない",
              "手を出さない", "手を出したくない", "避けるべき", "避けようやないか", "避けよう。",
              "避けたいで", "どうしても避けたい", "株が叩かれてしんどい",
              "好みじゃない", "好きじゃない",
              "好きではない", "傷ついてほしくない", "興味ない", "興味がない", "推せない",
              "買わない", "ダメや", "駄目や", "下がると思う", "リスクが高すぎ",
              "リスクが大きすぎる", "懸念しとる", "懸念があるで",
              "ここでは買い手ではなく", "売り手側やで", "利確して減らす",
              "何をどれだけ抱えているのか分からん", "いつの話かも分からん",
              "決算が悪かった", "勧める理由がない", "推す理由がない", "良い数字が見当たらない",
              # 「悪材料+待て」の組み合わせは待ち推奨ではなく実質弱気
              # （長い語が中立の「待たんとあかん」に勝つ）
              "悪かった。待たんとあかん", "悪かった。待たないとあかん", "悪い。待たんとあかん",
              "最悪のチャート", "最悪の銘柄", "ひどいチャート", "酷いチャート", "チャートが崩れ",
              "推奨できない", "推奨しない", "勧められない", "勧めない", "オススメできない",
              "薦めることができない", "薦められない", "勧めることができない",
              "インフレがまだ高すぎる",
              "おすすめできない", "近づかない", "近づかないでほしい", "近寄らない",
              "近寄ってほしくない", "近づいてほしくない", "触らない", "触りたくない",
              "出られなくなるんや", "中に何が入っているのかわからん",
              "どこにも行かないと思っとる", "どこにも行かない", "パスや", "パスやで",
              "見送るべき", "降りるべき", "逃げるべき", "危険や", "問題が多すぎ", "終わっとる",
              "今回は見送る", "見送ると言わざるを得ない", "見送りと言わざるを得ない",
              "見送ることにしよう", "見送ろうやないか", "利益成長力がない", "成長力がない",
              "CEOを失う", "とても奇妙や",
              "持つべきではない", "保有すべきではない", "買うべきではない",
              "買うことはしない", "見かけ倒し",
              "そのどれもない", "どれもないで", "何もかも足りない", "退屈な会社や",
              "割安株であってほしいんやが",
              "見込みは非常に乏しい", "見込みが乏しい", "利益を上げられる見込みは",
              "完全な投機", "ただの投機", "単なる投機", "投機でしかない",
              "博打や", "ギャンブルや", "宝くじ",
              "期待外れ", "支持できない", "支持し続けることはできない", "支持することはできない",
              "見限", "失望した", "がっかりや", "降参や", "諦めた", "さじを投げ",
              "損切りする", "損切りや", "再び買いたいと思うまで", "買いたいと思うまで",
              "賛成できない", "賛成できへん", "賛成しない", "反対や", "反対やで",
              "非常に厳しい状況", "厳しい業界", "厳しい事業", "逆風が強すぎ", "逆風が多すぎ",
              "やめたほうがええで", "やめた方がええで", "やめたほうがいいで", "やめとくべき",
              "持つのはやめた", "買うのはやめた",
              "買ってほしくない", "持ってほしくない", "強気になれたらええんやが",
              "保有するには難しすぎる", "持つには難しすぎる", "難しすぎる銘柄", "難しすぎるで",
              "保有しにくい株", "保有しにくい銘柄", "持ちにくい株", "持ちにくい銘柄",
              "多額の赤字", "赤字を出しとる", "赤字を垂れ流", "一生かかるかもしれん",
              "不安定で", "左右されすぎ",
              "保有する理由が思いつかない", "持つ理由が思いつかない", "買う理由が思いつかない",
              "保有する理由がない", "持つ理由がない", "買う理由がない",
              "ノーやで", "ノーや。", "答えはノー", "にはノーと言う", "ノーと言うで",
              "利益を出していない会社", "利益を出していない銘柄", "利益を出していないんや",
              "儲かっていない会社", "わいはなしや", "なしやで",
              "この会社は赤字", "負ける賭け", "分の悪い賭け", "赤字企業を買うというのは負け",
              "赤字が大きすぎる", "赤字企業は買う必要はない", "買う必要はないで",
              "二番煎じ", "大きな損失を出している", "違うものを探したい", "他を探したい",
              "新しくて違いがはっきりしているものを探したい", "ものを探したいで",
              "AIによって破壊される", "AIによって打撃を受ける", "破壊されるタイプの会社",
              "売っている人たちの意見にわいも賛成", "売り方に賛成",
              "落ちてくるナイフ", "落ちるナイフ", "ナイフを掴", "底が見えない",
              "バリュー・トラップ", "バリュートラップ", "欲しくない", "いらんで",
              "成長性がまったくない", "成長性がない", "成長がない",
              "良い株ではなかった", "良い銘柄ではなかった", "推奨してこなかった", "推奨したことはない",
              "いい会社ではない", "良い会社ではない", "ええ会社ではない",
              "いい銘柄ではない", "良い銘柄ではない",
              "本当にひどいで", "本当に酷いで", "株価は戻らない", "戻らないやろう",
              "ペナルティボックス", "決算をかなりひどく外した", "ひどく外した", "決算を外した",
              "失望的", "失望や", "非常に失望",
              "もう遅すぎる", "乗り遅れた", "buyするには遅すぎ", "買うには遅すぎ",
              "わいには買えない", "わいは買えない", "高すぎて買えない",
              "買うことはできない", "あらゆる局面で下がって",
              "厳しいで。", "株を買うことはない", "だけを理由に株を買うことはない",
              "大赤字", "売上がほとんどない", "ノーと言わざるを得ない", "ノーと言うしかない",
              "利確したほうがいい", "利確した方がいい", "利確すべき", "利益確定すべき",
              "半分は利確", "半分売ったほうがいい", "半分売った方がいい",
              "嫌われとる", "稼げる余地もあまりない", "稼げる余地はない", "余地もあまりない",
              "この株は買わん", "わいは買わんで", "この株は買えない", "ここでは買えない",
              "勧めることなんてできない", "勧めることはできない",
              "保有が非常に難しい", "保有が難しい銘柄", "見送らざるを得ない", "見送れと言う",
              "見送りや", "見送るで"]
NEUT_WORDS = ["待つべき", "待たないとあかん", "待たんとあかん", "待たなあかん", "待ちや",
              "様子見", "ホールド", "持ち続け", "半分売",
              "悪くない", "五分五分", "どちらとも", "分からん", "わからん",
              "判断が難しい", "難しいところや", "見極めが難しい", "難しい質問",
              "評価の分かれる", "評価が分かれる",
              "のほうが好き", "の方が好き", "のほうがいい", "の方がいい",
              "のほうがええ", "の方がええ", "派なんや", "投機枠", "余地はある",
              "銘柄のほうがわいはいいと思う", "のほうがわいはいい", "の方がわいはいい",
              "というほどでもない", "凄くいいというほどでもない",
              "もっといい銘柄がある", "もっと良い銘柄がある", "もっとええ銘柄がある",
              "他にいい銘柄がある", "他に良い銘柄がある",
              "良い価格で買える", "もっと良い価格", "もっと安く買える", "押し目を待",
              "下げる日を待って", "下げるのを待って", "天井値では買いたくない", "高値では買いたくない",
              "押し目で入", "下がったら買", "下げたところで買",
              "下がるまで待とう", "下がるまで待つ", "ここで推奨はできない", "今は推奨できない",
              "見届けてから", "見極めてから", "じっくり検討したらええ", "検討したらええと思う",
              "かなり論争がある", "論争があるで",
              # 別の対象を代わりに勧めるパターン（質問された銘柄自体への推奨ではない）
              "そのものを持つべき", "そのものを買うべき",
              "そのものを買った方がええ", "そのものを買った方がいい", "そのものを買った方",
              "本家を買う", "本家の方を", "の方を見てほしい", "のほうを見てほしい",
              "の方を買う", "のほうを買う",
              # 裸の「投機銘柄」は修飾語次第でどちらにも転ぶため単体では中立
              # （「素晴らしい投機銘柄」=bull、「完全な投機銘柄」=bearは別途長い語で登録済み。
              #   長い語が勝つルールで解決される）
              "投機銘柄", "投機的な株", "投機的な銘柄", "投機対象"]

# 仮定・条件節のパターン: この直後に続く極性語は「発言者の推奨」ではないため無効化
# 例:「買いたいのであれば」「買うのなら」→ bullとして数えない
COND_PATTERNS = [r'買いたいの(?:であれば|なら)', r'買うの(?:であれば|なら)', r'買いたければ',
                 r'売りたいの(?:であれば|なら)', r'売るの(?:であれば|なら)', r'売りたければ',
                 r'持ちたいの(?:であれば|なら)', r'持つの(?:であれば|なら)',
                 # もし/仮定節の中の上げ下げ予想は銘柄への推奨ではない
                 # 例:「もし金利が下がると思うなら、買い始めるべき」の「下がると思う」
                 r'もし[^。]{0,30}(?:上がる|下がる)と思うなら', r'(?:上がる|下がる)と思うなら',
                 # 「もし〜と本気で思うなら買ってもいい」のような譲歩付き許可も推奨ではない
                 r'もし[^。]{0,50}思うなら買ってもいい', r'思うなら買ってもいい',
                 r'思うなら買っても構わない', r'なら買ってもいいけど',
                 # 「投機したいのであれば〜ええと思うで」のような譲歩付き許可は
                 # 推奨ではないため、条件節から文末までを無効化
                 r'投機したいの(?:であれば|なら)[^。]*', r'ギャンブルしたいの(?:であれば|なら)[^。]*']

# 代替推奨の切り替えパターン: このパターン以降のコメントは「別の銘柄」の話なので
# 極性判定から除外し、それ以前に極性語が無ければ発言全体を中立にする
# 例:「持ってない人にはもう終わった株や。…J&Jを買うといい。J&Jは素晴らしいんや」
#   → 素晴らしい はJ&Jへの賛辞であってOrganonへの推奨ではない
# 例:「この業界に投資したいなら、MP Materialsを買うべきやで。これは本物や」
#   → 本物や はMP Materialsへの賛辞
ALT_PATTERNS = [r'を買うといい', r'を買うとええ',
                r'を買いなさい', r'に乗り換え',
                # 「XXを買ったほうがいい」= 別銘柄(英字名)への乗り換え推奨
                r"[A-Za-z][A-Za-z\s\.&’']{1,30}\s*を買った(?:ほう|方)が(?:いい|ええ)",
                r"[A-Za-z][A-Za-z\s\.&’']{1,30}\s*を買う(?:ほう|方)が(?:いい|ええ)",
                r'(?:業界|セクター|分野)に(?:投資し|参入し|とどまり)たいなら',
                r'なら[A-Za-z][A-Za-z\s\.&]{1,30}\s*を買うべき',
                r'のほうが[^。]{0,40}好きやで', r'の方が[^。]{0,40}好きやで',
                # 「XXを保有するほうがずっと好きやで」= 別銘柄(XX)を推す比較
                r'を保有する(?:方|ほう)が', r'を持つ(?:方|ほう)が', r'を持っとく(?:方|ほう)が',
                r'まだ[A-Za-z][A-Za-z\s\.&]{1,30}\s*の(?:方|ほう)がええ',
                r'の(?:方|ほう)がええと思うで',
                # 「一方で、XX社は…」で話題が別銘柄に切り替わるパターン
                # （固有名詞=英字で始まる銘柄名への言及+買い推奨が続く場合）
                r'一方で、?[A-Za-z][A-Za-z\s\.&’\']{1,30}\s*は',
                r'[A-Za-z][A-Za-z\s\.&’\']{1,30}\s*を(?:少し)?買い始めるべき',
                # 「もっと良い銘柄はXXや」= 別銘柄への乗り換え推奨
                r'もっと良い銘柄は[A-Za-z]', r'もっとええ銘柄は[A-Za-z]',
                r'より良い銘柄は[A-Za-z]', r'ベストは[A-Za-z]',
                # 「XXの方がええで」= 比較で他銘柄を推す（以降は他銘柄の話）
                r'[A-Za-z][A-Za-z\s\.&’\']{1,30}\s*の方がええで',
                r'[A-Za-z][A-Za-z\s\.&’\']{1,30}\s*のほうがええで',
                # 「〜に乗りたいならXXがええで」= 代替銘柄の提示
                r'に乗りたいなら\s*[A-Za-z]', r'をやりたいなら\s*[A-Za-z]',
                r'[A-Za-z][A-Za-z\s\.&’\']{1,30}\s*がええで']


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# -----------------------------------------
# 記事の取得・解析
# -----------------------------------------
def fetch_article(num):
    """記事番号 → dict or None(404)"""
    url = BASE.format(num)
    try:
        req = urllib.request.Request(url, headers=UA)
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return parse_article(num, url, html)


def parse_article(num, url, html):
    m = re.search(r'"datePublished":"(\d{4}-\d{2}-\d{2})T', html)
    posted = m.group(1) if m else None

    text = re.sub(r'<script.*?</script>', '', html, flags=re.S)
    text = re.sub(r'<style.*?</style>', '', text, flags=re.S)
    text = re.sub(r'<[^>]+>', '\n', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&gt;', '>').replace('&#8217;', "'")

    # 放送日「M/DのLightning Round」
    mb = re.search(r'の(\d{1,2})/(\d{1,2})のLightning Round', text)
    broadcast = None
    if mb and posted:
        bm, bd = int(mb.group(1)), int(mb.group(2))
        py, pm, pdd = map(int, posted.split("-"))
        year = py - 1 if (bm, bd) > (pm, pdd) else py
        try:
            broadcast = datetime.date(year, bm, bd).isoformat()
        except ValueError:
            pass
    if broadcast is None:
        broadcast = posted

    # 本文の銘柄ブロック抽出（「こんにちはマカベェです」〜「参考にさせてもらいます」）
    mm = re.search(r'こんにちはマカベェです。(.*?)(?:参考にさせてもら|応援よろしく)', text, re.S)
    body = mm.group(1) if mm else text
    lines = [l.strip() for l in body.split('\n') if l.strip()]

    stocks = []
    cur = None
    for l in lines:
        tm = re.match(r'^(.{2,70}?)\s*\(([A-Z]{1,5})\)\s*$', l)
        if tm and not re.match(r'^\d', tm.group(1)):
            if cur and cur["comment"]:
                stocks.append(cur)
            cur = {"ticker": tm.group(2), "name": tm.group(1).strip(), "comment": ""}
        elif cur is not None:
            if "Lightning Round" in l or l.startswith("ジム・クレイマー"):
                continue
            cur["comment"] = (cur["comment"] + " " + l).strip()
    if cur and cur["comment"]:
        stocks.append(cur)

    for s in stocks:
        s["stance"] = classify(s["comment"])

    return {"num": num, "url": url, "posted": posted, "broadcast": broadcast, "stocks": stocks}


def classify(comment):
    """最後に出現した極性語で3段階判定。無ければ ?
    - 引用文（カギ括弧内=他人のセリフ）と条件節（「買いたいのであれば」等）の中の語は無効
    - 重なり合う候補語は「長い方が勝つ」
      例:「Intelのほうが好きやで」→ 中立「のほうが好き」(6字) > 強気「好きやで」(4字)
      例:「上昇するところまで持ち続けるで」→ 強気「上昇するところまで持ち続け」(13字) > 中立「持ち続け」(4字)"""
    dead_spans = []   # 引用文+条件節+代替推奨以降（この範囲の語は一切数えない）
    for m in re.finditer(r'「[^」]*」', comment):
        dead_spans.append((m.start(), m.end()))
    for pat in COND_PATTERNS:
        for m in re.finditer(pat, comment):
            dead_spans.append((m.start(), m.end()))
    # 代替推奨（「XXを買うといい」等）が出たら、そこから文末までは別銘柄の話
    alt_start = None
    for pat in ALT_PATTERNS:
        m = re.search(pat, comment)
        if m and (alt_start is None or m.start() < alt_start):
            alt_start = m.start()
    if alt_start is not None:
        dead_spans.append((alt_start, len(comment)))

    def in_dead(s, e):
        return any(not (e <= ds or s >= de) for ds, de in dead_spans)

    # 全候補を収集
    cands = []  # (start, end, length, stance)
    for words, stance in [(NEUT_WORDS, "neutral"), (BULL_WORDS, "bull"), (BEAR_WORDS, "bear")]:
        for w in words:
            for m in re.finditer(re.escape(w), comment):
                if not in_dead(m.start(), m.end()):
                    cands.append((m.start(), m.end(), m.end() - m.start(), stance))
    # 目標株価（「400まで行くと思う」等）はbull候補
    for m in TARGET_PRICE_PAT.finditer(comment):
        if not in_dead(m.start(), m.end()):
            cands.append((m.start(), m.end(), m.end() - m.start(), "bull"))
    if not cands:
        # 代替推奨だけで構成されたコメント（「〜ならXXを買うといい」等）は中立
        return "neutral" if alt_start is not None else "?"

    # 重なり解決: 長い候補から順に確定し、確定済みと重なる短い候補は捨てる
    cands.sort(key=lambda c: -c[2])
    kept = []
    for s, e, ln, stance in cands:
        if not any(not (e <= ks or s >= ke) for ks, ke, _ in kept):
            kept.append((s, e, stance))
    kept.sort()
    return kept[-1][2]


# -----------------------------------------
# 履歴
# -----------------------------------------
def load_history():
    if os.path.exists(HISTORY_JSON):
        with open(HISTORY_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {"articles": {}}


def save_history(hist):
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)


def update_articles(hist, backfill=0):
    """新記事の検出+バックフィル。backfill>0なら未取得の古い記事もN件まで取得"""
    known = set(int(k) for k in hist["articles"].keys())
    added = 0

    # バックフィル（FIRST_ARTICLE〜既知の最大まで の穴埋め）
    if backfill > 0:
        upper = max(known) if known else FIRST_ARTICLE + 400
        todo = [n for n in range(FIRST_ARTICLE, upper) if n not in known]
        log(f"バックフィル対象: {len(todo)}件（今回は最大{backfill}件）")
        for n in todo[:backfill]:
            try:
                art = fetch_article(n)
            except Exception as e:
                log(f"  #{n}: 取得失敗 {e}")
                continue
            if art is None:
                hist["articles"][str(n)] = {"num": n, "skip": True}  # 欠番
            else:
                hist["articles"][str(n)] = art
                added += 1
                if added % 20 == 0:
                    log(f"  バックフィル {added}件目 #{n} ({art['broadcast']})")
                    save_history(hist)
            time.sleep(1.2)

    # 新記事（既知の最大+1から、404が3連続するまで）
    start = max(known) + 1 if known else FIRST_ARTICLE
    misses = 0
    n = start
    while misses < 3:
        try:
            art = fetch_article(n)
        except Exception as e:
            log(f"  #{n}: 取得失敗 {e}")
            break
        if art is None:
            misses += 1
        else:
            misses = 0
            hist["articles"][str(n)] = art
            added += 1
            log(f"  新記事 #{n} 放送{art['broadcast']} {len(art['stocks'])}銘柄")
        n += 1
        time.sleep(1.2)

    if added:
        save_history(hist)
        log(f"記事 {added}件 追加（計{len(hist['articles'])}件）")
    return added


# -----------------------------------------
# リターン計算
# -----------------------------------------
def collect_entries(hist):
    """検証対象の銘柄エントリ一覧（放送日がMIN_BROADCAST以降）"""
    entries = []
    for art in hist["articles"].values():
        if art.get("skip") or not art.get("broadcast"):
            continue
        if art["broadcast"] < MIN_BROADCAST:
            continue
        for s in art.get("stocks", []):
            entries.append({
                "num": art["num"], "url": art["url"], "broadcast": art["broadcast"],
                "ticker": s["ticker"], "name": s["name"],
                "comment": s["comment"], "stance": s["stance"],
            })
    return entries


def compute_returns(entries):
    """各エントリに 2週間〜6ヶ月後の騰落%（絶対 rets / 対S&P500相対 rel）を付与"""
    tickers = sorted({e["ticker"] for e in entries})
    log(f"価格取得: {len(tickers)}ティッカー")
    closes = {}
    CHUNK = 150
    for ci in range(0, len(tickers), CHUNK):
        chunk = tickers[ci:ci + CHUNK]
        data = yf.download(chunk, start="2024-12-01", auto_adjust=True,
                           progress=False, group_by="ticker", threads=True)
        for t in chunk:
            try:
                s = data[t]["Close"].dropna() if len(chunk) > 1 else data["Close"].dropna()
                if len(s) > 10:
                    closes[t] = s
            except Exception:
                continue
        time.sleep(1)
    log(f"  取得成功: {len(closes)}ティッカー")

    # ベンチマーク（S&P500）
    spx = yf.download("^GSPC", start="2024-12-01", auto_adjust=True,
                      progress=False)["Close"].squeeze().dropna()

    def period_ret(s, b, days):
        """b(放送日)からdays暦日後の騰落%。未到来はNone"""
        i0 = s.index.searchsorted(b)
        if i0 >= len(s):
            return None
        base = float(s.iloc[i0])
        target = b + pd.Timedelta(days=days)
        if target > s.index[-1]:
            return None
        j = s.index.searchsorted(target)
        if j >= len(s):
            return None
        return (float(s.iloc[j]) / base - 1) * 100

    for e in entries:
        s = closes.get(e["ticker"])
        e["rets"] = {}
        e["rel"] = {}
        if s is None:
            continue
        b = pd.Timestamp(e["broadcast"])
        i0 = s.index.searchsorted(b)
        if i0 >= len(s):
            continue
        e["base_price"] = round(float(s.iloc[i0]), 2)
        for label, days in PERIODS:
            v = period_ret(s, b, days)
            e["rets"][label] = round(v, 1) if v is not None else None
            vb = period_ret(spx, b, days)
            if v is not None and vb is not None:
                # 対S&P500の相対リターン: (1+v)/(1+vb)-1
                e["rel"][label] = round(((1 + v / 100) / (1 + vb / 100) - 1) * 100, 1)
            else:
                e["rel"][label] = None
    return entries


# -----------------------------------------
# 集計（的中率）
# -----------------------------------------
BBB_PAT = re.compile(r'buy\s*buy\s*buy|バイバイバイ', re.I)


def is_bbb(e):
    """クレイマー最強の買い推奨「BuyBuyBuy」を含む発言か"""
    return bool(BBB_PAT.search(e.get("comment", "")))


def summarize(entries, since=None, key="rets"):
    """{stance: {period: (的中率%, 平均リターン%, N)}}
    key="rets"=絶対リターン / key="rel"=対S&P500相対リターン
    相対の的中: 強気=S&P500に勝った / 弱気=S&P500に負けた
    stance "bbb" = BuyBuyBuy発言のみ（強気の最上級サブセット、的中=上昇）"""
    out = {}
    for stance in ("bull", "bbb", "neutral", "bear"):
        if stance == "bbb":
            grp = [e for e in entries if is_bbb(e)
                   and (since is None or e["broadcast"] >= since)]
        else:
            grp = [e for e in entries if e["stance"] == stance
                   and (since is None or e["broadcast"] >= since)]
        per = {}
        for label, _ in PERIODS:
            vals = [e.get(key, {}).get(label) for e in grp if e.get(key, {}).get(label) is not None]
            if not vals:
                per[label] = None
                continue
            if stance == "bear":
                hit = sum(1 for v in vals if v < 0)
            else:
                hit = sum(1 for v in vals if v > 0)
            per[label] = (round(hit / len(vals) * 100), round(sum(vals) / len(vals), 1), len(vals))
        out[stance] = per
    return out


# -----------------------------------------
# HTML
# -----------------------------------------
STANCE_LABEL = {"bull": ("強気 🟢", "#4ade80"), "neutral": ("中立/判定不能", "#64748b"),
                "bear": ("弱気 🔴", "#f87171"), "?": ("中立/判定不能", "#64748b"),
                "bbb": ("BuyBuyBuy 🔥", "#fbbf24")}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>クレイマー Lightning Round 検証 - {updated_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 24px;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f8fafc; }}
  h2 {{ font-size: 1.05rem; color: #cbd5e1; margin: 24px 0 10px; border-left: 4px solid #db2777; padding-left: 10px; }}
  .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
  .nav {{ display: flex; gap: 12px; margin-bottom: 20px; font-size: 0.85rem; flex-wrap: wrap; }}
  .nav a {{
    color: #60a5fa; text-decoration: none; background: #1e293b;
    padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;
  }}
  .nav a:hover {{ background: #334155; }}
  .nav a.active {{ background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }}
  table {{ border-collapse: collapse; font-size: 0.84rem; }}
  thead th {{
    background: #1e293b; color: #94a3b8; padding: 8px 12px;
    text-align: right; font-weight: 600; white-space: nowrap;
    position: sticky; top: 0; z-index: 2;
  }}
  thead th:first-child {{ text-align: left; }}
  td {{
    padding: 7px 12px; border-bottom: 1px solid #1e293b;
    text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
  }}
  td:first-child {{ text-align: left; }}
  tr:hover td {{ background: #16213a; }}
  .pos {{ color: #4ade80; }}
  .neg {{ color: #f87171; }}
  .dim {{ color: #64748b; font-size: 0.76rem; }}
  .comment {{ color: #94a3b8; font-size: 0.78rem; text-align: left; white-space: normal; max-width: 520px; }}
  .table-wrap {{ overflow: auto; max-height: 72vh; }}
  .note {{ font-size: 0.78rem; color: #64748b; margin-top: 16px; line-height: 1.9; max-width: 1150px; }}
  .updated {{ text-align: left; font-size: 0.78rem; color: #475569; margin-top: 12px; }}
</style>
<script data-goatcounter="https://kabuchiwa.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</head>
<body>
  <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:8px; font-weight:600;"><svg width="16" height="18" viewBox="0 0 32 36" style="vertical-align:-4px; margin-right:3px"><polygon points="3,1 13,9 2,15" fill="#262626"/><polygon points="29,1 19,9 30,15" fill="#262626"/><polygon points="5,4 11,9 4.5,12.5" fill="#c98f52"/><polygon points="27,4 21,9 27.5,12.5" fill="#c98f52"/><ellipse cx="6.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><ellipse cx="25.5" cy="21" rx="3.2" ry="5" fill="#e8d5b7"/><circle cx="16" cy="17" r="11" fill="#262626"/><circle cx="10.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="21.5" cy="12.5" r="1.7" fill="#c98f52"/><circle cx="11" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="21" cy="16" r="1.6" fill="#0a0a0a"/><circle cx="11.5" cy="15.4" r="0.55" fill="#e2e8f0"/><circle cx="21.5" cy="15.4" r="0.55" fill="#e2e8f0"/><ellipse cx="16" cy="23" rx="6" ry="4.5" fill="#c98f52"/><ellipse cx="16" cy="21" rx="2.1" ry="1.5" fill="#1a1a1a"/><path d="M12.8,25.5 Q12.3,33 16,35 Q19.7,33 19.2,25.5 Z" fill="#f06292"/><path d="M16,27 L16,33" stroke="#d81b60" stroke-width="0.9" fill="none"/></svg>かぶチワワの分析デッキ（<a href="https://x.com/kabuchiwa" style="color:#60a5fa; text-decoration:none;">@kabuchiwa</a>）</div>
  <nav class="nav">
    <a href="map.html" style="border-color:#94a3b8">デッキの見方</a>
    <a href="calendar.html" style="border-color:#94a3b8">イベント予定</a>
    <a href="yorimae.html" style="border-color:#94a3b8">寄り前</a>
    <a href="cpi.html" style="border-color:#7c3aed">米インフレと雇用</a>
    <a href="fedwatch.html" style="border-color:#7c3aed">FRB利上げ確率</a>
    <a href="totan.html" style="border-color:#7c3aed">日銀利上げ確率</a>
    <a href="kinri.html" style="border-color:#7c3aed">金利と為替</a>
    <a href="spriron.html" style="border-color:#7c3aed">SP500理論株価</a>
    <a href="riron.html" style="border-color:#7c3aed">日経理論株価</a>
    <a href="gaikoku.html" style="border-color:#2563eb">海外投資家</a>
    <a href="saitei.html" style="border-color:#2563eb">裁定取引</a>
    <a href="shutai.html" style="border-color:#2563eb">投資主体別</a>
    <a href="shinyou.html" style="border-color:#d97706">信用評価率</a>
    <a href="touraku.html" style="border-color:#d97706">騰落レシオ</a>
    <a href="karauri.html" style="border-color:#d97706">空売り比率</a>
    <a href="vix.html" style="border-color:#d97706">VIX温度計</a>
    <a href="sns.html" style="border-color:#d97706">SNS恐怖温度計</a>
    <a href="flow.html" style="border-color:#059669">資金フロー</a>
    <a href="daikin.html" style="border-color:#059669">売買代金</a>
    <a href="minervini_report_v2.html" style="border-color:#db2777">米国株 (Minervini)</a>
    <a href="jpminervini.html" style="border-color:#db2777">日本株 (Minervini)</a>
    <a href="haitou.html" style="border-color:#db2777">日本株 (配当)</a>
    <a href="insider.html" style="border-color:#db2777">インサイダー売買</a>
    <a href="margin.html" style="border-color:#db2777">銘柄チェッカー</a>
    <a href="buffett.html" style="border-color:#db2777">バフェット</a>
    <a href="cramer.html" class="active" style="border-color:#db2777">クレイマー</a>
    <a href="kijitsu.html" style="border-color:#db2777">信用期日</a>
    <a href="kijitsu_us.html" style="border-color:#db2777">下落日数(US)</a>
    <a href="fx_corr.html" style="border-color:#db2777">円安/円高相関</a>
    <a href="kasetsu.html" style="border-color:#94a3b8">仮説検証</a>
  </nav>
  <h1>ジム・クレイマー Lightning Round 検証</h1>
  <p class="subtitle">最終更新: {updated} | 発言{n_total}件（2025年1月放送分〜） | 出所: <a href="https://usa-option.com/category/stock/" style="color:#60a5fa">usa-option.com（マカベェさん訳）</a> | 騰落はyfinance終値ベース</p>

  <h2>的中率まとめ — クレイマーの言うことは当たるのか</h2>
{summary}
  <p class="dim" style="margin-top:6px">的中の定義: <b>絶対</b>行 = 強気=その後上昇 / 弱気=その後下落。
  <b>vs S&amp;P500</b>行 = 強気=S&amp;P500に勝った / 弱気=S&amp;P500に負けた（地合いを除いた真の目利き）。
  カッコ内は平均リターン（相対行は対S&amp;P500の超過分）。N=検証可能な発言数（期間未到来は除く）。中立・判定不能の発言は集計対象外（下の一覧には表示）。</p>

  <h2>発言と答え合わせ（放送日の新しい順）</h2>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>放送日</th><th>銘柄</th><th>判定</th><th style="text-align:left">コメント（マカベェさん訳）</th>
        <th>1週間</th><th>2週間</th><th>1ヶ月</th><th>2ヶ月</th><th>3ヶ月</th><th>6ヶ月</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
  <p class="note">
    ・Lightning Round = 視聴者の電話質問にクレイマーが即答するコーナー（Mad Money内）。準備なしの即答なので「本音の初期反応」が出やすい。<br>
    ・3段階判定はコメント内の言い回しから自動判定（最後に出た結論語を採用）。逆接や比較（「〜のほうが好き」）は中立になりやすい。
    判定不能は「?」で表示。<b>原文コメントを併記しているので、判定が怪しいものは自分の目で確認する</b>。<br>
    ・騰落は放送日の翌取引日終値を起点に、各期間後（暦日）の直近終値で計算。期間がまだ到来していない発言は「-」。<br>
    ・訳文は<a href="https://usa-option.com/category/stock/" style="color:#60a5fa">アメリカ発ーマカベェの米株取引</a>より。感謝。
  </p>
  <p class="updated">最終更新: {updated}</p>
</body>
</html>
"""


def fmt_ret(v):
    if v is None:
        return "<td>-</td>"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<td class="{cls}">{v:+.1f}%</td>'


def summary_table(entries):
    rows = []
    header = "".join(f"<th>{label}</th>" for label, _ in PERIODS)
    one_year_ago = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()
    for title, since in [("全期間（2025年1月〜）", None), ("直近1年", one_year_ago)]:
        body = []
        for key, key_label in [("rets", "絶対"), ("rel", "vs S&P500")]:
            stats = summarize(entries, since, key=key)
            for stance in ("bull", "bbb", "bear"):
                label, color = STANCE_LABEL[stance]
                tds = []
                for plabel, _ in PERIODS:
                    v = stats[stance].get(plabel)
                    if v is None:
                        tds.append("<td>-</td>")
                    else:
                        rate, avg, n = v
                        avg_cls = "pos" if avg > 0 else ("neg" if avg < 0 else "")
                        tds.append(f'<td><b>{rate}%</b> <span class="{avg_cls}">({avg:+.1f}%)</span>'
                                   f' <span class="dim">N={n}</span></td>')
                body.append(f'      <tr><td style="color:{color}; font-weight:700">{label}'
                            f' <span class="dim" style="font-weight:400">{key_label}</span></td>'
                            + "".join(tds) + "</tr>")
            if key == "rets":
                body.append('      <tr><td colspan="7" style="border-bottom:2px solid #334155; '
                            'padding:0"></td></tr>')
        rows.append(
            f'  <h3 style="font-size:0.88rem; color:#94a3b8; margin:10px 0 6px">{title}</h3>\n'
            f'  <div class="table-wrap" style="max-height:none">\n  <table>\n'
            f'    <thead><tr><th>判定</th>{header}</tr></thead>\n'
            f'    <tbody>\n' + "\n".join(body) + '\n    </tbody>\n  </table>\n  </div>')
    return "\n".join(rows)


def generate_html(entries):
    entries = sorted(entries, key=lambda e: (e["broadcast"], e["num"]), reverse=True)
    trs = []
    for e in entries:
        label, color = STANCE_LABEL.get(e["stance"], STANCE_LABEL["?"])
        rets = e.get("rets", {})
        tds = "".join(fmt_ret(rets.get(p)) for p, _ in PERIODS)
        trs.append(
            f'      <tr><td>{e["broadcast"]}</td>'
            f'<td><b>{e["ticker"]}</b> <span class="dim">{e["name"][:22]}</span></td>'
            f'<td style="color:{color}; font-weight:700">{label}</td>'
            f'<td class="comment">{e["comment"][:180]}</td>'
            f'{tds}</tr>')

    html = HTML_TEMPLATE.format(
        updated_date=datetime.date.today().isoformat(),
        updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_total=len(entries),
        summary=summary_table(entries),
        rows="\n".join(trs),
    )
    path = os.path.join(SCRIPT_DIR, REPORT_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML出力: {path}（{len(entries)}件）")


# -----------------------------------------
# push
# -----------------------------------------
def push_to_github():
    from git_lock_helper import wait_for_git_lock
    wait_for_git_lock(SCRIPT_DIR)  # 他スクリプトとのgit競合・放置ロック対策
    log("GitHub Pages に公開中...")
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "-C", SCRIPT_DIR, "add", REPORT_HTML,
                    "cramer_history.json", "cramer_screen.py", "cramer_run.bat",
                    ".gitignore"], check=True)
    result = subprocess.run(
        ["git", "-C", SCRIPT_DIR, "commit", "-m", "update cramer report " + today],
        capture_output=True)
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
            log("  Done: https://ichikon77.github.io/minervini/cramer.html")
            return
        except subprocess.CalledProcessError as e:
            log(f"  push failed (attempt {attempt}/5): {e}")
            time.sleep(10)
    log("  push failed finally")


# -----------------------------------------
# main
# -----------------------------------------
def reclassify_all(hist):
    """辞書更新後にhistory全体の判定を今のclassifyで付け直す"""
    changed = 0
    total = 0
    for art in hist["articles"].values():
        if art.get("skip"):
            continue
        for s in art.get("stocks", []):
            total += 1
            new = classify(s["comment"])
            if new != s.get("stance"):
                changed += 1
                s["stance"] = new
    save_history(hist)
    log(f"再判定: {total}件中 {changed}件を更新")


def main():
    log("クレイマー Lightning Round チェック開始")
    backfill = 0
    for a in sys.argv:
        if a.startswith("--backfill"):
            try:
                backfill = int(a.split("=")[1]) if "=" in a else int(sys.argv[sys.argv.index(a) + 1])
            except (ValueError, IndexError):
                backfill = 400

    hist = load_history()
    update_articles(hist, backfill=backfill)

    # 毎回、全発言を現在の判定辞書で付け直す（辞書改良が過去分にも自動反映されるように。
    # 1,500件程度なら1秒未満なのでコスト無視できる）
    reclassify_all(hist)

    entries = collect_entries(hist)
    if not entries:
        log("検証対象の発言がありません（まず --backfill 400 でバックフィルしてください）")
        return
    log(f"検証対象: {len(entries)}発言")

    entries = compute_returns(entries)
    generate_html(entries)

    if "--nopush" in sys.argv:
        log("--nopush 指定のため git push はスキップ")
    else:
        push_to_github()
    log("完了")


if __name__ == "__main__":
    main()
