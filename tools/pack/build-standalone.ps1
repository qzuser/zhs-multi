# 打包免安装版：一个不需要装 Python 的 zip
#
#   pwsh -File tools\pack\build-standalone.ps1
#
# 干的事：
#   1. 把 tools\*.py 复制到 build\stage\tools（_recon 之类的探查脚本不进包）
#   2. PyInstaller 把 app/ + zhs 打成一个 zhs-multi.exe（onefile，自带解释器）
#   3. csc 编译一键菜单（身边的 zhs-multi.exe 会被它自动认出来）
#   4. 攒成 dist\zhs-multi-standalone-<版本>\ 再压成 zip
#
# 需要：项目自己的 venv（..\.venv-zhs）里装了 pyinstaller，以及 Windows 自带的 csc.exe

param(
    [string]$Version = "1.0.0",
    [string]$VenvPython = "",
    [switch]$NoZip,
    [switch]$KeepState
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)

$here = $PSScriptRoot
$root = Split-Path -Parent (Split-Path -Parent $here)   # 项目根目录
$build = Join-Path $root "build"
$dist = Join-Path $root "dist"
$stageTools = Join-Path $build "stage\tools"
$pkgName = "zhs-multi-standalone-$Version"
$pkg = Join-Path $dist $pkgName

Write-Host "项目目录 : $root"

# ---------------------------------------------------------------- 解释器
if (-not $VenvPython) {
    $VenvPython = Join-Path (Split-Path -Parent $root) ".venv-zhs\Scripts\python.exe"
}
if (-not (Test-Path $VenvPython)) {
    throw "找不到 venv 里的 python：$VenvPython（用 -VenvPython 指定）"
}
& $VenvPython -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "$VenvPython 里没装 pyinstaller，先跑：`n  & '$VenvPython' -m pip install pyinstaller"
}
Write-Host "解释器   : $VenvPython"

# ---------------------------------------------------------------- 1. stage
Write-Host ""
Write-Host "[1/5] 准备要打进 exe 的 tools ..."
if (Test-Path (Join-Path $build "stage")) {
    Remove-Item -Recurse -Force (Join-Path $build "stage")
}
New-Item -ItemType Directory -Force -Path $stageTools | Out-Null
Get-ChildItem (Join-Path $root "tools") -File -Filter *.py | Copy-Item -Destination $stageTools
$staged = (Get-ChildItem $stageTools -File | ForEach-Object { $_.Name }) -join ", "
Write-Host "      $staged"

# ---------------------------------------------------------------- 2. exe
Write-Host ""
Write-Host "[2/5] PyInstaller 打 zhs-multi.exe（onefile，可能要几分钟）..."
$zhsSrc = Split-Path -Parent $root
$pyArgs = @(
    "-m", "PyInstaller", "--noconfirm", "--onefile", "--console", "--name", "zhs-multi",
    "--paths", $root,
    "--paths", (Join-Path $zhsSrc "zhs"),
    "--collect-submodules", "zhs",
    "--collect-submodules", "app",
    "--copy-metadata", "zhs",
    "--copy-metadata", "httpx",
    "--copy-metadata", "pydantic",
    "--copy-metadata", "loguru",
    "--add-data", "$stageTools;tools",
    "--distpath", $dist,
    "--workpath", $build,
    "--specpath", $build,
    (Join-Path $root "packaging\zhs_multi_main.py")
)
# 外部程序的日志走 stderr，直接 2>&1 会被 $ErrorActionPreference=Stop 当成终止错误，所以落到文件里
$pyiLog = Join-Path $build "pyinstaller.log"
$eap = $ErrorActionPreference
$ErrorActionPreference = "Continue"   # 否则 stderr 的第一行就会被当成终止错误
& $VenvPython @pyArgs *> $pyiLog
$rc = $LASTEXITCODE
$ErrorActionPreference = $eap
Get-Content $pyiLog -Tail 6 | ForEach-Object { Write-Host "      $_" }
if ($rc -ne 0) {
    throw "PyInstaller 失败（exit $rc），完整日志：$pyiLog"
}
Write-Host "      -> $dist\zhs-multi.exe"

# ---------------------------------------------------------------- 3. launcher
Write-Host ""
Write-Host "[3/5] 编译一键菜单 launcher ..."
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path $csc)) {
    throw "找不到 csc.exe（需要 .NET Framework 4.x）"
}
$launcherOut = Join-Path $build "zhs-multi-launcher.exe"
$cscLog = Join-Path $build "csc.log"
$eap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $csc /nologo /target:exe /out:$launcherOut /codepage:65001 /optimize+ (Join-Path $root "tools\launcher\ZhsLauncher.cs") *> $cscLog
$rc = $LASTEXITCODE
$ErrorActionPreference = $eap
if ($rc -ne 0) {
    Get-Content $cscLog | ForEach-Object { Write-Host "      $_" }
    throw "csc 失败（exit $rc）"
}
Write-Host "      -> $launcherOut"

