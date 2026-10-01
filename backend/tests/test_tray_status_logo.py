"""The tray badge must show the Alpha mark, and must never go blank.

The notification-area icon is the one surface that tells an operator "Alpha is
up" without opening anything. Two failure modes are worth pinning:

* **The logo is never composited.** ``Get-AlphaLogo`` silently returns ``$null``
  (or ``New-StatusIcon`` stops honouring ``-Logo``) and the badge quietly
  reverts to the drawn "A" it used before the mark existed. Nothing crashes, so
  nothing reports it - the regression is invisible unless you look at the tray.

* **The badge goes blank.** If logo loading throws *and* the glyph fallback is
  broken, the icon disappears from the tray entirely. The indicator that exists
  to make Alpha's state visible would be the one thing that stops being
  visible, with no error anywhere.

Both are checked by rendering the *shipped* icon region and counting pixels:
the healthy badge must differ from a glyph-only badge (so the mark really is
drawn) and must be substantially opaque (so it is really there).

The mark choice itself follows ``scripts/generate-brand-assets.mjs``, which
switches crop at ``FACE_MAX_SIZE = 24``: at or below it the *face* reads and the
*mane* averages into an unreadable dark blob. The badge clips the mark into a
22px circle, so the face sources must lead. That ordering is asserted here
because it is a judgement made once in the script and easy to undo by
reordering the candidate list.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAY_PS1 = REPO_ROOT / "recovery" / "tray_status.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="recovery/tray_status.ps1 is the Windows tray")

# Renders the shipped icon region twice: once against the real checkout, once
# against an empty root where every candidate asset is absent.
#
# The extraction is positional and mirrors the decision-table harness: it takes
# the block from ``function Write-TrayDiag`` (the start of the icon region,
# after the config block and its single-instance guard) up to
# ``function Test-PortUp``. Everything it needs from the config - ``$RepoRoot``
# and ``$LogDir`` - is supplied explicitly, so the harness never touches the
# real ``logs\`` directory or ``tray.pid``.
ICON_HARNESS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$RepoRoot = $env:ALPHA_TRAY_ICON_ROOT
$LogDir   = $env:ALPHA_TRAY_ICON_LOGDIR
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$src = Get-Content -LiteralPath $env:ALPHA_TRAY_SRC -Raw
$from = $src.IndexOf('function Write-TrayDiag')
$to   = $src.IndexOf('function Test-PortUp')
if ($from -lt 0 -or $to -lt 0) {
    Write-Output "HARNESS_BROKEN: markers missing ($from/$to)"
    exit 3
}
Invoke-Expression $src.Substring($from, $to - $from)

if ($AlphaLogo) { Write-Output ("LOGO=loaded " + $AlphaLogo.Width + "x" + $AlphaLogo.Height) }
else            { Write-Output "LOGO=null" }

# A glyph-only control badge, so "was the mark composited?" is a pixel
# comparison rather than an assumption about what the code intends.
$control = New-StatusIcon -Color 'LimeGreen' -Glyph 'A' -Logo $null

function Count-Opaque([System.Drawing.Bitmap]$b) {
    $n = 0
    for ($y = 0; $y -lt $b.Height; $y++) {
        for ($x = 0; $x -lt $b.Width; $x++) {
            if ($b.GetPixel($x, $y).A -gt 16) { $n++ }
        }
    }
    return $n
}

function Count-Differing([System.Drawing.Bitmap]$a, [System.Drawing.Bitmap]$b) {
    $n = 0
    for ($y = 0; $y -lt $a.Height; $y++) {
        for ($x = 0; $x -lt $a.Width; $x++) {
            $pa = $a.GetPixel($x, $y); $pb = $b.GetPixel($x, $y)
            if ($pa.A -ne $pb.A -or $pa.R -ne $pb.R -or $pa.G -ne $pb.G -or $pa.B -ne $pb.B) { $n++ }
        }
    }
    return $n
}

foreach ($pair in @(
    @('HEALTHY', $IconHealthy.Bitmap),
    @('WORKING', $IconWorking.Bitmap),
    @('FAILED',  $IconFailed.Bitmap),
    @('STOPPED', $IconStopped.Bitmap),
    @('CONTROL', $control.Bitmap)
)) {
    Write-Output ($pair[0] + "_OPAQUE=" + (Count-Opaque $pair[1]))
}
Write-Output ("HEALTHY_VS_CONTROL=" + (Count-Differing $IconHealthy.Bitmap $control.Bitmap))
Write-Output ("STOPPED_VS_CONTROL=" + (Count-Differing $IconStopped.Bitmap $control.Bitmap))
"""


def _render(asset_root: Path, workdir: Path, logdir: Path) -> dict[str, str]:
    """Render the shipped icons as if the checkout lived at ``asset_root``.

    ``asset_root`` is only ever *read* - it is where ``Get-AlphaLogo`` looks for
    the generated marks. The harness itself is written to ``workdir`` and logs
    to ``logdir`` so a positive run against the real checkout litters nothing.
    """
    harness = workdir / "icon_harness.ps1"
    harness.write_text(ICON_HARNESS, encoding="utf-8")

    env = dict(os.environ)
    env.update(
        {
            "ALPHA_TRAY_SRC": str(TRAY_PS1),
            "ALPHA_TRAY_ICON_ROOT": str(asset_root),
            "ALPHA_TRAY_ICON_LOGDIR": str(logdir),
        }
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
        check=False,
    )
    assert "HARNESS_BROKEN" not in proc.stdout, f"tray_status.ps1 icon region moved; the extraction harness lost its markers.\n{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, f"harness failed\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"

    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    assert "LOGO" in out, f"harness reported no logo outcome\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    return out


