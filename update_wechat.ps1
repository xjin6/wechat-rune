<#
  update_wechat.ps1 — one-click incremental update for one or more WeChat contacts.

  Nothing is hardcoded — pass the contact list + roots (or set the env vars), so
  this works for ANY account/contacts, not a fixed pair.

  Usage (PowerShell):
    .\update_wechat.ps1 -Contacts "wxid_aaa:alice,wxid_bbb:bob" `
        -Rel "<relationship root>" -Wiki "<obsidian wiki root>"

    # import keys from an external dump first (any format containing 64-hex keys):
    .\update_wechat.ps1 -Contacts "wxid_aaa:alice" -KeyFile "D:\path\all_keys.txt"

    # also AI homophone-correct the new voices (needs $env:ANTHROPIC_API_KEY):
    .\update_wechat.ps1 -Contacts "wxid_aaa:alice" -Correct

  Roots default to env vars WECHAT_VIBE_ROOT / WECHAT_WIKI_ROOT; repo root defaults
  to the script's own folder. Steps: (1) optional key import  (2) core-DB check
  (3) incremental diff  (4) transcribe new voices (resume, medium)  (5) re-export
  each .md  (6) sync to Obsidian wiki (image-less).
#>
param(
    [Parameter(Mandatory = $true)][string]$Contacts,   # "wxid:label,wxid:label,..."
    [string]$Rel  = $env:WECHAT_VIBE_ROOT,
    [string]$Wiki = $env:WECHAT_WIKI_ROOT,
    [string]$Repo = $(if ($env:WECHAT_REPO_ROOT) { $env:WECHAT_REPO_ROOT } else { $PSScriptRoot }),
    [string]$KeyFile,
    [switch]$Correct
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if (-not $Rel)  { Write-Host "[!] Need -Rel or env WECHAT_VIBE_ROOT (relationship root)." -ForegroundColor Red; exit 1 }
if (-not $Wiki) { Write-Host "[!] Need -Wiki or env WECHAT_WIKI_ROOT (Obsidian wiki root)." -ForegroundColor Red; exit 1 }
Set-Location $Repo

# parse "wxid:label,wxid:label" into objects
$ContactList = $Contacts.Split(",") | Where-Object { $_.Trim() } | ForEach-Object {
    $p = $_.Split(":"); [pscustomobject]@{ Wxid = $p[0].Trim(); Label = $p[1].Trim() }
}

function Step($n, $msg) { Write-Host "`n===== [$n] $msg =====" -ForegroundColor Cyan }

# ── 1. (optional) import + verify keys from an external dump ─────────
if ($KeyFile) {
    Step 1 "Import + verify keys: $KeyFile"
    if (-not (Test-Path $KeyFile)) { Write-Host "[!] Key file not found: $KeyFile" -ForegroundColor Red; exit 1 }
    python scripts/keys/import_keys.py "$KeyFile"
    if ($LASTEXITCODE -ne 0) { Write-Host "[!] Key import failed; aborting." -ForegroundColor Red; exit 1 }
}

# ── 2. core-DB key check (read straight from the key store) ─────────
Step 2 "Core-DB key check"
python scripts/keys/keystore.py show
# (keystore re-resolves wechat_keys.json from the pool; incremental_diff below will
#  surface any DB it still can't read as `DB messages: 0`.)

# ── 2.5 harvest manual edits from the Wiki .md back into the JSON sources ──
# You read/edit the image-less Wiki .md in Obsidian. Both .md files are regenerated
# every run, so we FIRST pull your hand-edited descriptions/transcripts back into
# image_descriptions.json / voice_map.json (matched via anchors from a fresh baseline;
# anything ambiguous is reported, never auto-applied). Run BEFORE any JSON mutation.
foreach ($c in $ContactList) {
    $wikiMd = Join-Path $Wiki "$($c.Label)\$($c.Label)_wechat.md"
    if (-not (Test-Path $wikiMd)) { continue }
    Step "2.5-$($c.Label)" "Harvest manual edits from Wiki md"
    $arch  = Join-Path $Rel "$($c.Label)\$($c.Label)_archive"
    $descs = Join-Path $arch "image_descriptions.json"
    $vmap  = Join-Path $arch "$($c.Label)_voice_map.json"
    $idx   = Join-Path $arch "image_index.json"
    $baseMd = Join-Path $env:TEMP "_harvest_base_$($c.Label).md"
    $baseAnch = Join-Path $env:TEMP "_harvest_anch_$($c.Label).json"
    python scripts/export_chat.py --wxid $c.Wxid --out $baseMd `
        --voice-json $vmap --image-index $idx --image-descriptions $descs --emit-anchors $baseAnch
    python scripts/harvest_edits.py --user-wiki $wikiMd --baseline-vibe $baseMd `
        --anchors $baseAnch --image-descriptions $descs --voice-map $vmap `
        --manual-registry (Join-Path $arch "manual_edits.json") --apply
    Remove-Item $baseMd, $baseAnch -ErrorAction SilentlyContinue
}

# ── 3. incremental diff ─────────────────────────────────────────────
Step 3 "Incremental diff"
$diffArgs = @("scripts/incremental_diff.py", "$Rel")
foreach ($c in $ContactList) { $diffArgs += "$($c.Wxid):$($c.Label)" }
python @diffArgs

# ── 4. transcribe new voices (resume, medium) ───────────────────────
foreach ($c in $ContactList) {
    Step "4-$($c.Label)" "Transcribe new voices"
    $vmap = Join-Path $Rel "$($c.Label)\$($c.Label)_archive\$($c.Label)_voice_map.json"
    $tArgs = @("scripts/transcribe_voices.py", "--wxid", $c.Wxid, "--model", "medium", "--out", $vmap)
    if ($Correct) { $tArgs += "--correct" }
    python @tArgs
}

# ── 5. re-export each .md ───────────────────────────────────────────
foreach ($c in $ContactList) {
    Step "5-$($c.Label)" "Re-export $($c.Label)_wechat.md"
    $arch = Join-Path $Rel "$($c.Label)\$($c.Label)_archive"
    python scripts/export_chat.py --wxid $c.Wxid `
        --out (Join-Path $Rel "$($c.Label)\$($c.Label)_wechat.md") `
        --voice-json (Join-Path $arch "$($c.Label)_voice_map.json") `
        --image-index (Join-Path $arch "image_index.json") `
        --image-descriptions (Join-Path $arch "image_descriptions.json")
}

# ── 6. sync to Obsidian wiki (image-less) ───────────────────────────
Step 6 "Sync to Obsidian wiki"
$wikiArgs = @("scripts/sync_to_wiki.py", "--vibe-root", "$Rel", "--wiki-root", "$Wiki")
foreach ($c in $ContactList) { $wikiArgs += $c.Label }
python @wikiArgs

Write-Host "`n[OK] Done." -ForegroundColor Green
