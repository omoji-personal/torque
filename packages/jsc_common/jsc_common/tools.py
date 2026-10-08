"""Start a command-line tool that Windows installs as a batch file (sf, sfdx, gemini).

npm and the Salesforce installer put `sf.cmd` on PATH, not `sf.exe`. Windows starts
a program named `sf` only when it is an .exe, so `subprocess.run(["sf", ...])` fails
there with "file not found" although the tool is installed. A batch file also runs
through cmd.exe, which reads `&`, `%`, `^` or `"` inside an argument as its own
syntax. `run` finds the tool and keeps every argument literal:

- a shim npm wrote is started as its own `node.exe SCRIPT`, without cmd.exe, so a
  timeout ends the tool itself;
- any other batch file runs through cmd.exe with each argument quoted for it; an
  argument with a line break cannot be quoted and is refused.

The tool is looked up on PATH only, never in the current folder. Everywhere else,
and when the tool is an .exe, the command is passed on unchanged. On Windows, text
output read without a stated encoding is read as UTF-8, which these tools write,
instead of the ANSI code page.
"""
from __future__ import annotations

import errno
import os
import re
import string
import subprocess

_BATCH = (".cmd", ".bat")
# npm's cmd-shim ends with: "%_prog%" [node options] "%dp0%\path\to\script" %*
_NPM_SHIM = re.compile(r'"%_prog%"\s+(?P<options>[^"\r\n]*?)"%dp0%\\(?P<script>[^"\r\n]+)"\s+%\*')
# Characters cmd.exe leaves alone outside quotes; an argument with any other
# ASCII character is quoted.
_PLAIN = frozenset(string.ascii_letters + string.digits + "#$*+-./:?@\\_")


def _folders(env) -> list[str]:
    path = (os.environ if env is None else env).get("PATH") or ""
    return [folder for folder in path.split(os.pathsep) if folder and os.path.isabs(folder)]


def _batch_file(argv, env, windows: bool) -> str | None:
    """The batch file Windows would need for this command, or None when the
    command starts as it is (not Windows, an .exe on PATH, or nothing found)."""
    if not windows or not argv:
        return None
    name = os.fspath(argv[0])
    if os.path.dirname(name):
        return name if name.lower().endswith(_BATCH) and os.path.isfile(name) else None
    folders = _folders(env)
    if os.path.splitext(name)[1]:
        return next((candidate for candidate in (os.path.join(folder, name) for folder in folders)
                     if name.lower().endswith(_BATCH) and os.path.isfile(candidate)), None)
    if any(os.path.isfile(os.path.join(folder, name + suffix)) for folder in folders for suffix in (".exe", ".com")):
        return None
    for folder in folders:
        for suffix in _BATCH:
            candidate = os.path.join(folder, name + suffix)
            if os.path.isfile(candidate):
                return candidate
    return None


def _npm_shim(batch: str, env) -> list[str] | None:
    """[node.exe, options..., script] for a shim npm wrote; None for any other batch file."""
    try:
        with open(batch, encoding="utf-8", errors="replace") as stream:
            match = _NPM_SHIM.search(stream.read(16384))
    except OSError:
        return None
    if not match:
        return None
    folder = os.path.dirname(os.path.abspath(batch))
    script = os.path.normpath(os.path.join(folder, match["script"].replace("\\", os.sep)))
    # The shim's own rule: node.exe beside it, else the one on PATH.
    node = next((candidate for candidate in (os.path.join(place, "node.exe") for place in (folder, *_folders(env)))
                 if os.path.isfile(candidate)), None)
    if node is None or not os.path.isfile(script):
        return None
    return [node, *match["options"].split(), script]


def _quoted(argument: str) -> str:
    """One argument as cmd.exe and then the program's own parser both read it back."""
    if any(character in argument for character in "\r\n\0"):
        raise OSError(errno.EINVAL, "an argument with a line break cannot be passed to a Windows batch file")
    quote = not argument or argument.endswith("\\") or any(
        (character < "\x80" and character not in _PLAIN) or "\x7f" <= character <= "\x9f" for character in argument)
    out: list[str] = []
    backslashes = 0
    for character in argument:
        if character == "\\":
            backslashes += 1
        else:
            if character == '"':
                # Double the backslashes before a quote, then double the quote itself.
                out.append("\\" * backslashes + '"')
            elif character == "%":
                # An empty substring of %cd% stops cmd.exe expanding %NAME%.
                out.append("%%cd:~,")
            backslashes = 0
        out.append(character)
    if quote:
        out.append("\\" * backslashes)
    text = "".join(out)
    return f'"{text}"' if quote else text


def _cmd_exe() -> str:
    return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "cmd.exe")


def _cmd_line(batch: str, arguments) -> str:
    """The cmd.exe command line that runs a batch file with literal arguments."""
    if '"' in batch or batch.endswith("\\"):
        raise OSError(errno.EINVAL, f"not a usable batch file path: {batch}")
    words = [f'"{batch}"', *(_quoted(os.fspath(argument)) for argument in arguments)]
    return 'cmd.exe /e:ON /v:OFF /d /c "' + " ".join(words) + '"'


def command(argv, env=None, *, windows: bool = os.name == "nt"):
    """(command, extra subprocess options) for argv; argv itself when nothing changes."""
    batch = _batch_file(argv, env, windows)
    if batch is None:
        return argv, {}
    direct = _npm_shim(batch, env)
    if direct is not None:
        return [*direct, *(os.fspath(argument) for argument in argv[1:])], {}
    return _cmd_line(batch, argv[1:]), {"executable": _cmd_exe()}


def run(argv, **options):
    """`subprocess.run` for a command whose first word may be a Windows batch file."""
    line, extra = command(argv, options.get("env"))
    if os.name == "nt" and (options.get("text") or options.get("universal_newlines")) and "encoding" not in options:
        options = {**options, "encoding": "utf-8"}
    return subprocess.run(line, **extra, **options)
