#!/usr/bin/env python3
"""jp-news-digest のDSL(純コード版)から en-news-digest のDSLを生成する。

設計方針(2026-06-15 改訂):
- ダイジェストは「機械整形 + 翻訳のみ」。LLMによる編集・分析・接地監査は廃止した
  (見出しを忠実に整形/翻訳する限り捏造の余地が無く、接地確認が不要になるため)
- jp はソースが日本語なので翻訳すら不要 → LLMゼロの純コードワークフロー
- en は英語ソースなので「コード整形 → 翻訳LLM 1回 → 出力」の1ノードだけLLMを使う

やること:
- 全ノードIDを '9' プレフィックスで付け替え(エッジ参照も文字列置換で追従)
- HTTPノード5本を英語メディアに差し替え + 3本追加(計8ソース)
- コードノードを Atom / Hacker News(Algolia JSON) 対応版に差し替え
- URLは翻訳に不要な巨大トークンなので、整形時に [[連番]] プレースホルダへ退避し url_map に保存
- IF(true) と 出力(end2) の間に「翻訳」LLM → 「URL復元」コード の2ノードを挿入し、end2 を復元後に繋ぐ

使い方:
    python scripts/gen_en_dsl.py
    → workflows/en-news-digest.yml を生成
"""

import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "workflows" / "jp-news-digest.yml"
DST = ROOT / "workflows" / "en-news-digest.yml"

# 旧ID(jp) → 役割の対応。新IDは '9' + 旧ID
ID_START = "1781187165020"
ID_CODE = "1781187326232"
ID_IF = "1781187440808"
ID_END2 = "1781188032135"  # 整形成功時の出力ノード
ID_TRANSLATE = "999178118900010"  # en で新設する翻訳LLMノード
ID_RESTORE = "999178118900011"  # en で新設するURL復元コードノード
HTTP_REPLACE = {  # 旧HTTPノードID → (新タイトル, URL)
    "1781189175977": ("AP News", "https://news.google.com/rss/search?q=site:apnews.com&hl=en-US&gl=US&ceid=US:en"),
    "17811893361310": ("Reuters", "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en"),
    "17811893942900": ("WSJ", "https://news.google.com/rss/search?q=site:wsj.com&hl=en-US&gl=US&ceid=US:en"),
    "17811893972820": ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    "17811893996220": ("Hacker News", "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=25"),
}
HTTP_NEW = [  # 追加3本: (新ID, タイトル, URL)
    ("999178118900001", "Simon Willison", "https://simonwillison.net/atom/everything/"),
    ("999178118900002", "Nautilus", "https://nautil.us/feed/"),
    ("999178118900003", "Aeon", "https://aeon.co/feed.rss"),
]
# コードノードの入力変数名 → 取得元HTTPノードID(新ID)
CODE_VARS = [
    ("body_ap", "9" + "1781189175977"),
    ("body_reuters", "9" + "17811893361310"),
    ("body_wsj", "9" + "17811893942900"),
    ("body_ars", "9" + "17811893972820"),
    ("body_hn", "9" + "17811893996220"),
    ("body_simonw", "999178118900001"),
    ("body_nautilus", "999178118900002"),
    ("body_aeon", "999178118900003"),
]

