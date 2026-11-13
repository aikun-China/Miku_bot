<#
.SYNOPSIS
    构建 MikuBot Windows 一键部署包（内置便携 Python 与 uv，目标机器无需预装任何环境）。

.DESCRIPTION
    产物：deploy/dist/MikuBot-<version>-portable-win-x64.zip
    包内结构：
        MikuBot-<version>-portable-win-x64/
        ├─ runtime/python/      便携版 CPython（python-build-standalone）
        ├─ runtime/uv/uv.exe    uv 可执行文件
        ├─ 一键启动.bat         日常启动（未部署或目录被移动时自动处理）
        ├─ 首次部署.bat         用包内 uv 创建 .venv 并同步依赖
        ├─ .env.example         环境变量模板（首次部署自动复制为 .env）
        └─ 仓库已跟踪的其余源码

.PARAMETER UvVersion
    uv 版本，默认 0.11.23（与开发环境一致，保证 uv.lock 可复现）。

.PARAMETER PythonVersion
    便携 Python 版本，默认 3.12。

.PARAMETER OutDir
    输出目录，默认 deploy/dist。

.PARAMETER NoZip
    只生成目录，不压缩。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy/build_portable.ps1
#>
[CmdletBinding()]
param(
    [string]$UvVersion = '0.11.23',
    [string]$PythonVersion = '3.12',
    [string]$OutDir,
    [switch]$NoZip
)

$ErrorActionPreference = 'Stop'

$script:DeployRoot = $PSScriptRoot
$script:RepoRoot   = Split-Path -Parent $script:DeployRoot
$script:CacheDir   = Join-Path $script:DeployRoot '.cache'
if (-not $OutDir) { $OutDir = Join-Path $script:DeployRoot 'dist' }

function Write-Step { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }

# Windows PowerShell 5.1 会把原生命令的 stderr 当成终止错误，
# 因此所有外部程序统一走这个包装，只按退出码判断成败。
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @(),
        [switch]$ShowOutput
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Exe @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    $lines = @($output | ForEach-Object { "$_" })
    if ($ShowOutput) { $lines | ForEach-Object { Write-Host "    $_" } }
    return [pscustomobject]@{ ExitCode = $code; Output = $lines }
}

function Get-DirSizeMB {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    $sum = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
    if (-not $sum) { return 0 }
    return [math]::Round(($sum / 1MB), 1)
}

# ── 版本号 ────────────────────────────────────────────────────────────────
$Version = '1.0.0'
$VersionFile = Join-Path $script:RepoRoot 'version.json'
if (Test-Path -LiteralPath $VersionFile) {
    try {
        $parsed = (Get-Content -LiteralPath $VersionFile -Raw -Encoding utf8 | ConvertFrom-Json)
        if ($parsed.version) { $Version = $parsed.version }
    } catch { }
}

$PkgName = "MikuBot-$Version-portable-win-x64"
$PkgRoot = Join-Path $OutDir $PkgName
$RuntimeDir = Join-Path $PkgRoot 'runtime'

