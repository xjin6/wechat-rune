<#
  update_wechat.ps1 — 一键增量更新 mex & jjwang 微信记录（手动运行）

  前提：先用外部工具把密钥导出成一个文本文件（任意格式，里面含 64-hex 密钥即可），
        默认路径见下方 $KeyFile，可用 -KeyFile 覆盖。

  本脚本依次执行：
    1. 导入并验证密钥（import_keys.py，兼容任意导出格式）
    2. 核心库齐全性检查（缺则终止并提示）
    3. 增量诊断（incremental_diff.py）
    4. 转录两位联系人的新语音（resume 续跑，medium 模型）
    5. 重新导出两个 .md（带语音/图片索引/图片描述缓存）
    6. 同步到 Obsidian wiki（去图嵌入）

  用法（PowerShell）：
    cd "C:\Users\jxi\OneDrive - Microsoft\Vibe\wechat-rune"
    .\update_wechat.ps1
    # 指定密钥文件：
    .\update_wechat.ps1 -KeyFile "D:\path\to\all_keys_xxx.txt"
    # 顺带 AI 同音字纠错（需先设好 $env:ANTHROPIC_API_KEY）：
    .\update_wechat.ps1 -Correct

  说明：
    - 语音 AI 纠错(-Correct)与“新图片 AI 描述”属于需要判断的步骤；不开 -Correct 时
      语音仍会转录，只是不自动纠错。若 diff 报告有较多新图片需要描述，建议交给
      agent 会话处理（描述质量更高）。
#>
param(
    [string]$KeyFile = "D:\WeChat history\xwechat_files\all_keys_20251101_124643.txt",
    [switch]$Correct
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Repo = "C:\Users\jxi\OneDrive - Microsoft\Vibe\wechat-rune"
$Rel  = "C:\Users\jxi\OneDrive - Microsoft\Vibe\relationship"
Set-Location $Repo

$Contacts = @(
    [pscustomobject]@{ Label = "jjwang"; Wxid = "wxid_clslfiswnis422" },
    [pscustomobject]@{ Label = "mex";    Wxid = "wxid_u5ejnrlk2mn122" }
)

function Step($n, $msg) { Write-Host "`n===== [$n] $msg =====" -ForegroundColor Cyan }

# ── 1. 导入并验证密钥 ───────────────────────────────────────────────
Step 1 "导入并验证密钥: $KeyFile"
if (-not (Test-Path $KeyFile)) {
    Write-Host "[!] 密钥文件不存在：$KeyFile" -ForegroundColor Red
    Write-Host "    请先用外部工具导出密钥，或用 -KeyFile 指定正确路径。" -ForegroundColor Red
    exit 1
}
python scripts/keys/import_keys.py "$KeyFile"
if ($LASTEXITCODE -ne 0) { Write-Host "[!] 导入密钥失败，终止。" -ForegroundColor Red; exit 1 }

# ── 2. 核心库齐全性检查 ─────────────────────────────────────────────
Step 2 "核心库齐全性检查"
$res = Get-Content "scripts\keys\import_keys_result.json" -Raw | ConvertFrom-Json
$missing = @()
foreach ($b in "message_0.db", "message_1.db", "contact.db", "media_0.db") {
    if (-not $res.core.$b) { $missing += $b }
}
if ($missing.Count -gt 0) {
    Write-Host "[!] 核心库密钥缺失：$($missing -join ', ')" -ForegroundColor Red
    Write-Host "    外部密钥文件里没有这些库的有效密钥。请确认该文件是本账号" -ForegroundColor Red
    Write-Host "    (magicxinjx_c092) 的完整导出，或改用监听模式：" -ForegroundColor Red
    Write-Host "      python scripts\keys\extract_key_windows.py --watch 180" -ForegroundColor Red
    Write-Host "    然后重启微信、点开两个聊天+通讯录。" -ForegroundColor Red
    exit 1
}
Write-Host "核心库齐全（message_0/message_1/contact/media_0）。" -ForegroundColor Green

# ── 3. 增量诊断 ─────────────────────────────────────────────────────
Step 3 "增量诊断"
python scripts/incremental_diff.py "$Rel" `
    "wxid_u5ejnrlk2mn122:mex" "wxid_clslfiswnis422:jjwang"

# ── 4. 转录新语音（resume, medium）──────────────────────────────────
foreach ($c in $Contacts) {
    Step "4-$($c.Label)" "转录新语音"
    $vmap = Join-Path $Rel "$($c.Label)\$($c.Label)_archive\$($c.Label)_voice_map.json"
    $tArgs = @("scripts/transcribe_voices.py", "--wxid", $c.Wxid, "--model", "medium", "--out", $vmap)
    if ($Correct) { $tArgs += "--correct" }
    python @tArgs
}

# ── 5. 重新导出 ─────────────────────────────────────────────────────
foreach ($c in $Contacts) {
    Step "5-$($c.Label)" "重新导出 $($c.Label)_wechat.md"
    $arch = Join-Path $Rel "$($c.Label)\$($c.Label)_archive"
    python scripts/export_chat.py --wxid $c.Wxid `
        --out (Join-Path $Rel "$($c.Label)\$($c.Label)_wechat.md") `
        --voice-json (Join-Path $arch "$($c.Label)_voice_map.json") `
        --image-index (Join-Path $arch "image_index.json") `
        --image-descriptions (Join-Path $arch "image_descriptions.json")
}

# ── 6. 同步到 wiki ──────────────────────────────────────────────────
Step 6 "同步到 Obsidian wiki"
python scripts/sync_to_wiki.py mex jjwang

Write-Host "`n✓ 全部完成。" -ForegroundColor Green
