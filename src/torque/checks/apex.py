"""Just enough reading of Apex source for the header, sharing and test rules.

Comments and string literals are masked with spaces of the same length (line breaks kept), so a
keyword inside a comment or a string never counts and positions still map to lines.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

DECL = re.compile(r"\b(class|interface|enum|trigger)\s+\w+", re.I)
SHARING = re.compile(r"\b(with|without|inherited)\s+sharing\b", re.I)
ASSERT = re.compile(r"\b(?:System\s*\.\s*)?assert\w*\s*\(|\bAssert\s*\.\s*\w+\s*\(", re.I)
IS_TEST = re.compile(r"@istest\b", re.I)
TEST_METHOD = re.compile(r"\btestmethod\b", re.I)
SEE_ALL = re.compile(r"\bseealldata\s*=\s*true\b", re.I)


@dataclass
class ApexInfo:
    kind: str | None          # class, interface, enum, trigger
    decl_line: int
    has_header: bool
    is_test: bool
    test_methods: int
    sharing: str | None       # "with sharing", ... or None
    has_assert: bool
    see_all_data_line: int | None
    masked: str


def mask(src: str) -> str:
    out = []
    i, n = 0, len(src)
    while i < n:
        two = src[i:i + 2]
        if two == "/*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
        elif two == "//":
            j = src.find("\n", i)
            j = n if j < 0 else j
        elif src[i] == "'":
            j = i + 1
            while j < n and src[j] != "'" and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            out.append("'" + re.sub(r"[^\n]", " ", src[i + 1:j - 1]) + ("'" if j - 1 > i else ""))
            i = j
            continue
        else:
            out.append(src[i])
            i += 1
            continue
        out.append(re.sub(r"[^\n]", " ", src[i:j]))
        i = j
    return "".join(out)


def line_at(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def read(src: str) -> ApexInfo:
    masked = mask(src)
    m = DECL.search(masked)
    if not m:
        return ApexInfo(None, 1, False, False, 0, None, False, None, masked)
    head = src[:m.start()]
    before = masked[:m.start()]
    is_test = bool(IS_TEST.search(before)) or bool(TEST_METHOD.search(masked))
    tests = len(IS_TEST.findall(masked[m.end():])) + len(TEST_METHOD.findall(masked))
    sharing = SHARING.search(masked[:m.end()])
    see = SEE_ALL.search(masked)
    return ApexInfo(kind=m.group(1).lower(), decl_line=line_at(masked, m.start()),
                    has_header=("/*" in head or "//" in head), is_test=is_test, test_methods=tests,
                    sharing=(sharing.group(0).lower() if sharing else None), has_assert=bool(ASSERT.search(masked)),
                    see_all_data_line=(line_at(masked, see.start()) if see else None), masked=masked)
