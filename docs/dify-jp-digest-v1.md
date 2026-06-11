# Dify構築手順: 日本ニュースダイジェスト v1

`/daily-news-digest-jp` のLLM処理部分(Phase 2.5〜3.6相当)をDifyワークフローとして再現する。
収集(collector)と保存はローカルに残し、Difyは「記事JSON in → 完成ダイジェストmd out」を担当する。

## 全体図(9ノード)

```
開始 ──> コード(記事整形) ──> IF/ELSE ──┬─(記事あり)──> LLM①トピック分析 ──> LLM②ダイジェスト生成
                                        │                                          │
                                        └─(0件)──> テンプレート(エラー文) ──> 終了B   v
                                                              終了A <── LLM④訂正適用 <── LLM③接地監査
```

LLM4段の役割分担: ①が「同一トピック検出+過去ダイジェストとの続報判定」、②が「フレーミング比較ダイジェスト生成」、
③が「見出しから内容を捏造していないかの監査」、④が「監査結果の反映と最終化」。
元コマンドで「スキップ禁止」と脅していた品質ゲート(③④)が、ここでは構造的に必ず通る。

---

## 事前準備

1. スタジオ → 「アプリを作成する」→「ワークフロー」を選択。名前: `jp-news-digest`
2. モデルは「test」アプリで使ったものと同じプロバイダーでOK(各LLMノードで個別に選択する)

## ノード1: 開始

「開始」ノードを選択し、入力フィールドを3つ追加する。

| 変数名 | 種類 | 必須 | 最大長 | 説明 |
|---|---|---|---|---|
| `articles_json` | 段落 | 必須 | **設定できる最大値**(実データは約34,000字。上限に当たったら報告を) | collector --mode jp の出力JSON |
| `previous_digests` | 段落 | 任意 | 最大値 | 直近2〜3本のダイジェストmd連結。初回は空でよい |
| `date_label` | 短文 | 必須 | 48 | 例: `2026年6月12日` |

## ノード2: コード(記事整形)

開始ノードの右の「+」→「コード」。入力変数に `articles_json`(開始/articles_json)を割り当て、言語はPython3。

```python
import json

def main(articles_json: str) -> dict:
    data = json.loads(articles_json)
    labels = {
        "nikkei": "日経新聞", "asahi": "朝日新聞", "sankei": "産経新聞",
        "reuters_jp": "Reuters JP", "toyokeizai": "東洋経済", "nhk": "NHK",
    }
    suffixes = (" - 日本経済新聞", " - 朝日新聞デジタル", " - 朝日新聞",
                " - 産経ニュース", " - 産経新聞", " - ロイター (Reuters Japan)",
                " - ロイター", " - 東洋経済オンライン")
    lines = []
    count = 0
    for key, src in data.get("sources", {}).items():
        lines.append(f"## {labels.get(key, key)}")
        if src.get("status") != "ok":
            lines.append(f"(取得失敗: {src.get('error', '不明')})")
            continue
        for it in src.get("items", []):
            title = (it.get("title") or "").strip()
            for suf in suffixes:
                if title.endswith(suf):
                    title = title[: -len(suf)]
                    break
            lines.append(f"- {title} ({it.get('pub_date', '')})")
            lines.append(f"  {it.get('url', '')}")
            count += 1
        lines.append("")
    return {"formatted_articles": "\n".join(lines), "article_count": count}
```

出力変数を2つ宣言する: `formatted_articles`(String)、`article_count`(Number)。

NHKは v1 では含まれない(認証付きローカルChromeが必要なため)。将来、呼び出し側が `sources` に
`nhk` キーを足したJSONを渡せば、このコードはそのまま対応する。

## ノード3: IF/ELSE(条件分岐)

コードノードの後ろに「IF/ELSE」を追加。条件: `コード/article_count` `>` `0`。

- IF(真) → ノード4へ
- ELSE → ノード8(テンプレート)へ

## ノード4: LLM① トピック分析

「LLM」ノードを追加。モデルは高品質なもの(test アプリと同じでOK)。
**「エラー処理」(失敗時のリトライ)を開き、リトライを有効化(最大3回)しておく** — 以降のLLMノード全部で同様に。

SYSTEMプロンプト:

