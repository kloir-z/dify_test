# Dify 学習メモ

気づいたこと・ハマったことを時系列で追記していく。

## 2026-06-11 リポジトリ立ち上げ

- Dify の API キーはワークスペース単位ではなく**アプリ単位**で発行される
- アプリ API(`/v1`)でできるのはチャット送信・ワークフロー実行など実行系のみ。
  DSL のエクスポート/インポートは管理画面(コンソール)からの手作業
- Raspberry Pi 4 (4GB) はメモリ不足で Dify セルフホストには不向きと判断(詳細は CLAUDE.md)

## 2026-06-12 jpダイジェストワークフロー v1〜v1.5 構築

- **コードノードはネットワーク禁止**(サンドボックス)。取得はHTTPリクエストノード、解析はコードノードに分担する
- **コードノードの入力変数リストと `main()` の引数は完全一致が鉄則**。余分な登録変数があると `unexpected keyword argument` で落ちる
- HTTPノードの「エラー時デフォルト値+リトライ」で、元collectorの「ソース単位のエラー許容」を宣言的に再現できた
- 無料枠のGemini系モデルは混雑時に 503 UNAVAILABLE が出やすい。リトライ間隔を伸ばすか別モデルへ
- **接地監査ノードは初回実行から捏造を実検出**(見出しにない「トマホーク49発」「40代幹部」等)。一方で v1 では実在見出しの数値を捏造と**誤判定する事例も発生** → 監査には見出しリストを参照として渡す(v1.5b)
- DSLエクスポートには秘密情報なし。モデルプラグイン依存(gemini等)が記録される

## 2026-06-15 ダイジェストを「整形+翻訳のみ」に再設計(接地監査を廃止)

- **接地監査ノードは「事実と解釈を1つのLLM出力に混ぜた」ことの後始末にすぎない**と気づいた。
  見出しを機械的に整形すれば生成が無く、捏造の余地が原理的に消える → 監査が不要になる
- トレードオフとして、メディアフレーミング比較・トピック横断トレンドなどの**編集的分析は捨てた**
  (これらは本質的に生成物で、残す限り接地リスクが付いて回るため)。出力は「ソース別・整形済み見出しリスト」
- **jp**: ソースが日本語なので翻訳すら不要 → **LLMゼロの純コードワークフロー**
  (HTTP×5 → コード整形 → IF(>0) → 出力)。LLM①〜④を全削除。`previous_digests` 入力も不要になり削除
- **en**: 英語ソースなので「コード整形 → 翻訳LLM **1回** → 出力」。LLM①〜④を翻訳1ノードに置換。
  翻訳プロンプトは「見出し行だけ訳す/構造・URL・日時・[N pts]は触らない/要約・論評禁止」で接地リスクを排除
- `gen_en_dsl.py` を改訂: jp(純コード)を元に、IF(true)と出力の間へ翻訳ノードを挿入する方式に。
  4LLM直列(分析→執筆→監査→編集)から1LLM(翻訳のみ)へ。コストも実行時間も大幅減
- **URLは翻訳に不要な巨大トークン**(Google News RSSは1本200字超)。整形時に `[[連番]]` プレースホルダへ
  退避して `url_map` に保存し、LLMにはURLを渡さない。翻訳後に「URL復元」コードノードで `[[N]]` を実URLへ戻す。
  en の構成は `code(整形+url_map) → IF → 翻訳LLM → URL復元code → 出力`。入力・出力トークン両方を節約
- **pub_date をJSTへ統一**(`to_jst()`)。文字列に書かれたTZを読んで変換し、推測しない方針:
  Google News RSSの `GMT` は本物のUTC(+9で翌日繰り上がりも処理)、HN/AtomのISOは `Z`・`+09:00`・`-04:00` の
  オフセットを解釈。**既に `+09:00` のものは二重変換しない**、TZ不明は誤変換を避けて素通し。出力は `YYYY-MM-DD HH:MM JST`
  - サンドボックス制約を避けるため正規表現を使わず文字列分割でパース(`email.utils` 等の追加importに依存しない)
  - jp/en 両コードに同じ `to_jst` を実装。jp実コードを抽出した9ケースのユニットテストとライブ実行で検証
