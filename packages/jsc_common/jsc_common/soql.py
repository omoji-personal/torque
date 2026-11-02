"""Encode a value inside a SOQL single-quoted string literal."""


def soql_literal(value: str) -> str:
    escapes = {'\\': '\\\\', "'": "\\'", '\n': '\\n', '\r': '\\r', '\t': '\\t', '\b': '\\b', '\f': '\\f'}
    return ''.join(escapes.get(char, char) for char in value)
