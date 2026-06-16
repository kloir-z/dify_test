#!/usr/bin/env python3
"""jp-news-digest と en-news-digest を1本に統合した combined-news-digest の DSL を生成する。

設計方針:
- 既存の2つの生成済みDSL(jp=純コード5ソース / en=8ソース+翻訳LLM)から、必要なノード
  「だけ」を抜き出して1つのグラフに合流させる。コード本体・HTTP設定・翻訳プロンプトは
  両DSLの実物をそのまま再利用する(ここで重複定義しない=単一の真実源を保つ)
- フロー:
    start ┬→ jp HTTP×5 → jp_code ─────────────────────────┐
          └→ en HTTP×8 → en_code → 翻訳 → URL復元 ─────────┤
                                                            └→ 連結 → 出力
  jp と en は独立なので start から並走し、最後の「連結」ノードで両者の完了を待ち合わせる
  (Dify は複数入力ノードを全入力到達で発火する=既存コードノードの合流と同じ挙動)
- 連結ノードが `# 統合ダイジェスト(date)` + `## 📰 国内ニュース…` + `## 🌐 海外ニュース…`
  を縦に結合する(notes 2026-06-15 の共通体裁。各ダイジェストは `##` セクション始まりで、
  ここで初めて `#` タイトルを1行足す)
- lint をクリーンに保つため、各コードノードの outputs 宣言は下流で参照される変数だけに絞る
  (jp_code: formatted_articles/date_label、en_code: formatted_articles/url_map)。
  コード本体は元のまま全変数を返すが、未参照の宣言を残すと未使用WARNになるため

不要にするノード(両DSLの IF/ELSE・エラーテンプレート・各 end・en側の start)は取り込まない。

使い方:
    python scripts/gen_combined_dsl.py
    → workflows/combined-news-digest.yml を生成
"""

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
JP = ROOT / "workflows" / "jp-news-digest.yml"
EN = ROOT / "workflows" / "en-news-digest.yml"
DST = ROOT / "workflows" / "combined-news-digest.yml"

# --- jp 側ノードID(原ID。jp DSL をそのまま使う) ---
JP_START = "1781187165020"
JP_CODE = "1781187326232"
JP_HTTP = ["1781189175977", "17811893361310", "17811893942900", "17811893972820", "17811893996220"]

# --- en 側ノードID(gen_en_dsl が全jp由来IDに '9' を前置している) ---
EN_CODE = "9" + JP_CODE
EN_HTTP = ["9" + h for h in JP_HTTP] + ["999178118900001", "999178118900002", "999178118900003"]
EN_TRANSLATE = "999178118900010"
EN_RESTORE = "999178118900011"

# --- このワークフローで新設するノード ---
COMBINE = "8000000000001"
END = "8000000000002"

EN_YOFF = 1000.0  # en 系ノードを下方向にずらし、jp 系と画面上で重ならないようにする

# 連結ノード: jp(整形済み)と en(翻訳+URL復元済み)を縦結合し、先頭にタイトルを付ける。
# jp_md は `## 📰 国内ニュース` 始まり、en_md は `## 🌐 海外ニュース` 始まりなのでそのまま並べる。
COMBINE_CODE = '''def main(jp_md, en_md, date_label):
    title = "# 統合ダイジェスト(" + (date_label or "") + ")"
    parts = [title, "", (jp_md or "").strip(), "", (en_md or "").strip()]
    digest = "\\n".join(parts).rstrip() + "\\n"
    return {"digest": digest}'''


def edge(src: str, src_type: str, tgt: str, tgt_type: str) -> dict:
    return {
        "data": {"isInIteration": False, "isInLoop": False, "sourceType": src_type, "targetType": tgt_type},
        "id": f"{src}-source-{tgt}-target",
        "source": src,
        "sourceHandle": "source",
        "target": tgt,
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 0,
    }


def build_combine_node() -> dict:
    return {
        "data": {
            "code": COMBINE_CODE,
            "code_language": "python3",
            "outputs": {"digest": {"children": None, "type": "string"}},
            "selected": False,
            "title": "連結",
            "type": "code",
            "variables": [
                {"value_selector": [JP_CODE, "formatted_articles"], "value_type": "string", "variable": "jp_md"},
                {"value_selector": [EN_RESTORE, "digest"], "value_type": "string", "variable": "en_md"},
                {"value_selector": [JP_CODE, "date_label"], "value_type": "string", "variable": "date_label"},
            ],
        },
        "height": 52,
        "id": COMBINE,
        "position": {"x": 2274.0, "y": 700.0},
        "positionAbsolute": {"x": 2274.0, "y": 700.0},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 242,
    }