- jp はライブ実取得で75件を整形できることを確認。プレースホルダ退避→復元のラウンドトリップも検証。
  両DSLとも `lint_dsl.py` 通過

## 2026-06-15 security-digest:純機械の「脆弱性 重要度フィルタ型」DSL を試作

- 現場の肌感「緊急対応は Log4Shell/OpenSSL 級くらいで稀」→ **CVEの件数 ≠ 対応すべき重大さ**。
  ダイジェストの役割は CVE 列挙でなく「重大なものだけ浮かせる重要度フィルタ」と再定義
- **重大度は純機械で判別できる(LLM不要・接地クリーン)**。実データで検証した3シグナル:
  - **CISA KEV 該当**(現に悪用されている)。当初は KEVカタログJSON と突合する設計だったが、
    **JSONが1.44MB > Difyのノード間変数上限1MB** で実行失敗("Text size is too large")。
    → CISA Advisories RSS の「KEV追加」告知("CISA Adds … Known Exploited …", 本文にCVE-ID)から取得に変更。
    これは「新規に悪用入りしたCVE」そのもので狙いに合致し、かつ軽量(全フィード1MB未満)
  - **横断出現**(同一CVEが JPCERT/CISA/ベンダー/報道に同時多発=大事件)
  - (EPSS=悪用予測スコアも有効だが API 必須 → コードノードのネット禁止により後フェーズ)
  - 例: Log4Shell は EPSS 0.94/上位0.04%。Palo Alto 実悪用や Oracle PeopleSoft ゼロデイを自動で最上段に
- **「新たな攻撃手法」の機械判別は弱い**(CVE紐付かず・キーワードはノイズ多)。今回はスコープ外に
- **重複対策(続報疲れ)を LLM なしで**: 既出CVE `seen_cves` を入力 → 集合差で除外、`new_cves` を
  出力して呼び出し側が状態ファイルに追記。実証: 1回目 hot20→表示15、2回目(既出除外)hot5 に収束
- 多形式対応: RSS2.0 / **RDF(RSS1.0=JPCERT, item属性・dc:date)** / Atom / KEV(JSON) を1コードノードで統一。
  CISA の pubDate は **2桁年**(`Fri,12 Jun 26`)で年が壊れた → `to_jst` に2桁年補正を追加
- 翻訳はハイブリッド(英語見出しのみ訳・日本語素通し)。生成は `scripts/gen_security_dsl.py`
- 日本の公的セキュリティRSS: **JPCERT/CC**(`/rss/jpcert.rdf`)・**JVN**(`jvn.jp/rss/jvn.rdf`)・
  **JVN iPedia**(`jvndb.jvn.jp/ja/rss/jvndb_new.rdf`)が現役。**IPA単独RSSは廃止**され JVN系に集約(IPA共同運営)
- 次フェーズ候補: EPSS(イテレーション/一括CSV)、PSIRT増強(Fortinet/MSRC/RedHat)、状態ファイル連携の runner

### 2026-06-15 追記:Dify をスリム化し、肉付けは後段(将来の Claude ルーチン)へ

- **問題**: CISA「KEV追加」告知 RSS は **CVE-ID しか持たない**ため、CISA 単独ソースのCVEは
  見出しが「カタログに1件追加」のまま=製品名も脆弱性の中身も分からず使えない
- **方針(役割分担)**: この Dify は「フローを把握して固定化する置き場」で、**いずれ Claude の
  ルーチン化**する前提。よって **Dify = 収集 + 機械フィルタ**に専念し、要注目CVEの素材を
  構造化 JSON `hot_json`(cve / srcs / dt / url / title)で返すだけにした。
  **肉付け(KEV突合)・整形・重複管理は後段(Claudeルーチン)が担う**
