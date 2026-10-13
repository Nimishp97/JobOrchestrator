"""Delimiting untrusted text (job descriptions, recruiter emails) so it stays data."""

import re

_LABEL = re.compile(r"[a-z][a-z0-9_]*")


def wrap_untrusted(label: str, text: str) -> str:
    """Wrap `text` in <label>...</label>, neutralizing any tag that could close the block.

    Any `<label`, `</label` (any case, optional spaces) inside the text has its `<` replaced
    with `&lt;`, so the text cannot end its own delimiter or fake a second block.
    """
    if not _LABEL.fullmatch(label):
        raise ValueError(f"invalid label: {label!r}")
    tag = re.compile(rf"<(\s*/?\s*{re.escape(label)}\b)", re.IGNORECASE)
    return f"<{label}>\n{tag.sub(r'&lt;\1', text)}\n</{label}>"