def build_end_node() -> dict:
    return {
        "data": {
            "outputs": [
                {"value_selector": [COMBINE, "digest"], "value_type": "string", "variable": "digest"},
                {"value_selector": [JP_CODE, "date_label"], "value_type": "string", "variable": "date_label"},
            ],
            "selected": False,
            "title": "出力",
            "type": "end",
        },
        "height": 115,
        "id": END,
        "position": {"x": 2620.0, "y": 700.0},
        "positionAbsolute": {"x": 2620.0, "y": 700.0},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 242,
    }


def main() -> None:
    jp_doc = yaml.safe_load(JP.read_text(encoding="utf-8"))
    en_doc = yaml.safe_load(EN.read_text(encoding="utf-8"))
    jp_nodes = {n["id"]: n for n in jp_doc["workflow"]["graph"]["nodes"]}
    en_nodes = {n["id"]: n for n in en_doc["workflow"]["graph"]["nodes"]}

    def shifted(src_nodes: dict, node_id: str, yoff: float = 0.0) -> dict:
        """ノードを複製し、必要なら y 座標をずらして返す"""
        node = copy.deepcopy(src_nodes[node_id])
        if yoff:
            for key in ("position", "positionAbsolute"):
                if isinstance(node.get(key), dict):
                    node[key] = dict(node[key])
                    node[key]["y"] = node[key].get("y", 0.0) + yoff
        return node

    nodes: list = []

    # --- jp: start(統合グラフ唯一の開始) + HTTP×5 + code ---
    nodes.append(shifted(jp_nodes, JP_START))
    for h in JP_HTTP:
        nodes.append(shifted(jp_nodes, h))
    jp_code = shifted(jp_nodes, JP_CODE)
    # 下流で使うのは整形本文と日付ラベルのみ(article_count は未使用→宣言から外す)
    jp_code["data"]["outputs"] = {
        "formatted_articles": {"children": None, "type": "string"},
        "date_label": {"children": None, "type": "string"},
    }
    nodes.append(jp_code)

    # --- en: HTTP×8 + code + 翻訳 + URL復元(jp と重ならないよう下にずらす) ---
    for h in EN_HTTP:
        nodes.append(shifted(en_nodes, h, EN_YOFF))
    en_code = shifted(en_nodes, EN_CODE, EN_YOFF)
    # 下流で使うのは整形本文(翻訳入力)と url_map(URL復元入力)のみ
    en_code["data"]["outputs"] = {
        "formatted_articles": {"children": None, "type": "string"},
        "url_map": {"children": None, "type": "string"},
    }
    nodes.append(en_code)
    nodes.append(shifted(en_nodes, EN_TRANSLATE, EN_YOFF))
    nodes.append(shifted(en_nodes, EN_RESTORE, EN_YOFF))

    # --- 連結 + 出力 ---
    nodes.append(build_combine_node())
    nodes.append(build_end_node())

    # --- エッジ ---
    edges: list = []
    for h in JP_HTTP:
        edges.append(edge(JP_START, "start", h, "http-request"))
        edges.append(edge(h, "http-request", JP_CODE, "code"))
    for h in EN_HTTP:
        edges.append(edge(JP_START, "start", h, "http-request"))
        edges.append(edge(h, "http-request", EN_CODE, "code"))
    edges.append(edge(EN_CODE, "code", EN_TRANSLATE, "llm"))
    edges.append(edge(EN_TRANSLATE, "llm", EN_RESTORE, "code"))
    edges.append(edge(JP_CODE, "code", COMBINE, "code"))
    edges.append(edge(EN_RESTORE, "code", COMBINE, "code"))
    edges.append(edge(COMBINE, "code", END, "end"))

    # --- ドキュメント組み立て(jp を骨格に流用。dependencies は gemini で共通) ---
    doc = copy.deepcopy(jp_doc)
    doc["app"]["name"] = "combined-news-digest"
    doc["app"]["description"] = (
        "国内5ソース+海外8ソースのニュース見出しを収集・整形し、海外分のみ日本語翻訳して"
        "1本の統合ダイジェストにまとめる(LLMは翻訳1回のみ)"
    )
    doc["app"]["icon"] = "🗞️"
    doc["workflow"]["graph"]["nodes"] = nodes
    doc["workflow"]["graph"]["edges"] = edges

    DST.write_text(
        yaml.dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120),
        encoding="utf-8",
    )
    print(f"generated: {DST} (nodes={len(nodes)}, edges={len(edges)})")


if __name__ == "__main__":
    main()
