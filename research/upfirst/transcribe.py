"""Transcribe the downloaded episodes with Deepgram nova-3 + diarization.

Resumable: an episode with a transcript already on disk is skipped, so a re-run after a
failure costs nothing and re-running the whole set is free.

filler_words is ON deliberately. Up First host copy is written and read; correspondent answers
are actually spoken. Filler density is the cleanest signal separating the two, which matters
because HN Radio is entirely read copy and should only imitate the parts that are also read copy.
"""
import argparse, json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
ENDPOINT = "https://api.deepgram.com/v1/listen"
PARAMS = {
    "model": "nova-3",
    "diarize": "true",
    "punctuate": "true",
    "smart_format": "true",
    "paragraphs": "true",
    "utterances": "true",
    "filler_words": "true",
    "utt_split": "0.8",
}


def key() -> str:
    k = os.environ.get("DEEPGRAM_API_KEY")
    if not k:
        sys.exit("DEEPGRAM_API_KEY is not set. export it, or pass --key.")
    return k


def transcribe(path: Path, api_key: str, retries: int = 3) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in PARAMS.items())
    req = urllib.request.Request(
        f"{ENDPOINT}?{qs}",
        data=path.read_bytes(),
        headers={"Authorization": f"Token {api_key}", "Content-Type": "audio/mpeg"},
        method="POST",
    )
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            # 4xx other than 429 is a bad request; retrying just burns time.
            if e.code < 500 and e.code != 429:
                raise RuntimeError(f"HTTP {e.code}: {body}") from e
            last = RuntimeError(f"HTTP {e.code}: {body}")
        except Exception as e:
            last = e
        time.sleep(2 ** attempt)
    raise last


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--key", default=None)
    p.add_argument("--limit", type=int, default=None, help="only do the N most recent")
    a = p.parse_args()
    api_key = a.key or key()

    manifest = json.loads((HERE / "manifest.json").read_text())
    if a.limit:
        manifest = manifest[: a.limit]

    done = failed = skipped = 0
    for i, e in enumerate(manifest, 1):
        audio = HERE / e["audio"]
        out = HERE / "transcripts" / f"{e['date']}.json"
        tag = f"[{i}/{len(manifest)}] {e['date']}"
        if out.exists() and out.stat().st_size > 0:
            print(f"  {tag} cached", file=sys.stderr)
            skipped += 1
            continue
        if not audio.exists():
            print(f"  {tag} NO AUDIO, run fetch.py first", file=sys.stderr)
            failed += 1
            continue
        t0 = time.time()
        try:
            res = transcribe(audio, api_key)
        except Exception as exc:
            print(f"  {tag} FAILED: {exc}", file=sys.stderr)
            failed += 1
            continue
        res["_episode"] = e
        out.write_text(json.dumps(res, indent=2) + "\n")
        secs = res.get("metadata", {}).get("duration", 0)
        spk = len({u.get("speaker") for u in res["results"].get("utterances", [])})
        print(f"  {tag} ok  {secs/60:.1f}min audio, {spk} speakers, {time.time()-t0:.0f}s",
              file=sys.stderr)
        done += 1

    print(f"\ntranscribed {done}, cached {skipped}, failed {failed}", file=sys.stderr)


if __name__ == "__main__":
    main()
