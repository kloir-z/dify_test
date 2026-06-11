# Dify 学習メモ

気づいたこと・ハマったことを時系列で追記していく。

## 2026-06-11 リポジトリ立ち上げ

- Dify の API キーはワークスペース単位ではなく**アプリ単位**で発行される
- アプリ API(`/v1`)でできるのはチャット送信・ワークフロー実行など実行系のみ。
  DSL のエクスポート/インポートは管理画面(コンソール)からの手作業
- Raspberry Pi 4 (4GB) はメモリ不足で Dify セルフホストには不向きと判断(詳細は CLAUDE.md)

## 2026-06-11 Dify Cloud 疎通確認

- Dify Cloud (api.dify.ai) は Cloudflare 配下。Python-urllib のデフォルト User-Agent は
  error 1010 (403) で弾かれる → 任意の UA を設定すれば通る
- チャットフロー型アプリは「公開する」を押すまで API が `app_unavailable` (400) を返す
- アプリ「test」(advanced-chat) で chat コマンドの疎通確認済み
