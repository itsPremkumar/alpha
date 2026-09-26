[CmdletBinding()]
param (
    [ValidateSet('status', 'check', 'apply', 'recover')]
    [string]$Command = 'check',
    [string]$Policy,
    [switch]$Force,
    [switch]$Yes,
    [switch]$Auto,
    [string]$TransactionId
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path $PSScriptRoot -Parent
$Uv = (Get-Command uv.exe -ErrorAction SilentlyContinue).Source
if (-not $Uv) { $Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source }
if (-not $Uv) { throw 'uv is required to run the Alpha update engine.' }

$Arguments = @('run', '--project', (Join-Path $RepoRoot 'backend'), '--no-sync', 'python', (Join-Path $RepoRoot 'scripts/auto_update.py'), $Command)
if ($Policy) { $Arguments += @('--policy', $Policy) }
if ($Force) { $Arguments += '--force' }
if ($Yes) { $Arguments += '--yes' }
if ($Auto) { $Arguments += '--auto' }
if ($Command -eq 'apply' -and $Yes -and -not $Auto -and -not $Force) { $Arguments += '--force' }
if ($TransactionId) { $Arguments += @('--transaction-id', $TransactionId) }

& $Uv @Arguments
exit $LASTEXITCODE
