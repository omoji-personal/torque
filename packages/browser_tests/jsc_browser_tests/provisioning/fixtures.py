"""Generic identifiers for operator-supplied client fixture builders."""
import secrets

def new_runid() -> str:
    return "TEST-" + secrets.token_hex(4)
