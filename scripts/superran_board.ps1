# SuperRAN 状态看板 —— 双击或在任意目录运行都可以
# 用法： powershell -File C:\Vibe\Wireless\SuperRAN\scripts\superran_board.ps1
#       加 -NoFetch 跳过远端刷新（离线时用），加 -NoOpen 不自动开浏览器

param([switch]$NoFetch, [switch]$NoOpen)

$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot 'superran_board.py'

# 不要用 $args，它是 PowerShell 的自动变量
$pyArgs = @($script)
if ($NoFetch) { $pyArgs += '--no-fetch' }
if ($NoOpen)  { $pyArgs += '--no-open' }

Push-Location $repo
try {
    & python @pyArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
