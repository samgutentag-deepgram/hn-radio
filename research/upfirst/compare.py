"""HN Radio's own episodes against the Up First corpus, on the same metrics.

This is the half that turns a target into a comparison. Scripts come from the live archive over
plain HTTPS (backend/app.py serves the volume's episodes dir as static files), so it is read-only
and needs no key.

    python3 pull_hnradio.py     # then
    python3 compare.py

The finding this was built to test, and the reason the fix changed: the show's segments are NOT
too short. They are median 33 words against Up First's 25. What they are is all the SAME length.
Up First's turn lengths spread 22.7x from p10 to p90; HN Radio's spread 3.9x. Consecutive turns
on Up First differ by a median 3.2x, on HN Radio by 1.8x. Both shows alternate speakers on ~90%
of turns, so the ping-pong is not the problem either. A show where every turn is a medium
paragraph traded at a fixed rate is what reads as machine-generated, and no amount of "write
longer sections" fixes it -- longer and uniform is still uniform.
"""
import glob, json, re, statistics as st, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
WORD = re.compile(r"[A-Za-z0-9'\-]+")


def turns_up_first():
    out = []
    for f in sorted((HERE / "npr_transcripts").glob("*.json")):
        ep = [t for t in json.loads(f.read_text())["turns"] if t["kind"] == "speech"]
        out.append([(t["speaker"], len(WORD.findall(t["text"]))) for t in ep])
    return out


def turns_hn_radio():
    out = []
    for f in sorted((HERE / "hnradio_scripts").glob("*.json")):
        d = json.loads(f.read_text())
        segs = d["segments"] if isinstance(d, dict) else d
        out.append([(s.get("speaker_key"), len(WORD.findall(s.get("text", "")))) for s in segs])
    return out


def report(name, eps):
    lens = [w for ep in eps for _, w in ep]
    runs = []
    for ep in eps:
        cur, n = None, 0
        for spk, _ in ep:
            if spk == cur:
                n += 1
            else:
                if cur is not None:
                    runs.append(n)
                cur, n = spk, 1
        runs.append(n)
    ratios = [max(a, b) / max(min(a, b), 1)
              for ep in eps for (_, a), (_, b) in zip(ep, ep[1:]) if min(a, b) > 0]
    q = st.quantiles(lens, n=100)
    iqr = st.quantiles(lens, n=4)
    held = sum(1 for r in runs if r > 1) / len(runs) * 100
    print(f"{name:10} n={len(lens):5} eps={len(eps):3}")
    print(f"  words/turn      median {st.median(lens):5.0f}   IQR {iqr[0]:.0f}-{iqr[2]:.0f}")
    print(f"  spread p90/p10  {q[89]/max(q[9],1):5.1f}x   <- dynamic range")
    print(f"  turn N vs N+1   median {st.median(ratios):5.2f}x   "
          f"{sum(1 for r in ratios if r>2)/len(ratios)*100:.0f}% of boundaries >2x uneven")
    print(f"  holds the floor {held:5.0f}% of turns run 2+ before the other speaks")


if __name__ == "__main__":
    uf, hr = turns_up_first(), turns_hn_radio()
    if not uf or not hr:
        sys.exit("need both corpora: pull_transcripts.py and pull_hnradio.py")
    report("Up First", uf)
    print()
    report("HN Radio", hr)
