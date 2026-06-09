<#
.SYNOPSIS
  ReportArchive repo watcher — daily diff scanner for report-skill release planning.

.DESCRIPTION
  Cycles report-skill v0.4 -> v0.11 (matched by audit history) repeatedly hit the
  same trap: a user had to *notice* the RA repo had moved and then trigger an
  audit by hand. This script removes that step. Run it on a cron / scheduled
  task; it tells you which RA commits since the last check are likely to need
  matching work in report-skill, and which are pure frontend UX (skip).

  Output:
    1. Commit list grouped by conventional prefix (feat / fix / refactor / ...).
    2. File-impact tally: backend-Python changes, frontend-only changes,
       migration files added.
    3. A recommended next action (run a deeper audit, or no-op).
    4. Persists the current HEAD sha so the next run starts from there.

.PARAMETER RaPath
  Path to the ReportArchive checkout. Default d:/ReportArchive.

.PARAMETER SinceSha
  Optional commit sha to scan FROM. If omitted, picks up from the persisted
  last-check sha (or HEAD~30 if first run).

.PARAMETER StatePath
  Where the last-check sha is persisted. Default %LOCALAPPDATA%/report-skill/.

.EXAMPLE
  .\scripts\watch_ra.ps1
  Reads since the persisted sha; persists current HEAD on exit.

.EXAMPLE
  .\scripts\watch_ra.ps1 -SinceSha v0.11.0
  Force-scan since the v0.11.0 release point.
#>
[CmdletBinding()]
param(
    [string]$RaPath = "d:/ReportArchive",
    [string]$SinceSha = "",
    [string]$StatePath = "$env:LOCALAPPDATA/report-skill/last_ra_check.txt"
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $RaPath)) {
    Write-Error "RA path not found: $RaPath"
    exit 1
}

# ---- resolve the starting sha --------------------------------------------- #
if (-not $SinceSha) {
    if (Test-Path $StatePath) {
        $SinceSha = (Get-Content $StatePath -Raw).Trim()
    }
}
if (-not $SinceSha) {
    $SinceSha = "HEAD~30"
}

