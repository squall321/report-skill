<#
.SYNOPSIS
  Build standalone .exe binaries (no Python required on receiver).

.DESCRIPTION
  Uses PyInstaller --onefile to bundle the entire skill — Python runtime,
  all dependencies, the bundled widget catalog + templates — into two
  single-file executables:

    dist\exe\report-skill.exe       (~25 MB)
    dist\exe\report-skill-mcp.exe   (~25 MB)

  Both work on any Windows 10+ machine with zero prerequisites.

  Run AFTER (or instead of) build_release.ps1 — the .exe artifacts get
  zipped separately into report-skill-standalone-vX.Y.Z.zip.

.PARAMETER NoRefresh
  Skip the live-backend refresh step. Defaults to ON because most exe
  builds happen against a frozen baseline.
#>
[CmdletBinding()]
param(
    [switch]$Refresh,
    [string]$Version
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$venv = Join-Path $repo "venv\Scripts"
$python = Join-Path $venv "python.exe"
$pyinstaller = Join-Path $venv "pyinstaller.exe"

if (-not (Test-Path $pyinstaller)) {
    Write-Host "==> installing pyinstaller" -ForegroundColor Cyan
    & $python -m pip install --quiet pyinstaller
}

# --- 1. optional refresh ---
if ($Refresh) {
    Write-Host "==> refreshing bundled data" -ForegroundColor Cyan
    & $python (Join-Path $repo "scripts\refresh_bundled_data.py")
    if ($LASTEXITCODE -ne 0) { throw "refresh exit=$LASTEXITCODE" }
}

# --- 2. resolve version ---
if (-not $Version) {
    $py = Get-Content (Join-Path $repo "pyproject.toml") -Raw
    if ($py -match '(?ms)^\s*version\s*=\s*"([^"]+)"') {
        $Version = $matches[1]
    } else {
        throw "no version in pyproject.toml"
    }
}
Write-Host "==> standalone version = $Version" -ForegroundColor Cyan

# --- 3. clean previous PyInstaller artifacts ---
$exeDist = Join-Path $repo "dist\exe"
$buildDir = Join-Path $repo "build\pyinstaller"
Remove-Item -Recurse -Force $exeDist, $buildDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $exeDist | Out-Null

# --- 4. build CLI ---
# `--add-data SRC;DEST` (Windows uses `;` as the data separator).
# SRC must be an ABSOLUTE path because PyInstaller resolves it relative
# to the .spec file's directory (which we set to build\pyinstaller).
# DEST is relative to the unpacked _MEIPASS at runtime.
$dataSrc = Join-Path $repo "src\report_skill\data"
$dataArg = "$dataSrc;report_skill\data"
$adapters = Get-ChildItem "src\report_skill\adapters\*.py" |
    Where-Object Name -ne "__init__.py" |
    ForEach-Object { "report_skill.adapters." + $_.BaseName }

$hidden = @(
    "report_skill.cli", "report_skill.cli_examples", "report_skill.cli_files",
    "report_skill.cli_llm", "report_skill.cli_templates",
    "report_skill.mcp_server", "report_skill.merge",
    "report_skill.orchestrator", "report_skill.report_ops",
    "report_skill.report_builder", "report_skill.tier",
    "report_skill.upload_chain", "report_skill.template_suggest",
    "report_skill.widget_suggest", "report_skill.tags",
    "report_skill.prompt", "report_skill.llm",
    "report_skill.adapters"
) + $adapters

$hiddenArgs = $hidden | ForEach-Object { "--hidden-import"; $_ }

Write-Host "==> building report-skill.exe (CLI)" -ForegroundColor Cyan
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$null = & $pyinstaller `
    --onefile --noconfirm `
    --name report-skill `
    --distpath $exeDist `
    --workpath $buildDir `
    --specpath $buildDir `
    --add-data $dataArg `
    @hiddenArgs `
    --console `
    "src\report_skill\cli.py" 2>&1
$ErrorActionPreference = $prev
if ($LASTEXITCODE -ne 0) { throw "pyinstaller CLI exit=$LASTEXITCODE" }

# `pyinstaller` writes the entry as `if __name__ == '__main__': sys.exit(...)`
# but typer apps need explicit invocation. Confirm the entry is correct:
# cli.py has `if __name__ == "__main__": app()` already. ✓

Write-Host "==> building report-skill-mcp.exe (MCP server)" -ForegroundColor Cyan
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$null = & $pyinstaller `
    --onefile --noconfirm `
    --name report-skill-mcp `
    --distpath $exeDist `
    --workpath $buildDir `
    --specpath $buildDir `
    --add-data $dataArg `
    @hiddenArgs `
    --console `
    "src\report_skill\mcp_server.py" 2>&1
$ErrorActionPreference = $prev
if ($LASTEXITCODE -ne 0) { throw "pyinstaller MCP exit=$LASTEXITCODE" }

$cli = Join-Path $exeDist "report-skill.exe"
$mcp = Join-Path $exeDist "report-skill-mcp.exe"
"==> built:"
"    $cli  ($([math]::Round((Get-Item $cli).Length/1MB,1)) MB)"
"    $mcp  ($([math]::Round((Get-Item $mcp).Length/1MB,1)) MB)"

# --- 5. assemble standalone release ---
$relName = "report-skill-standalone-v$Version"
$relDir = Join-Path $repo "dist\release-standalone\$relName"
Remove-Item -Recurse -Force (Join-Path $repo "dist\release-standalone") -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $relDir | Out-Null

Copy-Item $cli, $mcp -Destination $relDir
$skillsSrc = Join-Path $repo ".claude\skills"
if (Test-Path $skillsSrc) {
    $skillsDst = Join-Path $relDir ".claude\skills"
    New-Item -ItemType Directory -Force -Path $skillsDst | Out-Null
    # Recurse the whole tree — skills are now in <name>/SKILL.md directory format
    # (Claude Code requirement), not flat .md drops.
    Copy-Item "$skillsSrc\*" -Destination $skillsDst -Recurse -Force
}
Copy-Item (Join-Path $repo ".env.example") -Destination $relDir

# Standalone install script — totally different from the wheel one.
Copy-Item (Join-Path $repo "install-standalone.ps1") -Destination $relDir

# Double-click front door — Korean banner wrapper that calls install-standalone.ps1
# with -ExecutionPolicy Bypass so non-technical receivers don't need PowerShell knowledge.
Copy-Item (Join-Path $repo "setup.bat") -Destination $relDir

$recvDoc = Join-Path $repo "docs\RECEIVER-STANDALONE.md"
if (Test-Path $recvDoc) {
    Copy-Item $recvDoc -Destination (Join-Path $relDir "README.md")
} else {
    Copy-Item (Join-Path $repo "docs\RECEIVER.md") -Destination (Join-Path $relDir "README.md")
}

$zipPath = Join-Path $repo "dist\$relName.zip"
Remove-Item -Force $zipPath -ErrorAction SilentlyContinue
Compress-Archive -Path "$relDir\*" -DestinationPath $zipPath
$zipSize = (Get-Item $zipPath).Length
Write-Host "==> built $zipPath  ($([math]::Round($zipSize/1MB,1)) MB)" -ForegroundColor Green
Write-Host "    receiver unzips + runs .\install-standalone.ps1 — no Python required" -ForegroundColor DarkGray