EN_CODE = '''import json
import re
from datetime import datetime, timedelta, timezone

ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&apos;": "'"}


def unescape(text):
    for k, v in ENTITIES.items():
        text = text.replace(k, v)
    return text


def field(block, tag):
    f = re.search(r"<" + tag + r"[^>]*>(?:<!\\[CDATA\\[)?([\\s\\S]*?)(?:\\]\\]>)?</" + tag + r">", block)
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


def parse_rss(xml_text, strip_suffix=False, limit=15):
    """RSS 2.0 の <item> と Atom の <entry> 両対応"""
    items = []
    blocks = re.findall(r"<item>([\\s\\S]*?)</item>", xml_text or "")
    if blocks:
        for block in blocks:
            title = field(block, "title")
            if title:
                if strip_suffix and " - " in title:
                    title = title.rsplit(" - ", 1)[0]
                items.append({"title": title, "url": field(block, "link"), "pub_date": field(block, "pubDate")})
            if len(items) >= limit:
                break
        return items
    for block in re.findall(r"<entry>([\\s\\S]*?)</entry>", xml_text or "")[:limit]:
        title = field(block, "title")
        m = re.search(r'<link[^>]*href="([^"]+)"', block)
        pub = field(block, "updated") or field(block, "published")
        if title:
            items.append({"title": title, "url": m.group(1) if m else "", "pub_date": pub})
    return items


def parse_hn(json_text, limit=25):
    """Hacker News (Algolia API) の JSON。points を注目度として見出しに付ける"""
    items = []
    try:
        hits = json.loads(json_text or "{}").get("hits", [])
    except Exception:
        return items
    for h in hits[:limit]:
        title = h.get("title") or ""
        if not title:
            continue
        url = h.get("url") or "https://news.ycombinator.com/item?id=" + str(h.get("objectID", ""))
        items.append({"title": "[" + str(h.get("points", 0)) + " pts] " + title,
                      "url": url, "pub_date": h.get("created_at", "")})
    return items


def main(body_ap, body_reuters, body_wsj, body_ars, body_hn,
         body_simonw, body_nautilus, body_aeon):
    sources = [
        ("AP News", parse_rss(body_ap, strip_suffix=True)),
        ("Reuters", parse_rss(body_reuters, strip_suffix=True)),
        ("WSJ", parse_rss(body_wsj, strip_suffix=True)),
        ("Ars Technica", parse_rss(body_ars)),
        ("Hacker News", parse_hn(body_hn)),
        ("Simon Willison", parse_rss(body_simonw, limit=10)),
        ("Nautilus", parse_rss(body_nautilus, limit=10)),
        ("Aeon", parse_rss(body_aeon, limit=10)),
    ]
    lines = []
    url_map = {}
    count = 0
    for name, items in sources:
        lines.append("## " + name)
        if not items:
            lines.append("(取得失敗または0件)")
        for it in items:
            count += 1
            url_map[str(count)] = it["url"]
            # URLは翻訳に不要な巨大トークン。連番プレースホルダに退避し後段で復元する
            lines.append("- " + it["title"] + " (" + to_jst(it["pub_date"]) + ")")
            lines.append("  [[" + str(count) + "]]")
        lines.append("")

    jst_now = datetime.now(timezone(timedelta(hours=9)))
    date_label = str(jst_now.year) + "年" + str(jst_now.month) + "月" + str(jst_now.day) + "日"

    return {
        "formatted_articles": "\\n".join(lines),
        "article_count": count,
        "date_label": date_label,
        "url_map": json.dumps(url_map, ensure_ascii=False),
    }'''

# 翻訳ノードのシステムプロンプト。整形済みリストの「見出しだけ」を訳し、構造・
# プレースホルダ・日時・[N pts] は一切触らない。要約・解説・論評は禁止(=接地リスクをゼロに保つ)。
# URLは [[数字]] プレースホルダに退避済みで、LLMには渡さない(後段の「URL復元」で戻す)。
TRANSLATE_SYSTEM = """あなたは英語ニュース見出しリストの翻訳者です。与えられた「ソース別の見出しリスト」(Markdown)の各見出しを自然な日本語に翻訳します。これは翻訳タスクであり、要約・解説・論評は一切行いません。

# 厳守ルール
1. Markdownの構造(`## 媒体名`、`-` の箇条書き、プレースホルダ行、空行)はそのまま保持する
2. 翻訳するのは `-` で始まる見出し行の本文だけ。行末の日時の括弧 `(...)` と先頭の `[N pts]` は原文のまま残す
3. `[[数字]]`(例: `[[12]]`)はURLのプレースホルダ。記号も数字も1文字も変えず、翻訳・削除・並べ替え・採番変更を一切しない。各見出しの直後の行にそのまま残す
4. 固有名詞・製品名・社名・数値・引用句は原文に忠実に。定訳の無い固有名詞は原文のまま、または「日本語(原文)」と併記してよい
5. 見出しに無い情報を足さない。見出しから内容を推測して補わない。翻訳のみ
6. 「(取得失敗または0件)」はそのまま残す
7. 先頭に `# 海外ニュースダイジェスト({date})` の見出しを1行付ける(日付は与えられた値を使う)

# 出力
翻訳後のMarkdown本文だけを出力する。前置き・後書き・コードフェンスは不要。"""

