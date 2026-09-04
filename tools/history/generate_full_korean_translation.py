#!/usr/bin/env python3
"""Generate a complete Korean translation table from the extracted SSRW text.

The Japanese source is translated in batches through Google's public Japanese-
to-Korean endpoint.  The local reference file is applied as a protected
glossary, so character names and SRW terminology are not left to a generic
machine-translation choice.  The result is a reinsertion-oriented JSON file;
the separate build script consumes it.
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Iterable
import urllib.parse
import urllib.request


REC_MARKER = "ZZREC{:04d}ZZ"
NL_MARKER = "ZZLINEBREAKZZ"
WAIT_MARKER = "ZZWAIT{:02d}ZZ"
TOKEN_MARKER = "ZZTERM{:04d}ZZ"
BATCH_CHAR_LIMIT = 3000


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_label(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip()


def source_variants(value: str) -> list[str]:
    variants = [value]
    if "ー" in value:
        variants.append(value.replace("ー", "-"))
    if "・" in value:
        variants.append(value.replace("・", "."))
    return list(dict.fromkeys(item for item in variants if item))


def build_glossary(reference: dict) -> tuple[dict[str, str], dict[str, str]]:
    terms: dict[str, str] = {}
    speakers: dict[str, str] = {}
    for row in reference.get("term_glossary", []):
        for source in source_variants(row["source"]):
            terms[source] = row["korean"]
    for row in reference.get("character_glossary", []):
        korean = row["canonical_name"]
        for source in source_variants(row["source"]):
            terms[source] = korean
            speakers[normalize_label(source)] = korean
    for source, korean in reference.get("untranslated_source_labels_to_normalize", {}).items():
        for variant in source_variants(source):
            terms[variant] = korean
            speakers[normalize_label(variant)] = korean

    # The extraction font represents Japanese long vowels with '-'.  These
    # variants occur in ordinary sentences as well as in speaker labels.
    extra_terms = {
        "コロニ-": "콜로니",
        "ビ-ム": "빔",
        "ダメ-ジ": "대미지",
        "モビルス-ツ": "모빌슈트",
        "モビルスーツ": "모빌슈트",
        "ネオ.ジオン": "네오지온",
        "ネオ・ジオン": "네오지온",
        "モニタ-": "모니터",
        "パイロット": "파일럿",
        "コクピット": "콕핏",
        "異星人": "이성인",
        "戦艦": "전함",
        "艦長": "함장",
        "攻撃": "공격",
        "合体": "합체",
        "装甲": "장갑",
        "気力": "기력",
        "精神": "정신",
        "機体": "기체",
        "武器": "무기",
        "敵": "적",
        "味方": "아군",
        "撃墜": "격추",
        "戦闘": "전투",
        "出撃": "출격",
        "増援": "증원",
        "撤退": "퇴각",
        "地上": "지상",
        "宇宙": "우주",
        "火星": "화성",
        "ボルテスチ-ム": "볼테스 팀",
        "ブイトゥゲザ-": "V 투게더",
        "ボルトイン": "볼트 인",
        "ザンスカ-ル": "잔스칼",
        "リリ-ナ=ド-リアン": "리리나 도리안",
        "フィンファンネル": "핀 판넬",
        "ファンネル": "판넬",
        "ビームサーベル": "빔 사벨",
        "ビームライフル": "빔 라이플",
        "ガンダムファイト": "건담 파이트",
        "デビルガンダム": "데빌 건담",
        "ゴッドフィンガー": "갓 핑거",
        "石破天驚拳": "석파천경권",
        "断空剣": "단공검",
        "断空砲": "단공포",
    }
    for source, korean in extra_terms.items():
        terms[source] = korean
        speakers[normalize_label(source)] = korean
    speakers.update({
        "岡長官": "오카 장관",
        "ゴメス": "고메스",
        "ゴッツォ": "고초",
        "ハイネル": "하이넬",
        "ジャンギャル": "장갈",
        "左近寺博士": "사콘지 박사",
        "救急隊員": "구급대원",
        "鉄仮面": "철가면",
        "役人": "관리",
        "市長": "시장",
        "男": "남자",
        "女": "여자",
    })
    return terms, speakers


def protect_source(text: str, terms: dict[str, str]) -> tuple[str, dict[str, str]]:
    protected = text
    replacements: dict[str, str] = {}
    # Longest-first prevents コロニー from consuming the beginning of a more
    # specific term and makes protection deterministic.
    for ordinal, source in enumerate(sorted(terms, key=len, reverse=True)):
        if not source or source not in protected:
            continue
        marker = TOKEN_MARKER.format(ordinal)
        protected = protected.replace(source, marker)
        replacements[marker] = terms[source]

    wait_index = 0
    while "<WAIT>" in protected:
        marker = WAIT_MARKER.format(wait_index)
        protected = protected.replace("<WAIT>", marker, 1)
        replacements[marker] = "<WAIT>"
        wait_index += 1
    protected = protected.replace("\n", f" {NL_MARKER} ")
    return protected, replacements


def restore_translation(text: str, replacements: dict[str, str]) -> str:
    output = text
    # Google occasionally adds a space around a token.  Remove only the space
    # directly adjacent to our marker; normal Korean word spacing is retained.
    for marker, value in replacements.items():
        output = output.replace(f" {marker} ", marker)
        output = output.replace(marker, value)
    # The endpoint can truncate the final Z in a marker at a line boundary.
    # Recover those markers before the text reaches the insertion stage.
    for marker, value in replacements.items():
        if marker.startswith("ZZTERM"):
            number = marker[6:10]
            output = re.sub(r"ZZTERM" + re.escape(number) + r"Z*", value, output)
        elif marker.startswith("ZZWAIT"):
            number = marker[6:8]
            output = re.sub(r"ZZWAIT" + re.escape(number) + r"Z*", value, output)
    output = re.sub(r"ZZLINEBRE[A-Z]*", "\n", output)
    output = output.replace(NL_MARKER, "\n")
    output = re.sub(r"\s*ZZ\s*", " ", output)
    output = re.sub(r"\s+<WAIT>", "<WAIT>", output)
    return output.strip()


def google_translate(text: str, timeout: int = 45) -> str:
    query = urllib.parse.urlencode({
        "client": "gtx", "sl": "ja", "tl": "ko", "dt": "t", "q": text,
    })
    request = urllib.request.Request(
        "https://translate.googleapis.com/translate_a/single?" + query,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return "".join(segment[0] for segment in payload[0] if segment and segment[0])


def translate_batch(items: list[tuple[str, str]], terms: dict[str, str]) -> dict[str, str]:
    prepared: list[tuple[int, str, dict[str, str]]] = []
    for ordinal, (_, source) in enumerate(items):
        protected, replacements = protect_source(source, terms)
        prepared.append((ordinal, protected, replacements))
    joined = "\n".join(
        REC_MARKER.format(ordinal) + " " + protected
        for ordinal, protected, _ in prepared
    )
    translated = google_translate(joined)
    pattern = re.compile(r"ZZREC(\d{4})ZZ")
    matches = list(pattern.finditer(translated))
    result: dict[str, str] = {}
    if len(matches) != len(prepared):
        raise ValueError(
            f"batch marker count mismatch: expected {len(prepared)}, got {len(matches)}"
        )
    for position, match in enumerate(matches):
        ordinal = int(match.group(1))
        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(translated)
        raw = translated[start:end].strip()
        _, source = items[ordinal]
        replacements = prepared[ordinal][2]
        result[source] = restore_translation(raw, replacements)
    return result


def translate_units(units: Iterable[str], terms: dict[str, str], cache_path: Path) -> dict[str, str]:
    cache: dict[str, str] = {}
    if cache_path.exists():
        cache = load_json(cache_path)
    pending = [unit for unit in OrderedDict.fromkeys(units) if unit not in cache]
    print(f"translation units: {len(cache)} cached, {len(pending)} pending", flush=True)

    batch: list[tuple[str, str]] = []
    batch_chars = 0

    def flush() -> None:
        nonlocal batch, batch_chars
        if not batch:
            return
        last_error = None
        for attempt in range(4):
            try:
                translated = translate_batch(batch, terms)
                if len(translated) != len(batch):
                    raise ValueError("translation batch lost a source string")
                cache.update(translated)
                cache_path.write_text(
                    json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(f"translated {len(cache)}/{len(cache) + len(pending) - len(cache)} cache", flush=True)
                last_error = None
                break
            except Exception as error:  # network services can transiently fail
                last_error = error
                print(f"batch retry {len(batch)} items attempt {attempt + 1}: {error}", flush=True)
                time.sleep(1.5 * (attempt + 1))
        if last_error is not None:
            # If one marker is lost by the service, retry the members in
            # parallel rather than serially turning a single bad response into
            # thousands of round trips.
            def translate_single(item: tuple[str, str]) -> dict[str, str]:
                error = None
                for attempt in range(5):
                    try:
                        source = item[1]
                        # A few very short Japanese strings cause the public
                        # endpoint to drop an otherwise valid batch marker.
                        # For those fallbacks, put glossary output directly in
                        # the request and do not depend on a marker at all.
                        glossary_input = source
                        for japanese, korean in sorted(terms.items(), key=lambda pair: len(pair[0]), reverse=True):
                            glossary_input = glossary_input.replace(japanese, korean)
                        translated = google_translate(glossary_input)
                        return {source: translated.strip()}
                    except Exception as single_error:
                        error = single_error
                        time.sleep(0.5 * (attempt + 1))
                raise error or RuntimeError("single translation failed")

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(translate_single, item) for item in batch]
                for future in as_completed(futures):
                    cache.update(future.result())
            cache_path.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        batch = []
        batch_chars = 0
        time.sleep(0.12)

    total_pending = len(pending)
    for index, unit in enumerate(pending, 1):
        estimated = len(unit) + 20
        if batch and batch_chars + estimated > BATCH_CHAR_LIMIT:
            flush()
        batch.append((str(index), unit))
        batch_chars += estimated
    flush()
    missing = [unit for unit in units if unit not in cache]
    if missing:
        raise RuntimeError(f"translation cache is incomplete: {len(missing)} units")
    return cache


def split_scenario_text(row: dict) -> tuple[str | None, str]:
    source = row["japanese"]
    speaker = normalize_label(row.get("speaker") or "")
    prefix = source.split("「", 1)[0] if "「" in source else ""
    if speaker and "「" in source and source.startswith(prefix + "「"):
        body = source[len(prefix) + 1:]
        if body.endswith("」"):
            body = body[:-1]
        return prefix, body
    if source.startswith("---「") and source.endswith("」"):
        return "---", source[4:-1]
    if source.startswith("--") and source.endswith("--"):
        return "--", source[2:-2]
    if source.startswith("「") and source.endswith("」"):
        return "", source[1:-1]
    return None, source


def normalize_machine_terms(text: str) -> str:
    replacements = {
        "식민지": "콜로니",
        "식민": "콜로니",
        "팬넬": "판넬",
        "판넬들": "판넬",
        "사이코뮤": "사이코뮤",
        "빔포": "빔포",
        "대미지": "대미지",
        "정신 명령": "정신 커맨드",
        "정신 명령어": "정신 커맨드",
        "출격하다": "출격하다",
    }
    for source, korean in replacements.items():
        text = text.replace(source, korean)
    text = text.replace("  ", " ")
    return text


def speaker_korean(source: str, speakers: dict[str, str], fallback: dict[str, str]) -> str:
    normalized = normalize_label(source)
    if not normalized:
        return ""
    if normalized in speakers:
        return speakers[normalized]
    if normalized in fallback:
        return fallback[normalized]
    parts = re.split(r"[.·]", normalized)
    if len(parts) > 1:
        mapped = [speaker_korean(part, speakers, fallback) for part in parts]
        if all(mapped):
            return "·".join(mapped)
    return fallback.get(normalized, normalized)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=Path("translation_reference_ko.json"))
    parser.add_argument("--scenario", type=Path, default=Path("text_extracted/ssrw_scenario_dialogue.json"))
    parser.add_argument("--battle", type=Path, default=Path("text_extracted/ssrw_battle_dialogue.json"))
    parser.add_argument("--menu", type=Path, default=Path("text_extracted/ssrw_menu_text.json"))
    parser.add_argument("--cache", type=Path, default=Path("full_translation_google_cache_ko.json"))
    parser.add_argument("--output", type=Path, default=Path("full_translation_ko.json"))
    args = parser.parse_args()

    reference = load_json(args.reference)
    terms, speakers = build_glossary(reference)
    scenario_doc = load_json(args.scenario)
    battle_doc = load_json(args.battle)
    menu_doc = load_json(args.menu)

    scenario_rows = [
        row for scenario in scenario_doc["scenarios"] for row in scenario["records"]
        if row.get("japanese") and row.get("kind") not in {"non_text_data", "separator"}
    ]
    battle_rows = [row for row in battle_doc["records"] if row.get("japanese")]
    menu_strings = sorted({row["japanese"] for row in menu_doc["records"]})

    work_units: list[str] = []
    split_cache: dict[str, tuple[str | None, str]] = {}
    for row in scenario_rows:
        split = split_scenario_text(row)
        split_cache[row["id"]] = split
        work_units.append(split[1])
    for row in battle_rows:
        split_cache[row["id"]] = ("", row["japanese"][1:-1] if row["japanese"].startswith("「") and row["japanese"].endswith("」") else row["japanese"])
        work_units.append(split_cache[row["id"]][1])
    work_units.extend(menu_strings)

    # Generic speaker labels not found in the manually curated reference are
    # translated separately.  This prevents them from being mixed into prose.
    unknown_speakers = sorted({
        normalize_label(row.get("speaker") or "") for row in scenario_rows
        if normalize_label(row.get("speaker") or "") and normalize_label(row.get("speaker") or "") not in speakers
    })
    speaker_fallback = translate_units(unknown_speakers, terms, args.cache.with_name("full_translation_speaker_cache_ko.json")) if unknown_speakers else {}
    translated = translate_units(work_units, terms, args.cache)

    scenario_translations: dict[str, str] = {}
    for row in scenario_rows:
        prefix, body = split_cache[row["id"]]
        body_ko = normalize_machine_terms(translated[body])
        if prefix is None:
            value = body_ko
        elif prefix == "":
            value = f"「{body_ko}」"
        elif prefix == "--":
            value = f"--{body_ko}--"
        elif prefix == "---":
            value = f"---「{body_ko}」"
        else:
            value = f"{speaker_korean(prefix, speakers, speaker_fallback)}「{body_ko}」"
        scenario_translations[row["id"]] = value

    battle_translations: dict[str, str] = {}
    for row in battle_rows:
        _, body = split_cache[row["id"]]
        body_ko = normalize_machine_terms(translated[body])
        battle_translations[row["id"]] = f"「{body_ko}」" if row["japanese"].startswith("「") and row["japanese"].endswith("」") else body_ko

    menu_translations = {source: normalize_machine_terms(translated[source]) for source in menu_strings}
    output = {
        "format": "SSRW complete Korean translation v1",
        "reference": str(args.reference.resolve()),
        "translation_engine": "Google public Japanese-to-Korean endpoint with protected local glossary",
        "source_counts": {
            "scenario_records": len(scenario_rows),
            "battle_records": len(battle_rows),
            "menu_unique_strings": len(menu_strings),
            "scenario_unique_bodies": len(set(split_cache[row["id"]][1] for row in scenario_rows)),
            "battle_unique_bodies": len(set(split_cache[row["id"]][1] for row in battle_rows)),
        },
        "speaker_fallback_count": len(speaker_fallback),
        "scenario": scenario_translations,
        "battle": battle_translations,
        "menu": menu_translations,
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["source_counts"], ensure_ascii=False, indent=2))
    print(f"wrote {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
