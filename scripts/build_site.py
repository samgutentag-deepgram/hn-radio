"""Assemble the generated artifacts before deploy: feed.xml plus the JSON the app reads.

The episodes/ dir is NOT the web root. `backend/app.py` mounts `web/` at `/` and `episodes/`
at `/episodes`, so the landing page is `web/index.html`. This script used to also generate an
`episodes/index.html` landing page from a time when the static dir WAS the root; that page was
superseded by the app and removed.

Run: uv run python scripts/build_site.py
Set HN_RADIO_BASE_URL to the real origin so RSS + subscribe links are absolute. It must INCLUDE
the /episodes segment: enclosures live under it, and config.site_app_url() derives the human-facing
origin by stripping it back off, so a value without it puts every page link under /episodes.
  HN_RADIO_BASE_URL=https://hn-radio.fly.dev/episodes uv run python scripts/build_site.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import config, publish

written = publish.rebuild_site(config.EPISODES_DIR)
print(f"base url:  {config.site_base_url()}")
print(f"app url:   {config.site_app_url()}")
for name, path in written.items():
    print(f"{name + ':':11}{path}")
print(f"episodes:  {config.EPISODES_DIR}")
