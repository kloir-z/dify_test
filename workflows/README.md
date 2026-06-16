# workflows/

Dify のワークフロー DSL(YAML)をバージョン管理する置き場。

## 運用ルール

- Dify の画面右上「エクスポート DSL」でダウンロードした `.yml` をここに置いてコミットする
- ファイル名はアプリ名がわかるように(例: `summary-bot.yml`)
- 取り込みは Dify の「DSL ファイルをインポート」から行う
- DSL にはモデル設定やプロンプトが丸ごと入るが、**API キー等の秘密情報は含まれない**ので安心してコミットしてよい
- 一部の DSL は `scripts/gen_*_dsl.py` で生成している(手編集ではなくジェネレータを直して再生成する)

## 一覧

| ファイル | 生成元 | 概要 |
|---|---|---|
| `jp-news-digest.yml` | (手組み) | 国内ニュース見出しを5ソースから収集・整形(純コード、LLMなし) |
| `en-news-digest.yml` | `gen_en_dsl.py` | 海外8ソースを整形+翻訳LLM1回 |
| `security-digest.yml` | `gen_security_dsl.py` | 脆弱性フィードから要注目CVEを機械抽出(hot_json) |
| `irodori-script-prep.yml` | `gen_irodori_dsl.py` | irodori_test `/auto` の「題材 → script_processed.yaml + glossary.json」までを再現(mp3合成は範囲外) |

### irodori-script-prep の使い方

- 入力 `material` に題材テキストを直貼りして実行する
- 出力: `script_processed_yaml`(そのまま `projects/<dir>/script_processed.yaml` に保存)/ `glossary_json` / `final_warnings`(最終 check 結果)
- 後段: ローカルの irodori_test で `modal run src/synthesize.py --script-yaml <保存先> --output output.mp3`(glossary.json も同階層に置けば SRT に表記復元が乗る)
- **LLM は既定で Claude Sonnet**(品質重視)。インポート後に各 LLM ノード(台本生成 / 修正#1〜3 / glossary生成)でモデルを選び直すこと。Anthropic プラグイン未導入なら `gen_irodori_dsl.py` の `LLM_PROVIDER`/`LLM_MODEL` を gemini に変えて再生成する
- 検証: `python scripts/test_irodori_dsl.py`(前処理が原実装と一致するか等)
