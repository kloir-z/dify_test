#!/usr/bin/env python3
"""combined-news-digest の DSL を生成する(国内5+海外8を1本に統合)。

別リポジトリ `C:\\code\\news_digest`(combined を Python に移植して拡張したスキル)で
育てた 2 つの拡張を Dify DSL に取り込んだ版:

  1. 本文要約 : 見出しだけでは内容が分かりにくい Simon Willison / Nautilus / Aeon に
                1〜2文の日本語要約を `↳` 行で差し込む。要約は専用の「要約」LLM ノードで行う
                (翻訳ノードは「要約・論評禁止」の厳格プロンプトなので混ぜない)。
                ※ news_digest.py はフィード本文が薄いとき記事ページを補完取得するが、
                  **Dify のコードノードはネットワーク禁止**(notes 2026-06-12)なので
                  Dify 版の要約素材は「フィード埋め込み本文」に限定される(機能差)。
  2. URL 切替 : start の `with_urls` 入力(既定 no)で出典URLの併記を切り替える。
                既定は「見出し+日時のみ」(= news_digest の既定)。yes で国内/海外とも URL を併記。

フロー:
  start → {国内先頭=日経 / 海外先頭=AP}
  日経 → 残り国内4本 → 各国内HTTP → 国内整形(with_urls)
  AP   → 残り海外7本 → 各海外HTTP → 海外整形(formatted_articles / url_map / summary_src)
  海外整形 → 翻訳LLM ─┐
  海外整形 → 要約LLM ─┴→ URL復元+要約差し込み(with_urls)
  国内整形 ─┐
  URL復元   ┴→ 連結 → 出力

並列分岐は 1 ノード ≤10(Dify MAX_PARALLEL_LIMIT, Cloud 変更不可)を厳守:
  start=2 / 日経=5(残り4+整形) / AP=8(残り7+整形) / 海外整形=2(翻訳・要約)。

使い方:
    python scripts/gen_combined_dsl.py
    → workflows/combined-news-digest.yml を生成
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DST = ROOT / "workflows" / "combined-news-digest.yml"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 翻訳・要約 LLM のモデル(combined 既存の gemini 依存を踏襲)
LLM_PROVIDER = "langgenius/gemini/google"
LLM_MODEL = "gemini-3.1-flash-lite"
GEMINI_PLUGIN = ("langgenius/gemini:0.8.4@23aed1fa4d2c8015337bd18efa28b3dccf5b7309a"
                 "83b1501541fb7358518faec")

# --- ノード ID(既存 combined を踏襲。先頭ソースが残りをファンアウトするツリー)---------
ID_START = "1781187165020"
# 国内 HTTP(先頭=日経が残り4本をファンアウト)
JP_HTTP = [
    ("1781189175977", "日経", "https://news.google.com/rss/search?q=site:nikkei.com&hl=ja&gl=JP&ceid=JP:ja", "body_nikkei"),
    ("17811893361310", "朝日", "https://news.google.com/rss/search?q=site:asahi.com&hl=ja&gl=JP&ceid=JP:ja", "body_asahi"),
    ("17811893942900", "産経", "https://news.google.com/rss/search?q=site:sankei.com&hl=ja&gl=JP&ceid=JP:ja", "body_sankei"),
    ("17811893972820", "Reuters JP", "https://news.google.com/rss/search?q=site:jp.reuters.com&hl=ja&gl=JP&ceid=JP:ja", "body_reuters"),
    ("17811893996220", "東洋経済", "https://news.google.com/rss/search?q=site:toyokeizai.net&hl=ja&gl=JP&ceid=JP:ja", "body_toyokeizai"),
]
ID_JP_CODE = "1781187326232"
# 海外 HTTP(先頭=AP が残り7本をファンアウト)。末尾3本(Simon/Nautilus/Aeon)が要約対象
EN_HTTP = [
    ("91781189175977", "AP News", "https://news.google.com/rss/search?q=site:apnews.com&hl=en-US&gl=US&ceid=US:en", "body_ap"),
    ("917811893361310", "Reuters", "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en", "body_reuters"),
    ("917811893942900", "WSJ", "https://news.google.com/rss/search?q=site:wsj.com&hl=en-US&gl=US&ceid=US:en", "body_wsj"),
    ("917811893972820", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "body_ars"),
    ("917811893996220", "Hacker News", "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=25", "body_hn"),
    ("999178118900001", "Simon Willison", "https://simonwillison.net/atom/everything/", "body_simonw"),
    ("999178118900002", "Nautilus", "https://nautil.us/feed/", "body_nautilus"),
    ("999178118900003", "Aeon", "https://aeon.co/feed.rss", "body_aeon"),
]
ID_EN_CODE = "91781187326232"
ID_TRANSLATE = "999178118900010"
ID_SUMMARIZE = "999178118900020"   # 新設: 要約 LLM
ID_RESTORE = "999178118900011"     # URL復元 + 要約差し込み
ID_CONCAT = "8000000000001"
ID_END = "8000000000002"

# 要約対象(見出しだけでは内容が分かりにくいソース)
SUMMARIZE_SOURCES = {"Simon Willison", "Nautilus", "Aeon"}

# === 日付/エンティティ/RSS 解析の共通プレリュード(各コードノードに展開)===============
# コードノードは互いに独立実行のため共有できない。jp/en それぞれに同じ実装を持たせる
# (既存 DSL も to_jst を両コードに重複実装している方針)。
PRELUDE = r'''ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&apos;": "'"}


def unescape(text):
    for k, v in ENTITIES.items():
        text = text.replace(k, v)
    return text


def field(block, tag):
    f = re.search(r"<" + tag + r"[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</" + tag + r">", block)
    return unescape(f.group(1).strip()) if f else ""


MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
# 名前付きTZ→UTCからの分。文字列に書かれたTZをそのまま信頼して変換する(推測しない)
TZ_NAMES = {"GMT": 0, "UTC": 0, "UT": 0, "Z": 0, "JST": 540,
            "EST": -300, "EDT": -240, "CST": -360, "CDT": -300,
            "MST": -420, "MDT": -360, "PST": -480, "PDT": -420}


def _tzoff(tz):
    tz = tz.strip()
    if tz in TZ_NAMES:
        return TZ_NAMES[tz]
    if tz[:1] == "+":
        sign = 1
    elif tz[:1] == "-":
        sign = -1
    else:
        return None
    body = tz[1:].replace(":", "")
    if len(body) < 4:
        return None
    try:
        return sign * (int(body[0:2]) * 60 + int(body[2:4]))
    except ValueError:
        return None


def _parse_rfc822(raw):
    # 例: "Sun, 14 Jun 2026 21:15:38 GMT" / "Mon, 15 Jun 2026 09:00:00 +0000"
    parts = raw.replace(",", " ").split()
    if len(parts) < 5:
        return None
    try:
        tz = parts[-1]
        hms = parts[-2].split(":")
        year = int(parts[-3])
        mon = MONTHS.get(parts[-4][:3])
        day = int(parts[-5])
        hh = int(hms[0]); mm = int(hms[1]); ss = int(hms[2]) if len(hms) > 2 else 0
    except (ValueError, IndexError):
        return None
    off = _tzoff(tz)
    if mon is None or off is None:
        return None
    return datetime(year, mon, day, hh, mm, ss, tzinfo=timezone(timedelta(minutes=off)))


def _parse_iso(raw):
    # 例: "2026-06-15T09:00:00Z" / "...000Z" / "...+09:00" / "...-04:00"
    s = raw if "T" in raw else raw.replace(" ", "T", 1)
    try:
        year = int(s[0:4]); mon = int(s[5:7]); day = int(s[8:10])
    except (ValueError, IndexError):
        return None
    rest = s[11:]
    if not rest:
        return None
    off = None
    if rest[-1:] in ("Z", "z"):
        off = 0
        rest = rest[:-1]
    else:
        cut = -1
        for i in range(len(rest) - 1, -1, -1):
            if rest[i] in "+-":
                cut = i
                break
        if cut >= 0:
            off = _tzoff(rest[cut:])
            rest = rest[:cut]
    if off is None:
        return None  # TZ不明は変換しない(誤変換防止)
    hms = rest.split(".")[0].split(":")
    try:
        hh = int(hms[0]); mm = int(hms[1]); ss = int(hms[2]) if len(hms) > 2 else 0
    except (ValueError, IndexError):
        return None
    return datetime(year, mon, day, hh, mm, ss, tzinfo=timezone(timedelta(minutes=off)))


def to_jst(raw):
    """埋め込みTZを読み取りJSTへ変換。既に+09:00ならそのまま。読めなければ素通し"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if len(raw) >= 10 and raw[4:5] == "-" and raw[7:8] == "-":
        dt = _parse_iso(raw)
    else:
        dt = _parse_rfc822(raw)
    if dt is None:
        return raw
    jst = dt.astimezone(timezone(timedelta(hours=9)))
    return jst.strftime("%Y-%m-%d %H:%M JST")