```
あなたは日本のニュースを分析するベテラン編集者です。複数媒体の本日の見出しリストを受け取り、
後工程(ダイジェスト執筆)のための「トピック分析メモ」を作成します。

# タスク1: 同一トピック検出
全媒体の見出しを横断し、同じニュースを報じている記事をグループ化する。
- キーワードの一致だけでグルーピングしない。同じ地名・人名・組織名でも、続報・関連事案・
  派生事件で別事案が混在しがち。日付・関係者の属性・経緯まで一致するかを見出しから確認し、
  確認できない場合は「別事案の可能性あり」と注記する
- まず「別事案ではないか」と疑ってからグループ化すること

# タスク2: 過去ダイジェストとの照合(続報疲れの防止)
過去ダイジェストが与えられた場合、各トピックを次の3つに分類する:
- 「新規」: 過去ダイジェストに出ていない
- 「続報」: 既報だが新展開がある。何が新展開かを1行で書く
- 「既報・新展開なし」: 同内容の再掲。ダイジェストでは扱いを最小化すべきもの
過去ダイジェストが空の場合は全トピックを「新規」とする。

# 出力フォーマット
### トピック: [トピック名]
- 分類: 新規 / 続報(新展開: ...) / 既報・新展開なし
- 該当見出し: [媒体名] 見出し (各行1件、URLも併記)
- 注記: (別事案の可能性、グルーピングの確信度など。なければ省略)

最後に「## 単独記事」セクションを置き、どのグループにも属さないが重要そうな見出しを
媒体ごとに5件程度まで列挙する。
```

USERプロンプト(`{{...}}` は変数挿入ボタンで該当変数に置き換える):

```
# 本日の見出しリスト
{{コード/formatted_articles}}

# 過去ダイジェスト(直近2〜3本、空の場合あり)
{{開始/previous_digests}}
```

## ノード5: LLM② ダイジェスト生成

「LLM」ノードを追加(リトライ有効化)。

SYSTEMプロンプト:

```
あなたは日本のニュースダイジェストの執筆者です。本日の見出しリストとトピック分析メモから、
フレーミング比較付きダイジェスト(Markdown)を執筆します。

媒体の役割: 日経(経済・ビジネス視点)、朝日(リベラル寄り)、産経(保守寄り)、
Reuters JP(国際視点)、東洋経済(経済誌・深堀り)。

# 出力フォーマット
# 日本ニュースダイジェスト({date}) ← 与えられた日付を使う

## 日経新聞 — 経済・ビジネス
[主要記事を列挙し、各記事に1行の解説コメント]
[フレーミング注] 経済影響・企業視点に偏った報じ方があれば指摘

## 朝日新聞 — 総合(リベラル寄り)
(同様。他媒体との見出し比較・論調の差を指摘)

## 産経新聞 — 総合(保守寄り)
(同様)

## Reuters JP — 国際視点
(同様。国内紙が国内視点で報じる事象を国際文脈で捉え直す視点)

## 東洋経済 — 深堀り経済誌
(同様)

## 横断的なトレンド
[複数媒体で共通するテーマを3〜5個。「今回新しく動いたもの」を優先]

## メディアフレーミング比較
[同一トピックを報じた媒体の見出しを並べ、言葉選び・論調・省略された文脈の差を分析。
 最重要セクション。2〜4トピック]

# 品質保証ルール(厳守)
1. 層を分ける: 「見出し引用」(そのまま引用)と「フレーミング解釈」(憶測OK、ただし
   「〜と読める」「〜の可能性がある」等の解釈語尾で明示)を混ぜない。裏取りしていない
   事実(死亡者属性・発生日・経緯など)は断定で書かず「(未確認)」を付す
2. 入力は見出しと日時のみで、記事本文は取得していない。見出しから自明でない内容・主張・
   数値・結論を推測して書かない。特に「〜が抱える3つの構造問題」のような抽象見出しの
   中身を創作しない。踏み込めない記事は見出し引用+「(見出しのみ)」に留める
3. 強い断定(「最も〜」「明らかに〜」「二極化」等)は複数媒体の見出しで裏付く場合のみ
4. トピック分析メモの分類を反映する: 「既報・新展開なし」は冒頭・トレンドから外し、
   本編でも「(M月D日に既報、新展開なし)」の1行に留める。「続報」は差分だけを書く
5. 「別事案の可能性あり」と注記されたグループは、安易に1つの事案としてまとめない
6. 株価・件数・順位など変動する値は、見出しに明記されているもの以外書かない
```

USERプロンプト:

```
日付: {{開始/date_label}}

# トピック分析メモ
{{LLM①/text}}

# 本日の見出しリスト(全量)
{{コード/formatted_articles}}
```

## ノード6: LLM③ 接地監査

「LLM」ノードを追加(リトライ有効化)。生成時の意図は渡さず、ダイジェストだけを渡して先入観なく監査させる。

