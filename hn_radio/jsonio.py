"""One way to write a JSON artifact, so every artifact on disk looks the same.

This is here rather than on any one format module because it is not about any one format. It was
briefly a private helper in `publish.py`, then a public one in `manifest.py` -- which meant
`publish.publish` imported `manifest` to write `episode.json` and `script.json`, neither of which
is a manifest, and `scripts/add_chapters.py` hand-rolled its own `json.dumps` instead of importing
across that odd seam. Three writers for one job, and they did not agree.

Nothing here reads JSON. `json.loads` needs no wrapper.
"""

from __future__ import annotations

import json
from pathlib import Path


def write_json(path: Path, data) -> None:
    """Write `data` as indented JSON, keeping non-ASCII characters intact.

    `ensure_ascii=False` is deliberate: episode titles carry real punctuation and names, and
    escaping them to backslash-u escapes makes the artifacts unreadable for no benefit when
    everything downstream is UTF-8. It is also why this has one home. The copies that escaped and
    the copies that did not both touched `episode.json`, so whether a committed episode read
    `Diátaxis` or `Di\\u00e1taxis` came down to which tool wrote it last -- churn in every diff,
    signifying nothing.
    """
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
