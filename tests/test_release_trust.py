"""What a reviewer needs to trust a release: published checksums and a private reporting channel."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _steps():
    workflow = yaml.safe_load((ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8"))
    return workflow["jobs"]["offline"]["steps"]


def test_the_candidate_artifact_carries_the_checksums_of_its_wheel_and_sdist():
    steps = _steps()
    upload = next(i for i, step in enumerate(steps) if str(step.get("uses", "")).startswith("actions/upload-artifact@"))
    checksums = next(i for i, step in enumerate(steps) if "SHA256SUMS" in step.get("run", ""))
    step = steps[checksums]
    assert checksums < upload
    assert step["if"] == steps[upload]["if"] and step["working-directory"] == "dist"
    assert step["run"].startswith("sha256sum -- *.whl *.tar.gz")
    assert steps[upload]["with"]["path"] == "dist/*"


def test_ci_runs_the_offline_suite_on_a_bounded_number_of_workers_it_installs():
    [run] = [step["run"] for step in _steps() if "scripts/test-offline.py" in step.get("run", "")]
    assert run == "python scripts/test-offline.py -q --workers 4"
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dev = next(line for line in pyproject.splitlines() if line.startswith("dev = "))
    assert '"pytest-xdist>=' in dev


def test_users_and_maintainers_are_told_how_the_checksums_are_used():
    installation = (ROOT / "docs/installation.md").read_text(encoding="utf-8")
    assert "### Verify a release wheel" in installation and "(#verify-a-release-wheel)" in installation
    assert "sha256sum -c SHA256SUMS" in installation and "Get-FileHash" in installation
    flat = " ".join(installation.split())
    # Publishing is a maintainer step, so the guide does not promise every release has them.
    assert "When a GitHub release lists those checksums" in flat and "not yet attested" in flat
    assert "Each GitHub release lists" not in flat
    contributing = " ".join((ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").split())
    assert "paste the `SHA256SUMS` lines into its notes" in contributing


def test_vulnerabilities_have_a_private_route_and_a_fallback_until_it_is_switched_on():
    section = (ROOT / "SECURITY.md").read_text(encoding="utf-8").split("## Reporting a vulnerability", 1)[1]
    text = " ".join(section.split())
    # Conditional: the setting is the owner's to turn on; the contact fallback stays until then.
    assert "If the repository's **Security** tab offers **Report a vulnerability**" in text
    assert "https://github.com/omoji-personal/torque/security/advisories/new" in text
    assert "Otherwise open a sanitized issue requesting a private maintainer contact" in text
