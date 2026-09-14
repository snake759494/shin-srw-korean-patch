# 신 슈퍼로봇대전 한글패치

PlayStation 게임 **신 슈퍼로봇대전**(新スーパーロボット大戦, SLPS-00550)의 한국어 번역 패치입니다.
번역 데이터와 빌드 도구 전체를 담고 있으며, 정품 디스크만 있으면 패치 이미지를 직접 재현할 수 있습니다.

> **게임 데이터는 이 저장소에 없습니다.** 배포물은 원본 디스크에 적용하는 xdelta 바이너리 패치뿐입니다.
> 본인이 소유한 디스크에서 직접 추출한 이미지가 필요합니다.

현재 최신 릴리스는 **v1.0.13**이며, 초반부터 최종 시나리오까지 원문 대조 검수를 수행해
오타·띄어쓰기·인물명·용어를 정리하고 상태창의 동적 한자 값도 한글화했습니다. 무기 목록의
인라인 UI 글리프까지 원본과 대조해 보존했으며, v1.0.2는
부팅 검은 화면 문제로 철회되었습니다.

v1.0.13은 전투 중 검은 화면 프리징(라이딘·가이킹·19화 데이비드 공격 시)과 전투창의
파일럿 이름이 `산ウお`처럼 일본어로 보이던 문제를 근본적으로 고쳤습니다. 두 문제의 원인은
v1.0.8이 도입한 파일럿명 압축 코드 페이지 하나였습니다. 전투창은 이름 레코드를 스크립트로
실행하며 0xEC 이상을 명령어로 읽는데, 압축 페이지가 `드`·`아`를 그 자리에 놓아 해석기가
무한 루프에 빠졌고, 이 창은 전각 창이라 한 바이트 글자가 패치가 손대지 않은 전각 뱅크의
가나로 그려졌습니다. 이제 파일럿 이름은 다른 텍스트와 같은 16×16 한글로 저장되며, 새 검사
도구 `tools/qa_battle_names.py`가 완성 이미지에서 위험한 이름이 0개임을 확인합니다.
자세한 내용은 [docs/RELEASE_NOTES_v1.0.13.md](docs/RELEASE_NOTES_v1.0.13.md)에 있습니다.
그 밖에 유닛 능력창 `지형`, 대사·인터미션 문장, 정신 커맨드 표기 등의 제보를 원문 레코드와
대조해 반영했고, 시나리오 제목 그래픽과 타이틀 메뉴 4항목도 한글화되어 있습니다.

> **저장소 이전 안내** — 이전 배포 저장소(`snake7594/…`)는 계정 문제로 접근할 수 없게 되어
> 이 저장소로 옮겼습니다. `docs/` 의 지난 릴리스 노트에 적힌 이슈 번호는 이전 저장소 기준이며
> 여기서는 열리지 않습니다. 새 제보는 이 저장소의 Issues 에 남겨 주세요.

---

## 무엇이 한글화되었나

| 영역 | 상태 |
|---|---|
| 시나리오 대사 | 8,427 레코드 |
| 전투 대사 | 5,355 레코드 |
| 메뉴·시스템 메시지 | 전체 |
| 파일럿·로봇·무기 이름 | 전체 |
| 시나리오 제목 | 전체 |
| 정신기·지형·특수능력 설명 | 전체 |
| 파일럿 백과 / 로봇 백과 | 347 / 242 레코드 |
| 타이틀 화면 메뉴 *(그래픽)* | 4개 항목 |
| 시나리오 제목 화면 *(그래픽)* | 접두어 40 · 부제 69 |

