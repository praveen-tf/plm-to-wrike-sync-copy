"""Wrike description handling: section-preserving merge of PLM-owned sections.

The description interleaves PLM-owned sections (Material Codes, Contents) with
Wrike-owned content (Design Brief, manual notes). On update we replace ONLY the
PLM-owned section bodies and leave everything else exactly as it is in Wrike.
"""
from __future__ import annotations

import re

# Matches a section header like: <h5>Material Codes:     </h5>
_HEADER_RE = re.compile(r"<h5>\s*([^<:]+?)\s*:\s*</h5>")
# Trailing <br>/whitespace separators at the end of a section body.
_TRAIL_RE = re.compile(r"((?:<br>|\s)*)$")


def merge_description(existing: str, *, material_codes: str | None = None,
                      contents: str | None = None) -> str:
    """Replace only the Material Codes / Contents section bodies; preserve the rest.

    Only the PLM-owned section bodies are swapped; Wrike-owned content and the
    trailing <br>/whitespace separators around each body are left untouched. The
    sync timestamp is recorded in a card comment, not in the description.
    """
    replacements = {}
    if material_codes is not None:
        replacements["Material Codes"] = material_codes
    if contents is not None:
        replacements["Contents"] = contents

    headers = list(_HEADER_RE.finditer(existing))
    if not headers:
        return existing

    parts = [existing[: headers[0].start()]]  # any preamble before the first header
    for i, h in enumerate(headers):
        label = h.group(1).strip()
        body_start = h.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(existing)
        body = existing[body_start:body_end]
        if label in replacements:
            trail = _TRAIL_RE.search(body).group(1)  # keep trailing separators
            body = replacements[label] + trail
        parts.append(existing[h.start():body_start] + body)
    return "".join(parts)
