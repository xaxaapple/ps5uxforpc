# Сборка PC5 Launcher для Windows 11: PyInstaller (onedir) -> portable zip -> установщик Inno Setup.
# Запуск из любой папки:  powershell -ExecutionPolicy Bypass -File windows\build.ps1
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
$ver  = (Get-Content "$root\VERSION" -Raw).Trim()
Set-Location $PSScriptRoot
Write-Host "== PC5 Launcher ${ver}: PyInstaller"

python -m PyInstaller --noconfirm --clean --windowed --name PC5Launcher `
  --icon "assets\pc5.ico" --paths "." `
  --add-data "$root\app\web;web" `
  --add-data "pc5win\shim.js;pc5win" `
  --add-data "$root\VERSION;pc5win" `
  --collect-all webview --collect-all pythonnet --collect-all clr_loader `
  run_pc5.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }

# Необязательно: если рядом лежит папка windows\emulator с KytyPS5 (kyty_emulator.exe), кладём её в сборку как
# PC5Launcher\KytyPS5 - лаунчер найдёт её сам. Файлы для разработчиков (include, lib, 3rdparty) не копируем.
$emu = Join-Path $PSScriptRoot "emulator"
if (Test-Path (Join-Path $emu "kyty_emulator.exe")) {
  $dst = "dist\PC5Launcher\KytyPS5"
  Write-Host "== bundling KytyPS5 emulator from $emu"
  New-Item -ItemType Directory -Force -Path $dst | Out-Null
  Get-ChildItem $emu -Force | Where-Object { $_.Name -notin @("include", "lib", "3rdparty") } |
    Copy-Item -Destination $dst -Recurse -Force
}

$zip = "dist\PC5Launcher-$ver-windows-x64-portable.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "dist\PC5Launcher" -DestinationPath $zip
Write-Host "== portable: $zip"

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "$env:ProgramFiles\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) {
  Write-Host "== Inno Setup not found, installing via Chocolatey"
  choco install innosetup -y --no-progress
  $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}
& $iscc "/DAppVersion=$ver" "installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed ($LASTEXITCODE)" }
Write-Host "== installer: dist\PC5Launcher-Setup-$ver.exe"
