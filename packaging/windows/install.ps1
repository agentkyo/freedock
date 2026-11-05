<#
  FreeDock installer for Windows 10/11.

  Installs into %LOCALAPPDATA%\FreeDock\venv and runs through the signed pythonw.exe from
  python.org - no unsigned .exe, so it works with Smart App Control turned on.
  Creates Start-menu and Desktop shortcuts. Re-run to update.

  Usage (PowerShell):
    irm https://raw.githubusercontent.com/agentkyo/freedock/main/packaging/windows/install.ps1 | iex
#>
$ErrorActionPreference = "Stop"
$repo = "https://github.com/agentkyo/freedock"
$root = Join-Path $env:LOCALAPPDATA "FreeDock"
$venv = Join-Path $root "venv"

function Say($t) { Write-Host "==> $t" -ForegroundColor Cyan }

# 1. Python 3.11+ (python.org build: its pythonw.exe is signed)
$py = $null
foreach ($cand in @("py -3", "python")) {
    try {
        $v = & ([scriptblock]::Create("$cand -c `"import sys;print(sys.version_info>=(3,11))`"")) 2>$null
        if ($v -eq "True") { $py = $cand; break }
    } catch {}
}
if (-not $py) {
    Say "Installing Python 3.13 with winget"
    winget install -e --id Python.Python.3.13 --scope user --accept-source-agreements --accept-package-agreements | Out-Null
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
    $py = "py -3"
}

# 2. venv + package
Say "Creating $venv"
New-Item -ItemType Directory -Force $root | Out-Null
& ([scriptblock]::Create("$py -m venv `"$venv`""))
$pip = Join-Path $venv "Scripts\python.exe"
& $pip -m pip install -q --upgrade pip
$src = Resolve-Path (Join-Path $PSScriptRoot "..\..") -ErrorAction SilentlyContinue
if ($src -and (Test-Path (Join-Path $src "pyproject.toml"))) {
    Say "Installing FreeDock from $src"
    & $pip -m pip install -q "$src"
} else {
    Say "Installing FreeDock from GitHub"
    & $pip -m pip install -q "git+$repo"
}

# 3. Shortcuts that launch the signed pythonw.exe
$pyw = Join-Path $venv "Scripts\pythonw.exe"
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "FreeDock.lnk"))
    $lnk.TargetPath = $pyw
    $lnk.Arguments = "-m freedock"
    $lnk.WorkingDirectory = $root
    $lnk.Description = "FreeDock - MiraBox N4"
    $lnk.Save()
}

# 4. The vendor app must not run at the same time (both would fight over the screen)
if (Get-Process StreamDock -ErrorAction SilentlyContinue) {
    Write-Host "The official StreamDock app is running. Close it (tray icon > Exit) before using FreeDock." -ForegroundColor Yellow
}

Say "Done! Open FreeDock from the Start menu. Enable 'Start with the computer' in Settings."