- **スリム化の具体**: 翻訳LLMノード・URL復元ノード・薄い digest・`new_cves`/`url_map` 出力・
  gemini 依存を **削除**。End 出力は `hot_json` と `date_label` のみ(ノード10→7・エッジ11→9)。
  これで毎回の LLM 翻訳コスト/遅延も消えた
  - ※ いったん「ローカル runner が Dify を API 呼び出しして肉付け」する版を作ったが、
    将来 Claude ルーチン化するなら API キー・状態ファイル等は不要 → runner は破棄した
- **重複管理**: Dify は `seen_cves`(既出CVE)を入力で受け、集合差で除外する口を持つ。
  表示CVEは `hot_json` に入るので、後段が hot_json から既出リストを更新して次回 `seen_cves` に渡す

#### 肉付けの手順(後段=Claudeルーチンで再現するための記録)

- **情報源 = CISA KEV カタログ JSON**:
  `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json`(約1.44MB)。
  **Dify の変数1MB上限を超えるため Dify 内では突合不可**。後段なら無制約。
  LLM 推定で埋めると最新CVEで幻覚が出るので、**機械的引き当て**に徹する(権威情報のみ)
- **1件あたりのフィールド**: `cveID` / `vendorProject` / `product` / `vulnerabilityName` /
  `dateAdded` / `shortDescription` / `requiredAction` / `dueDate` / `knownRansomwareCampaignUse`("Known"|"Unknown") / `cwes`
- **表示の組み立て**(実証済み): `vendorProject + product — <日本語の種別>` を主表記にし、
  `dueDate`(=米連邦機関向け是正期限。緊急度の目安)を添える。`knownRansomwareCampaignUse=="Known"`
  なら「ランサムウェア悪用: 確認あり」を付ける。`cveID` が KEV に無ければニュース見出し(`title`)で代替
- **脆弱性名の日本語化を LLM なしで**: `vulnerabilityName` は「`<vendor/product>` `<種別>` Vulnerability」形式。
  接頭辞のベンダー/製品名を **最長一致**で剥がし(製品名がベンダー名を含むケース対策。
  さらに `product` が `vendor` で始まるなら主表記は `product` だけにして重複回避)、
  末尾 " Vulnerability" を除いた「種別」を下表で日本語化。**未収録の種別は英語のまま**(権威情報を壊さない)
- **種別→日本語 辞書**(KEV実データの頻出上位~50。新種別が出たら追記する):

  | English (種別。"Vulnerability" は除く) | 日本語(+「の脆弱性」) |
  |---|---|
  | Remote Code Execution | リモートコード実行 |
  | Code Execution / Arbitrary Code Execution | コード実行 / 任意コード実行 |
  | Privilege Escalation / Kernel Privilege Escalation | 権限昇格 / カーネル権限昇格 |
  | Use-After-Free | 解放済みメモリ使用(Use-After-Free) |
  | Memory Corruption / Scripting Engine Memory Corruption | メモリ破壊 / スクリプトエンジンのメモリ破壊 |
  | OS Command Injection / Command Injection / Code Injection | OSコマンドインジェクション / コマンドインジェクション / コードインジェクション |
  | SQL Injection | SQLインジェクション |
  | Authentication Bypass / Improper Authentication | 認証バイパス / 不適切な認証 |
  | Security Feature Bypass / SmartScreen Security Feature Bypass | セキュリティ機能バイパス / SmartScreenセキュリティ機能バイパス |
  | Path Traversal / Directory Traversal / Absolute Path Traversal | パストラバーサル / ディレクトリトラバーサル / 絶対パストラバーサル |
  | Information Disclosure | 情報漏えい |
  | Type Confusion | 型混同 |
  | Deserialization of Untrusted Data / Deserialization | 信頼できないデータのデシリアライゼーション / デシリアライゼーション |
  | Improper Input Validation | 不適切な入力検証 |
  | Improper Access Control / Incorrect Authorization | 不適切なアクセス制御 / 不適切な認可 |
  | Improper Privilege Management | 不適切な権限管理 |
  | Missing Authentication for Critical Function | 重要機能の認証欠如 |
  | Buffer Overflow / Heap(-Based) Buffer Overflow / Stack-Based Buffer Overflow | バッファオーバーフロー / ヒープバッファオーバーフロー / スタックバッファオーバーフロー |
  | Integer Overflow | 整数オーバーフロー |
  | Out-of-Bounds Write / Out-of-Bounds Read / Out-of-Bounds Read and Write | 境界外書き込み / 境界外読み取り / 境界外読み書き |
  | Cross-Site Scripting (XSS) | クロスサイトスクリプティング(XSS) |
  | Server-Side Request Forgery (SSRF) | サーバサイドリクエストフォージェリ(SSRF) |
  | Denial-of-Service | サービス拒否(DoS) |
  | Race Condition | 競合状態 |
  | Unrestricted File Upload / Unrestricted Upload of File with Dangerous Type | ファイルの無制限アップロード / 危険な種類のファイルの無制限アップロード |
  | Uncontrolled Resource Consumption | リソース消費の制御不備 |
  | Improper Encoding or Escaping of Output | 出力のエンコード/エスケープ不備 |
  | Incomplete Comparison with Missing Factors | 比較条件の欠落 |
  | Unspecified | 詳細未公開 |