TRANSLATE_USER = "日付: {{#9" + ID_CODE + ".date_label#}}\n\n{{#9" + ID_CODE + ".formatted_articles#}}"

# URL復元コードノード。翻訳済みテキスト中の [[数字]] を url_map の実URLに戻す。
RESTORE_CODE = '''import json
import re


def main(translated, url_map):
    try:
        m = json.loads(url_map or "{}")
    except Exception:
        m = {}

    def repl(mo):
        return m.get(mo.group(1), mo.group(0))

    digest = re.sub(r"\\[\\[(\\d+)\\]\\]", repl, translated or "")
    return {"digest": digest}'''


def build_translate_node() -> dict:
    """en 専用の翻訳LLMノードを構築する(jp 純コード版には存在しない)"""
    return {
        "data": {
            "context": {"enabled": False, "variable_selector": []},
            "model": {
                "completion_params": {"temperature": 0.3},
                "mode": "chat",
                "name": "gemini-3.1-flash-lite",
                "provider": "langgenius/gemini/google",
            },
            "prompt_template": [
                {"id": "tr-sys-0001", "role": "system", "text": TRANSLATE_SYSTEM},
                {"id": "tr-usr-0001", "role": "user", "text": TRANSLATE_USER},
            ],
            "retry_config": {"max_retries": 3, "retry_enabled": True, "retry_interval": 5000},
            "selected": False,
            "title": "翻訳",
            "type": "llm",
            "vision": {"enabled": False},
        },
        "height": 119,
        "id": ID_TRANSLATE,
        "position": {"x": 1015.0, "y": 376.0},
        "positionAbsolute": {"x": 1015.0, "y": 376.0},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 242,
    }


