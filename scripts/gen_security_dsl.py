#!/usr/bin/env python3
"""security-digest の DSL を生成する(純機械の「脆弱性 重要度フィルタ型」ダイジェスト)。

設計(2026-06-15 確定 / スリム化版):
- 目的: 大量のCVEから「現に悪用されている新規脆弱性」だけを機械的に浮かせる
- 重要度シグナルは純機械(LLM不使用): CISA KEV該当(=現に悪用) + CVE単位の横断出現
- 役割分担: 本ワークフローは「収集+機械フィルタ」に専念し、要注目CVEの素材を
  構造化 JSON(hot_json: cve/srcs/dt/url/title)で返すだけ。**肉付け(KEVカタログ突合で
  vendor/product/脆弱性名/是正期限を付与)・整形は後段(将来の Claude ルーチン)が担う**
  (KEV JSONは1.44MBでDifyの変数1MB上限を超えるため、Dify内では突合しない)。
  肉付けの手順と日本語化辞書は docs/notes.md に保存
- 重複対策: 既出CVE(seen_cves)を入力で受け取り集合差で除外。表示したCVEは hot_json に
  含まれるので、後段がそこから既出リストを更新する
- 多形式: RSS2.0 / RDF(RSS1.0=JPCERT) / Atom を1コードノードで統一処理
- KEVは巨大JSON(1.44MB>Dify上限1MB)のため、CISA RSSの「KEV追加」告知からCVEを取得
- LLMノードは持たない(翻訳・肉付けは後段)。EPSS等の拡充も後フェーズで後段側に

使い方:
    python scripts/gen_security_dsl.py
    → workflows/security-digest.yml を生成
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DST = ROOT / "workflows" / "security-digest.yml"

ID_START = "2000100001"
ID_JPCERT = "2000100002"
ID_CISA = "2000100003"
ID_CISCO = "2000100004"
ID_HN = "2000100005"
ID_KEV = "2000100006"
ID_CODE = "2000100007"
ID_END = "2000100010"

HTTP_SOURCES = [
    (ID_JPCERT, "JPCERT 注意喚起", "https://www.jpcert.or.jp/rss/jpcert.rdf", "body_jpcert"),
    (ID_CISA, "CISA Advisories", "https://www.cisa.gov/cybersecurity-advisories/all.xml", "body_cisa"),
    (ID_CISCO, "Cisco PSIRT", "https://sec.cloudapps.cisco.com/security/center/psirtrss20/CiscoSecurityAdvisory.xml", "body_cisco"),
    (ID_HN, "The Hacker News", "https://feeds.feedburner.com/TheHackersNews", "body_hn"),
]
# 注: CISA KEV カタログJSON(約1.44MB)は Dify のノード間変数上限(1MB)を超えて渡せない。
# 代わりに CISA Advisories RSS の「KEV追加」告知("CISA Adds … Known Exploited …")から
# CVE-ID を取る。これは「新規に悪用入りした CVE」そのもので、本ダイジェストの狙いに合致する。

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# === コードノード本体(ネット不使用・入力は取得済みボディ + seen_cves) ===
SECURITY_CODE = r'''import json
import re
from datetime import datetime, timedelta, timezone

CVE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)
JST = timezone(timedelta(hours=9))
WINDOW_DAYS = 14   # 既出除外のバックストップ(初回の大量バックログ流入を抑える)
CAP = 15           # 1回の表示上限。超過分は new_cves に含めず次回へ繰り越す

MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
TZ_NAMES = {"GMT": 0, "UTC": 0, "UT": 0, "Z": 0, "JST": 540,
            "EST": -300, "EDT": -240, "CST": -360, "CDT": -300,
            "MST": -420, "MDT": -360, "PST": -480, "PDT": -420}


def _tzoff(tz):
    tz = tz.strip()
    if tz in TZ_NAMES:
        return TZ_NAMES[tz]
    if tz[:1] == "+":
        sign = 1
    elif tz[:1] == "-":
        sign = -1
    else:
        return None
    body = tz[1:].replace(":", "")
    if len(body) < 4:
        return None
    try:
        return sign * (int(body[0:2]) * 60 + int(body[2:4]))
    except ValueError:
        return None


def _parse_dt(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        if len(raw) >= 10 and raw[4:5] == "-" and raw[7:8] == "-":
            s = raw if "T" in raw else raw.replace(" ", "T", 1)
            y, mo, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
            rest = s[11:]
            if not rest:
                return None
            off = None
            if rest[-1:] in ("Z", "z"):
                off = 0
                rest = rest[:-1]
            else:
                for i in range(len(rest) - 1, -1, -1):
                    if rest[i] in "+-":
                        off = _tzoff(rest[i:])
                        rest = rest[:i]
                        break
            if off is None:
                return None
            t = rest.split(".")[0].split(":")
            return datetime(y, mo, d, int(t[0]), int(t[1]), int(t[2]) if len(t) > 2 else 0,
                            tzinfo=timezone(timedelta(minutes=off)))
        p = raw.replace(",", " ").split()
        if len(p) < 5:
            return None
        off = _tzoff(p[-1])
        hm = p[-2].split(":")
        mo = MONTHS.get(p[-4][:3])
        y = int(p[-3])
        if y < 70:
            y += 2000
        elif y < 100:
            y += 1900
        if mo is None or off is None:
            return None
        return datetime(y, mo, int(p[-5]), int(hm[0]), int(hm[1]), int(hm[2]) if len(hm) > 2 else 0,
                        tzinfo=timezone(timedelta(minutes=off)))
    except (ValueError, IndexError):
        return None


def _jst_str(dt):
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M JST") if dt else "日付不明"


def _f(block, tag):
    m = re.search(r"<" + tag + r"[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</" + tag + r">", block)
    return m.group(1).strip() if m else ""


def parse_feed(xml, limit=30):
    xml = xml or ""
    items = []
    blocks = re.findall(r"<item[^>]*>([\s\S]*?)</item>", xml) or re.findall(r"<entry[^>]*>([\s\S]*?)</entry>", xml)
    for b in blocks[:limit]:
        title = _f(b, "title")
        if not title:
            continue
        link = _f(b, "link")
        if not link:
            m = re.search(r'<link[^>]*href="([^"]+)"', b)
            link = m.group(1) if m else ""
        dt = _parse_dt(_f(b, "pubDate") or _f(b, "dc:date") or _f(b, "updated") or _f(b, "published"))
        cves = set(c.upper() for c in CVE.findall(title + " " + b))
        items.append({"title": title, "url": link, "dt": dt, "cves": cves})
    return items


def _pick(items):
    # CISAの汎用見出し("CISA Adds...")より具体的な見出しを優先、次に長いもの
    cand = [it for it in items if not it["title"].startswith("CISA Adds")]
    return max(cand or items, key=lambda it: len(it["title"]))


def main(body_jpcert, body_cisa, body_cisco, body_hn, seen_cves):
    seen = set(c.upper() for c in CVE.findall(seen_cves or ""))

    feeds = [("JPCERT", body_jpcert), ("CISA", body_cisa),
             ("Cisco", body_cisco), ("The Hacker News", body_hn)]
    agg = {}
    kev = set()  # CISA RSS の「KEV追加」告知から得る『現に悪用されている新規CVE』
    for src, body in feeds:
        for it in parse_feed(body):
            if src == "CISA" and "Known Exploited" in it["title"]:
                kev |= it["cves"]
            for c in it["cves"]:
                a = agg.setdefault(c, {"items": [], "srcs": set(), "dt": None})
                a["items"].append(it)
                a["srcs"].add(src)
                if it["dt"] and (a["dt"] is None or it["dt"] > a["dt"]):
                    a["dt"] = it["dt"]

    now = datetime.now(JST)
    hot = []
    for c, a in agg.items():
        if c not in kev or c in seen:
            continue
        if a["dt"] is not None and (now - a["dt"]).days > WINDOW_DAYS:
            continue
        hot.append((c, a))
    hot.sort(key=lambda x: x[1]["dt"].timestamp() if x[1]["dt"] else 0.0, reverse=True)

    shown = hot[:CAP]
    date_label = "%d年%d月%d日" % (now.year, now.month, now.day)

    # 肉付け・整形は後段(将来の Claude ルーチン)が担う。
    # ここでは『要注目CVEの素材』だけを構造化して返す(KEVカタログJSONとの突合は後段)。
    hot_items = []
    for c, a in shown:
        rep = _pick(a["items"])
        hot_items.append({
            "cve": c,
            "srcs": sorted(a["srcs"]),
            "dt": _jst_str(a["dt"]),
            "url": rep["url"],
            "title": rep["title"],
        })

    return {
        "hot_json": json.dumps(hot_items, ensure_ascii=False),
        "date_label": date_label,
    }'''


def http_node(nid, title, url, x, y):
    return {
        "data": {
            "authorization": {"config": None, "type": "no-auth"},
            "body": {"data": [], "type": "none"},
            "desc": "",
            "headers": "User-Agent:" + UA,
            "method": "get",
            "params": "",
            "retry_config": {"max_retries": 3, "retry_enabled": True, "retry_interval": 1000},
            "selected": False,
            "ssl_verify": True,
            "timeout": {"max_connect_timeout": 0, "max_read_timeout": 0, "max_write_timeout": 0},
            "title": title,
            "type": "http-request",
            "url": url,
            "variables": [],
        },
        "height": 155, "id": nid,
        "position": {"x": x, "y": y}, "positionAbsolute": {"x": x, "y": y},
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": 242,
    }


def edge(src, tgt, st, tt, handle="source"):
    return {
        "data": {"isInLoop": False, "sourceType": st, "targetType": tt},
        "id": "%s-%s-%s-target" % (src, handle, tgt),
        "source": src, "sourceHandle": handle, "target": tgt, "targetHandle": "target",
        "type": "custom", "zIndex": 0,
    }


def main() -> None:
    nodes = []
    edges = []

    # 開始: seen_cves(既出CVEリスト)を任意入力で受ける
    nodes.append({
        "data": {
            "selected": False, "title": "開始", "type": "start",
            "variables": [{
                "default": "", "hint": "前回までに出力した CVE-ID(カンマ/改行区切り)。状態ファイルから渡す",
                "label": "seen_cves", "max_length": 999999, "options": [], "placeholder": "",
                "required": False, "type": "paragraph", "variable": "seen_cves",
            }],
        },
        "height": 116, "id": ID_START,
        "position": {"x": -520, "y": 360}, "positionAbsolute": {"x": -520, "y": 360},
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": 242,
    })

    # HTTP 5本
    for i, (nid, title, url, _var) in enumerate(HTTP_SOURCES):
        nodes.append(http_node(nid, title, url, -120, 40 + i * 175))
        edges.append(edge(ID_START, nid, "start", "http-request"))
        edges.append(edge(nid, ID_CODE, "http-request", "code"))

    # コードノード(整形・選別エンジン)
    nodes.append({
        "data": {
            "code": SECURITY_CODE, "code_language": "python3",
            "outputs": {
                "hot_json": {"children": None, "type": "string"},
                "date_label": {"children": None, "type": "string"},
            },
            "selected": False, "title": "整形・選別", "type": "code",
            "variables": (
                [{"value_selector": [nid, "body"], "value_type": "string", "variable": var}
                 for nid, _t, _u, var in HTTP_SOURCES]
                + [{"value_selector": [ID_START, "seen_cves"], "value_type": "string", "variable": "seen_cves"}]
            ),
        },
        "height": 52, "id": ID_CODE,
        "position": {"x": 320, "y": 360}, "positionAbsolute": {"x": 320, "y": 360},
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": 242,
    })

    # 出力: hot_json(肉付け素材) + date_label。肉付け・整形は後段(Claudeルーチン)が担う
    nodes.append({
        "data": {
            "outputs": [
                {"value_selector": [ID_CODE, "hot_json"], "value_type": "string", "variable": "hot_json"},
                {"value_selector": [ID_CODE, "date_label"], "value_type": "string", "variable": "date_label"},
            ],
            "selected": False, "title": "出力", "type": "end",
        },
        "height": 90, "id": ID_END,
        "position": {"x": 660, "y": 360}, "positionAbsolute": {"x": 660, "y": 360},
        "selected": False, "sourcePosition": "right", "targetPosition": "left",
        "type": "custom", "width": 242,
    })

    edges.append(edge(ID_CODE, ID_END, "code", "end"))

    doc = {
        "app": {
            "description": "脆弱性フィードから『現に悪用されている新規CVE』だけを機械的に抽出し、肉付け用の構造化データ(hot_json)を返す収集エンジン",
            "icon": "🛡", "icon_background": "#FFEAD5", "icon_type": "emoji",
            "mode": "workflow", "name": "security-digest", "use_icon_as_answer_icon": False,
        },
        "dependencies": [],
        "kind": "app", "version": "0.6.0",
        "workflow": {
            "conversation_variables": [], "environment_variables": [],
            "features": {
                "file_upload": {"enabled": False}, "opening_statement": "",
                "retriever_resource": {"enabled": True},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False}, "suggested_questions": [],
                "suggested_questions_after_answer": {"enabled": False},
                "text_to_speech": {"enabled": False, "language": "", "voice": ""},
            },
            "graph": {"edges": edges, "nodes": nodes, "viewport": {"x": 0, "y": 0, "zoom": 0.7}},
            "rag_pipeline_variables": [],
        },
    }
    DST.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120),
                   encoding="utf-8")
    print("generated: %s (nodes=%d, edges=%d)" % (DST, len(nodes), len(edges)))


if __name__ == "__main__":
    main()
