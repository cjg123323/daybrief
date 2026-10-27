<#
daybrief 대시보드를 Lively Wallpaper 라이브러리에 "원본 경로 링크"로 등록한다.

왜 필요한가: Lively에서 + 추가 → 파일 선택으로 넣으면 index.html이 Lively 폴더에 **복사**되어
(SaveData\wptmp\..., IsAbsolutePath=false) 매일 갱신되는 원본이 바탕화면에 반영되지 않는다.
이 스크립트는 Library\wallpapers\daybrief\LivelyInfo.json 을 만들고 FileName을 원본 절대경로로,
IsAbsolutePath를 true로 둔다. Lively는 그 파일을 직접 연다.

사용 (엔진 레포 루트에서):
  powershell -ExecutionPolicy Bypass -File scripts\install_lively.ps1 -Config ..\daybrief-me\profile.yaml
  powershell -ExecutionPolicy Bypass -File scripts\install_lively.ps1 -Uninstall

등록 후: Lively를 완전히 종료(트레이 → Exit)하고 다시 실행 → 라이브러리에서 "Daybrief" 선택.
Microsoft Store 판과 설치형(installer) 판 모두 지원. 레포를 옮기면 다시 실행할 것.
#>
param(
    [string]$Config = (Join-Path $PSScriptRoot "..\..\daybrief-me\profile.yaml"),
    [string]$Title = "Daybrief",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"

# Store 판은 AppData 쓰기가 LocalCache로 리다이렉트되므로 그쪽이 실제 위치. 없으면 설치형 경로.
$candidates = @(
    @(Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter "*LivelyWallpaper*" -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName "LocalCache\Local\Lively Wallpaper\Library" }) +
    @("$env:LOCALAPPDATA\Lively Wallpaper\Library") | Where-Object { $_ -and (Test-Path $_) }
)   # 바깥 @()가 없으면 후보가 하나일 때 문자열이 되어 [0]이 첫 글자를 집는다
if ($candidates.Count -eq 0) { throw "Lively Wallpaper 라이브러리 폴더를 찾지 못함. Lively를 한 번 실행한 뒤 다시 시도 (winget install rocksdanister.LivelyWallpaper)" }
$library = $candidates[0]
if (-not (Test-Path (Join-Path $library "wallpapers"))) { throw "라이브러리 구조가 예상과 다름: $library" }
$entry = Join-Path $library "wallpapers\daybrief"

if ($Uninstall) {
    if (Test-Path $entry) { Remove-Item -Recurse -Force $entry; Write-Output "removed $entry" } else { Write-Output "nothing to remove" }
    exit 0
}

$Config = (Resolve-Path $Config).Path
$html = Join-Path (Split-Path $Config -Parent) "dashboard\index.html"
if (-not (Test-Path $html)) { throw "not found: $html  (daybrief run 또는 daybrief render --config $Config 먼저)" }

New-Item -ItemType Directory -Force $entry | Out-Null

# 썸네일: Edge 헤드리스로 한 장 찍는다 (없어도 동작엔 지장 없음)
$thumb = $null
$edge = @("$env:ProgramFiles (x86)\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) {
    try {
        $png = Join-Path $entry "thumbnail.png"
        & $edge --headless=new --disable-gpu --hide-scrollbars --window-size=1280,720 --screenshot="$png" "file:///$($html -replace '\\','/')" 2>$null | Out-Null
        Start-Sleep -Seconds 3
        if (Test-Path $png) { $thumb = "thumbnail.png" }
    } catch { $thumb = $null }
}

$info = [ordered]@{
    AppVersion     = "2.0.0.0"
    Title          = $Title
    Thumbnail      = $thumb
    Preview        = $null
    Desc           = "daybrief daily research briefing (auto-updated by 'daybrief run')"
    Author         = "daybrief"
    License        = $null
    Contact        = "https://github.com/rocksdanister/lively"
    Type           = 1              # 1 = web (HTML)
    FileName       = $html          # 절대경로: Lively가 복사하지 않고 이 파일을 직접 연다
    Arguments      = $null
    IsAbsolutePath = $true
}
($info | ConvertTo-Json -Depth 3) | Out-File -Encoding utf8 (Join-Path $entry "LivelyInfo.json")

Write-Output "registered '$Title' in Lively library:"
Write-Output "  entry: $entry"
Write-Output "  html:  $html  (원본 링크, 복사 아님)"
Write-Output "다음: Lively 완전 종료(트레이 아이콘 → Exit) → 다시 실행 → 라이브러리에서 '$Title' 선택."
Write-Output "      이전에 파일 선택으로 넣은 복사본 항목이 있으면 라이브러리에서 지워도 됨."
