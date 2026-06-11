#!/usr/bin/env python3
"""jp-news-digest のDSLから en-news-digest のDSLを生成する。

やること:
- 全ノードIDを '9' プレフィックスで付け替え(プロンプト内の {{#id.var#}} 参照も追従)
- HTTPノード5本を英語メディアに差し替え + 3本追加(計8ソース)
- コードノードを Atom / Hacker News(Algolia JSON) 対応版に差し替え
- LLM①②のプロンプトを英語メディア用に書き換え(③④は汎用なのでそのまま)
- previous_digests を任意入力に変更

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
ID_LLM1 = "1781187450764"
ID_LLM2 = "1781187548626"
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
    count = 0
    for name, items in sources:
        lines.append("## " + name)
        if not items:
            lines.append("(取得失敗または0件)")
        for it in items:
            lines.append("- " + it["title"] + " (" + it["pub_date"] + ")")
            lines.append("  " + it["url"])
            count += 1
        lines.append("")

    jst_now = datetime.now(timezone(timedelta(hours=9)))
    date_label = str(jst_now.year) + "年" + str(jst_now.month) + "月" + str(jst_now.day) + "日"

    return {
        "formatted_articles": "\\n".join(lines),
        "article_count": count,
        "date_label": date_label,
    }'''

LLM1_SYSTEM = """あなたは海外ニュースを分析するベテラン編集者です。複数媒体の本日の見出しリスト(英語)を受け取り、後工程(日本語ダイジェスト執筆)のための「トピック分析メモ」を日本語で作成します。見出しの引用は原文(英語)のままで構いません。

# タスク1: 同一トピック検出
全媒体の見出しを横断し、同じニュースを報じている記事をグループ化する。
- キーワードの一致だけでグルーピングしない。同じ人名・組織名・地名でも、続報・関連事案・派生事件で別事案が混在しがち。日付・関係者の属性・経緯まで一致するかを見出しから確認し、確認できない場合は「別事案の可能性あり」と注記する
- まず「別事案ではないか」と疑ってからグループ化すること
- Hacker News の [N pts] は注目度(投票数)。高ポイントの技術話題は単独でも重要とみなす

# タスク2: 過去ダイジェストとの照合(続報疲れの防止)
過去ダイジェストが与えられた場合、各トピックを次の3つに分類する:
- 「新規」: 過去ダイジェストに出ていない
- 「続報」: 既報だが新展開がある。何が新展開かを1行で書く
- 「既報・新展開なし」: 同内容の再掲。ダイジェストでは扱いを最小化すべきもの
過去ダイジェストが空の場合は全トピックを「新規」とする。

# 出力フォーマット
### トピック: [トピック名(日本語)]
- 分類: 新規 / 続報(新展開: ...) / 既報・新展開なし
- 該当見出し: [媒体名] 見出し原文 (各行1件、URLも併記)
- 注記: (別事案の可能性、グルーピングの確信度など。なければ省略)

最後に「## 単独記事」セクションを置き、どのグループにも属さないが重要そうな見出しを媒体ごとに5件程度まで列挙する。"""