Write-Step "目标包 $PkgName"
New-Item -ItemType Directory -Force -Path $script:CacheDir, $OutDir | Out-Null
if (Test-Path -LiteralPath $PkgRoot) { Remove-Item -LiteralPath $PkgRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $PkgRoot, $RuntimeDir | Out-Null

# ── 1. uv 可执行文件 ─────────────────────────────────────────────────────
$script:UvExe = Join-Path $script:CacheDir 'uv.exe'
if (-not (Test-Path -LiteralPath $script:UvExe)) {
    Write-Step "下载 uv $UvVersion"
    $uvZip = Join-Path $script:CacheDir "uv-$UvVersion.zip"
    $uvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
    Invoke-WebRequest -Uri $uvUrl -OutFile $uvZip -UseBasicParsing
    $uvTmp = Join-Path $script:CacheDir "uv-$UvVersion-unpacked"
    if (Test-Path -LiteralPath $uvTmp) { Remove-Item -LiteralPath $uvTmp -Recurse -Force }
    Expand-Archive -LiteralPath $uvZip -DestinationPath $uvTmp -Force
    Copy-Item -LiteralPath (Join-Path $uvTmp 'uv.exe') -Destination $script:UvExe -Force
}
$uvVersionResult = Invoke-Native -Exe $script:UvExe -Arguments @('--version')
Write-Step ("uv: " + ($uvVersionResult.Output -join ' '))

# ── 2. 便携 Python ──────────────────────────────────────────────────────
$PyHome = Join-Path $script:CacheDir 'pythons'
New-Item -ItemType Directory -Force -Path $PyHome | Out-Null
$env:UV_PYTHON_INSTALL_DIR = $PyHome

function Find-CachedPython {
    param([string]$HomeDir, [string]$PyVersion)
    $dirs = Get-ChildItem -LiteralPath $HomeDir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "cpython-$PyVersion*" } |
            Sort-Object Name -Descending
    foreach ($d in $dirs) {
        $candidate = Join-Path $d.FullName 'python.exe'
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

Write-Step "准备便携 Python $PythonVersion"
$PyExe = Find-CachedPython -HomeDir $PyHome -PyVersion $PythonVersion
if (-not $PyExe) {
    $install = Invoke-Native -Exe $script:UvExe -Arguments @('python', 'install', $PythonVersion) -ShowOutput
    if ($install.ExitCode -ne 0) { throw "uv python install 失败（退出码 $($install.ExitCode)）" }
    $PyExe = Find-CachedPython -HomeDir $PyHome -PyVersion $PythonVersion
}
if (-not $PyExe) { throw "未能获取便携 Python $PythonVersion" }

$PyRoot = Split-Path -Parent $PyExe
$pyVersionResult = Invoke-Native -Exe $PyExe -Arguments @('-V')
Write-Step ("便携 Python: " + ($pyVersionResult.Output -join ' ') + "  ($PyRoot)")

# ── 3. 复制仓库已跟踪的源码 ─────────────────────────────────────────────
Write-Step '复制源码（按 git ls-files 白名单）'
$trackedResult = Invoke-Native -Exe 'git' -Arguments @('-C', $script:RepoRoot, 'ls-files')
if ($trackedResult.ExitCode -ne 0 -or -not $trackedResult.Output) {
    throw "无法用 git ls-files 枚举文件，请确认 $script:RepoRoot 是已提交的 git 仓库"
}

$srcCount = 0
# 这些文件只服务于开发者，且 start.bat / start.sh 依赖系统 PATH 里的 uv，
# 与「包内调用只指向内置运行时」的目标冲突，因此不打进部署包。
$excludeFromPackage = @('start.bat', 'start.sh', '.gitignore')
foreach ($rel in $trackedResult.Output) {
    $relNorm = $rel.Replace('\', '/')
    if ($relNorm -like 'deploy/*') { continue }
    if ($excludeFromPackage -contains $relNorm) { continue }
    $src = Join-Path $script:RepoRoot $rel
    if (-not (Test-Path -LiteralPath $src)) { continue }
    $dst = Join-Path $PkgRoot $rel
    $dstDir = Split-Path -Parent $dst
    if (-not (Test-Path -LiteralPath $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $srcCount++
}
Write-Step "源码 $srcCount 个文件"

# ── 4. 覆盖部署骨架 ─────────────────────────────────────────────────────
$PkgSkeleton = Join-Path $script:DeployRoot 'package'
if (-not (Test-Path -LiteralPath $PkgSkeleton)) { throw "缺少部署骨架目录：$PkgSkeleton" }
Write-Step '写入部署骨架'
$skelCount = 0
Get-ChildItem -LiteralPath $PkgSkeleton -Recurse -File -Force | ForEach-Object {
    $rel = $_.FullName.Substring($PkgSkeleton.Length + 1)
    $dst = Join-Path $PkgRoot $rel
    $dstDir = Split-Path -Parent $dst
    if (-not (Test-Path -LiteralPath $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
    Copy-Item -LiteralPath $_.FullName -Destination $dst -Force
    $skelCount++
}
Write-Step "骨架 $skelCount 个文件"

# ── 5. 内置运行时 ───────────────────────────────────────────────────────
Write-Step '写入运行时（便携 Python + uv）'
Copy-Item -LiteralPath $PyRoot -Destination (Join-Path $RuntimeDir 'python') -Recurse -Force
$uvDstDir = Join-Path $RuntimeDir 'uv'
New-Item -ItemType Directory -Force -Path $uvDstDir | Out-Null
Copy-Item -LiteralPath $script:UvExe -Destination (Join-Path $uvDstDir 'uv.exe') -Force

# ── 6. 校验产物 ─────────────────────────────────────────────────────────
$checkPy = Join-Path $RuntimeDir 'python\python.exe'
$checkUv = Join-Path $RuntimeDir 'uv\uv.exe'
if (-not (Test-Path -LiteralPath $checkPy)) { throw '产物缺少 runtime/python/python.exe' }
if (-not (Test-Path -LiteralPath $checkUv)) { throw '产物缺少 runtime/uv/uv.exe' }

$SizeMB = Get-DirSizeMB $PkgRoot
Write-Step "包体积 $SizeMB MB"

# ── 7. 打包 ─────────────────────────────────────────────────────────────
if (-not $NoZip) {
    Write-Step '压缩中（体积较大，请稍候）'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zipTmp = Join-Path $script:CacheDir "$PkgName.zip"
    $zipOut = Join-Path $OutDir "$PkgName.zip"
    if (Test-Path -LiteralPath $zipTmp) { Remove-Item -LiteralPath $zipTmp -Force }
    if (Test-Path -LiteralPath $zipOut) { Remove-Item -LiteralPath $zipOut -Force }
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $PkgRoot, $zipTmp, [System.IO.Compression.CompressionLevel]::Optimal, $true)
    Move-Item -LiteralPath $zipTmp -Destination $zipOut -Force
    $zipMB = [math]::Round((Get-Item -LiteralPath $zipOut).Length / 1MB, 1)
    Write-Step "已生成 $zipOut（$zipMB MB）"
}

Write-Host ''
Write-Host '构建完成：' -ForegroundColor Green
Write-Host "  目录  $PkgRoot"
Write-Host "  体积  $SizeMB MB"
if (-not $NoZip) { Write-Host "  压缩  $(Join-Path $OutDir "$PkgName.zip")" }
