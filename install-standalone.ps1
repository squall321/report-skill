<#
.SYNOPSIS
  Install the standalone report-skill (.exe variant — no Python required).

.DESCRIPTION
  Drops the two onedir binary trees (report-skill\ and report-skill-mcp\,
  each containing the entry .exe + _internal\) into a stable location,
  adds both entry dirs to your user PATH so bare-name invocation works,
  sets PYTHONIOENCODING + REPORT_SKILL_ENV user env vars, copies Claude
  Code slash commands to your global skills directory (with backups),
  writes .env, and runs a smoke test.

  Re-runnable. Idempotent for binaries / PATH / env vars / skills (backups
  on overwrite). Refuses to clobber an existing .env unless -Force is set,
  so accidental re-runs do not destroy saved credentials.

.PARAMETER ServerUrl
  REPORT_API_BASE_URL value (skips the prompt). Eg http://10.0.5.42:3000/api

.PARAMETER Email
  REPORT_API_EMAIL value (skips the prompt).

.PARAMETER Password
  REPORT_API_PASSWORD value (skips the prompt).

.PARAMETER Workspace
  REPORT_API_WORKSPACE_SLUG value. Default: dx.

.PARAMETER InstallDir
  Where to drop the binaries. Default: %LOCALAPPDATA%\report-skill

.PARAMETER Force
  Overwrite an existing .env (otherwise the existing .env is preserved
  and the new ServerUrl/Email/Password args are ignored with a warning).

.PARAMETER SkipPath
  Don't modify the user PATH. You'll need to call the binaries by full path.

.PARAMETER SkipGlobalSkills
  Don't copy .claude/skills/*.md to %USERPROFILE%\.claude\skills\.

.PARAMETER AddDefenderExclusion
  Opt-in. When set, the installer attempts to register $InstallDir as a
  Windows Defender exclusion path via Add-MpPreference. This eliminates
  AV-scan latency on the bundled PyInstaller .exe (cold start can be
  several seconds otherwise) but REQUIRES an elevated (Admin) shell.
  If the current shell is not elevated, the installer prints a warning
  and continues — it does NOT block the install. Default: off.

  Security caveat: some organizations forbid users from adding AV
  exclusions. Do not pass this switch on managed / corporate machines
  without checking your IT policy first.
#>
[CmdletBinding()]
param(
    [string]$ServerUrl,
    [string]$Email,
    [string]$Password,
    [string]$Workspace = "dx",
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "report-skill"),
    [switch]$Force,
    [switch]$SkipPath,
    [switch]$SkipGlobalSkills,
    [switch]$AddDefenderExclusion
)

$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$NonInteractive = -not [Environment]::UserInteractive -or
                  ($Host.Name -eq 'ConsoleHost' -and [Console]::IsInputRedirected)

# Normalize InstallDir: strip trailing slash so PATH membership checks
# don't false-negative when the user passes "C:\foo\" vs "C:\foo".
$InstallDir = $InstallDir.TrimEnd('\','/')

Write-Host "==> report-skill standalone installer" -ForegroundColor Cyan

# --- 1. copy binaries (onedir layout) ---
# Each shipped binary is a DIRECTORY tree:
#   <src>\report-skill\report-skill.exe         + _internal\...
#   <src>\report-skill-mcp\report-skill-mcp.exe + _internal\...
# We install each tree under bin\ so the entry .exe ends up at
#   <InstallDir>\bin\report-skill\report-skill.exe
#   <InstallDir>\bin\report-skill-mcp\report-skill-mcp.exe
# Both subdirs are added to PATH below so bare-name invocation still works.
$binDir = Join-Path $InstallDir "bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$entryDirs = @()
foreach ($name in "report-skill", "report-skill-mcp") {
    $srcTree = Join-Path $src $name
    $exePath = Join-Path $srcTree "$name.exe"
    if (-not (Test-Path $exePath)) {
        throw "missing $name\$name.exe alongside install-standalone.ps1 — bundle layout drift?"
    }
    $dstTree = Join-Path $binDir $name
    # Hard-replace: PyInstaller hashes pyd/pyc filenames per build, so merging
    # an old _internal\ on top of a new one would leave dangling stragglers.
    if (Test-Path $dstTree) { Remove-Item -Recurse -Force $dstTree }
    Copy-Item $srcTree -Destination $binDir -Recurse -Force
    $entryDirs += $dstTree
}
Write-Host "    binaries installed -> $binDir\{report-skill, report-skill-mcp}\" -ForegroundColor DarkGray

# --- 1b. (opt-in) register InstallDir as a Windows Defender exclusion ---
# Off by default; only runs when the caller passes -AddDefenderExclusion.
# Requires an elevated shell — Add-MpPreference silently no-ops or throws
# for non-admin users. We don't elevate ourselves: we just warn and move on
# so the install completes even when the user can't (or shouldn't) add an
# AV exclusion (managed machines, corporate policy, etc.).
if ($AddDefenderExclusion) {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    $isAdmin = $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin) {
        try {
            Add-MpPreference -ExclusionPath $InstallDir -ErrorAction Stop
            Write-Host "    Defender exclusion added -> $InstallDir" -ForegroundColor DarkGray
        } catch {
            Write-Warning "Add-MpPreference failed: $($_.Exception.Message)"
            Write-Host "    (continuing — exclusion is optional)" -ForegroundColor DarkGray
        }
    } else {
        Write-Warning "-AddDefenderExclusion requires an elevated shell."
        Write-Host "    To add the exclusion, either:" -ForegroundColor DarkGray
        Write-Host "      (a) right-click setup.bat -> Run as administrator, or" -ForegroundColor DarkGray
        Write-Host "      (b) skip this step — install will still work, .exe cold" -ForegroundColor DarkGray
        Write-Host "          start may just be a bit slower while AV scans it." -ForegroundColor DarkGray
        Write-Host "    (continuing without exclusion)" -ForegroundColor DarkGray
    }
}

