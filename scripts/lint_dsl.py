#!/usr/bin/env python3
"""Dify ワークフローDSL(エクスポートYAML)の簡易リンター。

Dify本体のチェックリストが拾わない問題を検出する:
- 存在しないノード/出力変数への参照(参照切れ)
- どこからも参照されていない入力・出力変数(未使用)
- 開始ノードから到達できないノード、エッジの宙吊り
- 常に真になる数値条件(件数 ≥ 0 など)
- リトライ無効のLLM/HTTPノード(情報表示)

使い方:
    python scripts/lint_dsl.py workflows/jp-news-digest.yml
"""

import re
import sys
from pathlib import Path

import yaml

VAR_REF = re.compile(r"\{\{#([0-9a-zA-Z_]+)\.([a-zA-Z0-9_\.]+)#\}\}")

# ノードタイプごとの出力変数(参照可否の判定に使う)
STATIC_OUTPUTS = {
    "llm": {"text"},
    "http-request": {"body", "status_code", "headers", "files"},
    "template-transform": {"output"},
    "if-else": set(),
    "end": set(),
    "answer": set(),
}

# 参照側にだけ現れる特殊な名前空間(sys.query 等)は許容する
SPECIAL_NAMESPACES = {"sys", "env", "conversation"}


def node_outputs(node: dict) -> set:
    ntype = node["data"].get("type", "")
    if ntype == "start":
        return {v["variable"] for v in node["data"].get("variables", [])}
    if ntype == "code":
        return set((node["data"].get("outputs") or {}).keys())
    return STATIC_OUTPUTS.get(ntype, set())


def walk_refs(obj, refs: list, path: str = "") -> None:
    """ノードdataを再帰的に歩き、value_selector と {{#id.var#}} 参照を集める"""
    if isinstance(obj, dict):
        sel = obj.get("value_selector") or obj.get("variable_selector")
        if isinstance(sel, list) and len(sel) >= 2 and all(isinstance(s, str) for s in sel):
            refs.append((sel[0], sel[1], path))
        for k, v in obj.items():
            walk_refs(v, refs, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk_refs(v, refs, f"{path}[{i}]")
    elif isinstance(obj, str):
        for m in VAR_REF.finditer(obj):
            refs.append((m.group(1), m.group(2).split(".")[0], path))


def main() -> None:
    # Windows コンソール (cp932) で ≥ 等が UnicodeEncodeError にならないように
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    dsl_path = Path(sys.argv[1])
    doc = yaml.safe_load(dsl_path.read_text(encoding="utf-8"))
    graph = doc["workflow"]["graph"]
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = graph["edges"]

    errors: list[str] = []
    warns: list[str] = []
    infos: list[str] = []

    def label(node_id: str) -> str:
        n = nodes.get(node_id)
        return f"[{n['data'].get('title', '?')}({node_id})]" if n else f"[不明({node_id})]"

    # --- 1. 参照の妥当性 / 参照マップの構築 ---
    referenced: set[tuple[str, str]] = set()
    for node in nodes.values():
        refs: list = []
        walk_refs(node["data"], refs)
        for target_id, var, path in refs:
            if target_id in SPECIAL_NAMESPACES:
                continue
            if target_id not in nodes:
                errors.append(
                    f"{label(node['id'])} が存在しないノード '{target_id}' を参照 ({path})"
                )
                continue
            outputs = node_outputs(nodes[target_id])
            if var not in outputs:
                errors.append(
                    f"{label(node['id'])} が {label(target_id)} に無い変数 '{var}' を参照"
                    f" (実際の出力: {sorted(outputs) or 'なし'})"
                )
            referenced.add((target_id, var))

    # --- 2. 未使用変数 ---
    for node in nodes.values():
        ntype = node["data"].get("type")
        if ntype in ("start", "code"):
            for var in node_outputs(node):
                if (node["id"], var) not in referenced:
                    warns.append(f"{label(node['id'])} の変数 '{var}' はどこからも参照されていない")

    # --- 3. エッジの妥当性と到達可能性 ---
    adj: dict[str, list[str]] = {}
    for e in edges:
        if e["source"] not in nodes:
            errors.append(f"エッジ {e['id']} の source が存在しない: {e['source']}")
            continue
        if e["target"] not in nodes:
            errors.append(f"エッジ {e['id']} の target が存在しない: {e['target']}")
            continue
        adj.setdefault(e["source"], []).append(e["target"])

    start_ids = [n["id"] for n in nodes.values() if n["data"].get("type") == "start"]
    seen: set[str] = set()
    stack = list(start_ids)
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, []))
    for node_id, node in nodes.items():
        if node_id not in seen:
            warns.append(f"{label(node_id)} は開始ノードから到達できない")
        if node["data"].get("type") not in ("end", "answer") and node_id not in adj:
            warns.append(f"{label(node_id)} に出力エッジがない(行き止まり)")

    # --- 4. 怪しい条件式 ---
    for node in nodes.values():
        if node["data"].get("type") != "if-else":
            continue
        for case in node["data"].get("cases", []):
            for cond in case.get("conditions", []):
                op = cond.get("comparison_operator", "")
                val = str(cond.get("value", ""))
                if op in ("≥", ">=") and val == "0":
                    warns.append(
                        f"{label(node['id'])} の条件 '≥ 0' は件数に対して常に真"
                        f"(偽側の経路が死んでいる可能性。'>' の間違いでは?)"
                    )

    # --- 5. リトライ設定(情報) ---
    for node in nodes.values():
        ntype = node["data"].get("type")
        if ntype not in ("llm", "http-request", "code", "tool"):
            continue
        rc = node["data"].get("retry_config")
        if not rc or not rc.get("retry_enabled"):
            infos.append(f"{label(node['id'])} ({ntype}) はリトライ無効")

    # --- レポート ---
    print(f"=== {dsl_path.name}: ノード{len(nodes)} / エッジ{len(edges)} ===")
    for msg in errors:
        print(f"  ERROR: {msg}")
    for msg in warns:
        print(f"  WARN : {msg}")
    for msg in infos:
        print(f"  INFO : {msg}")
    if not errors and not warns:
        print("  問題なし(INFOのみ)" if infos else "  問題なし")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
