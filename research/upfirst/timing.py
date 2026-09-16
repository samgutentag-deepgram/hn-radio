"""Measure the SILENCE between turns on Up First. The one thing NPR's transcripts cannot give.

Everything else in this directory reads NPR's published transcripts, which carry no timestamps.
Gaps are the exception: to know how long a real show holds between two stories you have to
measure the audio, so this is the one stage that needs the API key, and it runs on a handful of
episodes rather than the corpus.

    python3 fetch.py ... && python3 transcribe.py --limit 4 && python3 timing.py

What it compares against, from hn_radio/pacing.py and music.py at the time of writing:

    exchange       0.16s    two voices trading inside a story
    same_speaker   0.22s    one voice continuing
    story_change   0.85s    replaced by a sting where one lands:
                            CUE_GAP_SECONDS 0.16 + STING_SECONDS 2.0 + 0.16 = 2.32s
"""
import json, statistics as st, sys
from pathlib import Path

HERE = Path(__file__).parent
HN = {"exchange": 0.16, "same_speaker": 0.22, "story_change (sting)": 0.16 + 2.0 + 0.16}


def gaps_for(path):
    d = json.loads(path.read_text())
    utts = d["results"].get("utterances", [])
    out = []
    for a, b in zip(utts, utts[1:]):
        g = b["start"] - a["end"]
        if g < 0:
            continue
        out.append((g, a.get("speaker") == b.get("speaker"), a["end"]))
    return out


CLASSES = [
    ("inside the cold open", 1.2, 4.0, 60.0, "tease -> preview, under music"),
    ("cold open -> first story", 4.0, 8.0, 90.0, "the show proper starts"),
    ("between stories", 6.0, 12.0, None, "a full music bed, not a sting"),
]


def classify(gap, at, dur):
    """Where a long hold sits in the episode is what says which kind of hold it is."""
    if gap < 1.2:
        return None
    if at < 60:
        return "inside the cold open"
    if at < 90:
        return "cold open -> first story"
    if at > dur - 60:
        return "sign-off"
    return "between stories"


def main():
    files = sorted((HERE / "transcripts").glob("*.json"))
    if not files:
        sys.exit("no transcripts. run transcribe.py first.")

    turn, same, buckets, durs = [], [], {}, []
    for f in files:
        d = json.loads(f.read_text())
        utts = d["results"].get("utterances", [])
        dur = d.get("metadata", {}).get("duration", 0) or utts[-1]["end"]
        durs.append(dur)
        for a, b in zip(utts, utts[1:]):
            g = b["start"] - a["end"]
            if g < 0:
                continue
            kind = classify(g, a["end"], dur)
            if kind is None:
                (same if a.get("speaker") == b.get("speaker") else turn).append(g)
            else:
                buckets.setdefault(kind, []).append(g)

    print(f"UP FIRST, {len(files)} episodes, measured from the audio")
    print(f"  episode length median {st.median(durs)/60:.1f} min\n")
    print("  TURN TO TURN (under 1.2s: the conversation itself)")
    print(f"    speaker changes        median {st.median(turn):5.2f}s   n={len(turn)}")
    print(f"    same speaker continues median {st.median(same):5.2f}s   n={len(same)}")
    print("\n  STRUCTURAL HOLDS (over 1.2s: where the music is)")
    for k in ("inside the cold open", "cold open -> first story", "between stories", "sign-off"):
        v = buckets.get(k)
        if v:
            print(f"    {k:26} median {st.median(v):5.2f}s   n={len(v)}   "
                  f"range {min(v):.1f}-{max(v):.1f}")

    hn_total = 375.0   # a recent HN Radio episode, seconds
    uf_total = st.median(durs)
    print(f"\nHN RADIO today vs Up First, both as measured and scaled for show length")
    print(f"  (HN Radio runs {hn_total/60:.1f} min against Up First's {uf_total/60:.1f}, so the "
          f"scale factor is {hn_total/uf_total:.2f})\n")
    rows = [
        ("turn to turn", HN["exchange"], st.median(turn)),
        ("same speaker", HN["same_speaker"], st.median(same)),
        ("between stories", HN["story_change (sting)"],
         st.median(buckets.get("between stories", [0]))),
        ("inside the cold open", 0.55,
         st.median(buckets.get("inside the cold open", [0]))),
    ]
    print(f"  {'':24} {'HN Radio':>9} {'Up First':>9} {'UF scaled':>10} {'factor':>8}")
    for name, hn, uf in rows:
        scaled = uf * (hn_total / uf_total)
        print(f"  {name:24} {hn:8.2f}s {uf:8.2f}s {scaled:9.2f}s {scaled/max(hn,0.01):7.1f}x")


if __name__ == "__main__":
    main()
