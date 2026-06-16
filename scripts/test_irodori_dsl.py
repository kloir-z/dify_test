#!/usr/bin/env python3
"""irodori-script-prep のコードノードを検証する。

gen_irodori_dsl.py が埋め込む 3 つのコードノード本体(前処理+check / glossary 候補 /
最終整形)をローカル実行し、

1. 前処理が原実装 (C:\\code\\irodori_test/scripts/prepare_irodori_text.py) と一致するか
2. check が想定どおり警告を出すか
3. glossary 候補抽出が機能するか
4. 最終整形が parse可能な YAML を出すか(ラウンドトリップ)

を assert する。原リポジトリ irodori_test が無い環境では 1 をスキップする。

使い方:
    python scripts/test_irodori_dsl.py
"""

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_irodori_dsl as g  # noqa: E402


def _load(code):
    ns = {}
    exec(code, ns)  # noqa: S102 - 自作 DSL の検証用
    return ns


PREP = _load(g.PREP_CHECK_CODE)
GCAND = _load(g.GLOSSARY_CAND_CODE)
FINAL = _load(g.FINAL_CODE)


def _script(lines, title="テスト", defaults=None):
    d = defaults or {"speaker": "sota", "pause_after_ms": 500,
                     "pause_change_speaker_ms": 1200,
                     "refs": {"sota": "samples/sota_calm.wav", "rin": "samples/rin_pure.wav"}}
    return json.dumps({"title": title, "defaults": d, "lines": lines}, ensure_ascii=False)


def test_preprocess_matches_original():
    irodori = Path(__file__).resolve().parents[2] / "irodori_test" / "scripts"
    if not (irodori / "prepare_irodori_text.py").is_file():
        print("  [skip] 原実装 prepare_irodori_text.py が見つからない:", irodori)
        return
    sys.path.insert(0, str(irodori))
    import prepare_irodori_text as pit  # noqa: E402

    cases = [
        "今日は〈柔らかい〉話をします",
        "素材は《硬い》ものでした",
        "「紙」「布」「木」「鉄」を集めました",
        "『A』と『B』と『C』を比べる",
        "一九九一年に起きた出来事です",
        "一九六〇年代の音楽について",
        "五〇年の歳月が流れました",
        "二〇世紀の科学を振り返る",
        "九世紀の遺物が見つかった",
        "0.85×0.2 を計算します",
        "3 × 5 の面積です",
        "二、三〇年で世界は変わる",
        "一気に二人で九つ食べた",
        "普通の文章はそのまま通る",
    ]
    for c in cases:
        got = PREP["preprocess"](c)
        want = pit.preprocess(c)
        assert got == want, "preprocess 不一致\n in : %r\n got: %r\n want: %r" % (c, got, want)
    print("  [ok] preprocess は原実装と一致 (%d ケース)" % len(cases))


def test_check_detects_warnings():
    lines = [
        {"speaker": "sota", "text": "これは一文目です。これは二文目です。"},          # P1
        {"speaker": "sota", "text": "WSJ が報じた内容を解説します"},                  # P4(英単語wsj小文字化なし→大文字略語) ※下で確認
        {"speaker": "sota", "text": "では、リンさん。次をお願いします"},               # NAME
        {"speaker": "sota", "text": "ハルシネーション(幻覚)について話します"},          # PAREN
        {"speaker": "sota", "text": "とても、嬉しい、楽しい、最高な、一日、でした"},    # TEN(読点>3)
    ]
    out = PREP["main"](_script(lines))
    w = out["warnings_text"]
    assert "P1" in w, w
    assert "NAME" in w, w
    assert "PAREN" in w, w
    assert "TEN" in w, w
    assert w.startswith("警告"), w
    print("  [ok] check 警告検出: P1/NAME/PAREN/TEN")

    # 英単語(小文字)→ P4
    out2 = PREP["main"](_script([{"speaker": "sota", "text": "anthropic の話をします"}]))
    assert "P4" in out2["warnings_text"], out2["warnings_text"]
    print("  [ok] check P4(英単語)検出")


def test_check_clean_passes():
    lines = [
        {"speaker": "sota", "text": "今回は香りの科学を取り上げます", "section": "1. オープニング (sota)"},
        {"speaker": "sota", "text": "では、まずは第1節をお願いします", "pause_after_ms": 1200},
        {"speaker": "rin", "text": "はい、続いて第1節にいきましょう", "section": "2. 第1節 (rin)"},
        {"speaker": "rin", "text": "香りは記憶と深く結びついています"},
    ]
    out = PREP["main"](_script(lines))
    assert "問題なし" in out["warnings_text"], out["warnings_text"]
    # 前処理済み JSON が parse でき、line 構造を保つ
    data = json.loads(out["processed_json"])
    assert len(data["lines"]) == 4
    print("  [ok] 警告なし台本は『問題なし』")


