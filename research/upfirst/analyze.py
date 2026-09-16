"""Measure the things hn_radio/writers.py currently asserts by ear.

The point is not to copy Up First. It is to find out whether the guesses in the writer prompt sit
in the range a produced show actually uses, so a rule can cite a measurement the way the rest of
that file already does.

  metric                    the rule it speaks to (hn_radio/writers.py)
  ------------------------  ----------------------------------------------------------
  cold-open words/headline  "TEN WORDS OR FEWER. That cap is hard."
  last headline "And"       'The FINAL cold-open sentence must start with "And"'
  cold-open questions       "Read the cold open as MATTER-OF-FACT as possible"
  words per turn by role    "WRITE HOW PEOPLE ACTUALLY TALK" -- no length target exists
  host question share       HARD RULE 3, one real follow-up per story
  name mentions per story   "There is no target and no minimum"
  stings per episode        the music sting at every story change

(SOUNDBITE OF MUSIC) is the story delimiter. It is the same device HN Radio uses, which is what
makes the block structure comparable at all.

Every episode in the corpus has the identical block shape: (3, N, story, story, story, sign-off,
copyright). So the cold open is TWO blocks, not one -- a 3-turn tease of story 1 carrying the show
ID, then a separate block previewing the rest. They are measured apart because they behave
differently, and reading them as one block is what makes the ten-word cap look violated.
"""
import json, re, statistics as st, sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
SENT = re.compile(r"[^.!?]+[.!?]")
WORD = re.compile(r"[A-Za-z0-9'\-]+")
MUSIC = "SOUNDBITE OF MUSIC"
# The sign-off and the legal boilerplate. Not show copy; they would drag every average.
TAIL = re.compile(r"\b(and that'?s up first for|today'?s episode of up first was|"
                  r"copyright ©|npr transcripts are created|visit our website'?s terms)\b", re.I)


def words(t):
    return WORD.findall(t)


def blocks(turns):
    """Split an episode on music stings. Block 0 is the cold open, the last is the sign-off."""
    out, cur = [], []
    for t in turns:
        if t["kind"] == "stage" and MUSIC in t["text"]:
            if cur:
                out.append(cur)
            cur = []
            continue
        cur.append(t)
    if cur:
        out.append(cur)
    return out


