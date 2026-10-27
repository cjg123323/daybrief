<#
daybrief를 Windows 작업 스케줄러에 등록한다 (로그온 시 + 매일 지정 시각).
3단계(바탕화면)의 간이판: 지금은 `daybrief run`만 돌리고 로그를 남긴다.

사용 (엔진 레포 루트에서):
  powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -Config ..\daybrief-me\profile.yaml
  powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -Uninstall

- 경로는 등록 시점의 절대경로로 고정된다. 레포를 옮기면 다시 등록할 것.
- ANTHROPIC_API_KEY는 사용자 환경변수에 있어야 한다 (작업은 현재 사용자 권한으로 실행).
- 로그: <profile.yaml 폴더>\data\logs\<날짜>.log
#>
param(
    [string]$Config = (Join-Path $PSScriptRoot "..\..\daybrief-me\profile.yaml"),
    [string]$TaskName = "daybrief",
    [string]$DailyAt = "07:00",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed task '$TaskName'"
    exit 0
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$exe = Join-Path $root ".venv\Scripts\daybrief.exe"
if (-not (Test-Path $exe)) { throw "not found: $exe  (.venv를 먼저 만들고 pip install -e . 할 것)" }
$Config = (Resolve-Path $Config).Path
$logDir = Join-Path (Split-Path $Config -Parent) "data\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null

# cmd.exe 경유: stdout/stderr를 날짜별 로그 파일에 덧붙임 (%DATE%의 '/'는 '-'로 치환)
$cmd = "/c `"`"$exe`" run --config `"$Config`" >> `"$logDir\%DATE:/=-%.log`" 2>&1`""
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd -WorkingDirectory $root
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME),
    (New-ScheduledTaskTrigger -Daily -At $DailyAt)
)
# 켜져 있지 않았으면 다음 기회에 실행, 30분 제한, 이미 돌고 있으면 새 인스턴스 무시
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Force | Out-Null

Write-Output "registered task '$TaskName': at logon + daily $DailyAt"
Write-Output "  exe:    $exe"
Write-Output "  config: $Config"
Write-Output "  logs:   $logDir"
Write-Output "  dashboard: $(Join-Path (Split-Path $Config -Parent) "dashboard/index.html")  (Lively Wallpaper에 등록, README 참고)"
Write-Output "지금 한 번 돌려보기:  Start-ScheduledTask -TaskName $TaskName"
