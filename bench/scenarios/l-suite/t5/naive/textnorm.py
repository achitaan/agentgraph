"""Small text helpers shared by the contacts code (search, export and the address-book ordering)."""
import re
import unicodedata

_SPACES = re.compile(r"\s+")


def collapse_spaces(text):
    """Squeeze every run of white space into one space and strip both ends."""
    return _SPACES.sub(" ", text).strip()


def normalize(text):
    """The comparison form of a name: white space squeezed, ends stripped, lower case.

    Used wherever two names have to be compared or ordered, so that 'Ada  LOVELACE ' and 'ada lovelace' are the same.
    """
    text = collapse_spaces(text).lower()
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))


def initials(name):
    """'ada king lovelace' -> 'AKL'. Words are split on white space."""
    return "".join(word[0].upper() for word in name.split())


def shorten(text, width):
    """Cut `text` to at most `width` characters, ending with '...' when something was cut."""
    if width < 4:
        raise ValueError("width must be at least 4")
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def words(text):
    """The words of `text` in order, as written."""
    return text.split()


def title_case(name):
    """'ada  king LOVELACE' -> 'Ada King Lovelace' (each word capitalised, white space squeezed)."""
    return " ".join(word[:1].upper() + word[1:].lower() for word in name.split())


def strip_title(name):
    """Drop a leading courtesy title: 'Dr. Ada Lovelace' -> 'Ada Lovelace'. Other names are returned unchanged."""
    parts = name.split(None, 1)
    if len(parts) == 2 and parts[0].rstrip(".").lower() in ("dr", "mr", "mrs", "ms", "prof"):
        return parts[1]
    return name


def split_name(full):
    """'Ada King Lovelace' -> ('Ada King', 'Lovelace'). A single word is a first name with an empty last name."""
    parts = collapse_spaces(full).rsplit(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")