def main():
    files = sorted((HERE / "npr_transcripts").glob("*.json"))
    if not files:
        sys.exit("no transcripts. run fetch.py then pull_transcripts.py first.")

    tease_w, preview_w, co_counts, co_q, co_and = [], [], [], [], []
    turn_words = defaultdict(list)          # role -> [words per turn]
    story_turns, story_words, stings = [], [], []
    host_q_share, name_per_story = [], []
    host_pairs = Counter()

    for f in files:
        d = json.loads(f.read_text())
        turns = d["turns"]
        stings.append(sum(1 for t in turns if t["kind"] == "stage" and MUSIC in t["text"]))
        bl = blocks(turns)
        if len(bl) < 3:
            continue

        speech = [t for t in turns if t["kind"] == "speech" and not TAIL.search(t["text"])]
        hosts = [s for s in {t["speaker"] for t in speech if t["role"] == "HOST"}]
        host_pairs[tuple(sorted(hosts))] += 1

        # --- cold open = block 0 (the story-1 tease + show ID) AND block 1 (the rest).
        def heads_in(block):
            out = []
            for t in block:
                if t["kind"] != "speech":
                    continue
                if re.search(r"this is up first|i'?m [A-Z]", t["text"], re.I):
                    continue
                out += [h.strip() for h in SENT.findall(t["text"]) if h.strip()]
            return out

        tease, preview = heads_in(bl[0]), heads_in(bl[1])
        if tease:
            tease_w += [len(words(h)) for h in tease]
        if preview:
            preview_w += [len(words(h)) for h in preview]
        allh = tease + preview
        if allh:
            co_counts.append(len(allh))
            co_q.append(sum(1 for h in allh if h.endswith("?")) / len(allh) * 100)
            # "And" is the last-headline signal, and a headline here is a TURN, not a sentence:
            # a preview turn runs two or three sentences and the "And" sits at the front of the
            # whole turn. Measured per sentence it reads as 0% and means nothing.
            last_turns = [t for t in bl[1] if t["kind"] == "speech"]
            if last_turns:
                co_and.append(last_turns[-1]["text"].lower().lstrip().startswith("and"))

        # --- story blocks: everything between the cold open and the sign-off
        body = [b for b in bl[1:] if not any(TAIL.search(t["text"])
                                             for t in b if t["kind"] == "speech")]
        for b in body:
            sp = [t for t in b if t["kind"] == "speech"]
            if len(sp) < 3:
                continue
            story_turns.append(len(sp))
            story_words.append(sum(len(words(t["text"])) for t in sp))
            # names said out loud inside the story, by anyone
            # Both halves of every name in the block: correspondents get called "Scott" as
            # often as "Horsley", and counting only surnames halves the real number.
            names = set()
            for t in sp:
                for part in (t.get("name") or t["speaker"]).split():
                    if len(part) > 2:
                        names.add(part.title())
            said = sum(1 for t in sp for w in words(t["text"]) if w.title() in names)
            name_per_story.append(said)

        for t in speech:
            role = t["role"] if t["role"] in ("HOST", "BYLINE") else "GUEST/TAPE"
            turn_words[role].append(len(words(t["text"])))

        hq = [t for t in speech if t["role"] == "HOST"]
        sents = [s for t in hq for s in (SENT.findall(t["text"]) or [t["text"]])]
        if sents:
            host_q_share.append(sum(1 for s in sents if s.strip().endswith("?")) / len(sents) * 100)

    def p(v, n):
        return st.quantiles(v, n=100)[n - 1] if len(v) > 2 else (v[0] if v else 0)

    print(f"corpus: {len(files)} weekday episodes\n")
    print("COLD OPEN                                          | HN Radio today")
    print(f"  sentences, whole cold open  median {st.median(co_counts):>4.0f}          "
          f"| one per story")
    print(f"  TEASE words/sentence        median {st.median(tease_w):>4.0f}  p90 {p(tease_w,90):>3.0f} "
          f"| <= 10, hard cap")
    print(f"    at or under 10 words      {sum(1 for w in tease_w if w<=10)/len(tease_w)*100:>7.0f}%"
          f"          | 100% by rule")
    print(f"  PREVIEW words/sentence      median {st.median(preview_w):>4.0f}  p90 "
          f"{p(preview_w,90):>3.0f} | (no equivalent block)")
    print(f"  last preview starts w/ And  {sum(co_and)/len(co_and)*100:>7.0f}%          "
          f"| required")
    print(f"  headlines that ask          {st.mean(co_q):>7.0f}%          "
          f"| 0%, matter-of-fact by rule")

    print("\nWORDS PER TURN")
    for role in ("HOST", "BYLINE", "GUEST/TAPE"):
        v = turn_words.get(role) or [0]
        print(f"  {role:11} n={len(v):5}  median {st.median(v):>5.0f}  mean {st.mean(v):5.1f}  "
              f"p90 {p(v,90):>5.0f}")

    print("\nPER STORY")
    print(f"  turns          median {st.median(story_turns):>5.0f}  (range "
          f"{min(story_turns)}-{max(story_turns)})")
    print(f"  words          median {st.median(story_words):>5.0f}")
    print(f"  names said out loud   median {st.median(name_per_story):>3.0f} per story")
    print(f"  host lines that are questions  {st.median(host_q_share):>4.1f}%")
    print(f"\n  music stings per episode      median {st.median(stings):>4.0f}")
    print("\nHOST PAIRS")
    for pair, n in host_pairs.most_common():
        print(f"  {n:3}x  {' + '.join(pair)}")


if __name__ == "__main__":
    main()
