import bz2
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional

import mwparserfromhell


# Wiktionary 덤프 경로. 환경변수 WIKTIONARY_DUMP_PATH 로 덮어쓸 수 있다.
DEFAULT_DUMP_PATH = "data_pipeline/dump/enwiktionary-20250920-pages-articles-multistream.xml.bz2"
DUMP_PATH = os.getenv("WIKTIONARY_DUMP_PATH", "").strip() or DEFAULT_DUMP_PATH
OUTPUT_PATH = "data_pipeline/output/slang_raw.json"

TARGET_LABELS = {
    "slang",
    "internet slang",
    "internet",
    "aave",
    "informal",
    "colloquial",
}

# Wiktionary XML namespace
NS = "{http://www.mediawiki.org/xml/export-0.11/}"


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_english_section(wikitext: str) -> Optional[str]:
    """
    ==English== 섹션만 잘라낸다.
    다음 같은 레벨의 언어 섹션(==Korean== 등) 전까지만 반환.
    """
    match = re.search(r"(?ms)^==English==\s*\n?(.*?)(?=^==[^=].*?==\s*$|\Z)", wikitext)
    if not match:
        return None
    return match.group(1).strip()


def strip_wikicode(text: str) -> str:
    """
    위키마크업 제거 후 일반 문자열로 정리.
    """
    try:
        code = mwparserfromhell.parse(text)
        cleaned = code.strip_code(normalize=True, collapse=True)
    except Exception:
        cleaned = text

    return normalize_space(cleaned)


def parse_labels_from_definition(raw_line: str) -> List[str]:
    """
    정의 줄에서 slang 관련 라벨 추출.
    """
    labels = set()
    lower_line = raw_line.lower()

    # 괄호형 라벨: (slang), (internet slang), (informal) ...
    for label in TARGET_LABELS:
        if f"({label})" in lower_line:
            labels.add(label)

    # {{lb|en|...}}, {{label|en|...}}, {{lbl|en|...}}
    template_matches = re.findall(
        r"\{\{(?:lb|label|lbl)\|en\|([^{}]+?)\}\}",
        raw_line,
        flags=re.I,
    )
    for match in template_matches:
        parts = [p.strip().lower() for p in match.split("|")]
        for part in parts:
            if part in TARGET_LABELS:
                labels.add(part)

    # {{context|...|lang=en}}, {{cx|...|lang=en}}
    context_matches = re.findall(
        r"\{\{(?:context|cx)\|([^{}]+?)\}\}",
        raw_line,
        flags=re.I,
    )
    for match in context_matches:
        parts = [p.strip().lower() for p in match.split("|")]
        for part in parts:
            if part == "lang=en":
                continue
            if part in TARGET_LABELS:
                labels.add(part)

    # {{qualifier|...}}, {{q|...}}
    qualifier_matches = re.findall(
        r"\{\{(?:qualifier|q)\|([^{}]+?)\}\}",
        raw_line,
        flags=re.I,
    )
    for match in qualifier_matches:
        parts = [p.strip().lower() for p in match.split("|")]
        for part in parts:
            if part in TARGET_LABELS:
                labels.add(part)

    return sorted(labels)


def clean_definition_text(raw_line: str) -> str:
    """
    정의 줄 정리:
    - 앞의 # 제거
    - 맨 앞 라벨 템플릿 제거
    - 괄호 라벨 제거
    - 위키코드 제거
    """
    text = re.sub(r"^#+\s*", "", raw_line).strip()

    # 맨 앞에 연속으로 붙는 라벨/문맥 템플릿 제거
    while True:
        new_text = re.sub(
            r"^\{\{(?:lb|label|lbl|context|cx|qualifier|q)\|[^{}]+?\}\}\s*",
            "",
            text,
            flags=re.I,
        )
        if new_text == text:
            break
        text = new_text.strip()

    # 앞쪽 괄호 라벨 제거
    text = re.sub(
        r"^(?:\((?:slang|internet slang|internet|aave|informal|colloquial)\)\s*)+",
        "",
        text,
        flags=re.I,
    )

    cleaned = strip_wikicode(text)

    if cleaned in {"", ".", ";", ":"}:
        return ""

    return cleaned


def extract_usage_text(text: str) -> str:
    """
    {{ux|en|...}}, {{usex|en|...}}, {{uxi|en|...}} 같은 템플릿에서
    실제 예문만 최대한 보존해서 꺼낸다.
    """
    try:
        code = mwparserfromhell.parse(text)
        templates = code.filter_templates(recursive=True)

        for tpl in templates:
            name = str(tpl.name).strip().lower()

            if name in {"ux", "usex", "uxi"}:
                params = [str(p.value).strip() for p in tpl.params]

                # 보통 {{ux|en|example text}}
                # 첫 번째가 en이면 두 번째부터 예문 후보
                if len(params) >= 2 and params[0].lower() == "en":
                    candidate = params[1]
                    candidate = strip_wikicode(candidate)
                    if candidate:
                        return candidate

                # 언어코드 없이 들어와도 최후 fallback
                if params:
                    candidate = strip_wikicode(params[-1])
                    if candidate:
                        return candidate
    except Exception:
        pass

    return ""


