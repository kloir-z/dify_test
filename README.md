# dify_test

[Dify](https://dify.ai/) の学習用リポジトリ。Claude Code から Dify アプリを操作したり、
ワークフロー DSL をバージョン管理したりする練習場。

## 構成

```
.env.example         接続設定のテンプレート(コピーして .env を作る)
scripts/dify.py      Dify API を叩く最小 CLI(Python 標準ライブラリのみ)
scripts/lint_dsl.py  DSL リンター(参照切れ・未使用変数・常に真の条件などを検出。要 pyyaml)
workflows/           ワークフロー DSL(YAML)の置き場
docs/notes.md        学習メモ
```

DSL をエクスポートしたら、コミット前にリンターを通す:

```powershell
python scripts/lint_dsl.py workflows/jp-news-digest.yml
```

`workflows/*.yml` のコミット時には pre-commit フックが自動でリンターを掛け、ERROR があれば弾く。
クローン直後は一度だけ有効化が必要:

```powershell
git config core.hooksPath .githooks
```

## セットアップ

1. Dify でアプリを作り、「APIアクセス」画面で API キーを発行する
2. `.env.example` をコピーして `.env` を作り、`DIFY_BASE_URL` と `DIFY_API_KEY` を設定する
3. 動作確認:

```powershell
python scripts/dify.py info
python scripts/dify.py chat "こんにちは"
python scripts/dify.py workflow --inputs '{\"query\": \"テスト\"}'
```

日本語が文字化けする場合はコンソールを UTF-8 にする:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

注意: チャットフロー型アプリは Dify 画面右上の「公開する」を押すまで API から実行できない
(`app_unavailable` エラーになる)。

## Dify 本体をどこで動かすか

| 選択肢 | 判定 | メモ |
|---|---|---|
| Dify Cloud | ◎ 当面はこれ | Docker 不要ですぐ使える。`DIFY_BASE_URL=https://api.dify.ai/v1` |
| Windows PC にセルフホスト | ○ 本格的に触るなら | Docker Desktop のインストールが必要 |
| Raspberry Pi 4(手元の検証機) | ✕ 見送り | 4GB モデルで既存サービス稼働中、空きメモリ不足。Dify はコンテナ9個で4GB以上必要なため非現実的 |