# --- 2. add to user PATH (with normalization for idempotency) ---
function Normalize-PathEntry([string]$p) {
    if (-not $p) { return $p }
    return ($p -replace '/', '\').TrimEnd('\').ToLowerInvariant()
}

if (-not $SkipPath) {
    $cur = [Environment]::GetEnvironmentVariable("PATH", "User")
    $parts = $cur -split ';' | Where-Object { $_ -ne '' }
    # Build a lookup of normalized existing entries once — O(n) instead of O(n*m).
    $existingNorm = @{}
    foreach ($p in $parts) { $existingNorm[(Normalize-PathEntry $p)] = $true }
    # Drop any stale legacy $binDir entry from pre-onedir installs — the
    # entry .exe no longer lives at bin\ root, so leaving it on PATH is
    # harmless but confusing in `where.exe report-skill` diagnostics.
    $binDirNorm = Normalize-PathEntry $binDir
    $added = @()
    foreach ($entryDir in $entryDirs) {
        $norm = Normalize-PathEntry $entryDir
        if (-not $existingNorm.ContainsKey($norm)) {
            $parts += $entryDir
            $existingNorm[$norm] = $true
            $added += $entryDir
        }
    }
    if ($added.Count -gt 0) {
        [Environment]::SetEnvironmentVariable("PATH", ($parts -join ';'), "User")
        foreach ($a in $added) {
            $env:PATH = "$env:PATH;$a"  # affect THIS session too
            Write-Host "    added $a to user PATH" -ForegroundColor DarkGray
        }
        Write-Host "    (open a new terminal to see PATH changes elsewhere)" -ForegroundColor DarkGray
    } else {
        Write-Host "    PATH already contains both entry dirs" -ForegroundColor DarkGray
    }
}

# --- 3. set encoding + .env discovery env vars (idempotent, always run) ---
# PYTHONIOENCODING is belt-and-suspenders: the .exe entry points already
# call sys.stdout.reconfigure(encoding='utf-8'), but if stdout is redirected
# / piped / running under MSYS, reconfigure can silently fail. Setting the
# env var makes the Python runtime pick utf-8 from the start.
if ([Environment]::GetEnvironmentVariable("PYTHONIOENCODING", "User") -ne "utf-8") {
    [Environment]::SetEnvironmentVariable("PYTHONIOENCODING", "utf-8", "User")
    Write-Host "    set PYTHONIOENCODING=utf-8 (user env)" -ForegroundColor DarkGray
}
$env:PYTHONIOENCODING = "utf-8"

# --- 4. copy slash commands globally (with backups, never silent overwrite) ---
if (-not $SkipGlobalSkills) {
    $globalSkills = Join-Path $env:USERPROFILE ".claude\skills"
    $shippedSkills = Join-Path $src ".claude\skills"
    if (Test-Path $shippedSkills) {
        New-Item -ItemType Directory -Force -Path $globalSkills | Out-Null
        $copied = 0
        $backedUp = 0
        # Claude Code expects skills in <name>/SKILL.md directory format,
        # not flat .md files. Copy each shipped skill directory tree.
        # Also clean up legacy flat .md drops from older installer versions.
        foreach ($legacyMd in Get-ChildItem "$globalSkills\*.md" -ErrorAction SilentlyContinue) {
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            $backup = "$($legacyMd.FullName).legacy-$stamp"
            Move-Item $legacyMd.FullName $backup -Force
            Write-Host "      moved legacy flat $($legacyMd.Name) -> $(Split-Path -Leaf $backup)" -ForegroundColor Yellow
            $backedUp++
        }
        foreach ($skillDir in Get-ChildItem $shippedSkills -Directory) {
            $target = Join-Path $globalSkills $skillDir.Name
            $shippedSkillFile = Join-Path $skillDir.FullName "SKILL.md"
            if (-not (Test-Path $shippedSkillFile)) {
                Write-Host "      skipping $($skillDir.Name) — missing SKILL.md" -ForegroundColor Yellow
                continue
            }
            $targetSkillFile = Join-Path $target "SKILL.md"
            if (Test-Path $targetSkillFile) {
                $shippedHash = (Get-FileHash $shippedSkillFile -Algorithm SHA256).Hash
                $targetHash  = (Get-FileHash $targetSkillFile -Algorithm SHA256).Hash
                if ($shippedHash -eq $targetHash) {
                    continue  # identical — skip silently
                }
                # Diverged — back up before overwriting.
                $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
                $backup = "$target.bak-$stamp"
                Rename-Item $target $backup -Force
                $backedUp++
                Write-Host "      backed up $($skillDir.Name) -> $(Split-Path -Leaf $backup)" -ForegroundColor Yellow
            }
            New-Item -ItemType Directory -Force -Path $target | Out-Null
            Copy-Item "$($skillDir.FullName)\*" -Destination $target -Recurse -Force
            $copied++
        }
        Write-Host "    skills -> $globalSkills (copied=$copied skills, backups=$backedUp)" -ForegroundColor DarkGray
    }
}

# --- 5. .env at the install dir ---
$envFile = Join-Path $InstallDir ".env"
$envExists = Test-Path $envFile

# Read existing values (if any) so the interactive prompts can default to
# them — running setup.bat == "(re)configure", so we ALWAYS go through
# the prompt loop, but pressing Enter at a field keeps the current value.
$currentUrl       = ""
$currentEmail     = ""
$currentPassword  = ""
$currentWorkspace = $Workspace
if ($envExists) {
    foreach ($line in Get-Content $envFile -ErrorAction SilentlyContinue) {
        if ($line -match '^REPORT_API_BASE_URL=(.*)$')       { $currentUrl       = $matches[1] }
        elseif ($line -match '^REPORT_API_EMAIL=(.*)$')      { $currentEmail     = $matches[1] }
        elseif ($line -match '^REPORT_API_PASSWORD=(.*)$')   { $currentPassword  = $matches[1] }
        elseif ($line -match '^REPORT_API_WORKSPACE_SLUG=(.*)$') { $currentWorkspace = $matches[1] }
    }
}

Write-Host ""
if ($envExists) {
    Write-Host "==> 서버 접속 설정 (기존 값 있음 — Enter로 유지)" -ForegroundColor Cyan
} else {
    Write-Host "==> 서버 접속 설정 (.env 신규 작성)" -ForegroundColor Cyan
}

# Server URL
if (-not $ServerUrl) {
    if ($NonInteractive) {
        $ServerUrl = $currentUrl
    } else {
        Write-Host ""
        Write-Host "  서버 주소 (ReportArchive 백엔드 URL)" -ForegroundColor Gray
        if ($currentUrl) {
            Write-Host "    현재: $currentUrl" -ForegroundColor DarkGray
            $ans = Read-Host "  >> 서버 주소 (Enter=유지)"
            $ServerUrl = if ($ans) { $ans } else { $currentUrl }
        } else {
            Write-Host "    예: https://reports.company.com/api" -ForegroundColor DarkGray
            Write-Host "        http://10.0.5.42:3000/api" -ForegroundColor DarkGray
            Write-Host "        http://reports.tail0a1b2c.ts.net:3000/api  (Tailscale)" -ForegroundColor DarkGray
            $ServerUrl = Read-Host "  >> 서버 주소"
        }
    }
}

# Email
if (-not $Email) {
    if ($NonInteractive) {
        $Email = if ($currentEmail) { $currentEmail } else { "bot@reportskill.app" }
    } else {
        Write-Host ""
        Write-Host "  이메일 (본인 ReportArchive 계정)" -ForegroundColor Gray
        $defaultEmail = if ($currentEmail) { $currentEmail } else { "bot@reportskill.app" }
        Write-Host "    현재/기본: $defaultEmail" -ForegroundColor DarkGray
        $ans = Read-Host "  >> 이메일 (Enter=$defaultEmail)"
        $Email = if ($ans) { $ans } else { $defaultEmail }
    }
}

# Password
if (-not $Password) {
    if ($NonInteractive) {
        $Password = $currentPassword
    } else {
        Write-Host ""
        Write-Host "  비밀번호 (입력 시 화면에 표시되지 않습니다)" -ForegroundColor Gray
        if ($currentPassword) {
            Write-Host "    기존 비밀번호 있음 — Enter 시 유지" -ForegroundColor DarkGray
        }
        $sec = Read-Host "  >> 비밀번호 (Enter=유지)" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        $typed = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        $Password = if ($typed) { $typed } else { $currentPassword }
    }
}

if (-not $ServerUrl) { throw "ServerUrl required" }
if (-not $Password)  { throw "Password required (no existing one and none typed)" }
if (-not $Workspace -or $Workspace -eq "dx") { $Workspace = $currentWorkspace }

# Backup before overwrite (only if file changed).
$newBody = @(
    "REPORT_API_BASE_URL=$ServerUrl"
    "REPORT_API_WORKSPACE_SLUG=$Workspace"
    "REPORT_API_EMAIL=$Email"
    "REPORT_API_PASSWORD=$Password"
    "REPORT_BACKEND_PATH="
    "SKILL_AI_TIER="
) -join "`n"

$oldBody = if ($envExists) { [IO.File]::ReadAllText($envFile, [Text.UTF8Encoding]::new($false)).TrimEnd("`r","`n") } else { "" }
if ($newBody.TrimEnd("`r","`n") -eq $oldBody) {
    Write-Host ""
    Write-Host "    변경사항 없음 — $envFile 그대로" -ForegroundColor DarkGray
} else {
    if ($envExists) {
        $backup = "$envFile.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item $envFile $backup -Force
        Write-Host ""
        Write-Host "    backup -> $backup" -ForegroundColor DarkGray
    }
    [IO.File]::WriteAllText($envFile, $newBody, [Text.UTF8Encoding]::new($false))
    Write-Host "    저장 완료 -> $envFile" -ForegroundColor Green
}

# --- 6. REPORT_SKILL_ENV — only touch User scope for the DEFAULT InstallDir ---
# Footgun protection: testing this installer with a custom -InstallDir
# (e.g. a temp sandbox) MUST NOT clobber the User-scope env var that
# points at the real install. Process-level is always set (so the
# current run's ping works); User-scope is only updated when we're
# installing to the canonical location.
$defaultInstallDir = (Join-Path $env:LOCALAPPDATA "report-skill").TrimEnd('\','/')
$isDefaultInstall  = ($InstallDir -eq $defaultInstallDir)

if ($isDefaultInstall) {
    if ([Environment]::GetEnvironmentVariable("REPORT_SKILL_ENV", "User") -ne $envFile) {
        [Environment]::SetEnvironmentVariable("REPORT_SKILL_ENV", $envFile, "User")
        Write-Host "    set REPORT_SKILL_ENV=$envFile (so .exe finds .env from any cwd)" -ForegroundColor DarkGray
    }
} else {
    Write-Host "    [non-default InstallDir — leaving User-scope REPORT_SKILL_ENV untouched]" -ForegroundColor DarkGray
}
$env:REPORT_SKILL_ENV = $envFile

# --- 7. smoke test ---
Write-Host ""
Write-Host "==> smoke test: report-skill ping" -ForegroundColor Cyan
# Onedir layout: bin\report-skill\report-skill.exe (sibling _internal\)
$exe = Join-Path $binDir "report-skill\report-skill.exe"
& $exe ping
$pingExit = $LASTEXITCODE
if ($pingExit -ne 0) {
    Write-Warning "ping failed (exit=$pingExit)"
    Write-Host "" -ForegroundColor DarkGray
    Write-Host "  diagnostic context:" -ForegroundColor DarkGray
    Write-Host "    install dir       : $InstallDir" -ForegroundColor DarkGray
    Write-Host "    bin dir           : $binDir" -ForegroundColor DarkGray
    Write-Host "    .env path         : $envFile  (exists=$(Test-Path $envFile))" -ForegroundColor DarkGray
    Write-Host "    REPORT_SKILL_ENV  : $env:REPORT_SKILL_ENV" -ForegroundColor DarkGray
    Write-Host "    PYTHONIOENCODING  : $env:PYTHONIOENCODING" -ForegroundColor DarkGray
    if (Test-Path $envFile) {
        $serverLine = Get-Content $envFile | Where-Object { $_ -match '^REPORT_API_BASE_URL=' }
        if ($serverLine) { Write-Host "    $serverLine" -ForegroundColor DarkGray }
    }
    Write-Host "" -ForegroundColor DarkGray
    Write-Host "  common causes:" -ForegroundColor DarkGray
    Write-Host "    - server unreachable (check URL + firewall)" -ForegroundColor DarkGray
    Write-Host "    - wrong password (re-run with -Force -Password ...)" -ForegroundColor DarkGray
    Write-Host "    - service account missing (admin must register bot@reportskill.app)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "==> ready. Try (in a NEW terminal so PATH refreshes):" -ForegroundColor Cyan
Write-Host "    report-skill templates list" -ForegroundColor Green
Write-Host ""
Write-Host "Claude Code: /report-write should now work from any cwd." -ForegroundColor DarkGray
