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
| `combined-news-digest.yml` | `gen_combined_dsl.py` | 国内5ソース+海外8ソースを1グラフで収集し、`# 統合ダイジェスト` 1本にまとめる(翻訳LLM1回+見にくい3ソースの本文要約LLM1回) |
| `security-digest.yml` | `gen_security_dsl.py` | 脆弱性フィードから要注目CVEを機械抽出(hot_json) |
| `irodori-script-prep.yml` | `gen_irodori_dsl.py` | irodori_test `/auto` の「題材 → script_processed.yaml + glossary.json」までを再現(mp3合成は範囲外) |

### irodori-script-prep の使い方

- 入力 `material` に題材テキストを直貼りして実行する
- 出力: `script_processed_yaml`(そのまま `projects/<dir>/script_processed.yaml` に保存)/ `glossary_json` / `final_warnings`(最終 check 結果)
- 後段: ローカルの irodori_test で `modal run src/synthesize.py --script-yaml <保存先> --output output.mp3`(glossary.json も同階層に置けば SRT に表記復元が乗る)
- **LLM は既定で Claude Sonnet**(品質重視)。インポート後に各 LLM ノード(台本生成 / 修正#1〜3 / glossary生成)でモデルを選び直すこと。Anthropic プラグイン未導入なら `gen_irodori_dsl.py` の `LLM_PROVIDER`/`LLM_MODEL` を gemini に変えて再生成する
- 検証: `python scripts/test_irodori_dsl.py`(前処理が原実装と一致するか等)

### combined-news-digest の使い方

- 入力なしで実行すると、国内5ソース+海外8ソースを収集して1本の統合ダイジェストを返す
- 任意入力 `with_urls`: 既定 `no`(見出し+日時のみ)。`yes` を渡すと国内/海外の各見出しに出典URLを併記する(後から事実確認したい時用)。`gen_en/gen_security` 系の常時URL併記とは挙動が異なる点に注意
- フロー: `start → {国内/海外それぞれの先頭ソース}`、各言語の先頭ソースが残りの取得ノードをファンアウトし、整形へ合流。`国内→整形` と `海外→整形→{翻訳 / 要約}→URL復元` を最後の `連結` で待ち合わせて `出力`
- **見にくい3ソース(Simon Willison・Nautilus・Aeon)に本文要約を付与**: 海外整形がフィード埋め込み本文を `summary_src` に束ね、専用の「要約」LLM が `[[ID]] 要約文` を返し、URL復元ノードが該当見出しの下に `↳` 行で差し込む(翻訳ノードは「要約・論評禁止」なので別ノードに分離)。**コードノードはネット禁止のため記事ページ補完は不可**で、要約素材は「フィード本文の範囲」に限る(別リポジトリ `news_digest` の Python 版は記事ページ補完あり。notes 2026-06-18 参照)
- **媒体内の重複見出しを除去**: Google News RSS は同一記事を複数回返すことがあるため、各 `parse_rss`/`parse_hn` が正規化タイトル(空白畳み込み)で媒体内の重複を落とす(媒体をまたぐ同一トピックは別物として残す)
- **1ノードからの並列分岐は10本まで**(Dify の `MAX_PARALLEL_LIMIT`、Cloud では変更不可)。国内5+海外8=13本を `start` から一度に出すと上限超過で実行が止まるため、言語ごとに「先頭ソースが残りをファンアウトするツリー」にしてどのノードの分岐も ≤10 に抑えている(start=2 / 日経=5 / AP=8 / 海外整形=2)
- 出力 `digest`: `# 統合ダイジェスト(YYYY年M月D日)` + `## 📰 国内ニュース…` + `## 🌐 海外ニュース…`(notes 2026-06-15 の共通体裁。`#` タイトルはここで足す)。`date_label` も出力する
- 翻訳・要約 LLM の既定は `gemini-3.1-flash-lite`(`dependencies` は gemini プラグイン)。変えるなら各 LLM ノードで選び直す
- 生成: `python scripts/gen_combined_dsl.py`(手編集ではなくジェネレータを直して再生成する)
- lint: `python scripts/lint_dsl.py workflows/combined-news-digest.yml`(ERROR/WARN なし)
