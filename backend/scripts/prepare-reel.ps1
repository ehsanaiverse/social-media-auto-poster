# Prepares a video for an Instagram Reel and puts it in the Instagram incoming folder.
# Fixes the two things Meta requires that phone videos often lack:
#   moov atom at the front (faststart) and no edit lists.
# The source video is never changed or deleted.
#
# Usage (from the repo root or anywhere):
#   .\backend\scripts\prepare-reel.ps1 -Source "C:\Users\me\Downloads\clip.mp4" -Name reel2
# Then write the caption into backend\data\Instagram\incoming\reel2.txt

param(
    [Parameter(Mandatory = $true)] [string] $Source,
    [Parameter(Mandatory = $true)] [string] $Name
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Error "ffmpeg not found. Install it with: winget install Gyan.FFmpeg (normal PowerShell, then reopen the terminal)."
}
if (-not (Test-Path -LiteralPath $Source)) {
    Write-Error "Source video not found: $Source"
}

$incoming = Join-Path $PSScriptRoot "..\data\Instagram\incoming"
$incoming = (Resolve-Path -LiteralPath $incoming).Path
$target = Join-Path $incoming "$Name.mp4"
$temp = Join-Path (Split-Path $incoming -Parent) "$Name.preparing.tmp.mp4"

if (Test-Path -LiteralPath $target) {
    Write-Error "$target already exists. Choose another -Name."
}

# Write to a temp file OUTSIDE incoming first, so a half-written file is never picked up as a post.
& ffmpeg -hide_banner -loglevel error -y -i $Source -c copy -movflags +faststart -use_editlist 0 $temp
if ($LASTEXITCODE -ne 0) {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp }
    Write-Error "ffmpeg failed (exit $LASTEXITCODE). The source video was not changed."
}

Move-Item -LiteralPath $temp -Destination $target
Write-Host "Ready: $target"
Write-Host "Next: write the caption to $(Join-Path $incoming "$Name.txt"), then run: scan --platform instagram"