"""Recovery must keep its own watchdog alive and reject recycled process ids.

Only the extracted PowerShell functions run. All process discovery, termination,
sleep, file state and heartbeat writes are stubbed; no installed Alpha process
or scheduled task is touched by these tests.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

WATCHDOG = Path(__file__).resolve().parents[2] / "recovery" / "watchdog.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows watchdog contract")


def _run(tmp_path: Path, functions: list[str], scenario: str) -> dict:
    names = ", ".join(f"'{name}'" for name in functions)
    source = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:ALPHA_WD_SRC, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "Watchdog source does not parse: $errors" }
foreach ($name in @(__NAMES__)) {
    $definition = $ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    if (-not $definition) { throw "Missing function: $name" }
    Invoke-Expression $definition.Extent.Text
}
$script:Kills = @()
$script:Elapsed = 0
$script:Beats = @()
function taskkill { $script:Kills += [int]$args[1] }
function Start-Sleep { param([int]$Seconds) $script:Elapsed += $Seconds }
function Write-Heartbeat { param($Status, $Stack) $script:Beats += @{at=$script:Elapsed; status=$Status} }
function Write-WdLog { param($Message, $Level) }
__SCENARIO__
"""
    source = source.replace("__NAMES__", names).replace("__SCENARIO__", scenario)
    script = tmp_path / "watchdog-harness.ps1"
    script.write_text(source, encoding="utf-8-sig")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        env={**os.environ, "ALPHA_WD_SRC": str(WATCHDOG)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip())


@pytest.mark.parametrize("release_after", [None, 6], ids=["stuck-port", "port-freed"])
def test_component_recovery_refreshes_heartbeat_while_waiting(tmp_path: Path, release_after: int | None) -> None:
    release_at = release_after if release_after is not None else 1_000
    result = _run(
        tmp_path,
        ["Stop-PortTree"],
        f"""
function Get-NetTCPConnection {{ [pscustomobject]@{{ OwningProcess = 43210 }} }}
function Test-PortListening {{ param($Port) return ($script:Elapsed -lt {release_at}) }}
Write-Heartbeat -Status recovering
$outcome = Stop-PortTree -Port 8001
@{{outcome=$outcome; elapsed=$script:Elapsed; beats=@($script:Beats); kills=@($script:Kills)}} | ConvertTo-Json -Depth 4 -Compress
""",
    )
    assert result["outcome"] == ("stuck" if release_after is None else "killed")
    assert result["elapsed"] == (60 if release_after is None else release_after)
    assert result["kills"] == ([43210] * 3 if release_after is None else [43210])
    beats = result["beats"]
    times = [beat["at"] for beat in beats] + [result["elapsed"]]
    assert max(after - before for before, after in zip(times, times[1:])) <= 2
    assert all(beat["status"] == "recovering" for beat in beats)


@pytest.mark.parametrize(
    ("process_name", "command", "expected"),
    [
        ("powershell.exe", 'powershell.exe -File "C:\\Alpha [live]\\start.ps1" -NoBrowser', True),
        ("pwsh.exe", 'pwsh.exe -File "c:\\alpha [LIVE]\\start.ps1"', True),
        ("powershell.exe", 'powershell.exe -File "C:\\Other Alpha\\start.ps1"', False),
        ("powershell.exe", 'powershell.exe -File "C:\\Alpha [live]\\start.ps1.backup"', False),
        ("powershell.exe", 'powershell.exe -File "C:\\Alpha l\\start.ps1"', False),
        ("powershell.exe", "", False),
        ("editor.exe", 'editor.exe "C:\\Alpha [live]\\start.ps1"', False),
    ],
)
def test_process_identity_requires_this_installation_script(tmp_path: Path, process_name: str, command: str, expected: bool) -> None:
    # ConvertTo-Json/ConvertFrom-Json keep Windows path escaping independent
    # from PowerShell's single-quoted literal escaping.
    record = json.dumps({"Name": process_name, "CommandLine": command}).replace("'", "''")
    result = _run(
        tmp_path,
        ["Test-ManagedScriptProcess", "Stop-ManagedScriptProcess"],
        rf"""
$script:Record = '{record}' | ConvertFrom-Json
function Get-CimInstance {{ param($ClassName, $Filter, $ErrorAction) return $script:Record }}
$stopped = Stop-ManagedScriptProcess -ProcessId 43210 -ScriptPath 'C:\Alpha [live]\start.ps1'
@{{stopped=$stopped; kills=@($script:Kills)}} | ConvertTo-Json -Compress
""",
    )
    assert result == {"stopped": expected, "kills": [43210] if expected else []}


def test_unreadable_process_identity_never_terminates_the_pid(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        ["Test-ManagedScriptProcess", "Stop-ManagedScriptProcess"],
        r"""
function Get-CimInstance { throw 'process exited or access denied' }
$stopped = Stop-ManagedScriptProcess -ProcessId 43210 -ScriptPath 'C:\Alpha\start.ps1'
@{stopped=$stopped; kills=@($script:Kills)} | ConvertTo-Json -Compress
""",
    )
    assert result == {"stopped": False, "kills": []}


def test_stale_pid_file_cannot_kill_an_unrelated_process_but_duplicates_are_reaped(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        ["Test-ManagedScriptProcess", "Stop-ManagedScriptProcess", "Stop-StaleLauncher"],
        r"""
$AlphaPid = 'unused'
$StartScript = 'C:\Alpha\start.ps1'
function Get-PidFileValue { param($Path) return 43210 }
function Get-Process { param($Id, $ErrorAction) return @{Id=$Id} }
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    $foreign = [pscustomobject]@{ProcessId=43210; Name='powershell.exe'; CommandLine='powershell -File C:\Other\start.ps1'}
    $ours = [pscustomobject]@{ProcessId=43211; Name='powershell.exe'; CommandLine='powershell -File C:\Alpha\start.ps1 -NoBrowser'}
    if ($Filter -eq 'ProcessId=43210') { return $foreign }
    if ($Filter -eq 'ProcessId=43211') { return $ours }
    return @($foreign, $ours)
}
Stop-StaleLauncher
@{kills=@($script:Kills)} | ConvertTo-Json -Compress
""",
    )
    assert result["kills"] == [43211]


def test_stopping_watchdog_does_not_kill_a_recycled_pid(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        ["Test-ManagedScriptProcess", "Stop-ManagedScriptProcess", "Stop-WatchdogLoop"],
        r"""
$WatchdogPid = 'unused'
$WatchdogHeartbeat = 'unused-heartbeat'
$WatchdogScript = 'C:\Alpha\recovery\watchdog.ps1'
function Get-PidFileValue { param($Path) return 43210 }
function Get-Process { param($Id, $ErrorAction) return @{Id=$Id} }
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    return [pscustomobject]@{Name='powershell.exe'; CommandLine='powershell -File C:\Other\recovery\watchdog.ps1'}
}
function Remove-Item { param($Path, [switch]$Force, $ErrorAction) }
Stop-WatchdogLoop
@{kills=@($script:Kills)} | ConvertTo-Json -Compress
""",
    )
    assert result["kills"] == []
