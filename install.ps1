<#
.SYNOPSIS
  Install report-skill on the receiver machine.

.DESCRIPTION
  Steps:
    0. Resolve a real Python 3.11+ (python, then py -3.13/-3.12/-3.11;
       the Microsoft Store stub is detected and rejected)
    1. Create a Python venv in .\venv (or reuse if it exists)
    2. pip install the bundled wheel (offline via wheels\ when vendored)
    3. Interactive .env setup (server URL + service account credentials)
       + set user-scope REPORT_SKILL_ENV at the .env (with consent if it
       already points at another install)
    4. Smoke-test with `report-skill ping`
    5. Copy the Claude Code slash commands to %USERPROFILE%\.claude\skills\
       (skip with -SkipGlobalSkills)

  Re-runnable. Skips steps that are already done.

.PARAMETER ServerUrl
  REPORT_API_BASE_URL value (skips the prompt). Eg http://10.0.5.42:3000/api

.PARAMETER Email
  REPORT_API_EMAIL value (skips the prompt).

.PARAMETER Password
  REPORT_API_PASSWORD value (skips the prompt; you'd normally just paste it interactively).

.PARAMETER SkipGlobalSkills
  Don't copy .claude/skills/* to %USERPROFILE%\.claude\skills\.
  (Mirrors install-standalone.ps1 — by default the skills ARE installed
  globally so /report-write works from any cwd.)

.PARAMETER OverwriteEnvVar
  Repoint the user-scope REPORT_SKILL_ENV variable at THIS install's .env
  even when it currently points at a different install (e.g. an existing
  standalone install at %LOCALAPPDATA%\report-skill). Without this switch
  the installer warns and keeps the existing value (prompts when
  interactive).
#>
[CmdletBinding()]
param(
    [string]$ServerUrl,
    [string]$Email,
    [string]$Password,
    [string]$Workspace = "dx",
    [switch]$SkipGlobalSkills,
    [switch]$OverwriteEnvVar
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

# --- 0. resolve a usable Python (3.11+) ---
# A bare `python` on a fresh Windows box is often the Microsoft Store stub:
# a zero-length WindowsApps alias that opens the Store instead of running
# anything. Probe candidates in order and keep the first REAL 3.11+
# interpreter; otherwise fail with an actionable message.
function Resolve-Python {
    $candidates = @(
        @{ Cmd = 'python'; Args = @() },
        @{ Cmd = 'py';     Args = @('-3.13') },
        @{ Cmd = 'py';     Args = @('-3.12') },
        @{ Cmd = 'py';     Args = @('-3.11') }
    )
    foreach ($cand in $candidates) {
        $found = Get-Command $cand.Cmd -ErrorAction SilentlyContinue
        if (-not $found) { continue }
        # Store-stub fast path: lives under \WindowsApps\ as a 0-byte alias.
        if ($found.Source -and $found.Source -match '\\WindowsApps\\') {
            $stubFile = Get-Item $found.Source -ErrorAction SilentlyContinue
            if ($stubFile -and $stubFile.Length -eq 0) { continue }
        }
        # Must actually RUN and report >= 3.11. The Store stub also dies
        # here (exits non-zero) — belt and suspenders. Probe stderr is
        # merged + discarded so a missing `py -3.x` doesn't spam output.
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $null = & $cand.Cmd $cand.Args -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 2)" 2>&1
        $probeExit = $LASTEXITCODE
        $ErrorActionPreference = $prevEap
        if ($probeExit -eq 0) { return $cand }
    }
    return $null
}

# --- 1. venv ---
$venv = Join-Path $root "venv"
$venvPy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $py = Resolve-Python
    if (-not $py) {
        throw (@(
            "Python 3.11 이상을 찾지 못했습니다. / No usable Python 3.11+ found."
            "  1) https://www.python.org/downloads/ 에서 Python 3.11+ 를 설치하세요."
            "  2) 설치 화면에서 'Add python.exe to PATH' 를 반드시 체크하세요."
            "  Install Python 3.11+ from python.org with 'Add python.exe to PATH'"
            "  checked, open a NEW terminal, then re-run this installer."
            "  (Microsoft Store 의 python 스텁은 사용할 수 없습니다 /"
            "   the Microsoft Store python stub does not count.)"
        ) -join "`n")
    }
    $pyLabel = ("$($py.Cmd) $($py.Args -join ' ')").TrimEnd()
    Write-Host "    python: $pyLabel" -ForegroundColor DarkGray
    Write-Host "    creating venv..."
    & $py.Cmd $py.Args -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed (exit=$LASTEXITCODE). Is Python 3.11+ installed and on PATH?" }
} else {
    Write-Host "    venv already present" -ForegroundColor DarkGray
}

