"""Pull HN Radio's own script.json off the live archive. READ ONLY, no key.

backend/app.py mounts the Fly volume's episodes directory as static files, so every file here is
a plain GET against the public app. Only script.json: the words are what this compares, and the
audio is 30MB an episode.
"""
import argparse, json, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
BASE = "https://dg-devrel-hn-radio.fly.dev/episodes"


def get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-n", "--count", type=int, default=12)
    a = p.parse_args()
    out = HERE / "hnradio_scripts"
    out.mkdir(exist_ok=True)

    idx = json.loads(get(f"{BASE}/index.json"))
    eps = idx["episodes"] if isinstance(idx, dict) else idx
    ids = [e["id"] for e in eps][: a.count]
    ok = fail = 0
    for i in ids:
        dest = out / f"{i}.json"
        if dest.exists():
            print(f"  {i} cached", file=sys.stderr)
            continue
        try:
            dest.write_bytes(get(f"{BASE}/{i}/script.json"))
            print(f"  {i} ok", file=sys.stderr)
            ok += 1
        except Exception as e:
            print(f"  {i} FAILED: {e}", file=sys.stderr)
            fail += 1
        time.sleep(0.3)
    print(f"\npulled {ok}, failed {fail}", file=sys.stderr)


if __name__ == "__main__":
    main()
