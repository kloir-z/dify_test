#!/usr/bin/env python3
"""Dify API を叩く最小 CLI(標準ライブラリのみ・依存なし)。

使い方:
    python scripts/dify.py info
    python scripts/dify.py chat "こんにちは"
    python scripts/dify.py chat "続きを教えて" --conversation-id <id>
    python scripts/dify.py workflow --inputs '{"query": "テスト"}'

接続先と API キーは .env(または環境変数)の DIFY_BASE_URL / DIFY_API_KEY を使う。
API キーはアプリ単位なので、chat はチャットアプリ、workflow はワークフローアプリの
キーを設定すること。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    """リポジトリ直下の .env を環境変数に読み込む(既存の環境変数を優先)。"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def request(method: str, path: str, body: dict | None = None) -> dict:
    base_url = os.environ.get("DIFY_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("DIFY_API_KEY", "")
    if not base_url or not api_key:
        sys.exit("DIFY_BASE_URL / DIFY_API_KEY が未設定です。.env.example を参考に .env を作成してください。")

    req = urllib.request.Request(
        f"{base_url}{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Python-urllib のデフォルト UA は Dify Cloud 前段の Cloudflare に弾かれる (error 1010)
            "User-Agent": "dify-cli/0.1",
        },
        data=json.dumps(body).encode("utf-8") if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        sys.exit(f"HTTP {e.code} {e.reason}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"接続エラー: {e.reason}(DIFY_BASE_URL を確認してください)")


def cmd_info(_: argparse.Namespace) -> None:
    print(json.dumps(request("GET", "/info"), ensure_ascii=False, indent=2))
    print(json.dumps(request("GET", "/parameters"), ensure_ascii=False, indent=2))


def cmd_chat(args: argparse.Namespace) -> None:
    body = {
        "query": args.message,
        "inputs": json.loads(args.inputs),
        "user": args.user,
        "response_mode": "blocking",
        "conversation_id": args.conversation_id or "",
    }
    res = request("POST", "/chat-messages", body)
    print(res.get("answer", ""))
    print(f"\n[conversation_id: {res.get('conversation_id', '')}]", file=sys.stderr)


def cmd_workflow(args: argparse.Namespace) -> None:
    body = {
        "inputs": json.loads(args.inputs),
        "user": args.user,
        "response_mode": "blocking",
    }
    res = request("POST", "/workflows/run", body)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Dify API 最小クライアント")
    parser.add_argument("--user", default="dify-cli", help="Dify に渡すユーザー識別子")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", help="アプリ情報とパラメータを表示")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("chat", help="チャットアプリにメッセージを送る")
    p.add_argument("message")
    p.add_argument("--inputs", default="{}", help="アプリの入力変数(JSON)")
    p.add_argument("--conversation-id", default="", help="会話を継続する場合に指定")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("workflow", help="ワークフローを実行する")
    p.add_argument("--inputs", default="{}", help="ワークフローの入力変数(JSON)")
    p.set_defaults(func=cmd_workflow)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
