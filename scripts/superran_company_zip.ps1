# 给公司 Agent 打包一份 SuperRAN。
#
# 用法： powershell -File C:\Vibe\Wireless\SuperRAN\scripts\superran_company_zip.ps1
#
# 做三件事：
#   1. 从 GitHub 下载最新的 main 分支快照
#   2. 把 SHA 写进文件名——GitHub 的 zip 里没有 git 信息，文件名是最省事的版本标记
#   3. 打印出你要发给公司 Agent 的那句话
#
# 只读远端，不改本地任何分支或文件。

param(
    [string]$OutDir = "$env:USERPROFILE\Desktop\claude-artifacts",
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

Push-Location $repo
try {
    Write-Host "正在查最新版本..." -ForegroundColor DarkGray
    git fetch origin --quiet --prune
    $sha = (git rev-parse "origin/$Branch").Trim()
    if (-not $sha) { throw "拿不到 origin/$Branch 的 SHA，检查网络或分支名" }
    $short = $sha.Substring(0, 7)
    $date = Get-Date -Format "yyyyMMdd"

    if (-not (Test-Path -LiteralPath $OutDir)) {
        New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    }
    $zip = Join-Path $OutDir "SuperRAN-$date-$short.zip"

    Write-Host "正在下载 $short ..." -ForegroundColor DarkGray
    # 按 SHA 下载：解压后的目录名就是完整 SHA，公司 Agent 一眼能看出基线
    Invoke-WebRequest -Uri "https://github.com/TianLin0509/SuperRAN/archive/$sha.zip" `
                      -OutFile $zip -UseBasicParsing

    $size = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)

    Write-Host ""
    Write-Host "打包完成 ($size MB)" -ForegroundColor Green
    Write-Host ""
    Write-Host "绝对路径：$zip"
    Write-Host ""
    Write-Host "────────── 把下面这段连同 zip 一起发给公司 Agent ──────────" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "这是 SuperRAN 的源码快照（版本 $short）。"
    Write-Host "先读里面的 .agents/COMPANY.md，按它的规矩工作。"
    Write-Host "任务：拿我们公司的真实实现做参照，找出 SuperRAN 哪里实现得不对，"
    Write-Host "     按 COMPANY.md 里的模板写成一份 Markdown 报告给我。"
    Write-Host ""
    Write-Host "──────────────────────────────────────────────────────────" -ForegroundColor Cyan
    Write-Host ""
    # 收件箱永远指向主仓库，不能用脚本所在目录——从 worktree 里跑会指错地方
    $mainRepo = ((git worktree list --porcelain) | Select-Object -First 1) -replace '^worktree ', ''
    $mainRepo = $mainRepo -replace '/', '\'
    Write-Host "它给你 md 之后，复制到这里：" -ForegroundColor DarkGray
    Write-Host "  $mainRepo\docs\inbox\"
    Write-Host "然后对本地 Agent 说：处理 docs\inbox 里的公司审阅报告" -ForegroundColor DarkGray
}
finally {
    Pop-Location
}
