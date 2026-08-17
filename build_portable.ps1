$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectDir '.venv\Scripts\python.exe'
$distDir = Join-Path $projectDir 'dist\PhotoMetadataTool'
$buildDir = Join-Path $projectDir 'build'

Set-Location -LiteralPath $projectDir
& (Join-Path $projectDir 'setup.ps1') -NoLaunch

& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $projectDir 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) {
    throw '构建依赖安装失败。'
}

if (Test-Path -LiteralPath $distDir) {
    Remove-Item -LiteralPath $distDir -Recurse -Force
}
New-Item -ItemType Directory -Path $distDir -Force | Out-Null
New-Item -ItemType Directory -Path $buildDir -Force | Out-Null

& $venvPython -m PyInstaller --noconfirm --clean --onefile --windowed --name PhotoMetadataTool --distpath $distDir --workpath (Join-Path $buildDir 'gui') --specpath $buildDir (Join-Path $projectDir 'app.py')
if ($LASTEXITCODE -ne 0) {
    throw '主程序构建失败。'
}

& $venvPython -m PyInstaller --noconfirm --clean --onefile --console --hidden-import piexif --name PhotoMetadataWorker --distpath $distDir --workpath (Join-Path $buildDir 'worker') --specpath $buildDir (Join-Path $projectDir 'photo_pipeline.py')
if ($LASTEXITCODE -ne 0) {
    throw '后台程序构建失败。'
}

Copy-Item -LiteralPath (Join-Path $projectDir 'runtime') -Destination (Join-Path $distDir 'runtime') -Recurse
Copy-Item -LiteralPath (Join-Path $projectDir '启动程序.bat') -Destination $distDir
Copy-Item -LiteralPath (Join-Path $projectDir 'README.md') -Destination $distDir

$zipPath = Join-Path $projectDir 'dist\PhotoMetadataTool-Windows-x64.zip'
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -LiteralPath $distDir -DestinationPath $zipPath -CompressionLevel Optimal
Write-Host "便携包已生成：$zipPath"
