# 실행하지 마세요 — 기록용입니다

여기 있는 스크립트는 `data/` 의 번역 데이터가 **어떻게 만들어졌는지**를 남긴 기록입니다.
빌드 경로가 아니며, 지금 실행하면 대부분 실패하거나 완성된 번역을 되돌립니다.

## 왜 실행하면 안 되나

| 스크립트 | 실행했을 때 |
|---|---|
| `generate_full_korean_translation.py` | 기계번역 초벌 패스. `full_translation_ko.json` 을 **제자리에서 덮어씁니다.** 손질한 번역이 전부 사라집니다. |
| `merge_retrans.py` | 같은 파일을 덮어씁니다. 입력인 `retranslation_ko.json` 은 게임 원문을 담고 있어 저장소에 없습니다. |
| `apply_retrans.py` | 입력 `retrans/` 배치 파일이 저장소에 없습니다. |
| `apply_tighten.py` | 입력 `retranslation_ko.json` 이 저장소에 없습니다. |
| `rewrap_ko_lines.py` | **빌드 산출물**(`korean_translation_full_fixed/hangul_mapping.json`)을 입력으로 읽습니다. 소스가 산출물에 의존하는 순환 구조라 클린 체크아웃에서 돌지 않습니다. |
| `build_ssrw_korean_translation.py` | 초기 번역 빌더. 지금 빌드로 대체되었습니다. |
| `postprocess_full_korean_translation.py` | v2 세대 후처리. 대체되었습니다. |
| `build_ssrw_japanese_font_mapping.py` | **재실행 불가.** 자매작(컴플리트 박스)의 디스크와 미공개 OCR 도구가 있어야 합니다. 그래서 산출물인 `data/ssrw_japanese_font_mapping.json` 을 커밋합니다. |

## 그래도 남겨 둔 이유

- 번역이 기계번역 초벌에서 시작해 어떤 순서로 손질됐는지 보여줍니다.
- `rewrap_ko_lines.py` 에는 게임 렌더러의 폭 규칙(1바이트 8px, 2바이트 12px, 한 줄 216px)과
  줄바꿈 연쇄 로직이 그대로 들어 있어, 대사 배치 규칙의 사실상 명세입니다.
- `build_ssrw_japanese_font_mapping.py` 는 글리프 인덱스 → 문자 표가 어떻게 만들어졌는지에 대한
  유일한 설명입니다.

번역을 고치려면 이 스크립트들이 아니라 `data/` 의 JSON 을 직접 편집하고
`build_all.py` 를 다시 돌리세요.