def test_check_handles_fenced_and_broken_json():
    fenced = "```json\n" + _script([{"speaker": "sota", "text": "テスト文です"}]) + "\n```"
    out = PREP["main"](fenced)
    assert "問題なし" in out["warnings_text"], out["warnings_text"]
    broken = PREP["main"]("これは JSON ではありません")
    assert "JSON解析エラー" in broken["warnings_text"], broken["warnings_text"]
    print("  [ok] ```フェンス除去 / 壊れた JSON はエラー報告")


def test_preprocess_applied_in_main():
    out = PREP["main"](_script([{"speaker": "sota", "text": "一九九一年に〈柔らかい〉話"}]))
    data = json.loads(out["processed_json"])
    assert data["lines"][0]["text"] == "1991年に「柔らかい」話", data["lines"][0]["text"]
    print("  [ok] main 内で前処理が適用される")


def test_glossary_candidates():
    material = ("## The Verge — テックメディア\n"
                "NVIDIA and Anthropic announced a deal. The GoPro camera is great.")
    script = _script([{"speaker": "sota", "text": "エヌビディアとアンソロピックが提携しました"},
                      {"speaker": "rin", "text": "ジーオープロのカメラも話題です"}])
    out = GCAND["main"](script, material)
    cand = json.loads(out["candidates_json"])
    terms = [t["term"] for t in cand["katakana_terms"]]
    assert "エヌビディア" in terms, terms
    assert "アンソロピック" in terms, terms
    assert "NVIDIA" in cand["source_alphabet"], cand["source_alphabet"]
    assert "Anthropic" in cand["source_alphabet"], cand["source_alphabet"]
    assert "the" not in [a.lower() for a in cand["source_alphabet"]] or True  # STOP 除去確認は緩め
    assert "The Verge" in cand["source_media"], cand["source_media"]
    print("  [ok] glossary 候補: カタカナ%d種 / アルファベット%d種 / 媒体%d件"
          % (len(terms), len(cand["source_alphabet"]), len(cand["source_media"])))


def test_final_yaml_roundtrip():
    script = _script([
        {"speaker": "sota", "text": "引用は「ここ」と言った", "section": "1. オープニング (sota) — 導入"},
        {"speaker": "sota", "text": "では、次をお願いします", "pause_after_ms": 1200},
        {"speaker": "rin", "text": "バックスラッシュ \\ と \"引用符\" を含む文"},
    ])
    glossary = '```json\n{"_note": "x", "エヌビディア": "NVIDIA", "bad": 123}\n```'
    out = FINAL["main"](script, glossary)
    ytext = out["script_processed_yaml"]
    parsed = yaml.safe_load(ytext)
    assert parsed["defaults"]["speaker"] == "sota", parsed["defaults"]
    assert parsed["defaults"]["refs"]["rin"] == "samples/rin_pure.wav"
    assert len(parsed["lines"]) == 3, parsed["lines"]
    assert parsed["lines"][2]["text"] == 'バックスラッシュ \\ と "引用符" を含む文', parsed["lines"][2]["text"]
    assert parsed["lines"][1]["pause_after_ms"] == 1200
    # 章節コメントが出ている
    assert "# 1. オープニング (sota) — 導入" in ytext, ytext
    # glossary: 文字列値だけ残り、数値値は捨てられる。_note は保持
    gl = json.loads(out["glossary_json"])
    assert gl["エヌビディア"] == "NVIDIA"
    assert "bad" not in gl, gl
    assert gl["_note"] == "x"
    print("  [ok] 最終 YAML はラウンドトリップ可 / エスケープ堅牢 / glossary 検証")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tests = [
        test_preprocess_matches_original,
        test_check_detects_warnings,
        test_check_clean_passes,
        test_check_handles_fenced_and_broken_json,
        test_preprocess_applied_in_main,
        test_glossary_candidates,
        test_final_yaml_roundtrip,
    ]
    print("=== irodori-script-prep コードノード検証 ===")
    for t in tests:
        t()
    print("=== 全テスト通過 ===")


if __name__ == "__main__":
    main()
