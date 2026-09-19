#Requires -Version 5.1
<#
.SYNOPSIS
    Scan the staged diff for secrets and destructive patterns.

.DESCRIPTION
    An event-hook style guardrail (Hermes pattern). Read-only: it never modifies
    anything. By default it WARNS. With -Strict it exits non-zero so it can be
    wired into a hook once you trust it.

    Detects: AWS keys, private key blocks, common token prefixes, and
    destructive commands (rm -rf, force push, wmic, credential writes).

.EXAMPLE
    .\scripts\guardrails.ps1
    .\scripts\guardrails.ps1 -Strict
#>
[CmdletBinding()]
param([switch]$Strict)

$ErrorActionPreference = 'Stop'

$rules = @(
    @{ Name = 'AWS access key';      Pattern = 'AKIA[0-9A-Z]{16}' },
    @{ Name = 'Private key block';   Pattern = 'BEGIN (RSA |OPENSSH |EC |PGP )?PRIVATE KEY' },
    @{ Name = 'Slack token';         Pattern = 'xox[baprs]-[0-9A-Za-z-]{10,}' },
    @{ Name = 'GitHub token';        Pattern = 'gh[pousr]_[0-9A-Za-z]{20,}' },
    @{ Name = 'OpenAI style key';    Pattern = 'sk-[0-9A-Za-z]{20,}' },
    @{ Name = 'Generic secret assignment'; Pattern = '(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[''"][^''"\s]{8,}[''"]' },
    @{ Name = 'Destructive rm';      Pattern = 'rm\s+-[rRf]{1,2}\b' },
    @{ Name = 'Force push';          Pattern = 'git\s+push\s+.*(--force|-f\b)' },
    @{ Name = 'Blocked binary';      Pattern = '\bwmic\b' }
)

$diff = git diff --cached --unified=0 2>$null
if (-not $diff) {
    Write-Host 'No staged changes.' -ForegroundColor DarkGray
    exit 0
}

$hits = @()
foreach ($line in $diff) {
    if ($line -notmatch '^\+') { continue }          # only added lines
    if ($line -match '^\+\+\+') { continue }          # skip file header
    foreach ($rule in $rules) {
        if ($line -match $rule.Pattern) {
            $hits += [pscustomobject]@{ Rule = $rule.Name; Line = $line.Trim() }
            break
        }
    }
}

if ($hits.Count -eq 0) {
    Write-Host 'Guardrails: clean.' -ForegroundColor Green
    exit 0
}

Write-Host "Guardrails: $($hits.Count) finding(s)" -ForegroundColor Yellow
$hits | ForEach-Object { Write-Host ("  [{0}] {1}" -f $_.Rule, $_.Line) -ForegroundColor Yellow }

if ($Strict) { Write-Host 'Strict mode: failing.' -ForegroundColor Red; exit 1 }
Write-Host 'Warn-only (re-run with -Strict to fail the commit).' -ForegroundColor DarkGray
exit 0