'''

# === 国内整形コード(with_urls で URL 行の出力を切り替え)============================
JP_CODE = "import re\nfrom datetime import datetime, timedelta, timezone\n\n" + PRELUDE + r'''

def parse_rss(xml_text, strip_suffix=False, limit=15):
    items = []
    seen = set()  # 媒体内の重複見出しを除去(Google News RSS は同一記事を複数返す)
    for block in re.findall(r"<item>([\s\S]*?)</item>", xml_text or ""):
        title = field(block, "title")
        if title:
            if strip_suffix and " - " in title:
                title = title.rsplit(" - ", 1)[0]
            key = " ".join(title.split())
            if key not in seen:
                seen.add(key)
                items.append({"title": title, "url": field(block, "link"), "pub_date": field(block, "pubDate")})
        if len(items) >= limit:
            break
    return items


def main(body_nikkei, body_asahi, body_sankei, body_reuters, body_toyokeizai, with_urls):
    wu = (with_urls or "").strip().lower() in ("yes", "true", "1", "on")
    sources = [
        ("日経新聞", body_nikkei),
        ("朝日新聞", body_asahi),
        ("産経新聞", body_sankei),
        ("Reuters JP", body_reuters),
        ("東洋経済", body_toyokeizai),
    ]
    lines = ["## 📰 国内ニュース", ""]
    for name, body in sources:
        lines.append("### " + name)
        items = parse_rss(body, strip_suffix=True)
        if not items:
            lines.append("(取得失敗または0件)")
        for it in items:
            lines.append("- " + it["title"])
            if wu and it["url"]:
                lines.append("  " + it["url"])
            lines.append("  " + to_jst(it["pub_date"]))
        lines.append("")

    jst_now = datetime.now(timezone(timedelta(hours=9)))
    date_label = str(jst_now.year) + "年" + str(jst_now.month) + "月" + str(jst_now.day) + "日"
    return {
        "formatted_articles": "\n".join(lines),
        "date_label": date_label,
    }'''

# === 海外整形コード(formatted_articles / url_map / summary_src を出力)================
# parse_rss は want_body=True でフィード埋め込み本文も拾う(RSS: content:encoded→description /
# Atom: content→summary)。html_to_text+truncate で要約素材に整え、summary_src に id 付きで束ねる。
EN_CODE = "import json\nimport re\nfrom datetime import datetime, timedelta, timezone\n\n" + PRELUDE + r'''

