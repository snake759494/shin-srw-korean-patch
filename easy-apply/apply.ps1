#requires -version 3
# 신 슈퍼로봇대전 한글패치 v1.0.0 적용 엔진
# 이 스크립트는 "한글패치 적용하기.bat" 이 자동으로 실행합니다.
# (직접 실행하려면 원본 Track 1 .bin 을 인자로 넘기거나 같은 폴더에 두세요.)

$ErrorActionPreference = 'Stop'

$root   = $PSScriptRoot
$xdelta = Join-Path $root 'xdelta.exe'
$patch  = Join-Path $root 'shin-srw-korean-v1.0.0.xdelta'

$T1NAME  = 'Shin Super Robot Taisen (Track 1).bin'
$OUTNAME = 'Shin Super Robot Taisen Korean v1.0.0 (Track 1).bin'
$CUENAME = 'Shin Super Robot Taisen Korean v1.0.0.cue'

$EXP_SRC   = 'ef06dcf085fcccdc4617c2efed01f8b90d1dfd72f67e7cb325623eccb2514915'
$EXP_OUT   = '1e313d2c20a7b23100c90aa0fbc30961a96bb44bcd0b82f94861cd7246d8741b'
$EXP_PATCH = '51336e22955b95d7e36fd801e76fbf4ca725a5bda9dde9fdd26aecb3fa339cf5'
$EXP_TRK2  = '2fbf5a94ffc8b475741529c4a95d580c937ca37db31db227e0d6c7a917a1e95f'

function Get-Sha256([string]$p) {
    return (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower()
}
function Close-Window([int]$code) {
    Write-Host ''
    Write-Host '  이 창을 닫으려면 아무 키나 누르세요...' -ForegroundColor DarkGray
    exit $code
}
function Fail([string]$msg) {
    Write-Host ''
    Write-Host "  [오류] $msg" -ForegroundColor Red
    Close-Window 1
}

try {
    Write-Host ''
    Write-Host '============================================================'
    Write-Host '   신 슈퍼로봇대전 한글패치 v1.0.0'
    Write-Host '============================================================'
    Write-Host ''

    if (-not (Test-Path -LiteralPath $xdelta)) { Fail "xdelta.exe 가 없습니다. 압축을 푼 폴더의 파일을 모두 한곳에 두세요." }
    if (-not (Test-Path -LiteralPath $patch))  { Fail "shin-srw-korean-v1.0.0.xdelta 가 없습니다." }

    # --- 원본 Track 1 찾기: 드래그앤드롭 인자 > 스크립트 폴더 > 현재 폴더 ---
    $src = $null
    if ($args.Count -ge 1 -and $args[0] -and (Test-Path -LiteralPath $args[0])) {
        $src = (Resolve-Path -LiteralPath $args[0]).Path
    }
    elseif (Test-Path -LiteralPath (Join-Path $root $T1NAME)) {
        $src = (Resolve-Path -LiteralPath (Join-Path $root $T1NAME)).Path
    }
    elseif (Test-Path -LiteralPath (Join-Path (Get-Location).Path $T1NAME)) {
        $src = (Resolve-Path -LiteralPath (Join-Path (Get-Location).Path $T1NAME)).Path
    }
    if (-not $src) {
        Write-Host "  원본 게임 파일을 찾을 수 없습니다:" -ForegroundColor Yellow
        Write-Host "     $T1NAME"
        Write-Host ''
        Write-Host "   방법 1) 원본 Track 1, Track 2 를 이 폴더에 복사한 뒤 다시 실행"
        Write-Host "   방법 2) 원본 Track 1 .bin 을 '한글패치 적용하기.bat' 아이콘 위로 끌어다 놓기"
        Close-Window 1
    }

    $srcdir = Split-Path -Parent $src
    $out = Join-Path $srcdir $OUTNAME
    $cue = Join-Path $srcdir $CUENAME

    Write-Host "  원본: $src"
    Write-Host ''
    Write-Host '  [1/5] 패치 파일 검증...'
    if ((Get-Sha256 $patch) -ne $EXP_PATCH) { Fail "패치 파일이 손상되었습니다. 다시 내려받으세요." }

    Write-Host '  [2/5] 원본 디스크 검증... (수십 초 걸립니다)'
    $sh = Get-Sha256 $src
    if ($sh -ne $EXP_SRC) {
        Fail ("지원하지 않는 원본입니다 (SHA-256 불일치).`n" +
              "         일본판 신 슈퍼로봇대전(SLPS-00550)을 MODE2/2352 로 뜬`n" +
              "         Track 1 .bin 이 맞는지 확인하세요.`n" +
              "         현재값: $sh")
    }

    Write-Host '  [3/5] 오디오 트랙 확인...'
    $t2 = $null
    foreach ($cand in @(Get-ChildItem -LiteralPath $srcdir -Filter '*Track 2*.bin' -File -ErrorAction SilentlyContinue)) {
        if ((Get-Sha256 $cand.FullName) -eq $EXP_TRK2) { $t2 = $cand.Name; break }
    }
    if (-not $t2) {
        Write-Host '        원본 Track 2 를 찾지 못했습니다. 음악이 나오지 않을 수 있습니다.' -ForegroundColor Yellow
        $t2 = 'Shin Super Robot Taisen (Track 2).bin'
    } else {
        Write-Host "        $t2"
    }

    $needPatch = $true
    if (Test-Path -LiteralPath $out) {
        if ((Get-Sha256 $out) -eq $EXP_OUT) {
            Write-Host '  이미 패치가 적용되어 있습니다. CUE 파일만 새로 만듭니다.'
            $needPatch = $false
        } else {
            Remove-Item -LiteralPath $out -Force
        }
    }

    if ($needPatch) {
        Write-Host '  [4/5] 한글패치 적용 중... (1~2분 소요, 원본보다 약 0.5MB 커집니다)'
        & $xdelta -d -f -s $src $patch $out
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
            Fail "xdelta 적용이 실패했습니다 (종료 코드 $LASTEXITCODE)."
        }
        Write-Host '  [5/5] 결과 검증...'
        $oh = Get-Sha256 $out
        if ($oh -ne $EXP_OUT) {
            Remove-Item -LiteralPath $out -Force
            Fail ("결과 검증 실패 (SHA-256 불일치). 출력 파일을 삭제했습니다.`n         현재값: $oh")
        }
    }

    $cueLines = @(
        ('FILE "{0}" BINARY' -f $OUTNAME),
        '  TRACK 01 MODE2/2352',
        '    INDEX 01 00:00:00',
        ('FILE "{0}" BINARY' -f $t2),
        '  TRACK 02 AUDIO',
        '    INDEX 01 00:00:00'
    )
    Set-Content -LiteralPath $cue -Value $cueLines -Encoding ascii

    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host '   완료되었습니다!' -ForegroundColor Green
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host ''
    Write-Host '  에뮬레이터(DuckStation 등)에서 아래 CUE 파일을 여세요:'
    Write-Host ''
    Write-Host "     $cue" -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  * 원본 파일은 그대로 보존됩니다.'
    Write-Host '  * 반드시 .cue 파일로 여세요 (.bin 을 직접 열지 마세요).'
    Close-Window 0
}
catch {
    Fail $_.Exception.Message
}
