<#
.SYNOPSIS
  Build a self-contained release artifact for report-skill.

.DESCRIPTION
  Pipeline:
    1. (default ON) refresh bundled data from a live backend
    2. python -m build → wheel + sdist into dist\
    3. Assemble release zip:
       dist\release\report-skill-vX.Y.Z\
         report_skill-X.Y.Z-py3-none-any.whl
         .claude/skills/*.md
         .env.example
         install.ps1
         README.md
       → zipped to dist\report-skill-vX.Y.Z.zip

  Receiver workflow: unzip → .\install.ps1 → done.

.PARAMETER NoRefresh
  Skip the live-backend refresh step (use the snapshot already in src\report_skill\data\).
  Useful when offline or when the bundled baseline is intentionally pinned.

.PARAMETER Version
  Override the version stamped into the zip filename. Defaults to whatever's in pyproject.toml.

.PARAMETER WithExe
  Also produce the standalone .exe variant (calls build_exe.ps1 after the wheel build).
  Two zips end up in dist\: report-skill-vX.Y.Z.zip (wheel, ~170 KB) and
  report-skill-standalone-vX.Y.Z.zip (.exe, ~37 MB).

.PARAMETER ExeOnly
  Skip the wheel build entirely and only produce the standalone .exe variant.

.EXAMPLE
  .\scripts\build_release.ps1                          # wheel-only (default)
  .\scripts\build_release.ps1 -WithExe                 # wheel + standalone
  .\scripts\build_release.ps1 -ExeOnly                 # standalone-only
  .\scripts\build_release.ps1 -NoRefresh               # skip live sync
  .\scripts\build_release.ps1 -Version 0.1.0           # override version
#>
[CmdletBinding()]
param(
    [switch]$NoRefresh,
    [string]$Version,
    [switch]$WithExe,
    [switch]$ExeOnly
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$venv = Join-Path $repo "venv\Scripts"
$python = Join-Path $venv "python.exe"

if (-not (Test-Path $python)) {
    Write-Error "venv not found at $venv. Run setup_venv first."
}

# --- ExeOnly fast path: skip wheel, hand off to build_exe.ps1 ---
if ($ExeOnly) {
    Write-Host "==> ExeOnly: skipping wheel build, calling build_exe.ps1 directly" -ForegroundColor Cyan
    $exeArgs = @{}
    if ($Version)   { $exeArgs.Version = $Version }
    if (-not $NoRefresh) { $exeArgs.Refresh = $true }
    & (Join-Path $PSScriptRoot "build_exe.ps1") @exeArgs
    if ($LASTEXITCODE -ne 0) { throw "build_exe.ps1 exit=$LASTEXITCODE" }
    return
}

# --- 1. refresh bundled data ---
if (-not $NoRefresh) {
    Write-Host "==> refreshing bundled data from live backend" -ForegroundColor Cyan
    & $python (Join-Path $repo "scripts\refresh_bundled_data.py")
    if ($LASTEXITCODE -ne 0) { throw "refresh_bundled_data.py exit=$LASTEXITCODE" }
} else {
    Write-Host "==> skipping refresh (using existing src\report_skill\data)" -ForegroundColor Yellow
}

# --- 2. ensure `build` is installed in the venv ---
# pip silently emits progress on stderr even with --quiet; the script's
# default ErrorAction=Stop would treat that as fatal. Wrap with Continue
# + merge streams + check $LASTEXITCODE only.
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$null = & $python -m pip install --quiet --upgrade build 2>&1
$ErrorActionPreference = $prev
if ($LASTEXITCODE -ne 0) { throw "pip install build exit=$LASTEXITCODE" }

# --- 3. clean + build wheel + sdist ---
Write-Host "==> python -m build" -ForegroundColor Cyan
Remove-Item -Recurse -Force (Join-Path $repo "dist\*.whl"), (Join-Path $repo "dist\*.tar.gz") -ErrorAction SilentlyContinue
# `python -m build` writes progress to stderr; merge streams + check exit
# code only (don't let PS treat normal progress lines as fatal).
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$buildOut = & $python -m build --outdir (Join-Path $repo "dist") 2>&1
$ErrorActionPreference = $prev
$buildOut | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -ne 0) { throw "python -m build exit=$LASTEXITCODE" }

# --- 4. resolve version ---
if (-not $Version) {
    $pyproject = Get-Content (Join-Path $repo "pyproject.toml") -Raw
    if ($pyproject -match '(?ms)^\s*version\s*=\s*"([^"]+)"') {
        $Version = $matches[1]
    } else {
        throw "could not detect version in pyproject.toml"
    }
}
Write-Host "==> release version = $Version" -ForegroundColor Cyan

$wheel = Get-ChildItem (Join-Path $repo "dist\*.whl") | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $wheel) { throw "no .whl in dist\" }
Write-Host "    wheel: $($wheel.Name)" -ForegroundColor DarkGray

# --- 5. assemble release dir ---
$relName = "report-skill-v$Version"
$relDir  = Join-Path $repo "dist\release\$relName"
if (Test-Path (Join-Path $repo "dist\release")) {
    Remove-Item -Recurse -Force (Join-Path $repo "dist\release")
}
New-Item -ItemType Directory -Force -Path $relDir | Out-Null

# 5a. wheel
Copy-Item $wheel.FullName -Destination $relDir

# 5b. .claude/skills/*.md
$skillsSrc = Join-Path $repo ".claude\skills"
if (Test-Path $skillsSrc) {
    $skillsDst = Join-Path $relDir ".claude\skills"
    New-Item -ItemType Directory -Force -Path $skillsDst | Out-Null
    Copy-Item (Join-Path $skillsSrc "*.md") -Destination $skillsDst
}

# 5c. .env.example
Copy-Item (Join-Path $repo ".env.example") -Destination $relDir

# 5d. install.ps1
Copy-Item (Join-Path $repo "install.ps1") -Destination $relDir

# 5e. README — tailored receiver doc lives at docs/RECEIVER.md, fall back to main README
$recvDoc = Join-Path $repo "docs\RECEIVER.md"
if (Test-Path $recvDoc) {
    Copy-Item $recvDoc -Destination (Join-Path $relDir "README.md")
} else {
    Copy-Item (Join-Path $repo "README.md") -Destination $relDir
}

# 5f. INSTALL_SKILLS.md (slash-command global install instructions)
$installSkills = Join-Path $repo "INSTALL_SKILLS.md"
if (Test-Path $installSkills) {
    Copy-Item $installSkills -Destination $relDir
}

# --- 6. zip it ---
$zipPath = Join-Path $repo "dist\$relName.zip"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path "$relDir\*" -DestinationPath $zipPath
$zipSize = (Get-Item $zipPath).Length
Write-Host "==> built $zipPath  ($([math]::Round($zipSize/1KB,1)) KB)" -ForegroundColor Green
Write-Host "    unzip on the receiver side, then run .\install.ps1" -ForegroundColor DarkGray

# --- 7. optional standalone .exe variant ---
if ($WithExe) {
    Write-Host ""
    Write-Host "==> chaining standalone .exe build" -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "build_exe.ps1") -Version $Version
    if ($LASTEXITCODE -ne 0) { throw "build_exe.ps1 exit=$LASTEXITCODE" }
}
