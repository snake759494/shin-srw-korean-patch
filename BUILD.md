# 빌드 방법

정품 디스크에서 릴리스와 **바이트 단위로 같은** 이미지를 만드는 절차입니다.

---

## 1. 준비물

| 항목 | 조건 |
|---|---|
| Python | 3.11 이상. 개발과 검증은 **3.14** 에서 했습니다. |
| Pillow | `pip install -r requirements.txt`. 폰트 글리프를 렌더할 때 씁니다. |
| 원본 디스크 | Track 1 `.bin` (MODE2/2352, 650,139,840 바이트) + Track 2 `.bin` (32,104,800 바이트) |
| 빈 공간 | 약 1.4 GB (작업 폴더 + 결과 이미지) |
| xdelta3 | 릴리스 패치를 만들 때만 필요합니다. 빌드 자체에는 불필요합니다. |

원본 해시는 [README](README.md#원본-확인값) 에 있습니다. `setup_workspace.py` 가 자동으로 대조합니다.

---

## 2. 두 줄

```bash
py -3.14 setup_workspace.py --disc "E:\dump\Shin Super Robot Taisen (Track 1).bin"
py -3.14 build_all.py       --disc "E:\dump\Shin Super Robot Taisen (Track 1).bin"
```

Track 2 가 Track 1 과 같은 폴더에 있으면 자동으로 찾습니다. 아니면 `--track2` 로 지정하세요.

빌드는 몇 분 걸리고, 끝나면 결과 SHA-256 을 릴리스 값과 대조해 알려줍니다.

```
verifying the result ...
  size    650607888 ok
  sha256  7fb521902b964762f42d077702c46d287a01f3515bd677f1f1296466a7a27047
  matches the released build exactly.
```

결과물은 `work/korean_translation_full_fixed/` 에 `.bin` 2개와 `.cue` 로 나옵니다.

---

## 3. 각 단계가 하는 일

### `setup_workspace.py`

1. **원본 검증** — 크기와 SHA-256 을 대조합니다. 다르면 중단합니다.
2. **디스크 추출** — 빌드가 실제로 읽는 **15개 파일만** `work/extracted/` 로 꺼냅니다.
   (텍스트용 5개 + 타이틀 메뉴 그래픽 `SBDATA.BIN` + 시나리오 제목 그래픽 `MAP/SBTI0~8.DAT`)
   `SLPS_005.50`, `SCEDATA.BIN`, `BTT/BTTMES.BIN`, `PILOTDIC.BIN`, `ROBOTDIC.BIN`.
   각각 `data/extracted_manifest.tsv` 의 SHA-256 과 대조합니다. (`--all` 을 주면 69개 전부 남깁니다.)
3. **번역 데이터 배치** — `data/` 의 JSON 을 빌드가 기대하는 위치로 복사합니다.
4. **일본어 스크립트 재추출** — `tools/extract_ssrw_japanese_text.py` 로 대사 JSON 을 만듭니다.

4번이 중요합니다. 게임의 일본어 스크립트는 45 MB 짜리 퍼블리셔 저작물이라 저장소에 넣지 않고,
번역 대조에 필요할 때 **각자의 디스크에서** 다시 뽑습니다.

### `build_all.py`

`work/` 를 현재 디렉터리로 삼아 `tools/build_ssrw_full_translation.py` 를 실행합니다.
빌드 도구는 입력을 CWD 상대경로로 읽도록 쓰여 있어서, 저장소 트리를 그대로 두고
작업 폴더만 그 모양으로 꾸미는 편이 안전합니다 — 경로 리터럴을 고치다 검증된 결과가
바뀌는 일을 막습니다.

빌드가 하는 일:

- Galmuri14 를 12×12 글리프로 변환해 EXE 폰트 테이블에 삽입
- 시나리오 아카이브(`SCEDATA.BIN`)와 전투 대사(`BTTMES.BIN`)를 번역문으로 다시 채우고 재압축
- EXE 안의 문자열 풀 9종(이름·무기·시스템 메시지·시나리오 제목 등)을 재배치
- 타이틀 화면 메뉴와 시나리오 제목 화면의 타일 그래픽을 한글로 다시 그림
- 백과사전 2종 재구성
- ISO9660 디렉터리와 Mode2/Form1 섹터(EDC/ECC)를 다시 계산해 이미지 재조립

---

## 4. 릴리스 패치 만들기

```bash
py -3.14 make_release.py --version v1.0.8 \
    --disc "E:\dump\Shin Super Robot Taisen (Track 1).bin" \
    --xdelta "C:\tools\xdelta3.exe" \
    --bundle-xdelta "C:\tools\xdelta3.exe"
```

`.xdelta` 를 만든 뒤 **원본에 되돌려 적용해 결과 해시가 일치하는지 확인**하고,
일치할 때만 남깁니다. 함께 `release/SHA256SUMS_<버전>.txt` 와 간편 적용 zip 을 만듭니다.

`--bundle-xdelta` 를 지정하면 간편 적용 zip에 `xdelta.exe`가 함께 들어갑니다. xdelta는
라이선스 표기를 분리하려고 저장소에는 두지 않으며, 자세한 내용은
[`easy-apply/README.md`](easy-apply/README.md) 를 보세요.

---

## 5. 결과가 다르게 나올 때

| 증상 | 확인할 것 |
|---|---|
| `sha256` 불일치 | 원본 덤프가 다른가? `data/` 를 수정했는가? `work/` 를 지우고 1단계부터 다시. |
| `Pillow` 임포트 오류 | `pip install -r requirements.txt` |
| 추출 파일 해시 불일치 | 디스크는 통과했는데 추출이 어긋난 경우입니다. `work/` 를 지우고 다시 시도하세요. |
| 아레나 넘침 오류 | `data/remaining_translation_ko.json` 의 번역문을 늘렸을 때 납니다. EXE 문자열 풀은 크기가 고정입니다. |

`--preserve-scenario-zero` 옵션은 켜지 마세요. 기본값(꺼짐)으로 검증했고, 켜면 결과가 달라집니다.

---

## 6. `tools/history/` 에 대하여

번역이 어떻게 만들어졌는지 남겨 둔 스크립트입니다. **빌드 경로가 아니며 실행하면 안 됩니다** —
일부는 `data/full_translation_ko.json` 을 제자리에서 덮어써 완성된 번역을 되돌려 버립니다.
자세한 내용은 [`tools/history/README.md`](tools/history/README.md) 를 보세요.
