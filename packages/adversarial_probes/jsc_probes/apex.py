"""Editable Apex test drafts; generation is not successful testing.

This bounded lexer recognizes direct public/global methods of one top-level
class, not arbitrary Apex. Unsupported declarations remain visible. Every case
fails until its business assertion is supplied. No fixtures, DML or security/bulk
coverage are invented. Originally adopted from claudeblazer (Apache-2.0).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_LEX = re.compile(r"\s+|//[^\r\n]*|/\*[\s\S]*?\*/|'(?:\\.|[^'\\])*'|[A-Za-z_][A-Za-z0-9_]*|[0-9]+|.", re.DOTALL)
_MODIFIERS = {"public", "global", "private", "protected", "static", "virtual", "override", "abstract", "webservice", "testmethod", "final"}


def _tokens(source: str) -> list[str]:
    output = []
    for match in _LEX.finditer(source):
        token = match.group()
        if token.isspace() or token.startswith("//") or token.startswith("/*"):
            continue
        if token == "'" or source[match.start():match.start() + 2] == "/*":
            raise ValueError("Unterminated string or comment")
        output.append("<string>" if token.startswith("'") else token)
    return output


def _matching(tokens: list[str], start: int, left: str, right: str) -> int:
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index] == left:
            depth += 1
        elif tokens[index] == right:
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"Unbalanced {left}{right} in source declaration")


def _without_annotations(tokens: list[str]) -> tuple[list[str], list[str]]:
    annotations = []
    index = 0
    while index < len(tokens) and tokens[index] == "@":
        index += 1
        if index >= len(tokens) or not _IDENT.fullmatch(tokens[index]):
            raise ValueError("Malformed annotation")
        annotations.append(tokens[index].lower())
        index += 1
        if index < len(tokens) and tokens[index] == "(":
            index = _matching(tokens, index, "(", ")") + 1
    return tokens[index:], annotations


def _type_text(tokens: list[str]) -> str:
    """Validate simple/qualified types, collection generics and arrays."""
    def consume(index: int) -> int:
        if index >= len(tokens) or not _IDENT.fullmatch(tokens[index]):
            raise ValueError("Expected a type identifier")
        typename = tokens[index].lower()
        index += 1
        while index < len(tokens) and tokens[index] == ".":
            index += 1
            if index >= len(tokens) or not _IDENT.fullmatch(tokens[index]):
                raise ValueError("Incomplete qualified type")
            index += 1
        if index < len(tokens) and tokens[index] == "<":
            if typename not in {"list", "set", "map"}:
                raise ValueError("Only List, Set and Map generic types are supported")
            index = consume(index + 1)
            count = 1
            while index < len(tokens) and tokens[index] == ",":
                index = consume(index + 1)
                count += 1
            if index >= len(tokens) or tokens[index] != ">":
                raise ValueError("Unbalanced generic type")
            if count != (2 if typename == "map" else 1):
                raise ValueError("Wrong collection type argument count")
            index += 1
        while index + 1 < len(tokens) and tokens[index:index + 2] == ["[", "]"]:
            index += 2
        return index

    if not tokens or consume(0) != len(tokens):
        raise ValueError("Unsupported or malformed type")
    if any(token.lower() == "void" for token in tokens) and len(tokens) != 1:
        raise ValueError("Void is not a collection/array element type")
    return "".join(tokens)


@dataclass
class MethodSig:
    name: str
    params_raw: str
    is_static: bool
    return_type: str = "void"
    unsupported_reason: str | None = None

    def parameters(self) -> list[tuple[str, str]]:
        tokens = _tokens(self.params_raw)
        if not tokens:
            return []
        segments = []
        depth = 0
        start = 0
        for index, token in enumerate(tokens):
            if token == "<":
                depth += 1
            elif token == ">":
                depth -= 1
                if depth < 0:
                    raise ValueError("Unbalanced generic parameter")
            elif token == "," and depth == 0:
                segments.append(tokens[start:index])
                start = index + 1
        if depth != 0:
            raise ValueError("Unbalanced generic parameter")
        segments.append(tokens[start:])
        output = []
        for segment in segments:
            if len(segment) < 2 or not _IDENT.fullmatch(segment[-1]):
                raise ValueError("Each parameter needs a type and a name")
            typename = _type_text(segment[:-1])
            if typename.lower() == "void":
                raise ValueError("Void parameter is unsupported")
            output.append((typename, segment[-1]))
        return output


@dataclass
class ClassAnalysis:
    class_name: str
    methods: list[MethodSig] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    is_test: bool = False
    scope_notes: list[str] = field(default_factory=list)


def analyze_class(class_source: str, class_name: str | None = None) -> ClassAnalysis:
    tokens = _tokens(class_source)
    classes = []
    depth = 0
    boundary = 0
    for index, token in enumerate(tokens):
        if token.lower() == "class" and depth == 0:
            if index + 1 >= len(tokens) or not _IDENT.fullmatch(tokens[index + 1]):
                raise ValueError("Missing top-level class name")
            opening = index + 2
            while opening < len(tokens) and tokens[opening] != "{":
                opening += 1
            if opening == len(tokens):
                raise ValueError("Missing top-level class body")
            classes.append((tokens[index + 1], tokens[boundary:index], opening))
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced class braces")
            if depth == 0:
                boundary = index + 1
    if depth != 0 or len(classes) != 1:
        raise ValueError("Expected exactly one balanced top-level Apex class")
    name, prefix, opening = classes[0]
    if class_name is not None and name.lower() != class_name.lower():
        raise ValueError(f"Source declares {name}, not requested {class_name}")
    modifiers, annotations = _without_annotations(prefix)
    result = ClassAnalysis(name, is_test="istest" in annotations)
    abstract_class = "abstract" in [token.lower() for token in modifiers]
    closing = _matching(tokens, opening, "{", "}")
    constructors = []
    nested_types = set()
    start = opening + 1
    index = start
    paren_depth = 0
    while index < closing:
        token = tokens[index]
        if token == "(":
            paren_depth += 1
        elif token == ")":
            paren_depth -= 1
        if token not in ("{", ";") or paren_depth != 0:
            index += 1
            continue
        header, annotations = _without_annotations(tokens[start:index])
        lower = [part.lower() for part in header]
        if "testmethod" in lower or "istest" in annotations:
            result.is_test = True
        if any(kind in lower for kind in ("class", "interface", "enum")):
            kind_at = next(i for i, part in enumerate(lower) if part in {"class", "interface", "enum"})
            if kind_at + 1 < len(header):
                nested_types.add(header[kind_at + 1].lower())
            result.scope_notes.append("Nested type members are outside the selected direct public/global method scope")
        elif "(" in header and "=" not in header:
            argument_open = header.index("(")
            try:
                argument_close = _matching(header, argument_open, "(", ")")
                if argument_close != len(header) - 1 or argument_open < 1:
                    raise ValueError("Unsupported method declaration suffix")
                method_name = header[argument_open - 1]
                before_name = header[:argument_open - 1]
                method_modifiers = []
                while before_name and before_name[0].lower() in _MODIFIERS:
                    method_modifiers.append(before_name.pop(0).lower())
                visible = bool({"public", "global"} & set(method_modifiers))
                params = header[argument_open + 1:argument_close]
                if method_name.lower() == name.lower() and not before_name:
                    constructors.append((visible, params))
                elif visible:
                    if not _IDENT.fullmatch(method_name):
                        raise ValueError("Invalid method name")
                    method = MethodSig(method_name, " ".join(params), "static" in method_modifiers)
                    try:
                        method.return_type = _type_text(before_name)
                        method.parameters()
                        if token == ";" or "abstract" in method_modifiers:
                            raise ValueError("Abstract/declaration-only method needs an explicit implementation")
                    except ValueError as error:
                        method.unsupported_reason = str(error)
                    result.methods.append(method)
            except ValueError as error:
                result.diagnostics.append(f"Unsupported declaration: {' '.join(header)} ({error})")
        index = _matching(tokens, index, "{", "}") + 1 if token == "{" else index + 1
        start = index
    if paren_depth:
        result.diagnostics.append("Unbalanced method declaration parentheses; no guessed invocation")
    if start < closing and tokens[start:closing]:
        result.diagnostics.append("Unrecognized trailing member declaration; no guessed invocation")
    has_receiver = not abstract_class and (not constructors or any(visible and not params for visible, params in constructors))
    seen = set()
    unique = []
    for method in result.methods:
        if not method.is_static and not has_receiver:
            method.unsupported_reason = method.unsupported_reason or "No verified public/global zero-argument receiver constructor"
        try:
            signature_types = " ".join(typename for typename, _ in method.parameters()) + " " + method.return_type
        except ValueError:
            signature_types = method.return_type
        type_words = {word.lower() for word in _tokens(signature_types) if _IDENT.fullmatch(word)}
        if nested_types & type_words:
            method.unsupported_reason = method.unsupported_reason or "Nested type signature requires an explicitly qualified adapter"
        try:
            identity = (method.name.lower(), tuple(t.lower() for t, _ in method.parameters()))
        except ValueError:
            identity = (method.name.lower(), method.params_raw.lower())
        if identity not in seen:
            unique.append(method)
            seen.add(identity)
    result.methods = unique
    return result


def parse_methods(class_source: str) -> list[MethodSig]:
    """Return selected top-level methods, retaining unsupported reasons."""
    return analyze_class(class_source).methods


def _is_collection(typename: str) -> bool:
    return bool(re.match(r"(?i)^(list|set|map)<", typename)) or typename.endswith("[]")


def _call(class_name: str, method: MethodSig, arguments: list[str]) -> str:
    if method.unsupported_reason:
        raise ValueError(method.unsupported_reason)
    receiver = class_name if method.is_static else f"new {class_name}()"
    return f"{receiver}.{method.name}({', '.join(arguments)})"


def _null_invocation(class_name: str, method: MethodSig) -> str:
    return _call(class_name, method, [f"({typename})null" for typename, _ in method.parameters()])


def _empty_collection_invocation(class_name: str, method: MethodSig) -> str | None:
    params = method.parameters()
    if not any(_is_collection(typename) for typename, _ in params):
        return None
    def empty(typename: str) -> str:
        return f"new {typename}{{}}" if typename.endswith("[]") else f"new {typename}()"
    return _call(class_name, method, [empty(typename) if _is_collection(typename) else f"({typename})null" for typename, _ in params])


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ").replace("\r", " ") + "'"


def test_class_name(class_name: str) -> str:
    """Keep ordinary names, shortening long names with a stable collision suffix."""
    if not _IDENT.fullmatch(class_name) or len(class_name) > 40:
        raise ValueError("Source Apex class name must be a valid identifier of at most 40 characters")
    suffix = "AdversarialTest"
    if len(class_name + suffix) <= 40:
        return class_name + suffix
    digest = hashlib.sha256(class_name.lower().encode("utf-8")).hexdigest()[:8]
    return class_name[:40 - len(suffix) - 9] + "_" + digest + suffix


def _generate_test_class(class_name: str, methods: list[MethodSig], diagnostics: list[str] | None = None, scope_notes: list[str] | None = None) -> str:
    test_name = test_class_name(class_name)
    lines = ["@isTest(SeeAllData=false)", f"private class {test_name} {{", "",
             "    // EDITABLE DRAFT: generated source is not compiled, executed, or verified.",
             "    // Each case intentionally fails until its DRAFT assertion is replaced.",
             "    // Review the invocation and supply an expected result/state or exact exception.",
             "    // Calls may exercise target side effects in test context; no fixtures are invented.", ""]
    for message in diagnostics or []:
        lines.append("    // UNSUPPORTED: " + message.replace("\n", " ").replace("\r", " "))
    for message in dict.fromkeys(scope_notes or []):
        lines.append("    // SCOPE: " + message.replace("\n", " ").replace("\r", " "))
    generated = 0
    for index, method in enumerate(methods, start=1):
        signature = f"{method.name}({method.params_raw})"
        if method.unsupported_reason:
            lines.append(f"    // UNSUPPORTED {signature}: {method.unsupported_reason}")
            continue
        invocations = [("NullInputs", _null_invocation(class_name, method))]
        empty = _empty_collection_invocation(class_name, method)
        if empty is not None:
            invocations.append(("EmptyCollections", empty))
        for kind, invocation in invocations:
            generated += 1
            lines += ["    @isTest", f"    static void test{kind}_{index}() {{", f"        // Target: {signature}", "        Test.startTest();"]
            assignment = "" if method.return_type.lower() == "void" else f"{method.return_type} actual = "
            lines += [f"        {assignment}{invocation};", "        Test.stopTest();",
                      f"        System.assert(false, {_literal('DRAFT: supply a business expectation for ' + signature + ' / ' + kind)});", "    }", ""]
    if not generated or diagnostics or any(method.unsupported_reason for method in methods):
        lines += ["    @isTest", "    static void testDraftNeedsReview() {",
                  "        System.assert(false, 'DRAFT: unsupported or absent methods need explicit review; see source diagnostics.');", "    }", ""]
    lines += ["}", ""]
    return "\n".join(lines)


def generate_probes_for_class(class_source: str, class_name: str) -> str:
    analysis = analyze_class(class_source, class_name)
    if analysis.is_test:
        raise ValueError("Source is already an Apex test class")
    return _generate_test_class(analysis.class_name, analysis.methods, analysis.diagnostics, analysis.scope_notes)
