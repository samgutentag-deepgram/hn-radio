"""Measure the things HN Radio's writer prompt currently asserts without a number behind it.

Every metric here exists because a specific rule in hn_radio/writers.py picked a value by ear.
The point is not to copy Up First. It is to find out whether the guesses are in the right range,
so a rule can cite a measurement the way the rest of that file already does.

  metric                      the rule it speaks to
  --------------------------  --------------------------------------------------------
  cold_open_words             "TEN WORDS OR FEWER", a hard cap chosen by ear
  cold_open_count             one sentence per story, all in a single segment
  turn_words                  "write how people actually talk" -- no length target exists
  turns_per_story             how much back-and-forth a story actually carries
  name_rate                   "no target and no minimum" for saying each other's names
  question_rate               HARD RULE 3, one real follow-up per story
  filler_rate                 read copy vs. real talk, per speaker
"""
import json, re, statistics as st, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
FILLERS = {"uh", "um", "mhmm", "mm-hmm", "uh-huh", "huh", "er", "ah"}
SENT = re.compile(r"[^.!?]+[.!?]")


def words(t):
    return re.findall(r"[a-z0-9'\-]+", t.lower())


def load():
    out = []
    for f in sorted((HERE / "derived").glob("*.spans.json")):
        out.append((f.stem.replace(".spans", ""), json.loads(f.read_text())))
    return out


def host_names(spans):
    """Names the show actually uses, taken from the intro, so name-density is counted against
    real first names rather than a hardcoded list that goes stale when hosts change."""
    names = Counter()
    for s in spans[:40]:
        for m in re.finditer(r"\bI'?m ([A-Z][a-z]+)", s["transcript"]):
            names[m.group(1)] += 1
        for m in re.finditer(r"\bI'?m ([A-Z][a-z]+ [A-Z][a-z]+)", s["transcript"]):
            names[m.group(1).split()[0]] += 1
    return {n for n, _ in names.most_common(4)}


def main():
    eps = load()
    if not eps:
        sys.exit("no spans. run segment.py first.")

    co_words, turn_words, name_rates, q_rates, filler = [], [], [], [], Counter()
    spk_words = Counter()
    co_counts = []

    for date, spans in eps:
        show = [s for s in spans if s["kind"] in ("show", "intro")]
        if not show:
            continue
        names = host_names(spans)

        # Cold open: the headline run before the first sponsor break or the 3-minute mark,
        # whichever comes first. Up First previews the stories up top exactly as HN Radio does.
        first_sponsor = next((s["start"] for s in spans if s["kind"] == "sponsor"), 180.0)
        head = [s for s in show if s["start"] < min(first_sponsor, 180.0)]
        heads = [h.strip() for s in head for h in SENT.findall(s["transcript"])]
        # A headline is a standalone sentence; drop greetings and the show ID.
        heads = [h for h in heads
                 if not re.search(r"up first from npr|good morning|i'?m [A-Z]", h, re.I)]
        if heads:
            co_counts.append(len(heads))
            co_words += [len(words(h)) for h in heads]

        ep_turn_words, ep_names, ep_qs, ep_sents = [], 0, 0, 0
        for s in show:
            w = words(s["transcript"])
            if not w:
                continue
            ep_turn_words.append(len(w))
            spk_words[s.get("speaker")] += len(w)
            for tok in w:
                if tok in FILLERS:
                    filler[s.get("speaker")] += 1
            ep_names += sum(1 for tok in w if tok.capitalize() in names)
            sents = SENT.findall(s["transcript"]) or [s["transcript"]]
            ep_sents += len(sents)
            ep_qs += sum(1 for x in sents if x.strip().endswith("?"))

        turn_words += ep_turn_words
        total_w = sum(ep_turn_words) or 1
        name_rates.append(ep_names / total_w * 1000)
        q_rates.append(ep_qs / max(ep_sents, 1) * 100)

    def q(v, p):
        return st.quantiles(v, n=100)[p - 1] if len(v) > 2 else (v[0] if v else 0)

    print(f"corpus: {len(eps)} episodes\n")
    print("COLD OPEN")
    print(f"  headline sentences per episode : median {st.median(co_counts):.0f} "
          f"(range {min(co_counts)}-{max(co_counts)})")
    print(f"  words per headline             : median {st.median(co_words):.0f}  "
          f"mean {st.mean(co_words):.1f}  p90 {q(co_words,90):.0f}  max {max(co_words)}")
    print(f"  share at or under 10 words     : {sum(1 for w in co_words if w<=10)/len(co_words)*100:.0f}%")
    print("\nTURNS")
    print(f"  words per turn                 : median {st.median(turn_words):.0f}  "
          f"mean {st.mean(turn_words):.1f}  p90 {q(turn_words,90):.0f}")
    print(f"  turns per episode              : median {st.median([len(s) for _,s in eps]):.0f}")
    print("\nDENSITY (per episode)")
    print(f"  host-name mentions /1000 words : median {st.median(name_rates):.1f}")
    print(f"  questions as % of sentences    : median {st.median(q_rates):.1f}%")
    print("\nFILLER RATE BY SPEAKER (read copy vs. real talk)")
    for spk, w in spk_words.most_common(6):
        print(f"  S{spk}: {w:6} words, {filler[spk]:4} fillers = {filler[spk]/max(w,1)*1000:.1f}/1000")


if __name__ == "__main__":
    main()
