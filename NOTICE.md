# 고지

## Galmuri14

`font/Galmuri14.bdf` 는 quiple 이 제작한 [Galmuri14](https://github.com/quiple/galmuri) 글꼴입니다.

- 프로젝트 페이지: <https://quiple.dev/font/galmuri>
- 라이선스: SIL Open Font License 1.1

빌드가 이 글꼴에서 만들어 게임 폰트 테이블에 심는 12×12 비트맵, 그리고 xdelta 패치가 전달하는
그 비트맵 변경 내용에도 같은 글꼴 라이선스 조건이 적용됩니다. 전문은
[`LICENSES/Galmuri-OFL-1.1.md`](LICENSES/Galmuri-OFL-1.1.md) 에 있습니다.

## xdelta3

릴리스 zip 에 넣는 `xdelta.exe` 는 [xdelta3](https://github.com/jmacd/xdelta) 이며
Joshua P. MacDonald 및 기여자들의 저작물입니다. 수정하지 않았고, 라이선스 표기를 분리하기 위해
저장소에는 커밋하지 않습니다.

## 이 저장소에 없는 것

게임 ROM·디스크 이미지, PlayStation BIOS, 에뮬레이터, 원본 게임에서 추출한 폰트 비트맵,
게임의 일본어 스크립트 전문은 포함하지 않습니다. 빌드에 필요한 것들은 각자의 정품 디스크에서
`setup_workspace.py` 가 그때그때 추출합니다.

## 번역 데이터 안의 일본어

다음 파일에는 대조·조회용으로 게임 원문 일본어가 일부 남아 있습니다.

- `data/pilotdic_ko.json`, `data/robotdic_ko.json` — 백과사전 원문(항목당 수백 자)
- `data/fixed_label_translation_ko.json` — 고정폭 라벨의 원문(짧은 라벨)

빌드가 이 원문을 조회 키와 대조 원본으로 쓰기 때문에 제거하면 빌드가 실패합니다.
게임 바이트 덤프가 아니라 번역 작업 산출물의 일부로 포함되어 있습니다.

## 기계번역 이력

초벌 번역은 기계번역으로 시작해 전량 손질했습니다. 그 과정의 스크립트는
`tools/history/` 에 기록으로 남겨 두었고, 중간 산출물(기계번역 응답 캐시 등)은
저장소에 포함하지 않습니다.

## 비공식 프로젝트

이 프로젝트는 비공식 비상업 팬 번역이며 Banpresto, Winkysoft, Sony Interactive Entertainment
또는 그 밖의 권리자와 제휴하거나 승인받은 프로젝트가 아닙니다. 각 상표와 저작물의 권리는
해당 권리자에게 있습니다.

Galmuri 글꼴 자산을 제외한 코드와 문서에는 프로젝트 전체 라이선스를 아직 지정하지 않았습니다.
