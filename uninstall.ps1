<#
.SYNOPSIS
  Uninstall report-skill (standalone .exe variant; also cleans the
  machine-level traces left by the wheel variant).

.DESCRIPTION
  Reverses what the installers set up. Every step is individually guarded
  and reported — one failing step never blocks the rest:

    1. user PATH entries pointing into ...\report-skill\bin
       (onedir subdirs AND the legacy --onefile bin root)
    2. user-scope env vars: REPORT_SKILL_ENV, and PYTHONIOENCODING
       (the latter ONLY if it is exactly 'utf-8' — i.e. ours)
    3. Windows Defender exclusion for <InstallDir>, if present
       (needs an elevated shell; degrades to a printed instruction)
    4. <InstallDir> (%LOCALAPPDATA%\report-skill): bin\ + voc\ are removed
       outright; .env (+ backups) and logs\ ask first (or -Purge)
    5. global Claude Code skills (report-write, widgets-sync,
       bridge-process) — asks first (or -Skills)
    6. prints what a WHEEL-variant user must delete manually
       (their unzip folder + venv — this script can't know where it is)

.PARAMETER InstallDir
  Standalone install root. Default: %LOCALAPPDATA%\report-skill

.PARAMETER Purge
  Delete .env (+ .env backups) and logs\ without prompting.

.PARAMETER Skills
  Remove the global Claude Code skills without prompting.

.PARAMETER Force
  Kill running report-skill / report-skill-mcp processes instead of
  skipping the binary removal.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "report-skill"),
    [switch]$Purge,
    [switch]$Skills,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'   # every risky op sits in its own try/catch
