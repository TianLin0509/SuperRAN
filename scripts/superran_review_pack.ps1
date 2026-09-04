# 把一次改动打包成「审核包」，带进内网给内网 Agent 审。
#
# 用法： powershell -File C:\Vibe\Wireless\SuperRAN\scripts\superran_review_pack.ps1 <分支名或SHA>
# 例：   powershell -File ...\superran_review_pack.ps1 feat/sionna-rt-source

param(
    [Parameter(Mandatory = $true)][string]$Ref,
    [string]$Base = "develop",
    [string]$OutDir = "$env:USERPROFILE\Desktop\claude-artifacts"
)

$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Push-Location $repo
try {
    & python (Join-Path $PSScriptRoot 'superran_review_pack.py') $Ref --base $Base --out $OutDir
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