SYSTEMプロンプト:

```
あなたはニュースダイジェストの「接地監査役」です。各項目の解説が、与えられた情報
(見出しのみ。記事本文は誰も読んでいない)に接地しているかだけを機械的に判定します。
事実の正誤は問いません(別工程)。

各項目について判定:
- タイトルは内容自明か?(具体的な事案見出しは自明 / 「〜が抱える3つの構造問題」のような
  抽象・問題提起型・特集見出しは非自明)
- 解説は、タイトルだけからは導けない具体的な内容・主張・数値・トレンドを断定していないか?
- 解釈である旨の語尾(「〜と読める」等)や「(見出しのみ)」「(未確認)」の注記が
  必要なのに欠けていないか?

FLAG条件 = 「タイトル非自明」かつ「解説が具体内容・主張・トレンドを断定」の2点が揃う項目。

# 出力(簡潔に)
## FLAG項目
- [媒体/タイトル]: なぜ非接地か1行 / 提案(解釈語尾に修正 ／ 見出し引用のみに降格)
## 接地OK
- 件数のみ
```

USERプロンプト:

```
{{LLM②/text}}
```

## ノード7: LLM④ 訂正適用(最終化)

「LLM」ノードを追加(リトライ有効化)。

SYSTEMプロンプト:

```
あなたはダイジェストの最終編集者です。ダイジェスト原稿と接地監査結果を受け取り、
完成版Markdownを出力します。

# 手順
1. FLAG項目を監査の提案どおり修正する(原則: 見出し引用のみ+「(見出しのみ・本文未確認)」
   に降格、または解釈語尾への書き換え)
2. 降格・修正した項目について、冒頭の記述・「横断的なトレンド」・「メディアフレーミング比較」
   まで遡って整合を取る(該当項目を根拠にした断定が残っていないか確認)
3. 末尾に以下のセクションを追記する:
   ---
   ## 接地監査ログ(自動生成)
   - 監査項目: N件 / FLAG: M件 / 降格・修正: X件
   - [媒体/タイトル]: [対応を1行で]
4. FLAGが0件なら原稿をそのまま使い、監査ログに「FLAG 0件」と記す

# 出力
完成版ダイジェストのMarkdown本文だけを出力する。前置き・後書き・コードフェンスは不要。
```

USERプロンプト:

```
# ダイジェスト原稿
{{LLM②/text}}

# 接地監査結果
{{LLM③/text}}
```

## ノード8: テンプレート+終了B(0件エラー経路)

IF/ELSEのELSE側に「テンプレート」ノードを追加し、本文に:

```
エラー: 入力JSONから記事を1件も読み取れませんでした。collectorの出力を確認してください。
```

その後ろに「終了」ノードを追加し、出力変数 `digest` ← テンプレートの出力を割り当てる。

## ノード9: 終了A(正常経路)

LLM④の後ろに「終了」ノードを追加し、出力変数を割り当てる:

- `digest` ← `LLM④/text`
- `audit` ← `LLM③/text`(監査の生ログも返しておくとデバッグに便利)

---

## テスト実行(GUI)

1. 右上「実行」(デバッグとプレビュー)を押す
2. `articles_json` に `test-data/articles_jp_sample.json` の中身を全文貼り付け
3. `previous_digests` は空、`date_label` は `2026年6月12日`
4. 実行 → 各ノードの入出力をトレースで確認できる(どのノードで何が起きたか見えるのがDifyの売り)

## 公開とAPI実行

1. 右上「公開する」→「公開」
2. 左メニュー「APIアクセス」→ APIキーを発行(**チャットアプリ「test」とは別のキー**になる)
3. このリポジトリの `.env` の `DIFY_API_KEY` をこのキーに差し替え(または両方管理する仕組みに変える)
4. 実行:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$json = Get-Content test-data\articles_jp_sample.json -Raw -Encoding UTF8
@{articles_json=$json; previous_digests=''; date_label='2026年6月12日'} |
    ConvertTo-Json | Out-File test-data\inputs.json -Encoding utf8