본문 폰트는 [Galmuri14](https://quiple.dev/font/galmuri)를 12×12 글리프로 변환해 게임의 폰트 테이블에 심었습니다.
타이틀·시나리오 제목 화면은 텍스트가 아니라 타일 그래픽이라, 원본 서체를 실측해 한글로 다시 그렸습니다.
게임 렌더러가 한 글자당 8px(1바이트)·12px(2바이트)로 폭을 계산하므로, 모든 대사는 한 줄 216px 제한에 맞춰 재배치했습니다.

---

## 패치 적용 방법

### 준비물

- 정품 **신 슈퍼로봇대전**(SLPS-00550) 디스크를 **MODE2/2352(raw)** 로 추출한 `.bin` 2개
- PS1 에뮬레이터 (DuckStation 권장) 와 PS1 BIOS

MODE1/2048 로 뜬 이미지나 압축 포맷(`.chd`, `.pbp`, `.ecm`)은 그대로 쓸 수 없습니다.
`.chd` 를 쓰고 계시다면 `chdman extractcd` 로 `.bin`/`.cue` 를 먼저 만드세요.

### 원본 확인값

패치는 아래 이미지 하나에만 적용됩니다. 적용 전에 확인하세요.

**Track 1 (데이터) — 650,139,840 바이트**

| 알고리즘 | 값 |
|---|---|
| MD5 | `7aee4811d8cac9f439cec85722f5dc42` |
| SHA-1 | `2ddaa91b82cc3e748a6b6f91eac94d8be9e76efa` |
| SHA-256 | `ef06dcf085fcccdc4617c2efed01f8b90d1dfd72f67e7cb325623eccb2514915` |
| CRC32 | `D2B5BC5F` |

**Track 2 (오디오) — 32,104,800 바이트** · 패치가 건드리지 않습니다

| 알고리즘 | 값 |
|---|---|
| MD5 | `65aea234c174ee35fb574d981fe3fc4f` |
| SHA-1 | `bd1fde88c2b79e3cf8821c969373460d51f3155a` |
| SHA-256 | `2fbf5a94ffc8b475741529c4a95d580c937ca37db31db227e0d6c7a917a1e95f` |
| CRC32 | `9B94EA54` |

Windows 에서 확인하려면:

```powershell
Get-FileHash "Shin Super Robot Taisen (Track 1).bin" -Algorithm MD5
```

macOS / Linux:

```bash
md5sum "Shin Super Robot Taisen (Track 1).bin"
```

### 방법 1 — 간편 적용 (Windows, 권장)

1. 릴리스에서 `shin-srw-korean-v1.0.13-easy-apply.zip` 을 받아 압축을 풉니다.
2. 원본 `.bin` 2개를 압축 푼 폴더에 복사합니다.
3. **`한글패치 적용하기.bat`** 을 더블클릭합니다.
4. 원본 검증 → 패치 → 결과 검증이 자동으로 진행됩니다(1~2분).
5. 같은 폴더에 생긴 **`.cue`** 파일을 에뮬레이터에서 엽니다.

원본 `.bin` 을 `.bat` 아이콘 위로 끌어다 놓아도 됩니다.

### 방법 2 — xdelta 직접 실행

[xdelta3](https://github.com/jmacd/xdelta) 이 필요합니다.

```bash
xdelta3 -d -s "Shin Super Robot Taisen (Track 1).bin" \
        shin-srw-korean-v1.0.13.xdelta \
        "Shin Super Robot Taisen Korean v1.0.13 (Track 1).bin"
```

그 다음 아래 내용으로 `.cue` 파일을 만듭니다. Track 2 파일명은 실제 파일명과 같아야 합니다.

```
FILE "Shin Super Robot Taisen Korean v1.0.13 (Track 1).bin" BINARY
  TRACK 01 MODE2/2352
    INDEX 01 00:00:00
FILE "Shin Super Robot Taisen (Track 2).bin" BINARY
  TRACK 02 AUDIO
    INDEX 00 00:00:00
    INDEX 01 00:02:00
```

Track 2 파일에는 정품 디스크와 같은 150섹터(2초) 프리갭이 이미 들어 있으므로
`PREGAP`을 추가하지 말고 위처럼 `INDEX 00`과 `INDEX 01`을 사용해야 합니다.
이렇게 해야 `NULL.DA`가 가리키는 데이터 트랙 끝+150섹터와 오디오 Track 2의
실제 시작점이 SwanStation에서도 일치합니다.

### 결과 확인값

패치된 Track 1 은 원본보다 **468,048 바이트(199섹터) 큽니다.** 번역문이 원본보다 길어 일부 데이터를 디스크 뒤쪽으로 옮겼기 때문이며, 정상입니다.

**패치 결과 Track 1 — 650,589,072 바이트**

| 알고리즘 | 값 |
|---|---|
| MD5 | `3138ee33b8c17c2373b479c2627b0f55` |
| SHA-1 | `69c222a79c7c38a2d9486376d83ceba79053063d` |
| SHA-256 | `527c7b4c7205063cd7b9a51417a8a51d1d5d10ee20f71d1db000c6e570311ba7` |
| CRC32 | `DF8C9159` |

**패치 파일 `shin-srw-korean-v1.0.13.xdelta` — 609,894 바이트**

| 알고리즘 | 값 |
|---|---|
| SHA-256 | `0b911a7a94120a16259211e95d195f6bac45b0d2f5b4816d579fe4aae24280e0` |

### 문제 해결

| 증상 | 원인과 해결 |
|---|---|
| `지원하지 않는 원본입니다 (SHA-256 불일치)` | 다른 판이거나 MODE1/2048 덤프입니다. MODE2/2352 로 다시 추출하세요. |
| 음악이 안 나온다 | `.bin` 을 직접 열었거나 Track 2 가 `.cue` 와 다른 폴더에 있습니다. |
| 글자가 깨진다 | 패치가 덜 적용된 이미지입니다. 결과 SHA-256 을 확인하세요. |
| xdelta 가 `XD3_INVALID_INPUT` 을 낸다 | 원본이 아닌 파일에 적용했거나 패치 파일이 손상됐습니다. |

---

## 직접 빌드하기

정품 디스크와 Python 3.11+ 만 있으면 릴리스와 **바이트 단위로 동일한** 이미지를 만들 수 있습니다.

```bash
git clone https://github.com/snake759494/shin-srw-korean-patch
cd shin-srw-korean-patch
py -3.14 -m pip install -r requirements.txt

py -3.14 setup_workspace.py --disc "…\Shin Super Robot Taisen (Track 1).bin"
py -3.14 build_all.py       --disc "…\Shin Super Robot Taisen (Track 1).bin"
```

`build_all.py` 는 마지막에 결과 SHA-256 을 위 표의 값과 대조합니다. 자세한 설명은 [BUILD.md](BUILD.md) 를 보세요.

---

## 저장소 구성

```
tools/          빌드 도구 (ISO 재조립, 폰트 삽입, 텍스트 추출, LZ 재압축)
tools/history/  번역이 만들어진 과정의 기록 — 실행하지 마세요
data/           번역 데이터와 글리프 매핑
font/           Galmuri14 (SIL OFL 1.1)
easy-apply/     릴리스 zip 에 들어가는 간편 적용 스크립트
docs/           릴리스 노트
```

`work/`, `extracted/`, `text_extracted/`, 빌드 산출물은 모두 `.gitignore` 대상입니다.
이 저장소에는 게임에서 나온 바이트가 들어가지 않습니다.

---

## 권리 관계

- 이 프로젝트는 **비공식 비상업 팬 번역**이며, Banpresto · Winkysoft · Sony Interactive Entertainment 및 그 밖의 권리자와 아무 관계가 없습니다.
- 게임 ROM, BIOS, 에뮬레이터, `xdelta.exe` 는 이 저장소에 포함되지 않습니다.
- 폰트 `font/Galmuri14.bdf` 는 quiple 이 만든 Galmuri14 이며 SIL Open Font License 1.1 을 따릅니다. 전문은 [`LICENSES/Galmuri-OFL-1.1.md`](LICENSES/Galmuri-OFL-1.1.md) 에 있습니다.
- 그 밖의 고지는 [NOTICE.md](NOTICE.md) 를 보세요.

같은 방식으로 작업한 자매 프로젝트: srwcb-korean-patch (제2차 · 제3차 · EX 컴플리트 박스) — 새 계정으로 이전 준비 중
