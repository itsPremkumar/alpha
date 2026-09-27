#Requires -Version 5.1
<#
.SYNOPSIS
    Headless passthrough to the alpha CLI.

.DESCRIPTION
    OpenCode-style non-interactive invocation: wraps
    `uv run --project backend alpha <args>` so scripting and
    automation have one stable entry point. Adds no new execution surface.

.EXAMPLE
    .\scripts\aw.ps1 extensions list
    .\scripts\aw.ps1 --help
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    & uv run --project backend alpha @Rest
    exit $LASTEXITCODE
}
finally { Pop-Location }
