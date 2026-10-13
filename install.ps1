#Requires -Version 5.1
<#
.SYNOPSIS
  Install muse-memory as a Muse Code plugin (user scope) and seed memory stores.
.EXAMPLE
  .\install.ps1
  .\install.ps1 -CliOnly   # skip plugin install, only seed stores + CLI check
#>
param([switch]$CliOnly)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "== muse-memory install ==" -ForegroundColor Cyan

python3 --version 2>$null
if ($LASTEXITCODE -ne 0) { throw 'python3 is required on PATH (3.9+). Install it via `winget install -e --id Python.Python.3` or the Microsoft Store (which provides the python3 alias), then re-run.' }

Write-Host '[1/4] Running tests...'
python3 -m unittest discover -s (Join-Path $root 'tests')
if ($LASTEXITCODE -ne 0) { throw 'tests failed, aborting install' }

Write-Host '[2/4] Validating plugin...'
muse skills validate (Join-Path $root 'skills/memory') --json | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'skill validation failed' }
muse plugins validate $root --json | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'plugin validation failed' }

if (-not $CliOnly) {
  Write-Host '[3/4] Installing plugin (user scope)...'
  muse plugins install $root --scope user
  if ($LASTEXITCODE -ne 0) { throw 'plugin install failed' }
  muse plugins approve muse-memory
  if ($LASTEXITCODE -ne 0) { throw 'plugin approve failed' }
  muse plugins enable muse-memory
} else {
  Write-Host '[3/4] Skipped plugin install (-CliOnly).'
}

Write-Host '[4/4] Seeding memory stores...'
$env:PYTHONPATH = $root
python3 -m muse_memory init
python3 -m muse_memory stats

Write-Host 'Done. Restart muse to load hooks. Try /recall, /remember, /consolidate.' -ForegroundColor Green
