"""Small allowlist grammar: literal characters and ASCII letter/digit ranges."""

import re
from functools import lru_cache

DEFAULT_ALLOWED_CHARS = "[]0-9a-zA-Z -()"
_RANGE = re.compile(r"(?:[0-9]-[0-9]|[a-z]-[a-z]|[A-Z]-[A-Z])")


def allowed_characters(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError(
            "filename.allowed_chars must be a string of at most 4096 characters or null"
        )
    return _parse(value)


@lru_cache(maxsize=128)
def _parse(value):
    if value == "":
        return None
    allowed = set()
    index = 0
    while index < len(value):
        token = value[index : index + 3]
        if _RANGE.fullmatch(token):
            first, last = ord(token[0]), ord(token[2])
            if first > last:
                raise ValueError(f"Invalid descending range in filename.allowed_chars: {token}")
            allowed.update(chr(code) for code in range(first, last + 1))
            index += 3
        else:
            allowed.add(value[index])
            index += 1
    return frozenset(allowed)
