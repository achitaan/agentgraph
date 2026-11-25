"""Portable revision identifiers shared by readers and writers."""
import hashlib


def rev(*parts):
    """Hash length-prefixed parts, distinguishing missing from empty."""
    digest = hashlib.sha256()
    for part in parts:
        if part is None:
            digest.update(b"-")
            continue
        data = part.encode("utf-8") if isinstance(part, str) else bytes(part)
        digest.update(b"+%d:" % len(data))
        digest.update(data)
    return digest.hexdigest()[:16]
