# Build the one-click launcher without signing it.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\launcher\build.ps1
#   powershell ... -File tools\launcher\build.ps1 -OutFile my.exe
#
# Needs nothing but the Windows built-in .NET compiler (csc.exe, .NET Framework 4.x).
# Want the exe signed as well? Use sign.ps1 instead (needs a code-signing cert).
#
# ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less files as ANSI,
# so non-ASCII comments here would show up as mojibake.

param(
    [string]$OutFile = "zhs-multi-launcher.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$src = Join-Path $PSScriptRoot "ZhsLauncher.cs"
$out = Join-Path $root $OutFile

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path $csc)) {
    throw "csc.exe not found - install .NET Framework 4.x"
}

Write-Host "[1/2] compiling ..."
& $csc /nologo /target:exe /out:$out /codepage:65001 /optimize+ $src
if ($LASTEXITCODE -ne 0) {
    throw "csc failed with exit code $LASTEXITCODE"
}

Write-Host "[2/2] done"
Write-Host "      file : $out ($((Get-Item $out).Length) bytes)"
Write-Host ""
Write-Host "Rename it to whatever you like, e.g. the Chinese name used in the README."
exit 0
