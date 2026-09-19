#Requires -Version 5.1
<#
.SYNOPSIS
    Print project context files for pasting into an agent session.

.DESCRIPTION
    Hermes-style context files: AGENTS.md / CLAUDE.md are auto-discovered and
    concatenated. Read-only. Optionally copies to the clipboard.

.EXAMPLE
    .\scripts\print-context.ps1
    .\scripts\print-context.ps1 -Scoped backend
    .\scripts\print-context.ps1 -Clipboard
#>
[CmdletBinding()]
param(
    [string]$Scoped,
    [switch]$Clipboard
)

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\PREM KUMAR\Videos\alpha'

$files = @('AGENTS.md', 'CLAUDE.md', 'SOUL.md')
if ($Scoped) { $files += @("$Scoped\AGENTS.md", "$Scoped\CLAUDE.md") }

$out = New-Object System.Text.StringBuilder
foreach ($f in $files) {
    $p = Join-Path $root $f
    if (Test-Path $p) {
        [void]$out.AppendLine("===== $f =====")
        [void]$out.AppendLine((Get-Content $p -Raw))
        [void]$out.AppendLine('')
    }
}

$text = $out.ToString()
if (-not $text.Trim()) { Write-Host 'No context files found.' -ForegroundColor Yellow; exit 0 }

if ($Clipboard) { Set-Clipboard $text; Write-Host "Copied $($text.Length) chars to clipboard." -ForegroundColor Green }
else { Write-Output $text }
