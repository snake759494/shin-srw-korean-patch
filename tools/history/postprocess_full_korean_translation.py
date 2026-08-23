#!/usr/bin/env python3
"""Apply the final SRW terminology, control-code, and residual-kana pass."""

from __future__ import annotations

import json
import re
from pathlib import Path


RESIDUAL_JAPANESE = {
    "ああれぇーっ": "으아아아악",
    "ああれぇぇぇ": "으아아아악",
    "な、なんばしちょっと！ああ": "뭐, 뭘 하는 거야! 아아..",
    "なんばしちょっと": "뭘 하는 거야",
    "へへへーんだ": "헤헤, 메롱이다",
    "ハハッハッハッハッハッッ": "하하하하하하!",
    "このスパボロットを作": "이 슈퍼 보로트를 만들",
    "すげぇ": "대단하네",
    "やれはせん": "하게 두지 않겠다",
    "いかん": "안 되겠군",
    "びびる": "쫄다",
    "まぇ": "뭐야",
    "鈴": "스즈",
    "兜": "카부토",
    "孃": "아가씨",
    "卑怯よ": "비겁해",
    "ング": "닝",
    "ーーーーーーーーーーーーーーーーーーーーーーーーー": "으아아아아아아아아아아아아아아아아아아아아!",
    "ーーーーーーー": "으아아아아아아아!",
    "ぁ": "아",
    "ぉ": "오",
    "ぅ": "우",
    "ぇ": "에",
    "ぃ": "이",
    "へ": "헤",
    "ッ": "!",
    "っ": "!",
    "ィ": "이",
    "ー": "-",
    "な": "나",
    "あ": "아",
}

KOREAN_NORMALIZATIONS = {
    "오오쿠라 마룡대": "대공마룡대",
    "대공 마룡": "대공마룡",
    "오카장관": "오카 장관",
    "하마구치박사": "하마구치 박사",
    "고메즈": "고메스",
    "모빌스-츠": "모빌슈트",
    "네오.지온": "네오지온",
    "네오. 지온": "네오지온",
    "정신 명령어": "정신 커맨드",
    "정신 명령": "정신 커맨드",
    "전체지도": "전체 지도",
    "부대표": "부대표",
    "모노럴": "모노",
    "전멸 조건": "전멸 조건",
    "파일럿 !!": "파일럿!!",
    "빔-반응": "빔 반응",
    "에너지-반응": "에너지 반응",
    "마징가-Z": "마징가 Z",
    "슈퍼 보로트를": "슈퍼 보로트를",
}

MENU_TRANSLATIONS = {
    "地上": "지상",
    "モノラル": "모노",
    "ステレオ": "스테레오",
    "分離": "분리",
    "よろしいですか": "괜찮습니까?",
    "システム": "시스템",
    "部隊表": "부대표",
    "移動": "이동",
    "自分の命中率が": "자신의 명중률이",
    "精神": "정신",
    "精神ポイント": "정신 포인트",
    "能力": "능력",
    "全体マップ": "전체 지도",
    "精神検索": "정신 검색",
    "命令": "명령",
    "作戦目的": "작전 목적",
    "精神使用": "정신 사용",
    "音楽再生タイプの変更": "음악 재생 타입 변경",
    "行動終了していないユニットが": "행동을 종료하지 않은 유닛이",
    "ユニット能力": "유닛 능력",
    "パイロット能力": "파일럿 능력",
    "武器性能": "무기 성능",
    "システム設定": "시스템 설정",
    "サウンド": "사운드",
    "特殊操作": "특수 조작",
    "ボタン設定": "버튼 설정",
    "勝利条件": "승리 조건",
    "敗北条件": "패배 조건",
}

# The first scenario contains a special prologue record with a binary event
# prefix. Its text area must retain the original record allocation because
# the prefix is interpreted by the opening-event handler rather than by the
# ordinary variable-length dialogue reader.
SPECIAL_SCENARIO_OVERRIDES = {
    "SCE-000-0001": (
        "지구를 오염시킨 인류는\n"
        "우주로 이주했다..\n"
        "새 시대에 익숙해졌지만\n"
        "그러나 인류는\n"
        "이 우주에서도\n"
        "전쟁을 반복하고 있었다"
    ),
}


def clean_text(value: str) -> str:
    output = value
    output = (
        output.replace("·", "・")
        .replace("、", ",")
        .replace("！", "!")
        .replace("…", "...")
        .replace("”", "」")
        .replace('"', "'")
        .replace("~", "-")
        .replace("\u200b", "")
    )
    output = output.translate(str.maketrans({chr(code): chr(code - 32) for code in range(ord("a"), ord("z") + 1)}))
    # Restore malformed control tags produced by the translation endpoint.
    output = re.sub(
        r"<F([89A-Fa-f])\s*:\s*([0-9A-Fa-f]{2,4})>",
        lambda match: f"<F{match.group(1).upper()}:{match.group(2).upper()}>",
        output,
    )
    for source, korean in sorted(RESIDUAL_JAPANESE.items(), key=lambda pair: len(pair[0]), reverse=True):
        output = output.replace(source, korean)
    for source, korean in sorted(KOREAN_NORMALIZATIONS.items(), key=lambda pair: len(pair[0]), reverse=True):
        output = output.replace(source, korean)
    output = output.replace("ZZ", "")
    return output


def main() -> int:
    source_path = Path("full_translation_ko_v2.json")
    output_path = Path("full_translation_ko.json")
    document = json.loads(source_path.read_text(encoding="utf-8"))
    for section in ("scenario", "battle", "menu"):
        document[section] = {key: clean_text(value) for key, value in document[section].items()}

    # Keep the already reviewed opening scene rather than replacing it with
    # generic machine prose.  These records are part of the complete table.
    opening = json.loads(Path("opening_translation_ko.json").read_text(encoding="utf-8"))
    document["scenario"].update(opening["translations"])
    document["scenario"].update(SPECIAL_SCENARIO_OVERRIDES)
    document["menu"].update(MENU_TRANSLATIONS)
    document["postprocess"] = {
        "residual_kana_pass": True,
        "control_tag_normalization": True,
        "opening_scene_reviewed_records": len(opening["translations"]),
        "special_fixed_scenario_records": len(SPECIAL_SCENARIO_OVERRIDES),
        "menu_reviewed_strings": len(MENU_TRANSLATIONS),
    }
    output_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