# ---------------------------------------------------------------- 4. 攒包
Write-Host ""
Write-Host "[4/5] 攒免安装目录 ..."
if (Test-Path $pkg) { Remove-Item -Recurse -Force $pkg }
New-Item -ItemType Directory -Force -Path $pkg | Out-Null

Copy-Item (Join-Path $dist "zhs-multi.exe") (Join-Path $pkg "zhs-multi.exe")
Copy-Item $launcherOut (Join-Path $pkg "一键刷课.exe")
Copy-Item (Join-Path $root "config.ini") (Join-Path $pkg "config.ini")

$readme = @"
zhs-multi 免安装版 v$Version
========================================

这个文件夹里的东西够用了，**不需要装 Python**。

双击  一键刷课.exe
      出中文菜单：开刷 / 无人值守 / 看状态 / 扫码 / 体检 / 演练 / 点验证码。
      10 秒不操作默认走「看状态」，不会误开刷。

第一次用会要你扫码：菜单选 4，或者直接开刷（会自动弹二维码）。
二维码图片落在本文件夹的 state\qrcode.png，用智慧树 APP 扫。

也可以命令行用：
      一键刷课.exe status
      一键刷课.exe run -c 1 -j 1
      一键刷课.exe daemon --solve-captcha --every 120 --hours 6

文件说明
----------------------------------------
  一键刷课.exe    菜单入口（可以改名叫别的，不影响）
  zhs-multi.exe   真正的程序，被 一键刷课.exe 自动调用。
                  想直接命令行跑也行：zhs-multi.exe status
  config.ini      配置：线程数、倍速、阈值、课程序号……
  state\          第一次跑之后生成：cookies.json 登录态、qrcode.png 二维码
  logs\           日志：每轮刷了啥、平台回了什么

注意
----------------------------------------
  * state\cookies.json 等于你的账号，别发给别人。
  * 验证码自动点选（菜单 7）在这个版本里需要本机另外装 Python + playwright；
    没装就走「人工点一次」的流程，一样能继续刷。
  * 想换账号：删掉 state\cookies.json，重新扫码。
  * 杀软 / SmartScreen 可能提示未签名程序，选「仍要运行」。本程序不联网上报任何东西，
    只跟智慧树自己的接口说话。

许可：GPL-3.0（沿用上游 zhs）。
"@
[System.IO.File]::WriteAllText((Join-Path $pkg "使用说明.txt"), $readme, (New-Object System.Text.UTF8Encoding($true)))

# 可选：把本机的登录态带进去（调试用，正常发布别带）
if ($KeepState) {
    $st = Join-Path $root "state"
    if (Test-Path $st) {
        Copy-Item $st (Join-Path $pkg "state") -Recurse -Force
        Write-Host "      [warn] 连 state\ 一起打进去了（-KeepState）"
    }
}

# ---------------------------------------------------------------- 5. 签名 + 压缩
Write-Host ""
Write-Host "[5/5] 签名 + 压缩 ..."
$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Select-Object -First 1
if ($cert) {
    foreach ($f in @("zhs-multi.exe", "一键刷课.exe")) {
        $full = Join-Path $pkg $f
        try {
            $sig = Set-AuthenticodeSignature -FilePath $full -Certificate $cert `
                -TimestampServer "http://timestamp.digicert.com" -HashAlgorithm SHA256 -ErrorAction Stop
            Write-Host ("      {0}  {1}" -f $f, $sig.Status)
        } catch {
            $sig = Set-AuthenticodeSignature -FilePath $full -Certificate $cert -HashAlgorithm SHA256
            Write-Host ("      {0}  {1}（没打上时间戳）" -f $f, $sig.Status)
        }
    }
} else {
    Write-Host "      没有代码签名证书，跳过签名"
}

if (-not $NoZip) {
    $zip = Join-Path $dist "$pkgName.zip"
    if (Test-Path $zip) { Remove-Item -Force $zip }
    Compress-Archive -Path $pkg -DestinationPath $zip -CompressionLevel Optimal
    $mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host "      -> $zip ($mb MB)"
}

Write-Host ""
Write-Host "完成。目录：$pkg"
Get-ChildItem $pkg | ForEach-Object {
    $size = if ($_.PSIsContainer) { "<dir>" } else { "{0:N1} MB" -f ($_.Length / 1MB) }
    Write-Host ("  {0,-20} {1}" -f $_.Name, $size)
}
