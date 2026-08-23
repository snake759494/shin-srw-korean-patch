#!/usr/bin/env python3
"""Build the Korean opening-scene translation using the variable-length text path."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from build_ssrw_expansion_test import (
    SCENARIO_INDEX,
    EXE_LBA,
    patch_iso_metadata_and_append,
    rebuild_expanded_scedata,
)
from build_ssrw_screenshot_korean_test import (
    count_wide_usage,
    encode_text,
    load_or_extend_mapping,
    ordered_hangul,
    patch_font,
    patch_menu,
    patch_scenario,
    sha256,
)
from extract_ssrw_japanese_text import Codec


OPENING_OVERRIDES = {
    "SCE-001-0004": "장교「이 에너지 반응은 뭐지..\n발생원은 어디냐!」",
    "SCE-001-0005": "병사「예! 지금 조사 중입니다」",
    "SCE-001-0006": "장교「보통이 아니군, 이 양은..\n서둘러!」",
    "SCE-001-0007": "병사「화성입니다!\n화성의 콜로니가 폭발하고 있습니다」",
    "SCE-001-0008": "장교「바보 같은!?\n콜로니가 그렇게 쉽게 폭발할 리가 있나!\n잘 확인해!」",
    "SCE-001-0009": "병사「틀림없습니다!\n이, 이건!?」",
    "SCE-001-0010": "장교「무슨 일이지?」",
    "SCE-001-0011": "병사「콜로니가 공격받고 있습니다!」",
    "SCE-001-0012": "장교「뭐라고!? 공격이라고!?」",
    "SCE-001-0013": "병사「예, 빔포에 의해 차례차례\n파괴되고 있습니다!」",
    "SCE-001-0014": "장교「빔포라고? 마, 말도 안 돼..!?」",
    "SCE-001-0015": "병사「믿을 수 없는 질량의 빔이\n미확인 비행물체에서 발사되고 있습니다」",
    "SCE-001-0016": "장교「미확인 비행물체라고!?」",
    "SCE-001-0017": "병사「화성 콜로니군은 소멸했습니다」",
    "SCE-001-0018": "장교「내가 보고하러 가겠다!\n미확인 비행물체에서 눈을 떼지 마라」",
    "SCE-001-0019": "병사「예!」",
    "SCE-001-0022": "---「늦었나...\n조금만 시간이 더 있었다면 이쪽 전력을\n결집할 수 있었을 텐데...\n예상은 했지만 이 정도의 힘이라니.....\n발버둥 쳐 봐야 소용없다는 건가?」",
    "SCE-001-0023": "---「하지만,\n항복을 받아 줄까요?」",
    "SCE-001-0024": "---「글쎄, 모르겠군.\n하지만 우리에게 선택의 여지는 없다.<WAIT>\n작은 가능성이라도 거기에 걸 수밖에 없어.<WAIT>\n아무리 비열한 대우를 받더라도\n시간을 벌어야 한다.\n놈들의 지식과 기술을 훔칠 시간을 말이다」",
    "SCE-001-0026": "---「그것이 지구 인류에게 남겨진,\n단 하나의 선택지다」",
    "SCE-001-0028": "---「이렇게 된 이상\n방어 행동은 인류에게 해가 될 뿐,\n아무런 의미도 없다」",
    "SCE-001-0029": "---「하지만, 지구는...」",
    "SCE-001-0030": "---「그래, 끝까지 싸우겠지.<WAIT>\n그러면 인류는 완전히 멸망한다....\n그런 꼴을 당하게 둘 수는 없다.\n놈을 불러라」",
    "SCE-001-0033": "---「잔스칼 제국에 복수하고 싶은\n자네 마음은 이해하고 있다.\n하지만 지금은 인류를 위해\n지구로 내려가 주지 않겠나」",
    "SCE-001-0034": "---「알겠습니다」",
    "SCE-001-0035": "병사「정체불명의 비행물체, 접근 중!」",
    "SCE-001-0036": "장교「정체불명?\n이 시대에 정체불명의 기체라니 있을 수 없다.\n서둘러 확인해!」",
    "SCE-001-0037": "병사「여기는 지구방위군 극동 지부다.\n소속과 목적지를 밝혀라」",
    "SCE-001-0039": "병사「여기는 지구방위군 극동 지부다!\n...안 되겠습니다. 응답이 없습니다!」",
    "SCE-001-0040": "장교「뭐라고! 응답하지 않는다고!?\n현재 위치는?」",
    "SCE-001-0041": "병사「현재 위치,\n북위 30도, 동경 140도.\n곧장 북상하고 있습니다」",
    "SCE-001-0042": "장교「오가사와라 부근이군.\n이대로 가면 오토리섬 부근에 상륙하겠어.\n자네는 장관에게 보고해」",
    "SCE-001-0043": "병사「예」",
    "SCE-001-0045": "리리나「앗!?.....사람?\n....전투복?...군인이야?\n.....누군가를 불러야 해」",
    "SCE-001-0046": "히이로「....으으...」",
    "SCE-001-0047": "리리나「!?\n아직 어린애잖아...」",
    "SCE-001-0049": "히이로「윽...」",
    "SCE-001-0050": "리리나「가만히 있어.\n구급대가 왔어」",
    "SCE-001-0051": "히이로「!?」<WAIT>\n벌떡!<WAIT>\n하아, 하아, 하아...<WAIT>히이로「...봤나?」",
    "SCE-001-0052": "리리나「어, 뭘?」",
    "SCE-001-0053": "히이로「.......\n.......너를 죽인다」",
    "SCE-001-0054": "리리나「...뭐야... 이 사람」",
    "SCE-001-0057": "구급대원「부상자는 어디 있지!\n앗!? 무, 무슨 짓이야?!\n윽....\n기, 기다려!\n이봐!! 구급차를 돌려줘!」",
    "SCE-001-0059": "--저는..\n저는 리리나 도리안\n........당신은?--",
    "SCE-001-0060": "직원「남쪽 바다에서 정체불명의 비행물체\n접근 중!!」",
    "SCE-001-0061": "하마구치 박사「적의 공격이다!\n즉시 방어 태세에 들어가라!」",
    "SCE-001-0062": "하마구치 박사「배리어를 펼쳐라!」<WAIT>하마구치 박사「볼테스 팀!\n그대로 적과 맞붙으면\n틀림없이 패배한다!」",
    "SCE-001-0063": "켄이치「패배한다고!?」",
    "SCE-001-0064": "하마구치 박사「그렇다.\n볼트 인을 해야 이길 수 있다!」",
    "SCE-001-0065": "미츠요「먼저 1호기를 중심으로 V자 대형을\n만들어라!」<WAIT>미츠요「V 투게더!!」",
    "SCE-001-0066": "켄이치「V 투게더!!」<WAIT>켄이치「V 투게더 OK!!」",
    "SCE-001-0067": "미츠요「전원 빨간 버튼을 눌러라!」<WAIT>미츠요「레츠! 볼트 인!!」",
    "SCE-001-0068": "켄이치「레츠! 볼트!」",
    "SCE-001-0069": "켄이치「안 돼. 실패다」",
    "SCE-001-0070": "미츠요「힘내렴, 켄이치.\n다시 한번, 처음부터!」",
    "SCE-001-0071": "히요시「우와아!」",
    "SCE-001-0072": "하마구치 박사「미, 미츠요 박사!?\n무슨 짓을 하려는 거요!!」",
    "SCE-001-0074": "켄이치「어머니!?」",
    "SCE-001-0075": "다이지로「아아...」",
    "SCE-001-0076": "히요시「어머니!」",
    "SCE-001-0078": "켄이치「돌아와!!」",
    "SCE-001-0079": "미츠요「....여보!!」",
    "SCE-001-0080": "미츠요「켄이치! 다이지로! 히요시!」",
    "SCE-001-0081": "켄이치「어머니이이이!!」",
    "SCE-001-0083": "히요시「...으..으으윽...」",
    "SCE-001-0084": "메구미「...아..아..」",
    "SCE-001-0085": "켄이치「모두! 다시 한번 합체다!!」",
    "SCE-001-0086": "다이지로「체스토, 가자!!」",
    "SCE-001-0087": "켄이치「V 투게더!!」\n켄이치「레츠! 볼트!」",
    "SCE-001-0088": "잇페이「인!!」",
    "SCE-001-0089": "메구미「인!!」",
    "SCE-001-0090": "다이지로「인!!」",
    "SCE-001-0091": "히요시「인!!」",
    "SCE-001-0092": "켄이치「보오오오올테에에에스\n파아아아아아이브!!」"
}


def expand_names(
    compact: dict[str, str], allowed_hangul: set[str]
) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    names = (
        ("리리「", "리리나「"),
        ("히로「", "히이로「"),
        ("히「", "히이로「"),
        ("박사「", "하마구치 박사「"),
        ("켄「", "켄이치「"),
        ("미츠「", "미츠요「"),
        ("히요「", "히요시「"),
        ("다이「", "다이지로「"),
        ("메구「", "메구미「"),
        ("메「", "메구미「"),
        ("잇페「", "잇페이「")
    )
    output = dict(compact)
    changes = []
    for record_id, value in output.items():
        changed = value
        applied = []
        for short, full in names:
            count = changed.count(short)
            if count:
                changed = changed.replace(short, full)
                applied.append({"short": short[:-1], "full": full[:-1], "count": count})
        if applied:
            changes.append({"id": record_id, "changes": applied})
        output[record_id] = changed
    skipped_overrides = []
    for record_id, override in OPENING_OVERRIDES.items():
        missing = sorted({
            character for character in override
            if "가" <= character <= "힣" and character not in allowed_hangul
        })
        if missing:
            skipped_overrides.append({
                "id": record_id,
                "missing_hangul": missing,
                "reason": "현재 안전하게 확보된 284개 슬롯 밖의 글자이며, 미번역 일본어 슬롯을 침범하지 않도록 보류",
            })
            continue
        output[record_id] = override
    return output, changes, skipped_overrides


def main() -> int:
    parser = __import__("argparse").ArgumentParser(description=__doc__)
    parser.add_argument("--track", type=Path, default=Path("Shin Super Robot Taisen (Track 1).bin"))
    parser.add_argument("--track2", type=Path, default=Path("Shin Super Robot Taisen (Track 2).bin"))
    parser.add_argument("--exe", type=Path, default=Path("extracted/SLPS_005.50"))
    parser.add_argument("--scedata", type=Path, default=Path("extracted/SCEDATA.BIN"))
    parser.add_argument("--bdf", type=Path, default=Path(__file__).resolve().parent.parent.parent / "font" / "Galmuri14.bdf")
    parser.add_argument("--output-dir", type=Path, default=Path("korean_translation_opening"))
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    mapping_path = args.output_dir / "hangul_mapping.json"
    pristine_mapping = Path("korean_patch/hangul_mapping.json")
    if not pristine_mapping.exists():
        raise FileNotFoundError(pristine_mapping)
    shutil.copyfile(pristine_mapping, mapping_path)

    compact_doc = json.loads(Path("screenshot_translation_ko.json").read_text(encoding="utf-8"))
    allowed_hangul = {
        row["character"] for row in json.loads(mapping_path.read_text(encoding="utf-8"))["entries"]
    }
    translations, name_changes, skipped_overrides = expand_names(
        compact_doc["translations"], allowed_hangul
    )
    (Path("opening_translation_ko.json")).write_text(json.dumps({
        "source": compact_doc.get("source"),
        "reference": "translation_reference_ko.json",
        "scenario": SCENARIO_INDEX,
        "range": [4, 92],
        "control_markup": compact_doc.get("control_markup"),
        "translation_status": "translated_opening_scene",
        "font_slot_policy": "원문 사용 슬롯을 침범하지 않는 범위에서만 확장 표기를 적용",
        "skipped_natural_language_overrides": skipped_overrides,
        "translations": translations,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    mapping_doc = json.loads(Path("text_extracted/ssrw_japanese_font_mapping.json").read_text(encoding="utf-8"))
    codec = Codec(mapping_doc)
    usage = count_wide_usage([
        Path("text_extracted/ssrw_scenario_dialogue.json"),
        Path("text_extracted/ssrw_battle_dialogue.json"),
        Path("text_extracted/ssrw_menu_text.json"),
    ])
    existing = json.loads(mapping_path.read_text(encoding="utf-8"))["entries"]
    required = [row["character"] for row in existing]
    for character in ordered_hangul(list(translations.values())):
        if character not in required:
            required.append(character)
    hangul = load_or_extend_mapping(mapping_path, required, usage)
    if any(usage[index] for index in hangul.values()):
        raise ValueError("translation mapping consumed a glyph used by original text")

    source_exe = args.exe.read_bytes()
    source_scedata = args.scedata.read_bytes()
    menu_exe, menu_applied = patch_menu(source_exe, codec, hangul)
    patched_exe, _ = patch_font(menu_exe, args.bdf, hangul)
    baseline_scedata, _, _ = patch_scenario(source_scedata, compact_doc["translations"], codec, hangul)
    expanded_scedata, scenario_report, applied = rebuild_expanded_scedata(
        baseline_scedata, translations, codec, hangul
    )

    extracted_dir = args.output_dir / "extracted"
    extracted_dir.mkdir(exist_ok=True)
    (extracted_dir / args.exe.name).write_bytes(patched_exe)
    (extracted_dir / args.scedata.name).write_bytes(expanded_scedata)
    (args.output_dir / "translation_applied.json").write_text(json.dumps({
        "reference": "translation_reference_ko.json",
        "speaker_name_normalization": name_changes,
        "skipped_natural_language_overrides": skipped_overrides,
        "dialogue_records": applied,
        "menu_fields": menu_applied,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    base = "Shin Super Robot Taisen Korean Opening Translation"
    output_track = args.output_dir / f"{base} (Track 1).bin"
    shutil.copyfile(args.track, output_track)
    iso_report = patch_iso_metadata_and_append(
        output_track, args.track, patched_exe, source_exe, expanded_scedata
    )
    output_track2 = args.output_dir / f"{base} (Track 2).bin"
    shutil.copyfile(args.track2, output_track2)
    cue = args.output_dir / f"{base}.cue"
    cue.write_text(
        f'FILE "{output_track.name}" BINARY\n'
        "  TRACK 01 MODE2/2352\n"
        "    INDEX 01 00:00:00\n"
        f'FILE "{output_track2.name}" BINARY\n'
        "  TRACK 02 AUDIO\n"
        "    INDEX 01 00:00:00\n",
        encoding="ascii",
    )

    report = {
        "build": "variable-length Korean opening-scene translation",
        "reference": "translation_reference_ko.json",
        "translated_range": {"scenario": SCENARIO_INDEX, "first_record": 4, "last_record": 92},
        "translated_record_count": len(applied),
        "hangul_mapping_count": len(hangul),
        "all_hangul_slots_unused_by_original_text": True,
        "scenario_and_archive_growth": scenario_report,
        "iso_and_track_growth": iso_report,
        "sha256": {
            output_track.name: sha256(output_track.read_bytes()),
            output_track2.name: sha256(output_track2.read_bytes()),
            cue.name: sha256(cue.read_bytes()),
            "expanded_SCEDATA.BIN": sha256(expanded_scedata),
            "patched_SLPS_005.50": sha256(patched_exe),
        },
    }
    (args.output_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