- **実データ検証**: 元は「カタログに1件追加」だった13件すべてが Ivanti Sentry(OSコマンドインジェクション)/
  Google Chromium V8(境界外読み書き)/ BerriAI LiteLLM(コマンドインジェクション)/ Oracle PeopleSoft 等の
  「製品 + 日本語の種別 + 是正期限(+ランサム悪用)」に展開されることを確認済み

## 2026-06-11 Dify Cloud 疎通確認

- Dify Cloud (api.dify.ai) は Cloudflare 配下。Python-urllib のデフォルト User-Agent は
  error 1010 (403) で弾かれる → 任意の UA を設定すれば通る
- チャットフロー型アプリは「公開する」を押すまで API が `app_unavailable` (400) を返す
- アプリ「test」(advanced-chat) で chat コマンドの疎通確認済み

## 2026-06-15 3ダイジェスト統合に向けた共通体裁(出力フォーマット仕様)

- **目的**: jp-news / en-news / security の3ダイジェストを、いずれ1本の「統合ダイジェスト」に
  まとめる。今回採った方式は **「各 Dify が完成 markdown を出し、後段が縦に連結するだけ」**
  (体裁統一方式)。重複排除・優先順位付けはこの方式では不可(やるなら構造化素材方式に切替)
- **共通体裁(全ダイジェスト共通)**:
  - 各ダイジェストは自分の **`## <絵文字> ラベル` セクションから始める**(`#` のタイトル行は付けない)。
    統合時に後段が先頭へ `# 統合ダイジェスト(YYYY年M月D日)` を1行足して連結する
  - 階層: `##` 大区分 / `###` 小区分(媒体名 または CVE)
  - 項目: `- 本文` → 続き行は **2スペース字下げ** → **URL を独立行** → **日時を独立行**
    (`YYYY-MM-DD HH:MM JST`)。日時を見出し行の `(...)` に入れない
  - セクションラベル: `🔴 セキュリティ要対応(N件)` / `📰 国内ニュース` / `🌐 海外ニュース`
- **各ダイジェストの生成箇所**:
  - jp: Dify コードノードが `formatted_articles` を生成(`## 📰 国内ニュース` → `### 媒体名`)
  - en: Dify コード → 翻訳LLM → URL復元。翻訳は `-` 見出し本文のみ訳し、`##`/`###`/`[[N]]`/
    日時行/`[N pts]` は不可侵(プロンプトに明記)。出力は `## 🌐 海外ニュース` 始まり
  - security: Dify は `hot_json` を返すだけ。**後段(将来の Claudeルーチン)が KEV 肉付け →
    `## 🔴 セキュリティ要対応(N件)` セクションをこの体裁でレンダリング**(KEVはDify内で突合不可)