$InstallDir = $InstallDir.TrimEnd('\','/')
$NonInteractive = -not [Environment]::UserInteractive -or
                  ($Host.Name -eq 'ConsoleHost' -and [Console]::IsInputRedirected)

Write-Host "==> report-skill uninstaller" -ForegroundColor Cyan
Write-Host "    target install dir: $InstallDir" -ForegroundColor DarkGray

# Shared y/N gate: pre-approved by switch, refused when non-interactive
# (so a scripted run never hangs on Read-Host).
function Confirm-Step([string]$Question, [bool]$PreApproved) {
    if ($PreApproved) { return $true }
    if ($NonInteractive) {
        Write-Host "    [non-interactive: skipping — re-run with the matching switch]" -ForegroundColor Yellow
        return $false
    }
    $ans = Read-Host "  >> $Question (y/N)"
    return ($ans -match '^[yY]')
}

# --- 1. user PATH cleanup ---
Write-Host ""
Write-Host "==> [1/6] user PATH" -ForegroundColor Cyan
try {
    $cur = [Environment]::GetEnvironmentVariable("PATH", "User")
    $parts = @($cur -split ';' | Where-Object { $_ -ne '' })
    $keep = @()
    $dropped = @()
    foreach ($p in $parts) {
        # Match every historical variant: ...\report-skill\bin (legacy
        # --onefile root), ...\bin\report-skill, ...\bin\report-skill-mcp.
        $norm = ($p -replace '/', '\').TrimEnd('\').ToLowerInvariant()
        if ($norm -like '*\report-skill\bin*') { $dropped += $p } else { $keep += $p }
    }
    if ($dropped.Count -gt 0) {
        [Environment]::SetEnvironmentVariable("PATH", ($keep -join ';'), "User")
        foreach ($d in $dropped) {
            Write-Host "    removed from PATH: $d" -ForegroundColor DarkGray
        }
        Write-Host "    (open a new terminal to see PATH changes elsewhere)" -ForegroundColor DarkGray
    } else {
        Write-Host "    no report-skill entries on user PATH" -ForegroundColor DarkGray
    }
} catch {
    Write-Warning "PATH cleanup failed: $($_.Exception.Message)"
}

# --- 2. user-scope env vars ---
Write-Host ""
Write-Host "==> [2/6] user env vars" -ForegroundColor Cyan
try {
    $ptr = [Environment]::GetEnvironmentVariable("REPORT_SKILL_ENV", "User")
    if ($ptr) {
        [Environment]::SetEnvironmentVariable("REPORT_SKILL_ENV", $null, "User")
        Write-Host "    removed REPORT_SKILL_ENV (was: $ptr)" -ForegroundColor DarkGray
    } else {
        Write-Host "    REPORT_SKILL_ENV not set" -ForegroundColor DarkGray
    }
} catch {
    Write-Warning "REPORT_SKILL_ENV removal failed: $($_.Exception.Message)"
}
try {
    $enc = [Environment]::GetEnvironmentVariable("PYTHONIOENCODING", "User")
    if ($enc -eq "utf-8") {
        # Exactly the value our installer set — safe to remove.
        [Environment]::SetEnvironmentVariable("PYTHONIOENCODING", $null, "User")
        Write-Host "    removed PYTHONIOENCODING=utf-8" -ForegroundColor DarkGray
    } elseif ($enc) {
        Write-Warning "PYTHONIOENCODING=$enc was not set by report-skill — leaving it alone"
    } else {
        Write-Host "    PYTHONIOENCODING not set" -ForegroundColor DarkGray
    }
} catch {
    Write-Warning "PYTHONIOENCODING removal failed: $($_.Exception.Message)"
}

# --- 3. Windows Defender exclusion (opt-in at install time) ---
Write-Host ""
Write-Host "==> [3/6] Windows Defender exclusion" -ForegroundColor Cyan
try {
    # Reading exclusions may be hidden from non-admins (newer Defender
    # returns "N/A: Must be an administrator..."): treat that as unknown.
    $exclusionState = "unknown"
    try {
        $ex = @((Get-MpPreference -ErrorAction Stop).ExclusionPath) | Where-Object { $_ }
        if ($ex -match '^N/A') {
            $exclusionState = "unknown"
        } elseif (@($ex | Where-Object { $_.TrimEnd('\') -ieq $InstallDir }).Count -gt 0) {
            $exclusionState = "present"
        } else {
            $exclusionState = "absent"
        }
    } catch { }

    if ($exclusionState -eq "absent") {
        Write-Host "    no Defender exclusion for $InstallDir" -ForegroundColor DarkGray
    } else {
        $principal = New-Object Security.Principal.WindowsPrincipal(
            [Security.Principal.WindowsIdentity]::GetCurrent())
        $isAdmin = $principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)
        if ($isAdmin) {
            Remove-MpPreference -ExclusionPath $InstallDir -ErrorAction Stop
            Write-Host "    Defender exclusion removed -> $InstallDir" -ForegroundColor DarkGray
        } else {
            Write-Warning "cannot remove the Defender exclusion from a non-elevated shell."
            Write-Host "    If one was added at install time (-AddDefenderExclusion)," -ForegroundColor DarkGray
            Write-Host "    run this in an ADMIN PowerShell:" -ForegroundColor DarkGray
            Write-Host "      Remove-MpPreference -ExclusionPath '$InstallDir'" -ForegroundColor DarkGray
        }
    }
} catch {
    Write-Warning "Defender exclusion removal failed: $($_.Exception.Message)"
    Write-Host "    Run this in an ADMIN PowerShell:" -ForegroundColor DarkGray
    Write-Host "      Remove-MpPreference -ExclusionPath '$InstallDir'" -ForegroundColor DarkGray
}

# --- 4. install dir ---
Write-Host ""
Write-Host "==> [4/6] install dir: $InstallDir" -ForegroundColor Cyan
if (Test-Path $InstallDir) {
    # Running processes hold locks on bin\ — removal would fail halfway.
    $skipBin = $false
    try {
        $running = @(Get-Process -Name "report-skill*" -ErrorAction SilentlyContinue |
                     Where-Object { $_.Path -and $_.Path -like "$InstallDir\*" })
        if ($running.Count -gt 0) {
            $names = ($running | ForEach-Object { "$($_.ProcessName) (pid $($_.Id))" }) -join ', '
            if ($Force) {
                Write-Warning "killing running processes (-Force): $names"
                $running | Stop-Process -Force
                Start-Sleep -Seconds 1   # let file handles release
            } else {
                Write-Warning "report-skill is running: $names"
                Write-Host "    Claude Desktop / Claude Code 를 종료한 뒤 다시 실행하거나 -Force 를 쓰세요." -ForegroundColor Yellow
                Write-Host "    (Quit Claude Desktop / Claude Code, or re-run with -Force.)" -ForegroundColor Yellow
                $skipBin = $true
            }
        }
    } catch {
        Write-Warning "process check failed: $($_.Exception.Message)"
    }

    # 4a. binaries + voc cache — no prompt, they are fully regenerable.
    foreach ($sub in @("bin", "voc")) {
        $subPath = Join-Path $InstallDir $sub
        if (-not (Test-Path $subPath)) { continue }
        if ($sub -eq "bin" -and $skipBin) {
            Write-Host "    skipped bin\ (processes still running)" -ForegroundColor Yellow
            continue
        }
        try {
            Remove-Item -Recurse -Force $subPath
            Write-Host "    removed $sub\" -ForegroundColor DarkGray
        } catch {
            Write-Warning "could not remove ${subPath}: $($_.Exception.Message)"
        }
    }

    # 4b. credentials + logs — destructive for the USER, so ask (or -Purge).
    try {
        $envFiles = @(Get-ChildItem (Join-Path $InstallDir ".env*") -Force -ErrorAction SilentlyContinue)
        $logsDir = Join-Path $InstallDir "logs"
        $hasLogs = Test-Path $logsDir
        if ($envFiles.Count -gt 0 -or $hasLogs) {
            $q = "저장된 접속정보(.env)와 로그를 삭제할까요? / delete saved credentials (.env) and logs?"
            if (Confirm-Step $q $Purge.IsPresent) {
                foreach ($f in $envFiles) {
                    Remove-Item -Force $f.FullName
                    Write-Host "    removed $($f.Name)" -ForegroundColor DarkGray
                }
                if ($hasLogs) {
                    Remove-Item -Recurse -Force $logsDir
                    Write-Host "    removed logs\" -ForegroundColor DarkGray
                }
            } else {
                Write-Host "    kept .env / logs (delete later with -Purge)" -ForegroundColor Yellow
            }
        }
    } catch {
        Write-Warning ".env/logs removal failed: $($_.Exception.Message)"
    }

    # 4c. drop the dir itself once nothing (or only this script's copy) is left.
    try {
        $leftovers = @(Get-ChildItem $InstallDir -Force -ErrorAction SilentlyContinue)
        $onlyUninstaller = ($leftovers.Count -eq 1 -and $leftovers[0].Name -eq 'uninstall.ps1')
        if ($leftovers.Count -eq 0 -or $onlyUninstaller) {
            Remove-Item -Recurse -Force $InstallDir
            Write-Host "    removed $InstallDir" -ForegroundColor DarkGray
        } else {
            Write-Host "    left in place (not empty): $InstallDir" -ForegroundColor Yellow
        }
    } catch {
        Write-Warning "install dir removal failed: $($_.Exception.Message)"
    }
} else {
    Write-Host "    not present — nothing to do" -ForegroundColor DarkGray
}

# --- 5. global Claude Code skills ---
Write-Host ""
Write-Host "==> [5/6] global Claude Code skills" -ForegroundColor Cyan
try {
    $globalSkills = Join-Path $env:USERPROFILE ".claude\skills"
    $ours = @("report-write", "widgets-sync", "bridge-process")
    $present = @($ours | Where-Object { Test-Path (Join-Path $globalSkills $_) })
    if ($present.Count -gt 0) {
        $q = "전역 슬래시 명령($($present -join ', '))을 삭제할까요? / remove global skills?"
        if (Confirm-Step $q $Skills.IsPresent) {
            foreach ($s in $present) {
                Remove-Item -Recurse -Force (Join-Path $globalSkills $s)
                Write-Host "    removed $globalSkills\$s" -ForegroundColor DarkGray
            }
        } else {
            Write-Host "    kept skills (remove later with -Skills)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "    none of ours installed in $globalSkills" -ForegroundColor DarkGray
    }
} catch {
    Write-Warning "skills removal failed: $($_.Exception.Message)"
}

# --- 6. wheel-variant leftovers (manual) ---
Write-Host ""
Write-Host "==> [6/6] wheel-variant leftovers (manual step)" -ForegroundColor Cyan
Write-Host "    If you installed the WHEEL variant (unzip + install.ps1), this" -ForegroundColor DarkGray
Write-Host "    script cannot know where you unzipped it. Delete that folder" -ForegroundColor DarkGray
Write-Host "    yourself — it holds venv\, the .whl and its own .env:" -ForegroundColor DarkGray
Write-Host "      Remove-Item -Recurse -Force <your-unzip-folder>" -ForegroundColor DarkGray
Write-Host "    (wheel 설치본은 압축 푼 폴더를 직접 삭제하세요.)" -ForegroundColor DarkGray

Write-Host ""
Write-Host "==> uninstall finished." -ForegroundColor Green
