#!/usr/bin/env python3
"""DSL内のコードノードをローカル実行して検証する。

コードノードの入力変数がHTTPノードのbodyに繋がっている前提で、
そのURLを実際に取得して main() に流し込み、出力を表示する。
Difyにインポートする前の動作確認用。

使い方:
    python scripts/test_dsl_code.py workflows/en-news-digest.yml
"""

import sys
import urllib.request
from pathlib import Path

import yaml

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    dsl = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in dsl["workflow"]["graph"]["nodes"]}
    code_node = next(n for n in nodes.values() if n["data"].get("type") == "code")

    kwargs = {}
    for v in code_node["data"]["variables"]:
        src = nodes.get(v["value_selector"][0], {})
        url = src.get("data", {}).get("url")
        title = src.get("data", {}).get("title", "?")
        if not url:
            print(f"  skip {v['variable']}: HTTPノード由来でない")
            kwargs[v["variable"]] = ""
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as res:
                kwargs[v["variable"]] = res.read().decode("utf-8", errors="replace")
            print(f"  fetch OK  {title}: {len(kwargs[v['variable']])} bytes")
        except Exception as e:
            kwargs[v["variable"]] = ""
            print(f"  fetch NG  {title}: {e}")

    ns: dict = {}
    exec(code_node["data"]["code"], ns)  # noqa: S102 - 自作DSLの検証用
    result = ns["main"](**kwargs)

    print()
    for key, value in result.items():
        if key == "formatted_articles":
            continue
        print(f"{key}: {value}")
    print()
    print(result.get("formatted_articles", "")[:3000])


if __name__ == "__main__":
    main()
