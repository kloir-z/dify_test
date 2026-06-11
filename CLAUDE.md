# dify_test — プロジェクトメモ

Dify の学習用リポジトリ。目的は (1) Claude Code から Dify アプリを操作すること、
(2) ワークフロー DSL の バージョン管理。

## Dify への接続

- 接続設定は `.env`(`DIFY_BASE_URL` / `DIFY_API_KEY`)。`scripts/dify.py` が読み込む
- API キーは**アプリ単位**。chat 用とワークフロー用でキーが異なる点に注意
- Dify 本体は当面 Dify Cloud を想定(セルフホストに移行しても `.env` の変更だけで済む)

## Raspberry Pi 4 について(2026-06-11 調査)

- 手元の Raspberry Pi 4 は **Dify のセルフホスト先としては不適**と判断(4GB モデルで
  常駐サービスが多く空きメモリ不足)。接続情報等は `CLAUDE.local.md`(git 管理外)を参照

## 作業時の注意

- `.env` は git 管理外。秘密情報をコミットしない
- DSL をエクスポートしたら `workflows/` に置いてコミットする
- 学習の気づきは `docs/notes.md` に追記していく
