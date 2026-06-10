<#
.SYNOPSIS
  Install report-skill on the receiver machine.

.DESCRIPTION
  Steps:
    1. Create a Python venv in .\venv (or reuse if it exists)
    2. pip install the bundled wheel
    3. Interactive .env setup (server URL + service account credentials)
    4. Smoke-test with `report-skill ping`
    5. Print how to use the Claude Code slash commands (.claude/skills/)

  Re-runnable. Skips steps that are already done.

.PARAMETER ServerUrl
  REPORT_API_BASE_URL value (skips the prompt). Eg http://10.0.5.42:3000/api

.PARAMETER Email
  REPORT_API_EMAIL value (skips the prompt).

.PARAMETER Password
  REPORT_API_PASSWORD value (skips the prompt; you'd normally just paste it interactively).
#>
[CmdletBinding()]
param(
    [string]$ServerUrl,
    [string]$Email,
    [string]$Password,
    [string]$Workspace = "dx"
)
# Detect whether we're running interactively (terminal attached). Read-Host
# blows up in non-interactive contexts (CI / scripted installs) — when
# everything's been passed as args, skip the prompts entirely.
$NonInteractive = -not [Environment]::UserInteractive -or
                  ($Host.Name -eq 'ConsoleHost' -and
                   [Console]::IsInputRedirected)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

Write-Host "==> report-skill installer" -ForegroundColor Cyan

# --- 1. venv ---
$venv = Join-Path $root "venv"
$venvPy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "    creating venv..."
    & python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed. Is Python 3.11+ installed and on PATH?" }
} else {
    Write-Host "    venv already present" -ForegroundColor DarkGray
}

# --- 2. install wheel ---
$wheel = Get-ChildItem $root -Filter "*.whl" | Select-Object -First 1
if (-not $wheel) { throw "no .whl file found alongside install.ps1" }
Write-Host "    installing $($wheel.Name)..."
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet --force-reinstall $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit=$LASTEXITCODE)" }

# --- 3. .env setup ---
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Write-Host ""
    Write-Host "==> configure server connection (.env will be created)" -ForegroundColor Cyan

    if (-not $ServerUrl) {
        if ($NonInteractive) { throw "ServerUrl is required in non-interactive mode" }
        $ServerUrl = Read-Host "REPORT_API_BASE_URL (e.g. http://10.0.5.42:3000/api)"
    }
    if (-not $Email) {
        if ($NonInteractive) { $Email = "bot@reportskill.app" }
        else {
            $Email = Read-Host "REPORT_API_EMAIL (service account, default: bot@reportskill.app)"
            if (-not $Email) { $Email = "bot@reportskill.app" }
        }
    }
    if (-not $Password) {
        if ($NonInteractive) { throw "Password is required in non-interactive mode" }
        $sec = Read-Host "REPORT_API_PASSWORD" -AsSecureString
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        $Password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    if (-not $NonInteractive) {
        $ws = Read-Host "REPORT_API_WORKSPACE_SLUG (default: $Workspace)"
        if ($ws) { $Workspace = $ws }
    }

    $body = @(
        "REPORT_API_BASE_URL=$ServerUrl"
        "REPORT_API_WORKSPACE_SLUG=$Workspace"
        "REPORT_API_EMAIL=$Email"
        "REPORT_API_PASSWORD=$Password"
        "REPORT_BACKEND_PATH="
        "SKILL_AI_TIER="
    ) -join "`n"
    [IO.File]::WriteAllText($envFile, $body, [Text.UTF8Encoding]::new($false))
    Write-Host "    wrote $envFile" -ForegroundColor Green
} else {
    Write-Host "==> .env already present — skipping interactive setup" -ForegroundColor DarkGray
}

# --- 4. smoke test ---
Write-Host ""
Write-Host "==> smoke test: report-skill ping" -ForegroundColor Cyan
$reportSkill = Join-Path $venv "Scripts\report-skill.exe"
Push-Location $root
try {
    & $reportSkill ping
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "ping failed — check .env values + server reachability"
    }
} finally {
    Pop-Location
}

# --- 5. Claude Code slash-command pointer ---
Write-Host ""
Write-Host "==> next steps" -ForegroundColor Cyan
$skillsDir = Join-Path $root ".claude\skills"
if (Test-Path $skillsDir) {
    Write-Host "    Claude Code skills shipped with this release:" -ForegroundColor DarkGray
    Get-ChildItem $skillsDir -Recurse -Filter "SKILL.md" | ForEach-Object {
        Write-Host "      /$($_.Directory.Name)" -ForegroundColor DarkGray
    }
    Write-Host "    To make them available everywhere, copy to %USERPROFILE%\.claude\skills\:"
    Write-Host "      Copy-Item .claude\skills\* `$env:USERPROFILE\.claude\skills\ -Recurse -Force"
    Write-Host "    Or just `cd $root` and they auto-load (project-scoped)."
}
Write-Host ""
Write-Host "    CLI entry point: $reportSkill" -ForegroundColor DarkGray
Write-Host "    MCP entry point: $(Join-Path $venv 'Scripts\report-skill-mcp.exe')" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Try: report-skill templates list" -ForegroundColor Green
