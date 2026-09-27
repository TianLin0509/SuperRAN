# 给内网 Agent 打包一份 SuperRAN。
#
# 用法： powershell -File C:\Vibe\Wireless\SuperRAN\scripts\superran_company_zip.ps1
#
# 做三件事：
#   1. 从阿里云取得指定分支（默认 develop）的确定提交
#   2. 从该提交导出 zip，文件名和包内目录写明版本
#   3. 打印出你要发给内网 Agent 的那句话
#
# 只读远端；更新 FETCH_HEAD 与对象缓存，不改本地分支、索引或工作文件。

param(
    [string]$OutDir = "$env:USERPROFILE\Desktop\claude-artifacts",
    [string]$Branch = ""
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

Push-Location $repo
try {
    $project = Get-Content -LiteralPath (Join-Path $repo '.agents/project.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $Branch) { $Branch = $project.trunk }
    $remote = $project.cloudRepository.remote
    $expectedUrl = $project.cloudRepository.url
    if (-not $remote -or -not $expectedUrl) { throw '缺少阿里云仓库配置' }
    $actualUrl = @(git remote get-url --all $remote)
    if ($LASTEXITCODE -ne 0 -or $actualUrl.Count -ne 1 -or $actualUrl[0] -ne $expectedUrl) {
        throw '日常远端未切换到阿里云，请先运行 scripts/agent_repo.py init'
    }
    git check-ref-format "refs/heads/$Branch"
    if ($LASTEXITCODE -ne 0) { throw '分支名称不合法' }
    Write-Host "正在从阿里云核对 $Branch ..." -ForegroundColor DarkGray
    git fetch --quiet --no-tags $remote "refs/heads/$Branch"
    if ($LASTEXITCODE -ne 0) { throw '取回云端分支失败，未打包' }
    $sha = (git rev-parse --verify 'FETCH_HEAD^{commit}').Trim()
    if ($LASTEXITCODE -ne 0 -or $sha -notmatch '^[0-9a-f]{40}$') { throw '未取得确定的完整版本' }
    $short = $sha.Substring(0, 7)
    $date = Get-Date -Format "yyyyMMdd"

    if (-not (Test-Path -LiteralPath $OutDir)) {
        New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    }
    $zip = Join-Path $OutDir "$date-SuperRAN-$short-company.zip"
    if (Test-Path -LiteralPath $zip) { throw "目标文件已存在，保留原文件：$zip" }

    Write-Host "正在导出 $short ..." -ForegroundColor DarkGray
    git -c core.autocrlf=false archive --format=zip "--prefix=SuperRAN-$sha/" "--output=$zip" $sha
    if ($LASTEXITCODE -ne 0) { throw '导出失败，现有文件不能作为有效审核包' }

    $size = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)

    Write-Host ""
    Write-Host "打包完成 ($size MB)" -ForegroundColor Green
    Write-Host ""
    Write-Host "绝对路径：$zip"
    Write-Host ""
    Write-Host "────────── 把下面这段连同 zip 一起发给内网 Agent ──────────" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "这是 SuperRAN 的源码快照（版本 $short）。"
    Write-Host "先读里面的 .agents/COMPANY.md，按它的规矩工作。"
    Write-Host "任务：拿参照实现做基准，找出 SuperRAN 哪里实现得不对，"
    Write-Host "     按 COMPANY.md 里的模板写成一份 Markdown 报告给我。"
    Write-Host ""
    Write-Host "──────────────────────────────────────────────────────────" -ForegroundColor Cyan
    Write-Host ""
    # 收件箱永远指向主仓库，不能用脚本所在目录——从 worktree 里跑会指错地方
    $mainRepo = ((git worktree list --porcelain) | Select-Object -First 1) -replace '^worktree ', ''
    $mainRepo = $mainRepo -replace '/', '\'
    Write-Host "它给你 md 之后，复制到这里：" -ForegroundColor DarkGray
    Write-Host "  $mainRepo\docs\inbox\"
    Write-Host "然后对本地 Agent 说：处理 docs\inbox 里的内网审阅报告" -ForegroundColor DarkGray
}
finally {
    Pop-Location
}
