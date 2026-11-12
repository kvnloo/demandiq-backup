<#
DemandIQ on this Windows PC for the free beta: no Docker, no admin, no open ports.
Run from the checkout as yourself (not "Run as administrator"):

    powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1

Re-run after every `git pull`: it stops the server, reinstalls, rebuilds, restarts.
The way in from the internet is the shared Cloudflare Tunnel (trafficiq repo, deploy/tunnel). Steps: deploy\windows\README.md.
Same recipe as TrafficOpsIQ deploy/windows.
#>
param(
    [int]$Port = 8320,
    [string]$TaskName = 'DemandIQ'
)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$AppHome = Join-Path $env:LOCALAPPDATA 'demandiq'
$Venv = Join-Path $AppHome 'venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
New-Item -ItemType Directory -Force "$AppHome\data", "$AppHome\logs" | Out-Null

function Check($what) { if ($LASTEXITCODE) { throw "$what failed (exit $LASTEXITCODE)" } }

# End the running copy first (run.py, then its server), found by command line because
# the venv's python.exe is a launcher that starts the real interpreter as a child.
# Windows will not let pip replace files a live server has open.
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
foreach ($pattern in "*$PSScriptRoot\run.py*--port $Port", "*uvicorn*$Repo\backend*--port $Port") {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like $pattern } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Write-Host '1/4 Packages'
if (-not (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\oschwartz10612.Poppler_*" -ErrorAction SilentlyContinue)) {
    # Poppler reads uploaded count-sheet PDFs (pdf2image); run.py puts it on PATH.
    winget install --id oschwartz10612.Poppler -e --scope user --accept-source-agreements --accept-package-agreements; Check 'Poppler install'
}
if (-not (Test-Path $Py)) { py -3.12 -m venv $Venv; Check 'Creating the 3.12 venv (winget install Python.Python.3.12)' }
& $Py -m pip install -q --upgrade pip; Check 'pip upgrade'
& $Py -m pip install -q -r "$Repo\backend\requirements.txt" -r "$Repo\backend\requirements-forecasting.txt"; Check 'pip install'

Write-Host '2/4 Frontend build'
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Node.js is missing: winget install OpenJS.NodeJS.LTS, then open a new window' }
Push-Location "$Repo\frontend"
try { npm ci --no-audit --no-fund; Check 'npm ci'; npm run build; Check 'Frontend build' } finally { Pop-Location }

Write-Host '3/4 Password'
$EnvFile = Join-Path $AppHome 'app.env'
if (-not (Test-Path $EnvFile)) {
    $plain = $env:APP_PASSWORD
    if (-not $plain) {
        $secure = Read-Host 'Choose the beta password testers will type (APP_PASSWORD)' -AsSecureString
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    }
    if (-not $plain) { throw 'The password cannot be empty' }
    # Lives in your own AppData, which only your account can read.
    Set-Content -Path $EnvFile -Value "APP_PASSWORD=$plain" -Encoding UTF8
}

Write-Host '4/4 Scheduled task (start at logon, restart on failure)'
# Same shape as the Hermes_Gateway task: your account, not elevated, no time limit.
# conhost --headless runs a console program with no window.
$action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless `"$Py`" `"$PSScriptRoot\run.py`" --port $Port" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT30S'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

$ok = $false
foreach ($i in 1..90) {
    try { Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { Start-Sleep 1 }
}
if (-not $ok) { throw "The server did not answer on port $Port. Log: $AppHome\logs\server.log" }
Write-Host "Server up: http://127.0.0.1:$Port  (data in $AppHome\data)"
