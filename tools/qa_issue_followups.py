"""Static regression QA for the open-issue follow-up patch.

This checks the reported scenario and battle records, their repeated copies,
the executable pools used by the status and spirit-command windows, and the
absence of superseded terminology.  It never starts an emulator.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work" / "text_extracted" / "ssrw_japanese_text.json"
TRANSLATION = ROOT / "data" / "full_translation_ko.json"
EXPECTED_COUNTS = {"scenario": 8427, "battle": 5355, "menu": 28}


SCENARIO_EXPECTED = {
    "SCE-058-0118": "브라이트「과연 잔스칼이다.\n 쉽게는, 본국에 접근시켜 주지\n 않는군. 이대로 싸워도 승산은 없다.\n 철수 신호를 내려라」",
    "SCE-058-0111": "코우지「보스, 역시 보스보로트는\n 나랑 안 맞네.\n 마징가Z를 돌려줘」",
    "SCE-058-0113": "코우지「알겠다고. 보스는 대단해\n 저런 엉터리 로봇을\n 움직일 수 있으니까」",
    "SCE-058-0114": "보스「엉터리라니, 뭐가 엉터리냐!\n 열받는군!」",
    "SCE-058-0116": "보스「그렇지.\n 낡긴 했어도.. 응?\n 누케! 어디가 낡았다는 거냐!」",
    "SCE-058-0057": "라이「류세이, 너 여기까지 어떻게 온 거냐?」",
    "SCE-058-0059": "웃소「에-엣, 그런 게 가능한 건가요?」",
    "SCE-058-0060": "라이「웃소, 이런 녀석 말을\n 그냥 그대로 믿으면 안 된다.\n 이 녀석은 엄청난 거짓말쟁이야」",
    "SCE-058-0061": "류세이「아무렇지 않게 말하는군, 라이.\n 엄청난 거짓말쟁이라니 무슨 소리야!」",
    "SCE-058-0063": "류세이「어, 하하하하.\n 뭐, 조금은 했지만..\n 그래도 엄청난 거짓말쟁이는\n 아니잖아」",
    "SCE-058-0064": "웃소(어떤 사람일까, 이 사람)",
    "SCE-058-0069": "코우지「소개 같은 건 필요 없어」",
    "SCE-058-0071": "류세이「어랏, 화내고 가 버렸네\n 내가 뭔가 한 건가」",
    "SCE-058-0074": "하야토「예, 우리 겟타 팀과\n R-1의 류세이 다테 소위는\n 오늘부터 함장님의 지휘 아래로\n 배속되었습니다.」",
    "SCE-058-0075": "브라이트「도움은 되겠군.\n 전력은 언제든지 필요하네」",
    "SCE-058-0078": "라이「진 겟타!?\n 그, 그럼 드디어 봉인을 푼 겁니까?」",
    "SCE-058-0086": "오델로「뭐라고!?\n 그럼 샤크티가 루페 시노를\n 놓아줬다는 거야?」",
    "SCE-058-0087": "수지「응, 스테이션에 가까워졌을 때\n 엄마 목소리가 들린다고.\n 나는 말렸어. 그런데..」",
    "SCE-058-0089": "워렌「울지 마, 수지」",
    "SCE-058-0093": "브라이트「이제 와서 데려오는 건 무리다.\n 어쩔 수가 없다」",
    "SCE-058-0094": "웃소「샤크티를 두고 가자고요?\n 그건, 저는 싫어요!」",
    "SCE-058-0095": "마베트「웃소 군, 샤크티는 군인이 아니야.\n 걱정하지 않아도 괜찮아」",
    "SCE-058-0097": "올리퍼「마베트 말이 맞다.\n 게다가, 어떻게 잔스칼에\n 갈 건데?」",
    "SCE-058-0099": "올리퍼「그렇게 했다간 잔스칼에\n 가기는커녕 다가가기만 해도\n 벌집이 되고 만다」",
    "SCE-058-0121": "하야토「코우지 군, 유미 교수에게서\n 마징가Z의 개조용 파츠를 받아 왔다.\n 정비사를 불러와 주지 않겠나」",
    "SCE-058-0124": "하야토「음, 카부토 쥬조 박사가\n 남기신 개발 노트의 아이디어로\n 만들어졌다더군」",
    "SCE-058-0127": "하야토「그래, 그 상자 안의 것을\n 마징가Z에 조립해 넣어 주게.\n 설명서도 들어 있을 거다」",
    "SCE-059-0029": "샤아「그렇다. 외계인의 지식과\n 기술을 흡수하려면, 시간이 좀 더\n 필요하다.<WAIT> 그때까지 사람들이 얌전히\n 있어 줬으면 좋겠는데.\n 알겠나, 나나이」",
    "SCE-059-0033": "샤아「곁에 있어 주지 않으면\n 곤란해, 나나이」",
    "SCE-059-0045": "브라이트「아뇨, 대단한 건\n 아닙니다... 알겠습니다.\n 사이드5를 조사해 보겠습니다」",
    "SCE-059-0046": "자하남「뭐, 가 준다고!?\n 그렇군, 그렇군, 와하하하하!\n 그럼, 잘 부탁하네!」",
    "SCE-059-0048": "크로노클「나를 우습게 만든 놈들은\n 그리 쉽게 놓아주지 않겠다.<WAIT> 카테지나,\n 모빌슈트 대의 지휘를 맡아라!」",
    "SCE-059-0051": "올리퍼「크로노클이군!\n 어떻게 이리도 끈질긴 놈이지」",
    "SCE-059-0098": "카테지나「너는 네 생각만으로\n 너무 앞서 나가서 주위를 못 봐」",
    "SCE-060-0004": "샤아「나는, 네오 지온의 샤아다.<WAIT> 지금부터 이야기할 것을 잘 들어\n 주기 바란다.\n 전 인류의 미래가 걸린 일이다」",
    "SCE-060-0005": "네스「샤아입니다. 샤아가 일제히 방송을\n 송출하며 이야기하고 있습니다」",
    "SCE-060-0008": "아무로「말도 안 돼! 그건 완전히\n 노예나 다름없잖아!」",
    "SCE-060-0009": "코우지「이런 놈은 살려 둘 수 없겠는데.\n 그렇지, 보스.」",
    "SCE-060-0015": "올리퍼「저기가 사이드5다」",
    "SCE-060-0027": "아야「저와 라이가 갈게요.\n 괜찮지, 라이?」",
    "SCE-060-0031": "오델로「알고 있다니까, 운석이잖아.\n 너야말로, 더미를 흩뜨리지 마라」",
    "SCE-060-0042": "아무로「오델로, 저쪽으로 접근해 줘」",
    "SCE-060-0045": "아무로「오델로, 우리가 놈들의\n 주의를 끌 테니, 화이트 아크는\n 최대한 멀리 떨어져서 라 카이람에\n 연락해라」",
    "SCE-060-0060": "닥터 헬「실험은 지금이 중요한 때다.\n 무슨 짓을 해서라도, 놈들을\n 이곳에 접근시키지 마라!」",
    "SCE-060-0061": "젝스「핫!」\n (언제 봐도 기분 나쁜 놈들이군.\n 여기서 대체 무슨 실험을\n 하고 있는 거지?)",
    "SCE-060-0063": "닥터 헬「너는 여기를 맡아 지켜라.\n 고양이 새끼 한 마리도\n 들여보내지 마라!」",
    "SCE-060-0085": "히이로「....서툴군」",
    "SCE-060-0088": "젝스「이 톨기스\n 아직 조정이 서툴군」",
    "SCE-062-0007": "류세이「합체!? 아, 아직 위험한데-\n 지금까지 성공한 적이 없어서」",
    "SCE-062-0034": "아무로「진 소령, 아야의 안색이 안 좋아 보이는데,\n 오늘은 쉬게 해 주시죠」",
    "SCE-062-0036": "라이「SRX 합체 시 쓰는 아야의\n 염동력은 상당한 것입니다.\n 지치는 것도 무리가 아닙니다」",
    "SCE-062-0046": "코우지「이봐, 보스! 따라오지 말라고!」",
    "SCE-062-0047": "보스「흥, 네 녀석이\n 보로트를 따라온 거잖아.\n 짐이나 되지 마라!」",
    "SCE-062-0048": "코우지「쳇, 말은 잘하네!」",
    "SCE-063-0004": "나나이「사이드5의 기지가\n 파괴되었다는 연락이\n 들어왔습니다」",
    "SCE-063-0094": "카테지나「너는 네 생각만으로\n 너무 앞서 나가서 주위를 못 봐」",
}


BATTLE_EXPECTED = {
    "BTT-03-10-001A74": "「탄막이 얇다!! 뭘 하고 있나!!」",
    "BTT-03-19-001B0B": "「그렇게 두지 않겠다!!」",
    "BTT-03-1E-001B73": "「좌현 탄막이 얇다! 뭘 하고 있나!?」",
    "BTT-13-0D-005ABC": "「너희 따윈-!!」",
    "BTT-18-06-00794F": "「너희 따윈-!!」",
    "BTT-39-14-0171EB": "「뭐.. 마, 말도 안 돼..!?」",
    "BTT-E1-14-0471EB": "「뭐.. 마, 말도 안 돼..!?」",
    "BTT-55-32-0205AE": "「소용없다!\n 소용없어어어어!!」",
    "BTT-DE-04-045942": "「미라쥬 드릴!!」",
    "BTT-DF-04-046130": "「대설산 던지기\n 2단 뒤집기다-!!」",
    "BTT-F4-1F-04FD90": "「이걸로 끝내자고..\n 천상천하아앗..」",
}


POOL_EXPECTED = {
    "PILOTFULL": {"ブロッホ": "브롯흐"},
    "PILOTNAME": {"ブロッホ": "브롯흐"},
    "UNITNAME": {
        "ドッゴ-ラ": "돗고라",
        "ブルッケング": "브루켕",
        "ブルッケング(KM)": "브루켕(KM)",
    },
    "WEAPON": {
        "ミラ-ジュドリル": "미라쥬 드릴",
        "大雪山おろしニ段返し": "대설산 던지기 2단 뒤집기",
    },
    "SYS": {
        "エンジェル.ハイロゥ": "엔젤 하일로",
        "エンジェルハイロゥ": "엔젤 하일로",
    },
}


MENTAL_COMMANDS = {
    9: "가속",
    10: "열혈",
    11: "필중",
    12: "번뜩임",
    14: "각성",
    17: "집중",
    29: "혼",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from all_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_strings(child)


def terminated(data: bytes, offset: int, limit: int) -> bytes | None:
    cursor = offset
    while cursor < min(limit, len(data)):
        opcode = data[cursor]
        if opcode == 0xFF:
            return data[offset:cursor + 1]
        if 0xF0 <= opcode <= 0xF5:
            cursor += 2
        elif opcode >= 0xF6:
            # These are the renderer controls used by the executable pools.
            cursor += 1 + {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1,
                            0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 1,
                            0xFE: 2}.get(opcode, 0)
        else:
            cursor += 1
    return None


def source_ids(source: dict[str, Any], section: str) -> set[str]:
    if section == "scenario":
        return {
            row["id"]
            for scenario in source[section]["scenarios"]
            for row in scenario["records"]
            if row.get("japanese")
        }
    return {row["id"] for row in source[section]["records"] if row.get("japanese")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, help="built image directory to inspect")
    args = parser.parse_args()

    source = read_json(SOURCE)
    full = read_json(TRANSLATION)
    errors: list[str] = []
    counts = {section: len(full[section]) for section in EXPECTED_COUNTS}
    if counts != EXPECTED_COUNTS:
        errors.append(f"translation counts {counts} != {EXPECTED_COUNTS}")
    for section in ("scenario", "battle"):
        if set(full[section]) != source_ids(source, section):
            errors.append(f"{section} translation IDs do not match extracted source")

    for record_id, expected in SCENARIO_EXPECTED.items():
        if full["scenario"].get(record_id) != expected:
            errors.append(f"scenario mismatch at {record_id}")
    for record_id, expected in BATTLE_EXPECTED.items():
        if full["battle"].get(record_id) != expected:
            errors.append(f"battle mismatch at {record_id}")

    for group in (
        ("SCE-058-0190", "SCE-059-0146", "SCE-063-0142", "SCE-065-0172"),
        ("SCE-058-0142", "SCE-059-0098", "SCE-063-0094", "SCE-065-0124"),
    ):
        values = {full["scenario"].get(record_id) for record_id in group}
        if len(values) != 1:
            errors.append(f"repeated scenario records disagree: {', '.join(group)}")

    remaining = read_json(ROOT / "data" / "remaining_translation_ko.json")["pools"]
    for pool, entries in POOL_EXPECTED.items():
        for japanese, expected in entries.items():
            if remaining.get(pool, {}).get(japanese) != expected:
                errors.append(f"pool mismatch at {pool}/{japanese}")

    data_documents = [
        full,
        remaining,
        read_json(ROOT / "data" / "ssrw_glossary_ko.json"),
        read_json(ROOT / "data" / "tighten_src.json"),
        read_json(ROOT / "data" / "robotdic_ko.json"),
    ]
    superseded = (
        "류마", "브로흐", "도고라", "독고라", "브루켄그",
        "엔젤하이로", "엔젤.하이로우", "천상으천하", "무.. 마",
    )
    for document in data_documents:
        for value in all_strings(document):
            for residue in superseded:
                if residue in value:
                    errors.append(f"superseded term {residue!r} remains in Korean data")
                    break

    built_runtime_checks = 0
    if args.build_dir:
        build_dir = args.build_dir if args.build_dir.is_absolute() else ROOT / args.build_dir
        exe_path = build_dir / "SLPS_005.50"
        if not exe_path.is_file():
            # build_ssrw_full_translation keeps the rebuilt ISO members under
            # an extracted/ subdirectory before reassembly.
            exe_path = build_dir / "extracted" / "SLPS_005.50"
        if not exe_path.is_file():
            errors.append(f"built EXE not found: {exe_path}")
        else:
            sys.path.insert(0, str(ROOT / "tools"))
            from build_ssrw_screenshot_korean_test import encode_text
            from extract_ssrw_japanese_text import Codec

            codec = Codec(read_json(ROOT / "data" / "ssrw_japanese_font_mapping.json"))
            hangul = {
                row["character"]: int(row["glyph_index"])
                for row in read_json(ROOT / "data" / "hangul_mapping.json")["entries"]
            }
            image = exe_path.read_bytes()

            def check_table(table: int, index: int, korean: str, arena_end: int) -> None:
                nonlocal built_runtime_checks
                slot = table + 4 * index
                target = slot + struct.unpack_from("<I", image, slot)[0]
                actual = terminated(image, target, arena_end)
                expected = encode_text(korean, codec, hangul) + b"\xFF"
                built_runtime_checks += 1
                if actual != expected:
                    errors.append(
                        f"built pool mismatch at {table:#x}[{index}]: "
                        f"expected {korean!r}"
                    )

            for index, korean in MENTAL_COMMANDS.items():
                check_table(0x703B4, index, korean, 0x72434)
            for index, korean in enumerate(("육", "우", "공")):
                check_table(0x702BC, index, korean, 0x72434)

            status = encode_text("없있", codec, hangul) + b"\xFF" * 4
            if image[0x70300:0x70300 + len(status)] != status:
                errors.append("built inline status field at 0x70300 is not 없/있")
            built_runtime_checks += 1

            fixed = read_json(ROOT / "data" / "fixed_label_translation_ko.json")["entries"]
            for identifier, entry in fixed.items():
                if entry.get("korean") not in {"지형", "정신", "정신명령"}:
                    continue
                offset = int(entry["offset"], 16)
                expected = encode_text(entry["korean"], codec, hangul)
                built_runtime_checks += 1
                if image[offset:offset + len(expected)] != expected:
                    errors.append(f"built fixed label mismatch at {identifier}")

    print(f"translation counts: {counts}")
    print(f"issue records checked: {len(SCENARIO_EXPECTED) + len(BATTLE_EXPECTED)}")
    print(f"built executable checks: {built_runtime_checks}")
    if errors:
        print("QA FAILED:")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1
    print("QA PASS: issue follow-ups, repeated records, terminology, and executable UI checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