def html_to_text(html):
    """HTML を素のテキストへ。script/style を除き、ブロック境界を改行に、残りのタグを除去。"""
    if not html:
        return ""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br[^>]*>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|blockquote|tr|section|article)>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    html = unescape(html)
    lines = [re.sub(r"[ \t　]+", " ", ln).strip() for ln in html.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def truncate(text, limit=1600):
    """要約に十分な長さで切る。なるべく文末(。/. )で切って途中で切らない。"""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("。", ". ", "\n", " "):
        idx = cut.rfind(sep)
        if idx > limit * 0.5:
            return cut[: idx + 1].strip()
    return cut.strip()


def parse_rss(xml_text, strip_suffix=False, limit=15, want_body=False):
    """RSS 2.0 の <item> と Atom の <entry> 両対応。want_body で本文/抜粋も拾う。"""
    items = []
    seen = set()  # 媒体内の重複見出しを除去(Google News RSS は同一記事を複数返す)
    blocks = re.findall(r"<item>([\s\S]*?)</item>", xml_text or "")
    if blocks:
        for block in blocks:
            title = field(block, "title")
            if title:
                if strip_suffix and " - " in title:
                    title = title.rsplit(" - ", 1)[0]
                key = " ".join(title.split())
                if key not in seen:
                    seen.add(key)
                    it = {"title": title, "url": field(block, "link"), "pub_date": field(block, "pubDate")}
                    if want_body:
                        it["body"] = field(block, "content:encoded") or field(block, "description")
                    items.append(it)
            if len(items) >= limit:
                break
        return items
    for block in re.findall(r"<entry>([\s\S]*?)</entry>", xml_text or ""):
        title = field(block, "title")
        if not title:
            continue
        key = " ".join(title.split())
        if key in seen:
            continue
        seen.add(key)
        m = re.search(r'<link[^>]*href="([^"]+)"', block)
        pub = field(block, "updated") or field(block, "published")
        it = {"title": title, "url": m.group(1) if m else "", "pub_date": pub}
        if want_body:
            it["body"] = field(block, "content") or field(block, "summary")
        items.append(it)
        if len(items) >= limit:
            break
    return items


def parse_hn(json_text, limit=25):
    """Hacker News (Algolia API) の JSON。points を注目度として見出しに付ける"""
    items = []
    seen = set()
    try:
        hits = json.loads(json_text or "{}").get("hits", [])
    except Exception:
        return items
    for h in hits:
        title = h.get("title") or ""
        if not title:
            continue
        key = " ".join(title.split())
        if key in seen:
            continue
        seen.add(key)
        url = h.get("url") or "https://news.ycombinator.com/item?id=" + str(h.get("objectID", ""))
        items.append({"title": "[" + str(h.get("points", 0)) + " pts] " + title,
                      "url": url, "pub_date": h.get("created_at", "")})
        if len(items) >= limit:
            break
    return items


def main(body_ap, body_reuters, body_wsj, body_ars, body_hn,
         body_simonw, body_nautilus, body_aeon):
    # (媒体名, items, 要約対象か)
    sources = [
        ("AP News", parse_rss(body_ap, strip_suffix=True), False),
        ("Reuters", parse_rss(body_reuters, strip_suffix=True), False),
        ("WSJ", parse_rss(body_wsj, strip_suffix=True), False),
        ("Ars Technica", parse_rss(body_ars), False),
        ("Hacker News", parse_hn(body_hn), False),
        ("Simon Willison", parse_rss(body_simonw, limit=10, want_body=True), True),
        ("Nautilus", parse_rss(body_nautilus, limit=10, want_body=True), True),
        ("Aeon", parse_rss(body_aeon, limit=10, want_body=True), True),
    ]
    lines = ["## 🌐 海外ニュース", ""]
    url_map = {}
    summ_blocks = []  # 要約LLMに渡す本文素材(===[[id]]=== / 見出し / 本文)
    count = 0
    for name, items, do_summ in sources:
        lines.append("### " + name)
        if not items:
            lines.append("(取得失敗または0件)")
        for it in items:
            count += 1
            url_map[str(count)] = it["url"]
            # URLは翻訳に不要な巨大トークン。連番プレースホルダに退避し後段で復元する。
            lines.append("- " + it["title"])
            lines.append("  [[" + str(count) + "]]")
            lines.append("  " + to_jst(it["pub_date"]))
            if do_summ:
                # コードノードはネット禁止のため記事ページの補完取得はしない。
                # フィード埋め込み本文だけを素材にする(無ければ要約対象から外す)。
                btext = truncate(html_to_text(it.get("body", "")))
                if btext:
                    summ_blocks.append("===[[" + str(count) + "]]===\n" + it["title"] + "\n" + btext)
        lines.append("")

    return {
        "formatted_articles": "\n".join(lines),
        "url_map": json.dumps(url_map, ensure_ascii=False),
        "summary_src": "\n\n".join(summ_blocks),
    }'''

# === URL復元 + 要約差し込みコード ===================================================
# 翻訳済み海外 Markdown を行単位で処理。各見出し直後の [[N]] 行で:
#   - with_urls=yes なら実URL行に置換、no(既定)なら行を除去
#   - 要約LLM出力に [[N]] の要約があれば、見出しと日時の間に `↳` 行を差し込む
RESTORE_CODE = r'''import json
import re

PH = re.compile(r"^\s*\[\[(\d+)\]\]\s*$")          # 本文側: [[N]] 単独行
SUMM = re.compile(r"^\s*\[\[(\d+)\]\]\s*(.+)$")     # 要約側: [[N]] 要約文


def parse_summaries(text):
    """要約LLM出力(`[[N]] 要約文` の行)を {id: 要約} に。"""
    out = {}
    for ln in (text or "").split("\n"):
        m = SUMM.match(ln)
        if m and m.group(2).strip():
            out[m.group(1)] = m.group(2).strip()
    return out


def main(translated, url_map, summaries_text, with_urls):
    try:
        m = json.loads(url_map or "{}")
    except Exception:
        m = {}
    summaries = parse_summaries(summaries_text)
    wu = (with_urls or "").strip().lower() in ("yes", "true", "1", "on")
    out = []
    for ln in (translated or "").split("\n"):
        mo = PH.match(ln)
        if mo:
            sid = mo.group(1)
            if wu:
                url = m.get(sid)
                out.append("  " + url if url else ln)
            s = summaries.get(sid)
            if s:
                out.append("  ↳ " + s)
            continue
        out.append(ln)
    return {"digest": "\n".join(out)}'''

# === 連結コード(国内+海外を縦結合し # タイトルを足す)===============================
CONCAT_CODE = r'''def main(jp_md, en_md, date_label):
    title = "# 統合ダイジェスト(" + (date_label or "") + ")"
    parts = [title, "", (jp_md or "").strip(), "", (en_md or "").strip()]
    digest = "\n".join(parts).rstrip() + "\n"
    return {"digest": digest}'''

# === 翻訳 LLM プロンプト(見出しだけ訳す。要約・論評禁止)============================
TRANSLATE_SYSTEM = """あなたは英語ニュース見出しリストの翻訳者です。与えられた「ソース別の見出しリスト」(Markdown)の各見出しを自然な日本語に翻訳します。これは翻訳タスクであり、要約・解説・論評は一切行いません。

# 厳守ルール
1. Markdownの構造(`## 🌐 海外ニュース` 見出し、`### 媒体名` 見出し、`-` の箇条書き、プレースホルダ行、日時行、空行)はそのまま保持する
2. 翻訳するのは `-` で始まる見出し行の本文だけ。先頭の `[N pts]` は原文のまま残す
3. 各見出しの直後の2行は触らない: `[[数字]]`(例 `[[12]]`。URLプレースホルダ。記号も数字も1文字も変えない)と、その次の日時行(例 `2026-06-15 09:00 JST`)。翻訳・削除・並べ替え・採番変更を一切しない
4. 固有名詞・製品名・社名・数値・引用句は原文に忠実に。定訳の無い固有名詞は原文のまま、または「日本語(原文)」と併記してよい
5. 見出しに無い情報を足さない。見出しから内容を推測して補わない。翻訳のみ
6. 「(取得失敗または0件)」はそのまま残す
7. `## 🌐 海外ニュース` と `### 媒体名` の見出しはそのまま残す(媒体名は固有名詞。`#` のタイトル行は付けない)

# 出力
翻訳後のMarkdown本文だけを出力する。前置き・後書き・コードフェンスは不要。"""

# === 要約 LLM プロンプト(本文から1〜2文。本文に無いことは足さない)==================
SUMMARIZE_SYSTEM = """あなたは記事本文の要約者です。入力には、海外ニュースのうち見出しだけでは内容が分かりにくい記事の本文が `===[[ID]]===` 区切りで複数並んでいます(各ブロックは 1 行目=見出し、2 行目以降=本文)。各記事を1〜2文の自然な日本語に要約します。

# 厳守ルール
1. 出力は記事ごとに1行、`[[ID]] 要約文` の形式。ID は入力の `===[[ID]]===` の数字をそのまま使う(記号も数字も1文字も変えない)
2. 要約は1〜2文・改行なし。前置き・見出しの繰り返し・引用符・箇条書き・URLは付けない
3. 本文に書かれていないことを足さない。推測で補わない。本文の内容だけを要約する
4. 媒体は問わず「何についての記事か」が素直に分かる要約にする
5. 入力に記事ブロックが1件も無ければ、何も出力しない(空出力)

# 出力例
[[86]] 静止画をクリックすると短い動画として再生される仕組みの紹介。
[[96]] カルシウムとビタミンDのサプリには骨折予防効果が乏しいとする新研究の報告。"""


# --- ノード/エッジのビルダ -----------------------------------------------------------

def http_node(nid, title, url, x, y):
    return {
        "data": {
            "authorization": {"config": None, "type": "no-auth"},
            "body": {"data": [], "type": "none"},
            "desc": "",
            "headers": "User-Agent:" + UA,
            "method": "get",
            "params": "",
            "retry_config": {"max_retries": 3, "retry_enabled": True, "retry_interval": 100},
            "selected": False,
            "ssl_verify": True,
            "timeout": {"max_connect_timeout": 0, "max_read_timeout": 0, "max_write_timeout": 0},
            "title": title,
            "type": "http-request",
            "url": url,
            "variables": [],
        },
        "height": 155, "id": nid,
        "position": {"x": x, "y": y}, "positionAbsolute": {"x": x, "y": y},
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": 242,
    }


def code_node(nid, title, code, outputs, variables, x, y):
    return {
        "data": {
            "code": code, "code_language": "python3",
            "outputs": {k: {"children": None, "type": "string"} for k in outputs},
            "selected": False, "title": title, "type": "code", "variables": variables,
        },
        "height": 52, "id": nid,
        "position": {"x": x, "y": y}, "positionAbsolute": {"x": x, "y": y},
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": 242,
    }


def llm_node(nid, title, system, user, x, y):
    return {
        "data": {
            "context": {"enabled": False, "variable_selector": []},
            "model": {"completion_params": {"temperature": 0.3}, "mode": "chat",
                      "name": LLM_MODEL, "provider": LLM_PROVIDER},
            "prompt_template": [
                {"id": nid + "-sys", "role": "system", "text": system},
                {"id": nid + "-usr", "role": "user", "text": user},
            ],
            "retry_config": {"max_retries": 3, "retry_enabled": True, "retry_interval": 5000},
            "selected": False, "title": title, "type": "llm", "vision": {"enabled": False},
        },
        "height": 119, "id": nid,
        "position": {"x": x, "y": y}, "positionAbsolute": {"x": x, "y": y},
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": 242,
    }


def var(name, src_id, src_var):
    return {"value_selector": [src_id, src_var], "value_type": "string", "variable": name}


def edge(src, tgt, st, tt):
    return {
        "data": {"isInIteration": False, "isInLoop": False, "sourceType": st, "targetType": tt},
        "id": "%s-source-%s-target" % (src, tgt),
        "source": src, "sourceHandle": "source", "target": tgt, "targetHandle": "target",
        "type": "custom", "zIndex": 0,
    }


def build() -> dict:
    nodes = []
    edges = []

    # 開始: with_urls(URL併記を切り替える任意入力。既定 no)
    nodes.append({
        "data": {
            "selected": False, "title": "ユーザー入力", "type": "start",
            "variables": [{
                "default": "no",
                "hint": "出典URLを併記するなら yes。空または no で見出し+日時のみ(既定)",
                "label": "with_urls", "max_length": 48, "options": [], "placeholder": "no",
                "required": False, "type": "text-input", "variable": "with_urls",
            }],
        },
        "height": 116, "id": ID_START,
        "position": {"x": -540, "y": 400}, "positionAbsolute": {"x": -540, "y": 400},
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": 242,
    })

    # --- 国内: start → 日経 → 残り4本 → 各HTTP → 国内整形 ---
    for i, (nid, title, url, _v) in enumerate(JP_HTTP):
        nodes.append(http_node(nid, title, url, 0, 40 + i * 185))
        edges.append(edge(nid, ID_JP_CODE, "http-request", "code"))
    jp_head = JP_HTTP[0][0]
    edges.append(edge(ID_START, jp_head, "start", "http-request"))
    for nid, _t, _u, _v in JP_HTTP[1:]:
        edges.append(edge(jp_head, nid, "http-request", "http-request"))

    nodes.append(code_node(
        ID_JP_CODE, "国内整形", JP_CODE,
        ["formatted_articles", "date_label"],
        [var(v, nid, "body") for nid, _t, _u, v in JP_HTTP]
        + [var("with_urls", ID_START, "with_urls")],
        350, 350,
    ))

    # --- 海外: start → AP → 残り7本 → 各HTTP → 海外整形 ---
    for i, (nid, title, url, _v) in enumerate(EN_HTTP):
        nodes.append(http_node(nid, title, url, 0, 1050 + i * 185))
        edges.append(edge(nid, ID_EN_CODE, "http-request", "code"))
    en_head = EN_HTTP[0][0]
    edges.append(edge(ID_START, en_head, "start", "http-request"))
    for nid, _t, _u, _v in EN_HTTP[1:]:
        edges.append(edge(en_head, nid, "http-request", "http-request"))

    nodes.append(code_node(
        ID_EN_CODE, "海外整形", EN_CODE,
        ["formatted_articles", "url_map", "summary_src"],
        [var(v, nid, "body") for nid, _t, _u, v in EN_HTTP],
        350, 1350,
    ))

    # --- 翻訳 / 要約(海外整形からファンアウト)→ URL復元で合流 ---
    nodes.append(llm_node(
        ID_TRANSLATE, "翻訳", TRANSLATE_SYSTEM,
        "{{#" + ID_EN_CODE + ".formatted_articles#}}", 1015, 1250,
    ))
    nodes.append(llm_node(
        ID_SUMMARIZE, "要約", SUMMARIZE_SYSTEM,
        "{{#" + ID_EN_CODE + ".summary_src#}}", 1015, 1450,
    ))
    edges.append(edge(ID_EN_CODE, ID_TRANSLATE, "code", "llm"))
    edges.append(edge(ID_EN_CODE, ID_SUMMARIZE, "code", "llm"))

    nodes.append(code_node(
        ID_RESTORE, "URL復元", RESTORE_CODE, ["digest"],
        [
            var("translated", ID_TRANSLATE, "text"),
            var("url_map", ID_EN_CODE, "url_map"),
            var("summaries_text", ID_SUMMARIZE, "text"),
            var("with_urls", ID_START, "with_urls"),
        ],
        1620, 1350,
    ))
    edges.append(edge(ID_TRANSLATE, ID_RESTORE, "llm", "code"))
    edges.append(edge(ID_SUMMARIZE, ID_RESTORE, "llm", "code"))

    # --- 連結(国内整形 + 海外URL復元を待ち合わせ)→ 出力 ---
    nodes.append(code_node(
        ID_CONCAT, "連結", CONCAT_CODE, ["digest"],
        [
            var("jp_md", ID_JP_CODE, "formatted_articles"),
            var("en_md", ID_RESTORE, "digest"),
            var("date_label", ID_JP_CODE, "date_label"),
        ],
        2274, 700,
    ))
    edges.append(edge(ID_JP_CODE, ID_CONCAT, "code", "code"))
    edges.append(edge(ID_RESTORE, ID_CONCAT, "code", "code"))

    nodes.append({
        "data": {
            "outputs": [
                {"value_selector": [ID_CONCAT, "digest"], "value_type": "string", "variable": "digest"},
                {"value_selector": [ID_JP_CODE, "date_label"], "value_type": "string", "variable": "date_label"},
            ],
            "selected": False, "title": "出力", "type": "end",
        },
        "height": 90, "id": ID_END,
        "position": {"x": 2620, "y": 700}, "positionAbsolute": {"x": 2620, "y": 700},
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": 242,
    })
    edges.append(edge(ID_CONCAT, ID_END, "code", "end"))

    return {
        "app": {
            "description": ("国内5ソース+海外8ソースのニュース見出しを収集・整形し、海外分のみ日本語翻訳して"
                            "1本の統合ダイジェストにまとめる(見にくい3ソースは本文要約も付与)"),
            "icon": "🗞️", "icon_background": "#FFEAD5", "icon_type": "emoji",
            "mode": "workflow", "name": "combined-news-digest", "use_icon_as_answer_icon": False,
        },
        "dependencies": [{
            "current_identifier": None, "type": "marketplace",
            "value": {"marketplace_plugin_unique_identifier": GEMINI_PLUGIN, "version": None},
        }],
        "kind": "app", "version": "0.6.0",
        "workflow": {
            "conversation_variables": [], "environment_variables": [],
            "features": {
                "file_upload": {"enabled": False}, "opening_statement": "",
                "retriever_resource": {"enabled": True},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False}, "suggested_questions": [],
                "suggested_questions_after_answer": {"enabled": False},
                "text_to_speech": {"enabled": False, "language": "", "voice": ""},
            },
            "graph": {"edges": edges, "nodes": nodes,
                      "viewport": {"x": 0, "y": 0, "zoom": 0.7}},
            "rag_pipeline_variables": [],
        },
    }


def main() -> None:
    doc = build()
    DST.write_text(
        yaml.dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120),
        encoding="utf-8",
    )
    g = doc["workflow"]["graph"]
    print("generated: %s (nodes=%d, edges=%d)" % (DST, len(g["nodes"]), len(g["edges"])))


if __name__ == "__main__":
    main()
