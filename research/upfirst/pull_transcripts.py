"""Pull NPR's own published transcript for each episode and parse it into speaker turns.

Strictly better than STT for everything except timing. NPR labels every turn with a NAME and a
ROLE -- "LEILA FADEL, HOST" vs "SCOTT HORSLEY, BYLINE" -- which is the one distinction that
matters most here: host copy is written and read, correspondent copy is reported, and HN Radio is
entirely the former. Diarization would only ever give back "speaker 0" and leave that unresolved.

What it does NOT carry is timestamps, so anything measured in seconds still needs the audio.
That is what transcribe.py is for, on a small sample.

Markup note: NPR emits the transcript body as UNCLOSED <p> tags used as separators, so the
document is one long run of `<p><p>SPEAKER: <p><p>text`. Splitting on runs of <p> is the parse.
"""
import argparse, html, json, re, sys, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

BODY = re.compile(r'<div class="transcript storytext"[^>]*>(.*?)</div>', re.S)
# "LEILA FADEL, HOST:" / "FADEL:" / "UNIDENTIFIED PERSON #1:". Surnames carry accents, and the
# label may be alone in its chunk or sit in front of the line it introduces.
LABEL = re.compile(r"^([A-ZÁ-ÚÑ][A-ZÁ-ÚÑ0-9 .,'#\-]{1,48}?)"
                   r"(?:,\s*([A-Z][A-Za-z ,'\-]{2,40}?))?:\s*")
STAGE = re.compile(r"^\((.+)\)$")
# The membership plug NPR staples to the top and bottom of every transcript page.
BOILER = re.compile(r"your support helps make our show possible|unlocks access to our sponsor",
                    re.I)


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse(raw_html: str):
    m = BODY.search(raw_html)
    if not m:
        return None
    chunks = [strip_tags(c) for c in re.split(r"(?:<p>\s*)+", m.group(1))]
    chunks = [c for c in chunks if c and not BOILER.search(c)]

    turns, speaker, role = [], None, None
    for c in chunks:
        st = STAGE.match(c)
        if st:
            turns.append({"kind": "stage", "speaker": None, "role": None, "text": st.group(1)})
            continue
        lm = LABEL.match(c)
        if lm:
            speaker = lm.group(1).strip().rstrip(",")
            # A role only arrives with the full-name introduction; later turns are surname-only
            # and inherit nothing, so carry the last role seen for that speaker.
            role = (lm.group(2) or "").strip().upper() or None
            rest = c[lm.end():].strip()
            if not rest:
                continue
            c = rest
        if speaker is None:
            continue
        turns.append({"kind": "speech", "speaker": speaker, "role": role, "text": c})

    # NPR introduces a person in full ("LEILA FADEL, HOST") and refers to them by surname after
    # ("FADEL"). Left alone that is two speakers and every per-speaker count is wrong, so collapse
    # on the surname and let the full-name turn carry the role to all of them.
    known, full = {}, {}
    for t in turns:
        if t["kind"] != "speech" or not t["speaker"]:
            continue
        key = t["speaker"].split()[-1]
        if t["role"]:
            known.setdefault(key, t["role"])
        if len(t["speaker"].split()) > 1:
            full.setdefault(key, t["speaker"])
    for t in turns:
        if t["kind"] != "speech" or not t["speaker"]:
            continue
        key = t["speaker"].split()[-1]
        t["role"] = t["role"] or known.get(key)
        t["name"] = full.get(key, t["speaker"])
        t["speaker"] = key
    return turns


def get(url, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError("404 no transcript") from e
            last = e
        except Exception as e:
            last = e
        time.sleep(2 ** i)
    raise last


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    a = p.parse_args()

    out_dir = HERE / "npr_transcripts"
    out_dir.mkdir(exist_ok=True)
    manifest = json.loads((HERE / "manifest.json").read_text())
    if a.limit:
        manifest = manifest[: a.limit]

    ok = skip = fail = 0
    for i, e in enumerate(manifest, 1):
        tag = f"[{i}/{len(manifest)}] {e['date']}"
        dest = out_dir / f"{e['date']}.json"
        if dest.exists():
            print(f"  {tag} cached", file=sys.stderr); skip += 1; continue
        if not e.get("story_id"):
            print(f"  {tag} no story id in feed", file=sys.stderr); fail += 1; continue
        try:
            turns = parse(get(f"https://www.npr.org/transcripts/{e['story_id']}"))
        except Exception as exc:
            print(f"  {tag} FAILED: {exc}", file=sys.stderr); fail += 1; continue
        if not turns:
            print(f"  {tag} no transcript body on page", file=sys.stderr); fail += 1; continue
        dest.write_text(json.dumps({"episode": e, "turns": turns}, indent=2) + "\n")
        hosts = {t["speaker"] for t in turns if t["role"] == "HOST"}
        words = sum(len(t["text"].split()) for t in turns if t["kind"] == "speech")
        print(f"  {tag} {len(turns):3} turns, {words:5} words, hosts={sorted(hosts)}",
              file=sys.stderr)
        ok += 1
        time.sleep(a.delay)

    print(f"\npulled {ok}, cached {skip}, failed {fail}", file=sys.stderr)


if __name__ == "__main__":
    main()
