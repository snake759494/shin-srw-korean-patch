# easy-apply

릴리스의 `shin-srw-korean-<버전>-easy-apply.zip` 을 구성하는 원본입니다.

| 파일 | 역할 |
|---|---|
| `한글패치 적용하기.bat` | ASCII 런처(더블클릭). PowerShell 실행 정책을 우회해 `apply.ps1` 만 실행합니다 |
| `apply.ps1` | 실제 적용 엔진(UTF-8 BOM). SHA-256 검증 → xdelta 적용 → 결과 검증 → `.cue` 생성 |
| `사용법 - 먼저 읽어주세요.txt` | 배포용 안내문 |
| `xdelta3-정보.txt` | 제3자 도구 고지 |

`apply.ps1` 과 안내문은 **UTF-8 BOM** 이어야 합니다. Windows PowerShell 5.1 은 BOM 없는 UTF-8 을
ANSI 로 읽어 한글 메시지가 전부 깨집니다.

## xdelta.exe 를 커밋하지 않는 이유

배포 zip 에는 위 파일들과 함께 `xdelta.exe`(제3자 오픈소스 도구)와 해당 버전의 `.xdelta` 패치가
들어갑니다. `xdelta.exe` 는 라이선스 표기를 분리하기 위해 저장소에 커밋하지 않으므로,
`make_release.py` 가 만든 zip 에 **공개 전에 직접 넣어야 합니다.**

```bash
py -3.14 make_release.py --version v1.0.11 --disc "…(Track 1).bin" \
    --xdelta "C:\tools\xdelta3.exe" --bundle-xdelta "C:\tools\xdelta3.exe"
# --bundle-xdelta 를 주면 easy-apply zip에 xdelta.exe가 함께 들어갑니다.
```

xdelta3 바이너리는 <https://github.com/jmacd/xdelta/releases> 에서 받을 수 있습니다.

## 버전을 올릴 때

`apply.ps1` 상단의 상수 5개를 새 값으로 바꾸세요.

```powershell
$patch     = ... 'shin-srw-korean-vX.Y.Z.xdelta'
$OUTNAME   = 'Shin Super Robot Taisen Korean vX.Y.Z (Track 1).bin'
$CUENAME   = 'Shin Super Robot Taisen Korean vX.Y.Z.cue'
$EXP_OUT   = <새 결과 SHA-256>
$EXP_PATCH = <새 패치 SHA-256>
```

`$EXP_SRC` 와 `$EXP_TRK2` 는 원본 디스크의 값이라 바뀌지 않습니다.