def clean_example_text(raw_line: str) -> str:
    """
    예문 줄 정리:
    - #:, ##:, #* 등 prefix 제거
    - usage 템플릿이면 실제 예문 우선 추출
    - quote-* 계열은 예문 대신 인용문이라 기본적으로 제외
    """
    text = re.sub(r"^#+(?::|\*)+\s*", "", raw_line).strip()

    # quote-web, quote-book, quote-journal 등은 기본 제외
    if re.search(r"\{\{quote-[^|}]+", text, flags=re.I):
        return ""

    usage_text = extract_usage_text(text)
    if usage_text:
        return normalize_space(usage_text)

    cleaned = strip_wikicode(text)
    cleaned = normalize_space(cleaned)

    if cleaned in {"", ".", ";", ":"}:
        return ""

    return cleaned


def is_definition_line(line: str) -> bool:
    """
    #, ##, ### ... 로 시작하는 definition line
    단, #: / #* / ##: / ##* 같은 예문/인용문은 제외
    """
    return bool(re.match(r"^#+\s", line))


def is_example_line(line: str) -> bool:
    """
    #:, ##:, ###: ... 형태의 usage example line
    """
    return bool(re.match(r"^#+:\s*", line))


def is_quote_line(line: str) -> bool:
    """
    #*, ##*, ###* ... 형태의 quote/citation line
    """
    return bool(re.match(r"^#+\*\s*", line))


def extract_slang_entries(title: str, english_section: str) -> List[Dict]:
    """
    영어 섹션에서 definition / example / label 추출.
    라벨이 없어도 일단 entry는 만들고,
    최종 필터링은 main()에서 한다.
    """
    lines = english_section.splitlines()
    entries = []

    current_definition = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # definition line: # / ## / ### ...
        if is_definition_line(stripped):
            definition_en = clean_definition_text(stripped)
            labels = parse_labels_from_definition(stripped)

            if not definition_en:
                current_definition = None
                continue

            current_definition = {
                "word": title,
                "definition_en": definition_en,
                "definition_ko": "",
                "example_en": "",
                "example_ko": "",
                "source_label": labels,
                "service_category": "",
            }
            entries.append(current_definition)

        # usage example line: #: / ##: / ###:
        elif is_example_line(stripped):
            if current_definition and not current_definition["example_en"]:
                example_en = clean_example_text(stripped)
                if example_en:
                    current_definition["example_en"] = example_en

        # quote line: #* / ##* / ###*
        elif is_quote_line(stripped):
            # quote는 기본적으로 example 취급 안 함
            continue

    return entries


def iter_wiktionary_pages(dump_path: str):
    """
    bz2 압축 XML을 스트리밍으로 page 단위 순회.
    """
    with bz2.open(dump_path, "rb") as f:
        context = ET.iterparse(f, events=("end",))
        for _, elem in context:
            if elem.tag == f"{NS}page":
                title_elem = elem.find(f"{NS}title")
                revision_elem = elem.find(f"{NS}revision")
                text_elem = revision_elem.find(f"{NS}text") if revision_elem is not None else None

                title = title_elem.text if title_elem is not None else ""
                text = text_elem.text if text_elem is not None and text_elem.text else ""

                yield title, text
                elem.clear()


def is_valid_title(title: str) -> bool:
    """
    불필요한 페이지 제외.
    """
    if not title:
        return False

    # 재구성/부록/템플릿 등 제외
    if ":" in title:
        return False

    # 너무 긴 제목 제외
    if len(title) > 80:
        return False

    return True


def main():
    results = []
    seen = set()

    for i, (title, wikitext) in enumerate(iter_wiktionary_pages(DUMP_PATH), start=1):
        if i % 50000 == 0:
            print(f"[INFO] processed pages: {i}, current results: {len(results)}")

        if not is_valid_title(title):
            continue

        english_section = extract_english_section(wikitext)
        if not english_section:
            continue

        entries = extract_slang_entries(title, english_section)

        for entry in entries:
            # 관심 라벨 없는 건 제외
            if not entry["source_label"]:
                continue

            # 빈 definition 방지
            if not entry["definition_en"]:
                continue

            key = (entry["word"].lower(), entry["definition_en"].lower())
            if key in seen:
                continue

            seen.add(key)
            results.append(entry)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    example_count = sum(1 for x in results if x["example_en"])
    print(f"[DONE] saved {len(results)} entries to {OUTPUT_PATH}")
    print(f"[DONE] entries with example_en: {example_count}")


if __name__ == "__main__":
    main()