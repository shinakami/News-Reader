[CmdletBinding()]
param(
    [string]$RepositoryPath = (Split-Path -Parent $PSScriptRoot),
    [string]$Message = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-RepoGit {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    # Windows PowerShell 5.1 wraps native stderr lines as ErrorRecord objects.
    # Leave stderr visible, but return stdout only so warnings cannot be parsed
    # as file names, branch names, or other Git data.
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $result = @(& git -C $script:RepoRoot @Arguments)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        $details = ($result | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
        throw "git $($Arguments -join ' ') failed (exit $exitCode).`n$details"
    }
    return $result
}

function Get-AutomaticCommitMessage {
    param([string[]]$Paths)

    $normalized = @($Paths | ForEach-Object { $_.Replace("\", "/").ToLowerInvariant() })
    if ($normalized.Count -eq 0) {
        return "chore: update project files"
    }

    $documentationExtensions = @(".md", ".txt", ".rst")
    $onlyDocumentation = $true
    foreach ($path in $normalized) {
        if ($documentationExtensions -notcontains [IO.Path]::GetExtension($path)) {
            $onlyDocumentation = $false
            break
        }
    }

    if ($onlyDocumentation) {
        return "docs: update project documentation"
    }
    if (@($normalized | Where-Object { $_ -match '(^|/)(test|tests)/|(^|/)test_[^/]+\.py$' }).Count -eq $normalized.Count) {
        return "test: update automated tests"
    }
    if (@($normalized | Where-Object { $_ -match 'stock_dynamic|stock_monitor|stock_market_dashboard' }).Count -gt 0) {
        return "feat: update stock dashboard"
    }
    if (@($normalized | Where-Object { $_ -match 'news(_reader)?|news\.py' }).Count -gt 0) {
        return "feat: update news dashboard"
    }
    if (@($normalized | Where-Object { $_ -match '(^|/)(tools|\.githooks)/|hook' }).Count -gt 0) {
        return "chore: update repository automation"
    }
    if ($normalized.Count -eq 1) {
        $name = [IO.Path]::GetFileNameWithoutExtension($normalized[0]).Replace("_", "-")
        return "chore: update $name"
    }
    return "chore: update $($normalized.Count) project files"
}

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git was not found. Configure SourceTree to use Embedded Git or add Git to PATH."
    }

    $requestedPath = (Resolve-Path -LiteralPath $RepositoryPath).Path
    $script:RepoRoot = $requestedPath
    $topLevel = @(Invoke-RepoGit -Arguments @("rev-parse", "--show-toplevel"))[0].ToString().Trim()
    $script:RepoRoot = (Resolve-Path -LiteralPath $topLevel).Path

    Write-Host "Repository: $script:RepoRoot" -ForegroundColor Cyan

    $branch = (@(Invoke-RepoGit -Arguments @("branch", "--show-current")) -join "").Trim()
    if ([string]::IsNullOrWhiteSpace($branch)) {
        throw "Detached HEAD detected. Check out a branch in SourceTree before running this action."
    }

    $conflicts = @(Invoke-RepoGit -Arguments @("diff", "--name-only", "--diff-filter=U"))
    if ($conflicts.Count -gt 0) {
        throw "Unresolved merge conflicts exist:`n$($conflicts -join [Environment]::NewLine)"
    }

    $remoteUrl = (@(Invoke-RepoGit -Arguments @("remote", "get-url", "origin")) -join "").Trim()
    if ([string]::IsNullOrWhiteSpace($remoteUrl)) {
        throw "Remote 'origin' is not configured."
    }

    $status = @(Invoke-RepoGit -Arguments @("status", "--short", "--untracked-files=all"))
    Write-Host "Branch: $branch" -ForegroundColor Cyan
    Write-Host "Remote: $remoteUrl" -ForegroundColor Cyan

    if ($status.Count -gt 0) {
        Write-Host "Current Git changes:" -ForegroundColor Yellow
        $status | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host "No local file changes were found."
    }

    if ($DryRun) {
        $previewPaths = @(
            Invoke-RepoGit -Arguments @("status", "--porcelain=v1", "--untracked-files=all") |
                ForEach-Object { $_.ToString().Substring(3).Trim('"') }
        )
        $previewMessage = if ($Message) { $Message.Trim() } else { Get-AutomaticCommitMessage -Paths $previewPaths }
        Write-Host "Dry run only; no files were staged, committed, or pushed." -ForegroundColor Green
        if ($status.Count -gt 0) {
            Write-Host "Proposed commit message: $previewMessage" -ForegroundColor Green
        }
        exit 0
    }

    Write-Host "Checking origin/$branch ..."
    Invoke-RepoGit -Arguments @("fetch", "origin") | Out-Null
    $remoteMatches = @(Invoke-RepoGit -Arguments @("branch", "--remotes", "--list", "origin/$branch"))
    $remoteExists = $remoteMatches.Count -gt 0

    if ($remoteExists) {
        $counts = (@(Invoke-RepoGit -Arguments @("rev-list", "--left-right", "--count", "HEAD...origin/$branch")) -join " ").Trim() -split "\s+"
        $behind = [int]$counts[1]
        if ($behind -gt 0) {
            throw "origin/$branch is ahead by $behind commit(s). Pull and resolve it in SourceTree first."
        }
    }

    if ($status.Count -gt 0) {
        $changedPaths = @(
            @(Invoke-RepoGit -Arguments @("diff", "--name-only", "--diff-filter=ACDMRTUXB"))
            @(Invoke-RepoGit -Arguments @("diff", "--cached", "--name-only", "--diff-filter=ACDMRTUXB"))
            @(Invoke-RepoGit -Arguments @("ls-files", "--others", "--exclude-standard"))
        ) | Sort-Object -Unique
        $newFiles = @(Invoke-RepoGit -Arguments @("ls-files", "--others", "--exclude-standard"))
        $sensitivePatterns = @(
            '(^|/)\.env($|\.)',
            '\.(pem|p12|pfx|key)$',
            '(^|/)(credentials|secrets?)(\.|/|$)'
        )
        $sensitiveFiles = @($newFiles | Where-Object {
            $candidate = $_.ToString().Replace("\", "/").ToLowerInvariant()
            @($sensitivePatterns | Where-Object { $candidate -match $_ }).Count -gt 0
        })
        if ($sensitiveFiles.Count -gt 0) {
            throw "Sensitive new files were detected and nothing was committed:`n$($sensitiveFiles -join [Environment]::NewLine)"
        }

        $pythonChanged = @($changedPaths | Where-Object { $_.ToString().ToLowerInvariant().EndsWith(".py") }).Count -gt 0
        if ($pythonChanged) {
            $venvPython = Join-Path $script:RepoRoot ".venv\Scripts\python.exe"
            $pythonCommand = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }
            Write-Host "Running Python compile check ..."
            & $pythonCommand -m compileall -q (Join-Path $script:RepoRoot "src") (Join-Path $script:RepoRoot "main.py")
            if ($LASTEXITCODE -ne 0) {
                throw "Python compile check failed. Nothing was staged by this action; fix the errors and try again."
            }
        }

        Write-Host "Staging all non-ignored changes ..."
        Invoke-RepoGit -Arguments @("add", "--all") | Out-Null

        $stagedPaths = @(Invoke-RepoGit -Arguments @("diff", "--cached", "--name-only", "--diff-filter=ACDMRTUXB"))
        if ($stagedPaths.Count -eq 0) {
            throw "No committable changes remain after applying .gitignore."
        }

        $commitMessage = if ($Message) { $Message.Trim() } else { Get-AutomaticCommitMessage -Paths $stagedPaths }
        if ([string]::IsNullOrWhiteSpace($commitMessage)) {
            throw "The commit message is empty."
        }

        Write-Host "Commit message: $commitMessage" -ForegroundColor Green
        Invoke-RepoGit -Arguments @("commit", "-m", $commitMessage) | ForEach-Object { Write-Host $_ }
    }

    $upstream = (@(Invoke-RepoGit -Arguments @("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") -AllowFailure) -join "").Trim()
    if ([string]::IsNullOrWhiteSpace($upstream)) {
        Write-Host "Pushing and setting upstream origin/$branch ..."
        Invoke-RepoGit -Arguments @("push", "--set-upstream", "origin", $branch) | ForEach-Object { Write-Host $_ }
    } else {
        Write-Host "Pushing $branch ..."
        Invoke-RepoGit -Arguments @("push", "origin", $branch) | ForEach-Object { Write-Host $_ }
    }

    $head = (@(Invoke-RepoGit -Arguments @("log", "-1", "--pretty=format:%h %s")) -join "").Trim()
    Write-Host "Completed: $head" -ForegroundColor Green
    Write-Host "SourceTree can now be refreshed to show the synchronized state." -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "STOPPED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
