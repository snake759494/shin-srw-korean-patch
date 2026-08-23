# data/

번역 데이터와, 디스크에서 다시 만들 수 없는 파생물입니다.
`setup_workspace.py` 가 이 파일들을 `work/` 안의 정해진 자리로 복사합니다.

## 번역

| 파일 | 내용 |
|---|---|
| `full_translation_ko.json` | 시나리오 8,427 · 전투 5,355 · 메뉴 28 레코드. 빌드 필수 |
| `remaining_translation_ko.json` | EXE 문자열 풀(파일럿·유닛·무기·시리즈·BGM·시스템 메시지)의 한국어 |
| `fixed_label_translation_ko.json` | 고정폭 라벨과 SYSMSG. 원문 대조가 어긋나면 빌드가 멈춥니다 |
| `speaker_fix_ko.json` | 시나리오 1의 화자 이름 복원 |
| `screenshot_translation_ko.json` | 오프닝 고정 슬롯 |
| `pilotdic_ko.json` / `robotdic_ko.json` | 파일럿·로봇 백과 |
| `tighten_src.json` | 가장 빡빡한 시나리오 멤버용 축약 번역 |

## 번역 기준

| 파일 | 내용 |
|---|---|
| `ssrw_glossary_ko.json` | 용어집. 고유명사 표기 정책 |
| `translation_reference_ko.json` | 등장 작품·인물·관계 정리 |

## 파생물 (디스크에서 재생성 불가)

| 파일 | 왜 커밋하나 |
|---|---|
| `hangul_mapping.json` | 한글 글리프를 게임 폰트 슬롯에 배정한 시드 표. 빌드가 여기서 시작해 필요한 글자를 채웁니다 |
| `ssrw_japanese_font_mapping.json` | 글리프 인덱스 → 문자 표(1바이트 256 / 2바이트 1536). 자매작 디스크와 미공개 OCR 도구로 만들어 재실행할 수 없습니다 |
| `ssrw_japanese_font_mapping.tsv` | 위 표의 사람이 읽는 형태 |
| `extracted_manifest.tsv` | 원본 디스크 75개 엔트리의 경로·LBA·크기·SHA-256. 추출 결과 검증에 씁니다 |

## 여기 없는 것

게임의 일본어 스크립트 전문(`ssrw_scenario_dialogue.json` 등 약 45 MB)은 커밋하지 않습니다.
`setup_workspace.py` 가 각자의 디스크에서 다시 뽑습니다.

`fixed_label_translation_ko.json` · `pilotdic_ko.json` · `robotdic_ko.json` 에는 대조용 일본어 원문이
남아 있습니다. 빌드가 조회 키로 쓰기 때문에 제거할 수 없습니다 — [NOTICE.md](../NOTICE.md) 참고.
