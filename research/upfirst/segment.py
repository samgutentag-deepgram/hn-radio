"""Cut each transcript into show / sponsor / credits and emit a clean readable transcript.

The patterns below are the STARTING point, not the finished ones. Sponsor and credit copy is
formulaic enough that regex works, but the exact wording drifts and only a run against real
transcripts shows which variants NPR is actually using this month. `--report` prints every span
it cut with surrounding context so the cuts can be eyeballed before anything is measured on them.
"""
import argparse, json, re, sys
from pathlib import Path

HERE = Path(__file__).parent

# Sponsor reads. NPR's underwriting copy is boilerplate and opens with one of these.
SPONSOR_OPEN = re.compile(
    r"\b("
    r"support for (npr|this (npr )?podcast|this program)"
    r"|(and )?the following message come(s)? from"
    r"|this message comes from"
    r"|support (for this podcast )?(and the following message )?come(s)? from"
    r"|npr'?s sponsors"
    r")\b", re.I)
# A sponsor read ends when the show resumes; these are what the resume sounds like.
SPONSOR_CLOSE = re.compile(
    r"\b(it'?s up first|you'?re listening to up first|this is up first|now to|meanwhile|"
    r"back to|our next story|first up|let'?s (turn|go) to)\b", re.I)

# The end-of-show credit roll. Not conversation; it would skew every average it touched.
CREDITS = re.compile(
    r"\b(today'?s (episode|show) (was )?(edited|produced) by"
    r"|(our|the) (technical director|executive producer|supervising editor|engineer)"
    r"|we get engineering support from"
    r"|and our technical director"
    r"|(was|were) (edited|produced|mixed) by [A-Z]"
    r")\b", re.I)

# The fixed open. Useful to keep, but flagged so it is measured as format and not as talk.
INTRO = re.compile(r"\b(up first from npr news|good morning,? i'?m)\b", re.I)


def label(utterances):
    """Tag every utterance show | sponsor | credits | intro. Sticky: a sponsor read runs
    across several utterances, so once opened it stays open until something closes it."""
    out, in_sponsor, in_credits = [], False, False
    for u in utterances:
        t = u.get("transcript", "")
        kind = "show"
        if in_credits or CREDITS.search(t):
            in_credits, kind = True, "credits"
        elif SPONSOR_OPEN.search(t):
            in_sponsor, kind = True, "sponsor"
        elif in_sponsor:
            if SPONSOR_CLOSE.search(t):
                in_sponsor, kind = False, "show"
            else:
                kind = "sponsor"
        elif INTRO.search(t):
            kind = "intro"
        out.append({**u, "kind": kind})
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--report", action="store_true", help="print what was cut, for eyeballing")
    a = p.parse_args()

    files = sorted((HERE / "transcripts").glob("*.json"))
    if not files:
        sys.exit("no transcripts. run fetch.py then transcribe.py first.")

    totals = {}
    for f in files:
        data = json.loads(f.read_text())
        utts = data["results"].get("utterances", [])
        if not utts:
            print(f"  {f.stem}: no utterances (was diarize/utterances on?)", file=sys.stderr)
            continue
        tagged = label(utts)

        for u in tagged:
            secs = u["end"] - u["start"]
            totals[u["kind"]] = totals.get(u["kind"], 0) + secs

        # Readable, speaker-labeled, sponsor-free. This is what gets read for style.
        lines = []
        for u in tagged:
            if u["kind"] in ("sponsor", "credits"):
                continue
            mark = " [INTRO]" if u["kind"] == "intro" else ""
            lines.append(f"[{u['start']:7.1f}] S{u.get('speaker','?')}{mark}: {u['transcript']}")
        (HERE / "derived" / f"{f.stem}.txt").write_text("\n".join(lines) + "\n")

        # The spans, kept so a measurement can always be traced back to what it was taken from.
        (HERE / "derived" / f"{f.stem}.spans.json").write_text(
            json.dumps([{k: u[k] for k in ("start", "end", "speaker", "kind", "transcript")}
                        for u in tagged], indent=2) + "\n")

        if a.report:
            print(f"\n=== {f.stem} ===")
            for u in tagged:
                if u["kind"] in ("sponsor", "credits"):
                    print(f"  CUT {u['kind']:8} [{u['start']:6.1f}] {u['transcript'][:90]}")

    grand = sum(totals.values()) or 1
    print("\ncorpus time by kind:", file=sys.stderr)
    for k, v in sorted(totals.items(), key=lambda x: -x[1]):
        print(f"  {k:9} {v/60:7.1f} min  {v/grand*100:5.1f}%", file=sys.stderr)


if __name__ == "__main__":
    main()