- **統合(将来の Claudeルーチン)**: `# 統合ダイジェスト(date)` を付け、🔴 → 📰 → 🌐 の順に連結。
  date_label は各ワークフローが出力するのでそれを使う

## 2026-06-15 irodori_test `/auto` の mp3 手前までを Dify で再現(irodori-script-prep)

- **目的**: 別リポジトリ `C:\code\irodori_test` の `/auto`(題材 → Irodori-TTS の台本 →
  前処理 → check 修正 → glossary → mp3 合成)のうち、**mp3 合成(Step6 Modal)以外**を Dify 化。
  成果物は `script_processed.yaml` + `glossary.json`(後段でローカルの Modal に渡す素材)
- **対応**: Step2 台本生成=LLM「台本生成」 / Step4 前処理=コード(`prepare_irodori_text` 移植) /
  Step5 check 修正=`check_irodori_text` 移植 + 修正LLM / Step5.5 glossary=コード候補抽出 + LLM 生成
- **マニュアルで確認した制約 2 点**:
  - コードノードのサンドボックスは**プリインストール済みパッケージのみ**(既定 numpy/pandas/requests 等)。
    `import yaml`(PyYAML)は保証されない → 台本は**内部 JSON で持ち回り、最終的に YAML テキストを
    手書きシリアライズ**(stdlib の json/re/collections のみ。既存ワークフローの stdlib 縛りと同方針)
  - Dify には **Loop ノード**(前回結果を積み上げる反復+終了条件:最大回数/break/Exit ノード)があるが、
    状態変数・assigner・入れ子子ノードで **DSL 構造が複雑** → 手書きでの一発インポートはリスク高
- **修正ループは「線形3パス + 警告が空なら no-op」で実装**(真の Loop ノードを使わない):
  生成→check#0→修正#1→check#1→修正#2→check#2→修正#3→check#3。各修正LLMは警告レポート先頭が
  「問題なし/警告0件」なら入力をそのまま返す。SKILL の「**ループは最大3回まで**」と挙動一致。
  分岐マージの「どの上流を読むか」曖昧問題が起きず、`lint_dsl.py` も素直に通る(全ノード線形)
- **check は原実装を移植しつつ拡張**: ① NAME 検出に現行デフォルト女声 `リン`(さん)を追加
  (原実装は `コトハ`/`ソウタ` のみ。制約#23)、② 原 check_irodori_text に無い **読点ルール#1**
  (1 line >3個 / 全体 >5.0/100字)を warn 化して**人間レビュー無しの自動ループでも#1を担保**。
  P2/P9(山括弧・連続カギ括弧)は前処理で消えるので check からは省略、P5(YOMI)は info 扱い
- **glossary**: `/auto` は script.yaml の `source:` 欄が指すソース MD を典拠にするが、本ワークフローは
  入力が題材直貼りなので**題材原文そのものをソース代わり**にして `build_glossary` を移植
  (台本のカタカナ × 題材のアルファベット語彙)。表記復元は字幕用で**音声には不影響**
- **LLM は既定 Claude Sonnet**(`script-author` の Sonnet/Opus 運用に合わせ品質重視)。`dependencies: []`
  にしてあるので、インポート時に Dify がプラグイン導入を促す。各 LLM ノードでモデル選択を確定すること。
  弱くてよければ `gen_irodori_dsl.py` の `LLM_PROVIDER`/`LLM_MODEL` を gemini に差し替えて再生成
- **検証**: `scripts/test_irodori_dsl.py` で前処理が原実装と完全一致(14ケース)・check 警告検出・
  ```フェンス/壊れJSON 耐性・最終 YAML のラウンドトリップ(バックスラッシュ/引用符エスケープ)を assert。
  `lint_dsl.py` は ERROR/WARN なし(ノード13/エッジ12)
- **真の Loop ノード化**は v2 候補(一度 UI で手組みした loop DSL を雛形に取れたら差し替え)