def test_tray_status_keeps_its_utf8_bom() -> None:
    """PowerShell 5.1 misreads a stripped-BOM script; the other recovery tests pin this too."""
    head = TRAY_PS1.read_bytes()[:3]
    assert head == b"\xef\xbb\xbf", "recovery/tray_status.ps1 must stay UTF-8 with BOM for PowerShell 5.1"


def test_badge_composites_the_mark_and_is_not_blank(tmp_path: Path) -> None:
    """The real checkout must draw the mark, and the badge must actually be visible.

    ``HEALTHY_VS_CONTROL`` differing means the logo was composited over the
    badge rather than ignored; a high opaque count means the icon would render
    in the tray instead of vanishing from it.
    """
    # The real checkout: the marks are present, so the mark must be composited.
    logdir = tmp_path / "logs"
    result = _render(REPO_ROOT, tmp_path, logdir)

    assert result["LOGO"].startswith("loaded"), f"no mark loaded from the real checkout: {result}"
    assert int(result["HEALTHY_VS_CONTROL"]) > 0, "the healthy badge is pixel-identical to a glyph-only badge: Get-AlphaLogo's result is not reaching New-StatusIcon"
    assert int(result["HEALTHY_OPAQUE"]) > 300, f"healthy badge is blank/transparent: {result}"
    assert int(result["STOPPED_OPAQUE"]) > 300, f"stopped badge is blank/transparent: {result}"

    # Every non-failure state wears the mark; the failure badge is measured
    # against the same control only to prove it is not blank.
    assert int(result["FAILED_OPAQUE"]) > 300, f"failed badge is blank/transparent: {result}"


def test_missing_assets_fall_back_to_the_drawn_glyph(tmp_path: Path) -> None:
    """A stripped checkout must degrade to the "A", never to an invisible icon.

    The fallback has to be *exact* - identical to the glyph-only control - so a
    partial checkout cannot leave a half-composited badge on screen.
    """
    # An empty root: every candidate is absent, so the drawn glyph must win.
    root = tmp_path / "no_assets"
    root.mkdir()
    logdir = tmp_path / "logs"

    result = _render(root, tmp_path, logdir)

    assert result["LOGO"] == "null", f"logo reported loaded from an empty root: {result}"
    assert int(result["HEALTHY_VS_CONTROL"]) == 0, "the fallback badge differs from the glyph-only control: a partial checkout would render a half-composited icon"
    assert int(result["HEALTHY_OPAQUE"]) > 300, f"fallback badge is blank: {result}"

    # The rejection must be diagnosable from logs\tray.log alone.
    tray_log = logdir / "tray.log"
    assert tray_log.exists(), "the fallback wrote nothing to tray.log"
    text = tray_log.read_text(encoding="utf-8")
    assert "logo candidate absent" in text, text
    assert "using the drawn 'A' fallback" in text, text


def test_face_sources_lead_because_the_badge_is_sub_24px() -> None:
    """``FACE_MAX_SIZE`` is 24 and the badge circle is 22, so the face must lead.

    Reordering the candidate list to put a mane crop first is not a crash - it
    is a slow degradation to an unreadable dark blob at tray size, which is
    exactly what the brand generator documents the crop switch to prevent.
    """
    src = TRAY_PS1.read_text(encoding="utf-8")
    start = src.index("function Get-AlphaLogo")
    end = src.index("function New-StatusIcon", start)
    body = src[start:end]

    first_face = body.find("IcoSize = 24")
    if first_face < 0:
        first_face = body.find("favicon-16x16.png")
    first_mane = body.find("favicon-32x32.png")
    if first_mane < 0:
        first_mane = body.find("icon-192.png")

    assert first_face >= 0, "Get-AlphaLogo no longer offers a face crop (size <= 24)"
    assert first_mane >= 0, "Get-AlphaLogo no longer offers a mane fallback"
    assert first_face < first_mane, "a mane crop is tried before a face crop; at the badge's 22px circle the mane averages into an unreadable dark blob"
    # Comments discuss the poster as the one thing that must *not* be used, so
    # only live code is checked for it appearing as a candidate.
    live = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    assert "alpha.png" not in live, "the poster itself must never be a candidate: it carries the ALPHA wordmark and the tagline, and no mark may include the type"


def test_failure_badge_keeps_the_exclamation_glyph() -> None:
    """The failure state keeps "!" rather than the mark.

    Colour alone is easy to miss in a 16px tray; on the one state where being
    noticed matters most, the glyph carries the signal. This is a deliberate
    exception to the mark-everywhere rule and worth pinning so a tidy-up pass
    does not silently remove it.
    """
    src = TRAY_PS1.read_text(encoding="utf-8")
    failure_line = next(line for line in src.splitlines() if "$IconFailed" in line)

    assert '"Crimson"' in failure_line, failure_line
    assert '"!"' in failure_line, failure_line
    assert "-Logo" not in failure_line, "the failure badge now wears the mark: it loses the '!' that makes a 16px red dot distinguishable from the other states"