python scripts/dify.py workflow --inputs-file test-data\inputs.json
```

(34KBのJSONはコマンドライン引数の長さ上限を超えるため `--inputs-file` で渡す)

## v1の既知の制限(次以降で対応)

- NHKなし(認証付きローカルChrome必須のため。呼び出し側でJSONに混ぜる設計で対応予定)
- 記事本文なし(見出しのみ)。本文取得+Webファクトチェック(Phase 3.5相当)はv2で検索ツールを足して対応
- 過去ダイジェストは呼び出し側が渡す(Difyワークフローは実行間で状態を持たない)

---

# v1.5: ニュース取得もDify内に移す

コードノードはサンドボックス実行でネットワーク禁止のため、取得は「HTTPリクエストノード×5(並列)」、
解析は「コードノード」に分担させる。collector(--mode jp)のDify内再現になる。

## 変更後の全体図

```
開始 ─┬─> HTTP(日経) ──┐
      ├─> HTTP(朝日) ──┤
      ├─> HTTP(産経) ──┼─> コード(RSS解析・整形) ─> IF/ELSE ─> (以降v1と同じLLM①〜④)
      ├─> HTTP(Reuters)┤
      └─> HTTP(東洋経済)┘
```

## 手順1: 開始ノードの変更

- `articles_json` フィールドを**削除**(もう外から渡さない)
- `previous_digests`(任意)と `date_label`(必須)はそのまま残す

## 手順2: HTTPリクエストノードを5つ追加

開始ノードから線を5本引き、それぞれ「HTTPリクエスト」ノードに繋ぐ(並列実行になる)。
各ノードの設定:

- メソッド: GET
- URL(ノード名も媒体名にしておくと見やすい):
  - 日経: `https://news.google.com/rss/search?q=site:nikkei.com&hl=ja&gl=JP&ceid=JP:ja`
  - 朝日: `https://news.google.com/rss/search?q=site:asahi.com&hl=ja&gl=JP&ceid=JP:ja`
  - 産経: `https://news.google.com/rss/search?q=site:sankei.com&hl=ja&gl=JP&ceid=JP:ja`
  - Reuters JP: `https://news.google.com/rss/search?q=site:jp.reuters.com&hl=ja&gl=JP&ceid=JP:ja`
  - 東洋経済: `https://news.google.com/rss/search?q=site:toyokeizai.net&hl=ja&gl=JP&ceid=JP:ja`
- ヘッダー: `User-Agent` = `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36`
- **エラー処理: 「デフォルト値」を選び、`body` のデフォルトを空文字に**。さらにリトライを有効化(3回)
  - こうすると1媒体が落ちても全体は止まらず、残り4媒体でダイジェストが出る
  - (元のcollectorの「ソース単位のエラー許容」と同じ挙動になる)

## 手順3: コードノードを置き換え

v1の「コード(記事整形)」ノードの入力変数を、5つのHTTPノードの `body` に変更する:

| 入力変数名 | 割り当て |
|---|---|
| `body_nikkei` | HTTP(日経)/body |
| `body_asahi` | HTTP(朝日)/body |
| `body_sankei` | HTTP(産経)/body |
| `body_reuters` | HTTP(Reuters JP)/body |
| `body_toyokeizai` | HTTP(東洋経済)/body |

コード全文を以下に差し替え(RSS解析は正規表現で行う。サンドボックスで確実に使える機能だけに寄せている):

```python
import re

ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&apos;": "'"}


def unescape(text: str) -> str:
    for k, v in ENTITIES.items():
        text = text.replace(k, v)
    return text


def parse_rss(xml_text: str, limit: int = 15) -> list:
    items = []
    for m in re.finditer(r"<item>([\s\S]*?)</item>", xml_text or ""):
        block = m.group(1)

        def field(tag: str) -> str:
            f = re.search(
                r"<" + tag + r">(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</" + tag + r">", block
            )
            return unescape(f.group(1).strip()) if f else ""

        title = field("title")
        if title:
            # Google News RSS の「見出し - 媒体名」サフィックスを除去
            if " - " in title:
                title = title.rsplit(" - ", 1)[0]
            items.append({"title": title, "url": field("link"), "pub_date": field("pubDate")})
        if len(items) >= limit:
            break
    return items


def main(body_nikkei: str, body_asahi: str, body_sankei: str,
         body_reuters: str, body_toyokeizai: str) -> dict:
    sources = [
        ("日経新聞", body_nikkei),
        ("朝日新聞", body_asahi),
        ("産経新聞", body_sankei),
        ("Reuters JP", body_reuters),
        ("東洋経済", body_toyokeizai),
    ]
    lines = []
    count = 0
    for name, body in sources:
        lines.append(f"## {name}")
        items = parse_rss(body)
        if not items:
            lines.append("(取得失敗または0件)")
        for it in items:
            lines.append(f"- {it['title']} ({it['pub_date']})")
            lines.append(f"  {it['url']}")
            count += 1
        lines.append("")
    return {"formatted_articles": "\n".join(lines), "article_count": count}
```

