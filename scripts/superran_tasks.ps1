# 任务看板 —— 每天开工先跑这个，它告诉你「现在该做什么」。
#
# 用法： powershell -File C:\Vibe\Wireless\SuperRAN\scripts\superran_tasks.ps1
#       加 -NoOpen 不自动开浏览器

param([switch]$NoOpen)

$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

$pyArgs = @((Join-Path $PSScriptRoot 'superran_tasks.py'))
if ($NoOpen) { $pyArgs += '--no-open' }

Push-Location $repo
try {
    & python @pyArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
