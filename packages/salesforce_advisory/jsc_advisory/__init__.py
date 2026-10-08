"""Nonrestrictive, read-only Salesforce advisory capabilities.

The package is deliberately outside the toolkit's hooks and write wrappers. Its default
CLI contract is informational: incomplete or negative verifier outcomes are
represented in data, not as a failing exit status. Callers must explicitly
request ``--strict`` if they want either reflected as exit 3.
"""

__version__ = "0.1.0"
