"""Values of command-line flags, read the way the Salesforce CLI reads them: a
flag's value may be attached (`--flag=value`), and a multi-value flag takes every
following word up to the next flag (`-m A B -o org`). Legacy sfdx selectors also
take comma-separated lists."""
from __future__ import annotations

# Flags whose value may be several words.
MULTI = {"-m", "--metadata", "-d", "--source-dir", "-f", "--files", "--file", "--sobject-tree-files",
         "--sobjecttreefiles", "-p", "--sourcepath", "-x", "--manifest", "--plan"}
# Legacy selectors whose value may be a comma-separated list.
COMMA = {"--sourcepath", "-p", "--sobjecttreefiles", "--metadata", "-m"}


def values(argv: list[str], names, legacy: bool = False) -> list[str]:
    """Every value given to any flag in `names`, in order."""
    names = set(names)
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        name, eq, attached = tok.partition("=")
        if tok.startswith("--") and eq and name in names:
            found = [attached]
            i += 1
            while name in MULTI and i < len(argv) and not argv[i].startswith("-"):
                found.append(argv[i])
                i += 1
        elif tok in names:
            found = []
            j = i + 1
            while j < len(argv) and not argv[j].startswith("-"):
                found.append(argv[j])
                j += 1
                if tok not in MULTI:
                    break
            i = j
        else:
            i += 1
            continue
        for value in found:
            flag = name if eq else tok
            if legacy and flag in COMMA and "," in value:
                out += [v for v in value.split(",") if v]
            else:
                out.append(value)
    return out


def is_legacy(argv: list[str]) -> bool:
    """An sfdx-style command (`sfdx force:source:deploy ...`)."""
    return any(":" in tok and not tok.startswith("-") for tok in argv[1:2])
