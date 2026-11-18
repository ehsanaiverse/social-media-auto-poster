"""Caption text for LinkedIn's Posts API ("little" text format).

LinkedIn treats ``| { } @ [ ] ( ) < > # \\ * _ ~`` as markup and requires a
backslash before each one that should appear as plain text, even outside any
markup. Unescaped, a caption such as "Launch (beta)" may not come out as written.

Rules here: every reserved character is escaped, except a ``#`` that starts a
hashtag (``#`` followed by a letter or digit, and not directly after one, so
"C#" stays plain text). ``@`` is always escaped: mentions are not supported.
"""

RESERVED = frozenset("|{}@[]()<>#\\*_~")

# LinkedIn documents 3,000 characters for post text (UGC Post API); the Posts API names no number.
MAX_CAPTION_LENGTH = 3000


def escape_caption(text: str) -> str:
    """``text`` with every reserved character escaped, keeping ``#word`` hashtags."""
    escaped = []
    for index, char in enumerate(text):
        if char in RESERVED and not (char == "#" and _starts_hashtag(text, index)):
            escaped.append("\\")
        escaped.append(char)
    return "".join(escaped)


def caption_length(text: str) -> int:
    """Length as LinkedIn's limit is checked here: UTF-16 code units of the caption as written.

    This is how browsers count text, and never less than the number of characters:
    an emoji counts as two, which errs on the safe side. Escaping backslashes are
    not counted; they are not part of what readers see.
    """
    return len(text.encode("utf-16-le")) // 2


def caption_problem(text: str) -> str | None:
    """Why this caption cannot be posted, or None."""
    length = caption_length(text)
    if length > MAX_CAPTION_LENGTH:
        return f"caption is {length} characters; LinkedIn allows at most {MAX_CAPTION_LENGTH}"
    return None


def _starts_hashtag(text: str, index: int) -> bool:
    following = text[index + 1] if index + 1 < len(text) else ""
    previous = text[index - 1] if index > 0 else ""
    return following.isalnum() and not previous.isalnum()
