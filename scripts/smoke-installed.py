#!/usr/bin/env python3
"""Exercise the installed CLI from a fresh directory, without an org or model call."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from legacy_commands import check_source_commands


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-wheel", action="store_true")
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="torque-installed-") as temporary:
        root = Path(temporary).resolve()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("TORQUE_", "JSC_")) and k != "PYTHONPATH"}

        def call(*args):
            run = subprocess.run([sys.executable, "-m", "torque", *args],
                                 cwd=root, env=env, capture_output=True, text=True, timeout=30)
            if run.returncode:
                raise AssertionError(f"CLI failed {args}: {run.stderr}\n{run.stdout}")
            return run.stdout

        if options.require_wheel:
            probe = subprocess.run([sys.executable, "-c", "import torque; print(torque.__file__)"],
                                   cwd=root, env=env, capture_output=True, text=True, check=True)
            assert "site-packages" in Path(probe.stdout.strip()).parts, probe.stdout

        call("--help")
        for route in ("advisory", "qa", "revert", "logs", "browser", "meeting", "lesson", "probes", "ai-regression"):
            call(route, "--help")
        for route in ("deploy", "data", "org", "recover", "change", "demo"):
            call(route, "--help")
        call("doctor")
        catalogue = json.loads(call("workflows", "list", "--json"))
        rows = catalogue if isinstance(catalogue, list) else catalogue["workflows"]
        check_source_commands(rows)
        for row in rows:
            assert call("workflows", "show", row["name"]).strip(), row

        private = root / "firm"
        call("workspace", "init", str(private), "--name", "Synthetic consulting firm", "--profile", "solution-lead")
        assert (private / "AGENTS.md").is_file()
        assert len(list((private / ".claude/commands").glob("*.md"))) == len(rows)
        assert len(list((private / ".agents/skills").glob("*/SKILL.md"))) == 3
        assert "Salesforce Solution Lead" in (private / "profile.md").read_text(encoding="utf-8")
        for client in ("alpha", "beta"):
            call("client", "add", client, "--workspace", str(private), "--org", f"synthetic-{client}")
        firm_marker = "SYNTHETIC_FIRM_NOTE_SMOKE"
        alpha_marker = "SYNTHETIC_ALPHA_NOTE_SMOKE"
        sibling_marker = "SYNTHETIC_BETA_NOTE_SMOKE"
        with (private / "profile.md").open("a", encoding="utf-8") as notes:
            notes.write("\n" + firm_marker + "\n")
        (private / "clients/alpha/context.md").write_text(alpha_marker + "\n", encoding="utf-8")
        (private / "clients/beta/context.md").write_text(sibling_marker + "\n", encoding="utf-8")
        proof = private / "clients/alpha/artifacts/proof.txt"
        proof.write_text("Synthetic observation before the next session", encoding="utf-8")
        call("session", "add", "--workspace", str(private), "--client", "alpha",
             "--summary", "Synthetic Alpha-only outcome; no org operation was performed.", "--status", "prepared",
             "--evidence", str(proof))
        call("session", "add", "--workspace", str(private), "--client", "beta",
             "--summary", "Synthetic Beta-private marker", "--status", "incomplete")
        context = call("context", "--workspace", str(private), "--client", "alpha", "--json")
        assert "Alpha-only" in context and "Beta-private" not in context
        assert json.loads(context)["sessions"][0]["evidence_integrity"] == "matches_reference"
        proof.write_text("Synthetic observation changed after recording", encoding="utf-8")
        handoff = call("handoff", "--workspace", str(private), "--client", "alpha")
        assert "Alpha-only" in handoff and "Beta-private" not in handoff and "user-reported" in handoff
        assert firm_marker in handoff and alpha_marker in handoff, "Selected client handoff omitted working notes"
        assert sibling_marker not in handoff, "Selected client handoff leaked sibling notes"
        assert "Evidence integrity: changed" in handoff
        doctor = json.loads(call("doctor", "--workspace", str(private), "--client", "alpha", "--json"))
        assert doctor["client"]["evidence_problems"] == 1
        assert doctor["client"]["sessions_checked"] == 1
        if options.require_wheel:
            assert "site-packages" in Path(doctor["installation"]["package"]).parts
        call("lesson", "show", "--workspace", str(private), "--client", "alpha")
        demo = json.loads(call("demo", str(root / "demo"), "--json"))
        assert not demo["org_calls"] and demo["synthetic"]
        record = json.loads(call("change", "show", demo["change_id"], "--workspace", demo["workspace"],
                                 "--client", demo["client"], "--json"))
        assert record["assessment"]["not_yet_reported_pass"] == ["AC1", "AC2", "AC3"]
        ids = {}
        for client in ("alpha", "beta"):
            for number in (1, 2):
                change = json.loads(call("change", "create", "--workspace", str(private), "--client", client,
                    "--title", f"{client} change {number}", "--outcome", f"{client} outcome {number}",
                    "--criterion", "Selected user can save", "--criterion", "Negative access case", "--json"))
                ids[client, number] = change["id"]
                call("change", "note", change["id"], "--workspace", str(private), "--client", client,
                     "--kind", "next_step", "--text", f"{client}-private-next-step-{number}")
        call("change", "check", ids['alpha', 1], "--workspace", str(private), "--client", "alpha",
             "--criterion", "AC1", "--result", "fail", "--summary", "Synthetic fixture reports save failure")
        # Each CLI call is a fresh process: continuation must survive the writer.
        context = json.loads(call("context", "--workspace", str(private), "--client", "alpha", "--json"))
        assert len(context["changes"]) == 2 and "beta-private" not in json.dumps(context)
        assert "alpha-private-next-step-1" in json.dumps(context)
        selected = next(item for item in context["changes"] if item["id"] == ids['alpha', 1])
        assert selected["assessment"]["reported_fail"] == 1
        report = call("change", "handoff", ids['alpha', 1], "--workspace", str(private), "--client", "alpha")
        assert "Synthetic fixture reports save failure" in report and "alpha-private-next-step-1" in report
        recipe = private / ".claude/commands/diagnose.md"
        recipe.write_text(recipe.read_text(encoding="utf-8") + "\nLocal synthetic customization.\n", encoding="utf-8")
        preview = json.loads(call("workspace", "upgrade", str(private), "--check", "--json"))
        assert preview["check"] and preview["conflicts"]
        call("workspace", "upgrade", str(private), "--json")
        assert "Local synthetic customization." in recipe.read_text(encoding="utf-8")
        selected_recipe = call("workflows", "show", "diagnose", "--workspace", str(private))
        assert selected_recipe.strip() == recipe.read_text(encoding="utf-8").strip(), "CLI did not return the preserved local recipe"
        assert "Local synthetic customization." in selected_recipe
        # A CLI installed outside a checkout must still recognize the destination's identity.
        checkout = root / "synthetic-torque-source"
        (checkout / "src/torque").mkdir(parents=True)
        (checkout / "src/torque/__init__.py").touch()
        (checkout / "src/torque/workspace.py").touch()
        (checkout / "pyproject.toml").write_text('[project]\nname = "torque-salesforce"\n', encoding="utf-8")
        forbidden = checkout / "private"
        denied = subprocess.run([sys.executable, "-m", "torque", "workspace", "init", str(forbidden),
                                 "--name", "Synthetic private firm"], cwd=root, env=env,
                                capture_output=True, text=True, timeout=30)
        assert denied.returncode == 2 and "source checkout" in denied.stderr and not forbidden.exists()
        print(f"Installed CLI verified: nine delegates, six public routes, {len(rows)} recipes, offline demo, private init, two-client/four-change fresh-process continuity, evidence drift, complete client doctor, source/private boundary, reported failure handoff and customized workspace upgrade.")


if __name__ == "__main__":
    main()