LLM2_SYSTEM = """あなたは海外ニュースの日本語ダイジェスト執筆者です。本日の英語見出しリストとトピック分析メモから、フレーミング比較付きダイジェスト(Markdown、日本語)を執筆します。

媒体の役割: AP News(通信社・ファクトベース)、Reuters(通信社・国際)、WSJ(ビジネス・経済)、Ars Technica(テック・科学)、Hacker News(テックコミュニティ。points=投票数=注目度)、Simon Willison(AI・開発の個人ブログ)、Nautilus(科学エッセイ)、Aeon(思想・哲学エッセイ)。

# 出力フォーマット
# 海外ニュースダイジェスト({date}) ← 与えられた日付を使う

## AP News — 通信社(ファクトベース)
[主要記事を列挙し、各記事に日本語1行解説。見出しは和訳し、固有名詞は原文に忠実に]
[フレーミング注] 通信社らしいストレート報道か、選題に偏りがあれば指摘

## Reuters — 国際
(同様)

## WSJ — ビジネス・経済
(同様。市場・企業視点への寄りを指摘)

## Ars Technica — テック・科学
(同様)

## Hacker News — コミュニティの注目
[points上位を中心に。開発者コミュニティが何に注目しているかを1行ずつ]

## ブログ・エッセイ(Simon Willison / Nautilus / Aeon)
[新着があれば紹介。なければ「更新なし」と1行]

## 横断的なトレンド
[複数媒体で共通するテーマを3〜5個。「今回新しく動いたもの」を優先]

## メディアフレーミング比較
[同一トピックを報じた媒体(特にAP/Reuters/WSJ)の見出しを並べ、言葉選び・論調・省略された文脈の差を分析。最重要セクション。2〜4トピック]

# 品質保証ルール(厳守)
1. 層を分ける: 「見出し引用」(そのまま引用・和訳)と「フレーミング解釈」(憶測OK、ただし「〜と読める」「〜の可能性がある」等の解釈語尾で明示)を混ぜない。裏取りしていない事実は断定で書かず「(未確認)」を付す
2. 入力は見出しと日時のみで、記事本文は取得していない。見出しから自明でない内容・主張・数値・結論を推測して書かない。抽象的・問題提起型の見出しの中身を創作しない。踏み込めない記事は見出し引用+「(見出しのみ)」に留める
3. 強い断定(「最も〜」「明らかに〜」等)は複数媒体の見出しで裏付く場合のみ
4. トピック分析メモの分類を反映する: 「既報・新展開なし」は冒頭・トレンドから外し、本編でも「(M月D日に既報、新展開なし)」の1行に留める。「続報」は差分だけを書く
5. 「別事案の可能性あり」と注記されたグループは、安易に1つの事案としてまとめない
6. 株価・件数・順位など変動する値は、見出しに明記されているもの以外書かない
7. 和訳は意訳でよいが、固有名詞・数値・引用句は原文に忠実に"""


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    doc_probe = yaml.safe_load(text)
    old_ids = [n["id"] for n in doc_probe["workflow"]["graph"]["nodes"]]
    # ID付け替え(プロンプト内 {{#id.var#}} とエッジ参照も文字列置換で追従)
    for oid in sorted(old_ids, key=len, reverse=True):
        text = text.replace(oid, "9" + oid)
    doc = yaml.safe_load(text)

    doc["app"]["name"] = "en-news-digest"
    doc["app"]["description"] = "海外ニュースの見出しを8ソースから収集しフレーミング比較ダイジェストを生成"
    doc["app"]["icon"] = "🌏"

    graph = doc["workflow"]["graph"]
    nodes = {n["id"]: n for n in graph["nodes"]}

    # 開始ノード: previous_digests を任意に
    for v in nodes["9" + ID_START]["data"]["variables"]:
        v["required"] = False

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

    # コードノード差し替え
    code_node = nodes["9" + ID_CODE]
    code_node["data"]["code"] = EN_CODE
    code_node["data"]["variables"] = [
        {"value_selector": [src_id, "body"], "value_type": "string", "variable": var}
        for var, src_id in CODE_VARS
    ]

    # LLM①②のSYSTEMプロンプト差し替え(USERプロンプトは参照のみなのでID置換で済んでいる)
    nodes["9" + ID_LLM1]["data"]["prompt_template"][0]["text"] = LLM1_SYSTEM
    nodes["9" + ID_LLM2]["data"]["prompt_template"][0]["text"] = LLM2_SYSTEM

    # ダイジェスト名もen用に(LLM②のフォーマット見出しはSYSTEM内で指定済み)
    DST.write_text(
        yaml.dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120),
        encoding="utf-8",
    )
    print(f"generated: {DST} (nodes={len(graph['nodes'])}, edges={len(graph['edges'])})")


if __name__ == "__main__":
    main()