def build_restore_node() -> dict:
    """翻訳結果の [[数字]] を実URLに戻すコードノード(en 専用)"""
    return {
        "data": {
            "code": RESTORE_CODE,
            "code_language": "python3",
            "outputs": {"digest": {"children": None, "type": "string"}},
            "selected": False,
            "title": "URL復元",
            "type": "code",
            "variables": [
                {"value_selector": [ID_TRANSLATE, "text"], "value_type": "string", "variable": "translated"},
                {"value_selector": ["9" + ID_CODE, "url_map"], "value_type": "string", "variable": "url_map"},
            ],
        },
        "height": 52,
        "id": ID_RESTORE,
        "position": {"x": 1620.0, "y": 376.0},
        "positionAbsolute": {"x": 1620.0, "y": 376.0},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 242,
    }


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    doc_probe = yaml.safe_load(text)
    old_ids = [n["id"] for n in doc_probe["workflow"]["graph"]["nodes"]]
    # ID付け替え(エッジ参照も文字列置換で追従)。長いIDから処理して部分一致を防ぐ
    for oid in sorted(old_ids, key=len, reverse=True):
        text = text.replace(oid, "9" + oid)
    doc = yaml.safe_load(text)

    doc["app"]["name"] = "en-news-digest"
    doc["app"]["description"] = "海外ニュースの見出しを8ソースから収集し整形・日本語翻訳する(LLMは翻訳1回のみ)"
    doc["app"]["icon"] = "🌏"

    graph = doc["workflow"]["graph"]
    nodes = {n["id"]: n for n in graph["nodes"]}

    # HTTPノード差し替え
    http_template = None
    for oid, (title, url) in HTTP_REPLACE.items():
        node = nodes["9" + oid]
        node["data"]["title"] = title
        node["data"]["desc"] = ""
        node["data"]["url"] = url
        http_template = node

    # HTTPノード追加3本(既存ノードを複製してID・URL・位置を変更)
    base_y = http_template["position"]["y"]
    for i, (nid, title, url) in enumerate(HTTP_NEW):
        node = copy.deepcopy(http_template)
        node["id"] = nid
        node["data"]["title"] = title
        node["data"]["desc"] = ""
        node["data"]["url"] = url
        for key in ("position", "positionAbsolute"):
            node[key] = dict(node[key])
            node[key]["y"] = base_y + 190.0 * (i + 1)
        graph["nodes"].append(node)
        # 開始→HTTP、HTTP→コード のエッジを複製パターンで追加
        graph["edges"].append({
            "data": {"isInLoop": False, "sourceType": "start", "targetType": "http-request"},
            "id": f"9{ID_START}-source-{nid}-target",
            "source": "9" + ID_START, "sourceHandle": "source",
            "target": nid, "targetHandle": "target",
            "type": "custom", "zIndex": 0,
        })
        graph["edges"].append({
            "data": {"isInLoop": False, "sourceType": "http-request", "targetType": "code"},
            "id": f"{nid}-source-9{ID_CODE}-target",
            "source": nid, "sourceHandle": "source",
            "target": "9" + ID_CODE, "targetHandle": "target",
            "type": "custom", "zIndex": 0,
        })

    # コードノード差し替え(8ソース対応版)。url_map を出力に追加
    code_node = nodes["9" + ID_CODE]
    code_node["data"]["code"] = EN_CODE
    code_node["data"]["variables"] = [
        {"value_selector": [src_id, "body"], "value_type": "string", "variable": var}
        for var, src_id in CODE_VARS
    ]
    code_node["data"]["outputs"]["url_map"] = {"children": None, "type": "string"}

    # 翻訳・復元ノードを挿入: IF(true) → 翻訳 → URL復元 → 出力(end2)
    graph["nodes"].append(build_translate_node())
    graph["nodes"].append(build_restore_node())
    end2_id = "9" + ID_END2
    for e in graph["edges"]:
        if e["id"] == f"9{ID_IF}-true-{end2_id}-target":
            e["target"] = ID_TRANSLATE
            e["data"]["targetType"] = "llm"
            e["id"] = f"9{ID_IF}-true-{ID_TRANSLATE}-target"
    graph["edges"].append({
        "data": {"isInIteration": False, "isInLoop": False, "sourceType": "llm", "targetType": "code"},
        "id": f"{ID_TRANSLATE}-source-{ID_RESTORE}-target",
        "source": ID_TRANSLATE, "sourceHandle": "source",
        "target": ID_RESTORE, "targetHandle": "target",
        "type": "custom", "zIndex": 0,
    })
    graph["edges"].append({
        "data": {"isInIteration": False, "isInLoop": False, "sourceType": "code", "targetType": "end"},
        "id": f"{ID_RESTORE}-source-{end2_id}-target",
        "source": ID_RESTORE, "sourceHandle": "source",
        "target": end2_id, "targetHandle": "target",
        "type": "custom", "zIndex": 0,
    })

    # 出力ノードを復元後ダイジェストに繋ぎ替える
    nodes[end2_id]["data"]["outputs"] = [
        {"value_selector": [ID_RESTORE, "digest"], "value_type": "string", "variable": "digest"},
        {"value_selector": ["9" + ID_CODE, "date_label"], "value_type": "string", "variable": "date_label"},
    ]

    DST.write_text(
        yaml.dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120),
        encoding="utf-8",
    )
    print(f"generated: {DST} (nodes={len(graph['nodes'])}, edges={len(graph['edges'])})")


if __name__ == "__main__":
    main()
