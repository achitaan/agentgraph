"""URL slugs."""


def slugify(text):
    """Turn text into a URL slug.

    Lower-case it, replace every run of characters that are not ASCII letters or digits
    with a single "-", and strip "-" from both ends.
    slugify("  Hello, World!  ") == "hello-world"
    slugify("Already-a-slug") == "already-a-slug"
    slugify("!!!") == ""
    A non-string argument raises TypeError.
    """
    raise NotImplementedError
