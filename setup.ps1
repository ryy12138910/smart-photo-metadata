param(
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectDir '.venv\Scripts\python.exe'
$venvPythonw = Join-Path $projectDir '.venv\Scripts\pythonw.exe'
$requirementsPath = Join-Path $projectDir 'requirements.txt'
$requirementsStamp = Join-Path $projectDir '.venv\.requirements.sha256'
$runtimeDir = Join-Path $projectDir 'runtime'
$downloadDir = Join-Path $projectDir '.downloads'
$ocrInstaller = Join-Path $downloadDir 'Umi-OCR_Rapid_v2.1.5.7z.exe'
$ocrUrl = 'https://github.com/hiroi-sora/Umi-OCR/releases/download/v2.1.5/Umi-OCR_Rapid_v2.1.5.7z.exe'
$ocrSha256 = '659c55896c32a5e019dc7bde1713d0e5c73186a2c653bed84c4480fa1795b722'

Set-Location -LiteralPath $projectDir

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host '正在创建程序运行环境...'
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        & $pythonCommand.Source -3 -m venv (Join-Path $projectDir '.venv')
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw '未找到 Python。请安装 Python 3.10 或更高版本，或从 GitHub Releases 下载便携包。'
        }
        & $pythonCommand.Source -m venv (Join-Path $projectDir '.venv')
    }
}

$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
$installedHash = if (Test-Path -LiteralPath $requirementsStamp) {
    (Get-Content -LiteralPath $requirementsStamp -Raw).Trim()
}
else {
    ''
}
if ($installedHash -ne $requirementsHash) {
    Write-Host '正在安装程序依赖...'
    & $venvPython -m pip install --disable-pip-version-check -r $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw '程序依赖安装失败，请检查网络后重试。'
    }
    Set-Content -LiteralPath $requirementsStamp -Value $requirementsHash -Encoding ascii
}

$ocrExe = Get-ChildItem -LiteralPath $runtimeDir -Filter 'Umi-OCR.exe' -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $ocrExe) {
    New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    Write-Host '首次使用：正在下载离线 OCR 组件（约 100 MB）...'
    Invoke-WebRequest -Uri $ocrUrl -OutFile $ocrInstaller -UseBasicParsing
    $actualHash = (Get-FileHash -LiteralPath $ocrInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $ocrSha256) {
        throw 'OCR 组件校验失败，请删除 .downloads 后重试。'
    }
    Write-Host '正在解压离线 OCR 组件...'
    $process = Start-Process -FilePath $ocrInstaller -ArgumentList @('-y', "-o$runtimeDir") -Wait -PassThru -NoNewWindow
    if ($process.ExitCode -ne 0) {
        throw 'OCR 组件解压失败。'
    }
    $ocrExe = Get-ChildItem -LiteralPath $runtimeDir -Filter 'Umi-OCR.exe' -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $ocrExe) {
        throw 'OCR 组件不完整：未找到 Umi-OCR.exe。'
    }
}

if (-not $NoLaunch) {
    Write-Host '正在启动程序...'
    Start-Process -FilePath $venvPythonw -ArgumentList @((Join-Path $projectDir 'app.py')) -WorkingDirectory $projectDir
}
