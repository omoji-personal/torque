"""The command reference the recipes used to carry lives in each command's --help.

A recipe keeps the judgment (when to use a command, what not to infer, what to report) and
names the help that holds the forms, options, exit codes and id rules. Each fact moved out of
a recipe is asserted here against the real parser's help, so it cannot drop out of both."""
import contextlib
import io

import pytest

from torque import cli
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# (torque words, the recipes that point to its --help, phrases the help must hold)
MOVED = [
    (("advisory",), ("advisory",),
     ("needs lists its known direct requirements", "receipt composes knowledge, impact and evidence",
      "not a write authorization and not proof of a complete outcome", "exits 3 when the report is incomplete")),
    (("lesson",), ("lesson",),
     ("the part after its date is enough", "matches several lessons changes nothing",
      "topic words such as report or flow are read as a task or type", "list [--state candidate|active|retired|stale]",
      "candidate, not reviewed: a hint, check it", "--evidence reproduced:REF | official-doc:URL",
      "edit ID --set applies.types=...", "org Ids and org hosts", "It cannot catch every secret or name",
      "--superseded-by ID", "reproduced, official-doc, second-occurrence or consultant-said",
      "promote ID --to firm|platform|rule|skill|recipe|check [--name N]", "keys left out and custom names",
      "harvest --host claude|codex|antigravity [--dir PATH]", "add --from-file PATH", "Exit codes: 0 done")),
    (("lesson", "helpful"), ("lesson-helpful",), ("matches several lessons changes nothing and lists them",)),
    (("lesson", "stale"), ("lesson-stale",),
     ("--reason is required", "--superseded-by names the lesson that replaces it", "torque lesson restore ID")),
    (("recover",), (), ("The legacy spelling torque revert revert show|preview|discard still works",)),
    (("recover", "show"), ("revert-show",),
     ("An existing manifest does not prove the operation completed or can be reversed",
      "partial captures, unfinished finalization and unsupported operations stay listed")),
    (("recover", "preview"), (), ("Preview does not execute a restore",)),
    (("recover", "run"), ("revert",),
     ("A refusal for missing pre-state or an unsupported reversal", "absent/renamed_unknown")),
    (("recover", "discard"), ("revert-discard",),
     ("The org is not reverted, and the bundle and its recovery artifacts are kept",)),
    (("qa", "token-grant"), ("qa-token-grant",),
     ("does not grant Salesforce access or authorize a write", "--org, --skip-target and --reason are required",
      "no skip token, org allowlist, terminal grant or maintainer window")),
    (("qa", "token-show"), ("qa-token-show",), ("does not grant Salesforce access or authorize a write",)),
    (("qa", "token-revoke"), ("qa-token-revoke",),
     ("evidence and other clients' state stay as they are", "does not grant Salesforce access")),
    (("browser", "sanitize-replay"), ("qa-sanitize-replay",),
     ("up to four rounds of percent-decoding", "needs no org connection",
      "code which builds or decodes an address at run time", "Exit 0: no known shape found",
      "Exit 2: rejected", "Exit 1: the file does not exist")),
    (("probes",), ("probes",),
     ("typed null inputs", "empty-collection case", "stored as `actual`", "fails at its DRAFT assertion",
      "never logged as success", "Actual @isTest classes and legacy testMethod classes are skipped",
      "containing Test are still drafted", "--force to replace both", "then 61.0; --api-version overrides it",
      "MANUAL_REQUIRED with compiled and executed false")),
    (("logs",), ("logs",),
     ("not the log of an exact job or user", "--since-deploy is a recent-time approximation",
      "Exit 0 with logs_analyzed: 0", "Exit 2 when a query or retrieval failed", "logs_unread counts the rest",
      "not org health")),
    (("meeting",), ("process-meeting",),
     ("Omit --transcript when there is none", "sensitivity options tune long recordings",
      "sends each selected frame image to the Gemini CLI", "not a generic provider selector")),
    (("context",), ("context",),
     ("records outside --limit, --since or --all", "With --json and no --max-chars the notes are complete",
      "the records still follow --limit, --since and --all", "shown as corrected")),
    (("session",), ("session-resume",),
     ("recheck each referenced evidence file against its recorded hash", "the journal is not rewritten",
      "an unchanged hash does not prove the recorded result", "shown as corrected")),
    (("session", "add"), ("session-save",),
     ("Repeat --check and --evidence as needed", "referenced and hashed, not copied",
      "Exit 4: the record IS saved", "do not run session add again")),
    (("handoff",), ("handoff",), ("writing the new render to FILE.new", "kept as FILE.bak")),
]


def help_text(monkeypatch, words) -> str:
    monkeypatch.setenv("COLUMNS", "400")
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        cli.main([*words, "--help"])
    return " ".join(out.getvalue().split())


@pytest.mark.parametrize("words,recipes,phrases", MOVED, ids=[" ".join(row[0]) for row in MOVED])
def test_the_reference_a_recipe_dropped_is_in_the_named_help(monkeypatch, words, recipes, phrases):
    text = help_text(monkeypatch, words)
    missing = [phrase for phrase in phrases if phrase not in text]
    assert not missing, (words, missing)
    for name in recipes:
        recipe = " ".join((REPO / ".claude" / "commands" / f"{name}.md").read_text(encoding="utf-8").split())
        assert f"torque {' '.join(words)} --help" in recipe, (name, words)


def test_each_qa_token_recipe_keeps_its_name_and_the_shared_legacy_rule():
    for name in ("qa-token-grant", "qa-token-show", "qa-token-revoke"):
        source = (REPO / "workflows" / f"{name}.md").read_text(encoding="utf-8")
        assert source.startswith(f"# /{name}\n") and "{{qa-token " in source
        rendered = " ".join((REPO / ".claude" / "commands" / f"{name}.md").read_text(encoding="utf-8").split())
        assert "Use it only when the user explicitly needs compatibility" in rendered
        assert "Do not mint a token because a check is missing" in rendered