# --- 2. install wheel ---
$wheel = Get-ChildItem $root -Filter "*.whl" | Select-Object -First 1
if (-not $wheel) { throw "no .whl file found alongside install.ps1" }

# pip self-upgrade is best-effort: an offline / firewalled machine can't
# reach PyPI, and that must NOT kill the install — the venv's bundled pip
# is plenty for installing local wheels. Single attempt, warning only.
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$null = & $venvPy -m pip install --quiet --upgrade pip 2>&1
$ErrorActionPreference = $prev
if ($LASTEXITCODE -ne 0) {
    Write-Warning "pip self-upgrade failed (offline?) — continuing with the bundled pip"
}

Write-Host "    installing $($wheel.Name)..."
$wheelsDir = Join-Path $root "wheels"
if (Test-Path $wheelsDir) {
    # Vendored dependency wheels shipped in the zip (see build_release.ps1)
    # — fully offline install, no PyPI round-trip on the receiver.
    Write-Host "    using vendored dependencies from wheels\ (no PyPI access needed)" -ForegroundColor DarkGray
    & $venvPy -m pip install --quiet --force-reinstall --no-index --find-links $wheelsDir $wheel.FullName
} else {
    & $venvPy -m pip install --quiet --force-reinstall $wheel.FullName
}
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

# --- 3b. REPORT_SKILL_ENV so the CLI finds .env from any cwd ---
# Mirrors install-standalone.ps1: a user-scope discovery pointer at the
# absolute .env path. Careful: if a DIFFERENT install already owns the
# variable (e.g. the standalone variant at %LOCALAPPDATA%\report-skill),
# silently overwriting would hijack that install's credentials lookup —
# require explicit consent (-OverwriteEnvVar, or a prompt when interactive).
$existingPtr = [Environment]::GetEnvironmentVariable('REPORT_SKILL_ENV', 'User')
$setPtr = $true
if ($existingPtr -and $existingPtr -ne $envFile) {
    Write-Warning "REPORT_SKILL_ENV (user) already points at another install: $existingPtr"
    if ($OverwriteEnvVar) {
        Write-Host "    -OverwriteEnvVar set — repointing to $envFile" -ForegroundColor Yellow
    } elseif ($NonInteractive) {
        $setPtr = $false
        Write-Host "    keeping existing value (pass -OverwriteEnvVar to repoint)" -ForegroundColor Yellow
    } else {
        $ans = Read-Host "    다른 설치를 가리키고 있습니다 — 이 설치로 덮어쓸까요? / repoint to this install? (y/N)"
        if ($ans -notmatch '^[yY]') {
            $setPtr = $false
            Write-Host "    keeping existing value" -ForegroundColor Yellow
        }
    }
}
if ($setPtr -and $existingPtr -ne $envFile) {
    [Environment]::SetEnvironmentVariable('REPORT_SKILL_ENV', $envFile, 'User')
    Write-Host "    set REPORT_SKILL_ENV=$envFile (so the CLI finds .env from any cwd)" -ForegroundColor DarkGray
}
$env:REPORT_SKILL_ENV = $envFile  # this session too

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

# --- 5. Claude Code slash commands -> global skills dir ---
Write-Host ""
Write-Host "==> next steps" -ForegroundColor Cyan
$skillsDir = Join-Path $root ".claude\skills"
if (Test-Path $skillsDir) {
    Write-Host "    Claude Code skills shipped with this release:" -ForegroundColor DarkGray
    Get-ChildItem $skillsDir -Recurse -Filter "SKILL.md" | ForEach-Object {
        Write-Host "      /$($_.Directory.Name)" -ForegroundColor DarkGray
    }
    if (-not $SkipGlobalSkills) {
        # Actually install them globally (default ON, mirrors the standalone
        # installer) so /report-write works from any cwd — not just here.
        $globalSkills = Join-Path $env:USERPROFILE ".claude\skills"
        New-Item -ItemType Directory -Force -Path $globalSkills | Out-Null
        Copy-Item (Join-Path $skillsDir "*") -Destination $globalSkills -Recurse -Force
        Write-Host "    skills installed -> $globalSkills (opt out with -SkipGlobalSkills)" -ForegroundColor Green
    } else {
        Write-Host "    [-SkipGlobalSkills] to install them globally later, run:"
        Write-Host "      Copy-Item .claude\skills\* `$env:USERPROFILE\.claude\skills\ -Recurse -Force"
    }
    Write-Host "    Or just `cd $root` and they auto-load (project-scoped)."
}
Write-Host ""
Write-Host "    CLI entry point: $reportSkill" -ForegroundColor DarkGray
Write-Host "    MCP entry point: $(Join-Path $venv 'Scripts\report-skill-mcp.exe')" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Try: report-skill templates list" -ForegroundColor Green