Push-Location $RaPath
try {
    $headSha = (git rev-parse --short HEAD).Trim()
    $headDate = (git log -1 --pretty="%ai").Trim()

    # Validate $SinceSha resolves; if not, fall back to HEAD~30
    $null = git rev-parse $SinceSha 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Warning: $SinceSha not found — falling back to HEAD~30" -ForegroundColor Yellow
        $SinceSha = "HEAD~30"
    }

    # ---- collect commits -------------------------------------------------- #
    $rawLog = git log "$SinceSha..HEAD" --pretty="%h|%ai|%s"
    if (-not $rawLog) {
        Write-Host "ReportArchive: no new commits since $SinceSha" -ForegroundColor Green
        Write-Host "Current HEAD: $headSha ($headDate)" -ForegroundColor DarkGray
        # Persist anyway so we advance the cursor.
        $null = New-Item -ItemType Directory -Force -Path (Split-Path $StatePath) -ErrorAction SilentlyContinue
        $headSha | Out-File -FilePath $StatePath -Encoding utf8 -NoNewline
        return
    }

    $categories = [ordered]@{
        feat     = New-Object System.Collections.ArrayList
        fix      = New-Object System.Collections.ArrayList
        refactor = New-Object System.Collections.ArrayList
        perf     = New-Object System.Collections.ArrayList
        chore    = New-Object System.Collections.ArrayList
        docs     = New-Object System.Collections.ArrayList
        style    = New-Object System.Collections.ArrayList
        other    = New-Object System.Collections.ArrayList
    }
    foreach ($line in $rawLog -split "`n") {
        if (-not $line) { continue }
        $parts = $line -split "\|", 3
        if ($parts.Length -lt 3) { continue }
        $sha = $parts[0].Trim()
        $date = ($parts[1] -split " ")[0]
        $subj = $parts[2].Trim()
        $cat = "other"
        foreach ($k in "feat", "fix", "refactor", "perf", "chore", "docs", "style") {
            if ($subj -match "^$k(\(|:)") { $cat = $k; break }
        }
        $null = $categories[$cat].Add("$sha  $date  $subj")
    }

    $totalCount = ($rawLog -split "`n" | Where-Object { $_ }).Count

    # ---- file impact tally ----------------------------------------------- #
    $backendFiles = @(git log "$SinceSha..HEAD" --name-only --pretty=format: -- 'backend/**/*.py' | Where-Object { $_ -and $_ -notmatch '^backend/tests/' } | Sort-Object -Unique)
    $frontendFiles = @(git log "$SinceSha..HEAD" --name-only --pretty=format: -- 'frontend/**' | Where-Object { $_ } | Sort-Object -Unique)
    $migrationFiles = @(git log "$SinceSha..HEAD" --name-only --pretty=format: -- 'backend/migrations/versions/**' | Where-Object { $_ } | Sort-Object -Unique)
    $routesChanged = @(git log "$SinceSha..HEAD" --name-only --pretty=format: -- 'backend/**/routes.py' | Where-Object { $_ } | Sort-Object -Unique)
    $registryChanged = @(git log "$SinceSha..HEAD" --name-only --pretty=format: -- 'backend/app/widgets/registry.py' | Where-Object { $_ } | Sort-Object -Unique)
    $schemaChanged = @(git log "$SinceSha..HEAD" --name-only --pretty=format: -- 'backend/**/schemas.py' | Where-Object { $_ } | Sort-Object -Unique)

    # ---- print summary --------------------------------------------------- #
    Write-Host ""
    Write-Host "ReportArchive : $totalCount new commits since $SinceSha (HEAD = $headSha)" -ForegroundColor Cyan
    Write-Host ""

    foreach ($k in $categories.Keys) {
        if ($categories[$k].Count -gt 0) {
            Write-Host "  [$k] $($categories[$k].Count)" -ForegroundColor Yellow
            foreach ($e in $categories[$k]) { Write-Host "    $e" -ForegroundColor DarkGray }
        }
    }
    Write-Host ""

    Write-Host "  File impact" -ForegroundColor Magenta
    Write-Host "    backend Python files    : $($backendFiles.Count)" -ForegroundColor DarkGray
    Write-Host "    frontend files          : $($frontendFiles.Count)" -ForegroundColor DarkGray
    Write-Host "    migrations added        : $($migrationFiles.Count)" -ForegroundColor DarkGray
    Write-Host "    routes.py touched       : $($routesChanged.Count)" -ForegroundColor DarkGray
    Write-Host "    schemas.py touched      : $($schemaChanged.Count)" -ForegroundColor DarkGray
    Write-Host "    widgets/registry.py     : $($registryChanged.Count)" -ForegroundColor DarkGray
    Write-Host ""

    # ---- recommended action --------------------------------------------- #
    $needsAudit = ($backendFiles.Count -gt 0 -or $migrationFiles.Count -gt 0)
    $needsCatalog = ($registryChanged.Count -gt 0)

    if ($needsAudit) {
        Write-Host "ACTION RECOMMENDED" -ForegroundColor Green
        Write-Host "  Run deeper impact analysis:" -ForegroundColor Green
        Write-Host "    python scripts/check_ra_impact.py --since-sha $SinceSha" -ForegroundColor Green
        if ($needsCatalog) {
            Write-Host "  Widget content schema changed — refresh the bundled catalog:" -ForegroundColor Green
            Write-Host "    python scripts/refresh_bundled_data.py" -ForegroundColor Green
        }
        if ($migrationFiles.Count -gt 0) {
            Write-Host "  Schema migrations detected — may indicate new tables / fields:" -ForegroundColor Green
            foreach ($m in $migrationFiles) { Write-Host "    $m" -ForegroundColor DarkGray }
        }
    } else {
        Write-Host "OK: changes appear frontend-only — report-skill no-op" -ForegroundColor Green
    }
    Write-Host ""

    # ---- persist new cursor --------------------------------------------- #
    $null = New-Item -ItemType Directory -Force -Path (Split-Path $StatePath) -ErrorAction SilentlyContinue
    $headSha | Out-File -FilePath $StatePath -Encoding utf8 -NoNewline
    Write-Host "Cursor advanced to $headSha (saved at $StatePath)" -ForegroundColor DarkGray
}
finally {
    Pop-Location
}