出力変数はv1と同じ: `formatted_articles`(String)、`article_count`(Number)。

## 手順4: テスト

「実行」→ 入力は `date_label`(と任意の `previous_digests`)だけになっているはず。
これでテスト時の34KB貼り付けが不要になり、API呼び出しも軽くなる:

```powershell
python scripts/dify.py workflow --inputs '{\"date_label\": \"2026年6月12日\", \"previous_digests\": \"\"}'
```

## 改良: 監査の誤検知対策(v1.5b)

v1の実行で、実在の見出しに含まれる数値(「米トマホーク49発発射」)を監査が「捏造」と
誤判定して降格させる事例が出た。原因は、LLM③がダイジェストしか見ておらず、
「実在の見出し由来」か「執筆者の創作」かを区別できないこと。

対策: LLM③のUSERプロンプトに見出しリストを参照として追加する。

```
# 監査対象ダイジェスト
{{LLM②/text}}

# 参照: 本日の見出しリスト(ここに含まれる情報は「接地あり」とみなす)
{{コード/formatted_articles}}
```

あわせてLLM③のSYSTEMプロンプトのFLAG条件を1行修正:
「解説は、タイトルだけからは導けない〜」→「解説は、タイトルおよび参照見出しリストの
どこからも導けない具体的な内容・主張・数値・トレンドを断定していないか?」

トレードオフ: 監査ノードの入力トークンが増える(見出しリスト約7,000字)。
誤検知(本物の事実の過剰降格)と引き換えなら払う価値がある。

## 改良: 日付の自動生成(v1.6)

`date_label` は取得に影響しないただのタイトル飾りなので、入力をやめてワークフロー内で
自動生成する。サンドボックスの時計はUTCのため+9時間でJSTにする。

手順:
1. 開始ノードから `date_label` フィールドを削除(入力は `previous_digests` だけになる)
2. コード(RSS解析・整形)ノードに出力変数 `date_label`(String)を追加し、コードを下記に差し替え
3. LLM②のUSERプロンプトの日付参照を `開始/date_label` → `コード/date_label` に変更

```python
import re
from datetime import datetime, timedelta, timezone

ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&apos;": "'"}


def unescape(text: str) -> str:
    for k, v in ENTITIES.items():
        text = text.replace(k, v)
    return text


def parse_rss(xml_text: str, limit: int = 15) -> list:
    items = []
    for m in re.finditer(r"<item>([\s\S]*?)</item>", xml_text or ""):
        block = m.group(1)

        def field(tag: str) -> str:
            f = re.search(
                r"<" + tag + r">(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</" + tag + r">", block
            )
            return unescape(f.group(1).strip()) if f else ""

        title = field("title")
        if title:
            if " - " in title:
                title = title.rsplit(" - ", 1)[0]
            items.append({"title": title, "url": field("link"), "pub_date": field("pubDate")})
        if len(items) >= limit:
            break
    return items


def main(body_nikkei: str, body_asahi: str, body_sankei: str,
         body_reuters: str, body_toyokeizai: str) -> dict:
    sources = [
        ("日経新聞", body_nikkei),
        ("朝日新聞", body_asahi),
        ("産経新聞", body_sankei),
        ("Reuters JP", body_reuters),
        ("東洋経済", body_toyokeizai),
    ]
    lines = []
    count = 0
    for name, body in sources:
        lines.append(f"## {name}")
        items = parse_rss(body)
        if not items:
            lines.append("(取得失敗または0件)")
        for it in items:
            lines.append(f"- {it['title']} ({it['pub_date']})")
            lines.append(f"  {it['url']}")
            count += 1
        lines.append("")

    jst_now = datetime.now(timezone(timedelta(hours=9)))
    date_label = f"{jst_now.year}年{jst_now.month}月{jst_now.day}日"

    return {
        "formatted_articles": "\n".join(lines),
        "article_count": count,
        "date_label": date_label,
    }
```

これでAPI呼び出しは `{"previous_digests": ""}` だけになる。
鮮度フィルタ(pub_dateで直近24時間に絞る)は必要になったら parse_rss に足す。

## v1.5でも残る制限

- NHK(認証Chrome必須)は引き続き外。将来 `extra_articles` 入力を足して呼び出し側から混ぜる
- enモード相当を作る場合、ブログ新着判定(`last_checked.json`)の状態は呼び出し側持ちになる
- Google News RSSがDify CloudのサーバーIPをブロックする可能性はゼロではない(その場合はv1方式に戻す)
