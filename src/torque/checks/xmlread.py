"""A small XML tree that remembers line numbers.

ElementTree does not keep lines, and a finding a consultant can act on needs one. Tags are stored
without their namespace (Salesforce metadata uses one default namespace), so `node.tag` is the
local name. A document type declaration is refused: metadata never needs one, and refusing it
keeps entity expansion out.
"""
from __future__ import annotations

from xml.parsers import expat


class XmlError(ValueError):
    """The file is not well-formed XML (or holds a DOCTYPE)."""

    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line


class Node:
    __slots__ = ("tag", "attrs", "children", "text", "line", "end_line", "parent")

    def __init__(self, tag: str, attrs: dict, line: int, parent: "Node | None"):
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node] = []
        self.text = ""
        self.line = line
        self.end_line = line
        self.parent = parent

    def child(self, name: str) -> "Node | None":
        for c in self.children:
            if c.tag == name:
                return c
        return None

    def all(self, name: str) -> list["Node"]:
        return [c for c in self.children if c.tag == name]

    def value(self, name: str) -> str:
        """Stripped text of the first child called `name`, or ""."""
        c = self.child(name)
        return c.text.strip() if c is not None else ""

    def flag(self, name: str) -> bool:
        return self.value(name).lower() == "true"

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()

    def fingerprint(self) -> tuple:
        """Structure and text without whitespace, the root tag or its attributes, so a <CustomField>
        file and a <fields> entry of a Metadata API object compare equal when they hold the same
        definition. Order of children is kept: it can matter."""
        def walk(n: Node):
            return (n.tag, n.text.strip(), tuple(sorted(n.attrs.items())), tuple(walk(c) for c in n.children))
        return (self.text.strip(), tuple(walk(c) for c in self.children))


def _local(name: str) -> str:
    return name.rsplit(" ", 1)[-1]


def parse(data: bytes) -> Node:
    """Parse bytes into a Node tree. Raises XmlError with the line of the problem."""
    parser = expat.ParserCreate(namespace_separator=" ")
    try:
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    except AttributeError:  # pragma: no cover - very old expat
        pass
    stack: list[Node] = []
    root: list[Node] = []

    def start(name, attrs):
        node = Node(_local(name), {_local(k): v for k, v in attrs.items()}, parser.CurrentLineNumber,
                    stack[-1] if stack else None)
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)

    def end(_name):
        node = stack.pop()
        node.end_line = parser.CurrentLineNumber

    def chars(text):
        if stack:
            stack[-1].text += text

    def doctype(*_args):
        raise XmlError("the file has a document type declaration; Salesforce metadata never needs one",
                       parser.CurrentLineNumber)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.StartDoctypeDeclHandler = doctype
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise XmlError(f"not well-formed XML: {expat.ErrorString(exc.code)}", exc.lineno) from None
    if not root:
        raise XmlError("no root element", 1)
    return root[0]
