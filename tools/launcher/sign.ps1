# Rebuild + sign the one-click launcher.
#
#   powershell -ExecutionPolicy Bypass -File tools\launcher\sign.ps1
#
# Needs: Windows built-in .NET compiler (csc.exe) plus a code-signing cert with a
# private key in Cert:\CurrentUser\My. List what you have:
#   Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Select Subject
# then pass -CertMatch <part of the subject>. No cert? Just use build.ps1 instead.
#
# ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less files as ANSI,
# so Chinese comments here would show up as mojibake.

param(
    [string]$OutFile = "",
    [string]$CertMatch = "",
    [string]$TimeStamp = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$src = Join-Path $PSScriptRoot "ZhsLauncher.cs"

if (-not $OutFile) {
    # Avoid a Chinese literal here: PowerShell 5.1 reads BOM-less scripts as ANSI.
    $cands = @(Get-ChildItem $root -Filter *.exe -File)
    if ($cands.Count -eq 1) {
        $OutFile = $cands[0].Name
    } else {
        throw "cannot guess the exe name ($($cands.Count) candidates in $root) - pass -OutFile"
    }
}
$out = Join-Path $root $OutFile

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path $csc)) {
    throw "csc.exe not found - install .NET Framework 4.x"
}

Write-Host "[1/3] compiling ..."
& $csc /nologo /target:exe /out:$out /codepage:65001 /optimize+ $src
if ($LASTEXITCODE -ne 0) {
    throw "csc failed with exit code $LASTEXITCODE"
}

Write-Host "[2/3] signing ..."
$certs = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert)
if ($CertMatch) {
    $certs = @($certs | Where-Object { $_.Subject -match $CertMatch })
}
$cert = $certs | Select-Object -First 1
if (-not $cert) {
    Write-Host "available code-signing certs:"
    Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
        ForEach-Object { Write-Host "  $($_.Subject)" }
    throw "no code-signing cert selected - pass -CertMatch <part of the subject>"
}
Write-Host "      cert: $($cert.Subject)  (expires $($cert.NotAfter.ToString('yyyy-MM-dd')))"

try {
    $sig = Set-AuthenticodeSignature -FilePath $out -Certificate $cert `
        -TimestampServer $TimeStamp -HashAlgorithm SHA256 -ErrorAction Stop
} catch {
    Write-Host "      timestamp server unreachable, signing without it"
    $sig = Set-AuthenticodeSignature -FilePath $out -Certificate $cert -HashAlgorithm SHA256
}

Write-Host "[3/3] verify ..."
$v = Get-AuthenticodeSignature $out
Write-Host "      status : $($v.Status)"
Write-Host "      signer : $($v.SignerCertificate.Subject)"
if ($v.TimeStamperCertificate) {
    Write-Host "      stamped: $($v.TimeStamperCertificate.Subject)"
}
Write-Host "      file   : $out ($((Get-Item $out).Length) bytes)"

if ($v.Status -ne "Valid") {
    Write-Host ""
    Write-Host "  NOT valid: $($v.StatusMessage)"
    Write-Host "  If it says UntrustedRoot, your own cert is not trusted on this machine yet."
    Write-Host "  Export it and add it to CurrentUser\Root, or just ship the unsigned exe."
    exit 1
}
exit 0
