"""Rewrite the archive's WORDS to match the two-person cast the archive recast gave it.

THE PROBLEM THIS EXISTS FOR. The recast changed every archive voice and deliberately changed no
words, on an instruction that turned out to be wrong. So all 19 episodes still say names and places
that are not in them: a host thanks Marcus, introduces Priya, then the same co-host voice answers as
both. Counted off disk: 211 name mentions across 191 lines, and 60 sentences naming a
desk that no longer exists.

`recast.rewrite_role_names` does the name half and only the name half, in memory, during a recast.
It cannot help here, because the recast already happened: the mapping and the `absorbed` bookkeeping
it keys off are gone. What survives is better anyway. `.recast-backup/` holds the pristine
pre-recast script for all 19, so the old-to-new voice mapping is recoverable per episode by joining
the two segment lists ON TEXT. Verified for all 19: no old voice maps to two new ones.

Not by position. 2026-08-18 and 2026-08-19 are two segments shorter on disk than in their backups,
because the cold open was merged from separate headline reads into one read with internal pauses
after those backups were taken. A positional zip lines the wrong segments up for everything after
the merge, silently. That was the first version of this script and the length guard caught it.

WHY IT IS NOT JUST A RENAME. Every old correspondent collapses onto the ONE new co-host, so a naive
substitution turns "Thank you, Marcus. Priya, at the AI desk:" into the same name twice in a breath.
19 lines do that. Only 2 become a true self-reference, which is why the desk clauses matter more
than the names: strip the clause and the second naming usually goes with it.

HOW THE REWRITE WORKS, and the design constraint is auditability rather than cleverness. An ordered
table of (pattern, replacement, why) runs against the ORIGINAL text, where the old names still make
the syntax legible, and every substitution is recorded with the rule that made it. Then the names
are renamed. Then anything still naming one person twice is flagged rather than guessed at. No
model, no Anthropic call, no writer: this is string surgery whose every edit a human can check.

Most specific rule first, because the general clause-strippers would otherwise eat the connective
that a specific rule needs to keep the sentence grammatical. "Priya, you're at the AI desk, what's
the trick here?" is the worked example: strip only "at the AI desk" and you get "Priya, you're ,
what's the trick here?".

    uv run python scripts/fix_archive_words.py                 # report, writes nothing
    uv run python scripts/fix_archive_words.py --review        # + an HTML page to read
    uv run python scripts/fix_archive_words.py --apply         # rewrite script.json in place
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import config  # noqa: E402

BACKUP = config.PROJECT_ROOT / ".recast-backup"
REVIEW = config.PROJECT_ROOT / "docs" / "archive-word-fix.html"

# Every word that precedes "desk" in the archive, counted rather than assumed:
#   maker 19, drama 16, ai 14, security 11, systems 1, plus "your desk" x2 and "the desk" x1.
# `systems` was missing from the first version of this list and survived the whole pass, which is
# why the list is now derived from a count over episodes/*/script.json instead of from memory.
# `main` is kept although the archive never says it: cast.py's older seat names included it.
DESK = r"(?:AI|ai|maker|security|drama|systems|main)"

# Ordered. Each entry is (pattern, replacement, why). The `why` is printed beside every edit, so a
# reviewer reads the intent rather than reverse-engineering the regex.
RULES = [
    # --- specifics that must consume their own connective ------------------------------------
    (rf",\s*you're at the {DESK} desk with the hands-on view\.", ", you have the hands-on view.",
     "keeps the point of the line (hands-on) and drops the furniture"),
    (rf",\s*you're at the {DESK} desk,", ",",
     "'you're at the X desk, <question>' leaves a dangling 'you're' if only the clause goes"),
    (rf",\s*you have the {DESK} desk\.", ", this one's yours.",
     "the whole sentence was a handoff; say the handoff"),
    (r",\s*you have the desk on this one\.", ", this one's yours.",
     "'you have the desk' is show furniture with no desk named; same handoff"),
    (rf",\s*anything to add from the {DESK} desk\b", ", anything to add",
     "the question survives without the desk"),
    (rf",\s*take it at the {DESK} desk\.", ", take it.",
     "'take it' is already the handoff"),
    (rf",\s*over to you at the {DESK} desk\b", ", over to you",
     "'over to you' is the handoff; the desk was the redundant half"),
    (rf",\s*back to you at the {DESK} desk\.", ", back to you.",
     "same, for the return handoff"),
    (rf"\s*at the {DESK} desk(?=[.,])", "",
     "trailing 'at the X desk' after a complete handoff clause"),
    # --- the general clause strippers --------------------------------------------------------
    (rf",\s*at the {DESK} desk\b", "",
     "appositive: 'NAME, at the X desk: ...' -> 'NAME: ...'"),
    (rf",\s*{DESK} desk\b", "",
     "bare appositive: 'NAME, maker desk, ...' -> 'NAME, ...'"),
    # --- sentences that name a desk with no correspondent attached ---------------------------
    (rf"\bWhich brings us to the drama desk\b", "Which brings us to the comments",
     "no name to keep; the comments are what the drama desk was"),
    (rf"\bbefore the drama desk\b", "before the comments",
     "same, mid-sentence"),
    (rf"\bso (\w+) is at the drama desk\.", r"so \1 has the thread.",
     "keeps the name and says what they actually do now"),
    (rf"\bthe {DESK} desk\b", "the comments",
     "catch-all for a surviving bare mention; flagged for review either way"),
]

# SECOND PASS, and this is the half a rename cannot do.
#
# Every old correspondent collapses onto ONE co-host, so a line where the host thanked Marcus and
# then handed off to Priya becomes a line that says the same name twice in a breath: "Thank you,
# Tanner. Tanner: four hundred and sixty-two points...". 19 lines do it. In a two-person show you do
# not re-name the person you just thanked, so the fix is to drop the SECOND naming and repair the
# grammar around the hole it leaves.
#
# `{n}` is the duplicated name, injected per line. Ordered, most specific first, same as RULES.
# Applied only to a segment that still names one person twice after the rename, so a legitimate
# repetition in a single-name line is never touched.
DEDUPE_RULES = [
    (r"(?P<keep>{n}), {n}'s got", r"\g<keep>, you've got",
     "'Hold that thought, X, X's got the thread later' -> 'you've got'"),
    (r"\. {n}, this is your desk\.", ". This one's yours.",
     "'your desk' survives the desk strip because it is possessive, not 'the X desk'"),
    (r"\. {n}\.$", ". Over to you.",
     "a bare trailing name was the handoff; say the handoff instead of repeating the name"),
    (r"\. {n}: (?P<w>\w)", lambda m: ". " + m.group("w").upper(),
     "'Thank you, X. X: <story>' -> drop the colon introduction, capitalize the sentence"),
    (r"\. {n}, (?P<w>\w)", lambda m: ". " + m.group("w").upper(),
     "'Thanks, X. X, <clause>' -> drop the re-address and capitalize the clause"),
    (r", so {n}, ", ", so ",
     "'so X, take it' -> 'so take it'; the sentence already knows who"),
    (r", {n}, (?P<w>\w)", lambda m: ", " + m.group("w"),
     "mid-sentence re-address, lower case because the clause continues"),
]

# The names the archive can say, including voices since retired by ear: an archived script still
# names whoever read it.
DISPLAY = {
    "flux-haley-en": "Haley", "flux-alexis-en": "Alexis",
    "flux-marcus-en": "Marcus", "flux-priya-en": "Priya", "flux-renee-en": "Renee",
    "flux-jack-en": "Jack", "flux-cole-en": "Cole", "flux-drew-en": "Drew",
    "flux-jun-en": "Jun",
}


def display_name(voice_id: str) -> str:
    if voice_id in DISPLAY:
        return DISPLAY[voice_id]
    return voice_id.replace("flux-", "").replace("-en", "").capitalize()


def voice_map(episode: str) -> dict:
    """old voice id -> new voice id, recovered by matching segments ON TEXT.

    NOT by position, and the first version of this was and it broke on two episodes. 2026-08-18 and
    2026-08-19 have 28 and 29 segments in the backup against 26 and 27 on disk, because the cold
    open was merged from separate headline reads into one read with internal pauses AFTER those
    backups were taken. So the two files are legitimately different lengths and a positional zip
    lines the wrong segments up, silently, for every segment after the merge.

    Text is the reliable join because the recast changed voices and not words, which is the whole
    reason this script has to exist. Segments whose text appears more than once in either file are
    skipped rather than guessed at, and a voice that maps to two different new voices raises: both
    mean the backup is not the input to what is on disk, and a rename built on a wrong mapping is
    worse than no rename at all.
    """
    old = json.loads((BACKUP / episode / "script.json").read_text())
    new = json.loads((config.EPISODES_DIR / episode / "script.json").read_text())

    def unique_by_text(segs):
        counts = Counter(s["text"] for s in segs)
        return {s["text"]: s for s in segs if counts[s["text"]] == 1}

    old_by_text, new_by_text = unique_by_text(old), unique_by_text(new)
    shared = set(old_by_text) & set(new_by_text)
    if not shared:
        raise ValueError(f"{episode}: no segment text in common with the backup")

    mapping: dict = {}
    for text in shared:
        a, b = old_by_text[text]["voice_id"], new_by_text[text]["voice_id"]
        prev = mapping.setdefault(a, b)
        if prev != b:
            raise ValueError(f"{episode}: {a} maps to both {prev} and {b}")
    return mapping


def rewrite(text: str, renames: dict) -> tuple:
    """Return (new_text, [(rule_index, why, before, after), ...]).

    Desk clauses go FIRST, while the old names still make the syntax readable, then the names.
    """
    fired = []
    out = text
    for i, (pat, repl, why) in enumerate(RULES):
        while True:
            m = re.search(pat, out)
            if not m:
                break
            nxt = out[:m.start()] + re.sub(pat, repl, m.group(0), count=1) + out[m.end():]
            if nxt == out:
                break
            fired.append((i, why, m.group(0).strip(), None))
            out = nxt
    # Names next, and only whole words. Recorded in `fired` like any other edit, so a line whose
    # ONLY change was a rename still explains itself on the review page instead of showing a blank.
    if renames:
        pattern = r"\b(" + "|".join(sorted(map(re.escape, renames), key=len, reverse=True)) + r")\b"
        seen = set()

        def _swap(m):
            seen.add(m.group(1))
            return renames[m.group(1)]

        out = re.sub(pattern, _swap, out)
        for old_name in sorted(seen):
            fired.append(("rename", f"{old_name} is not in this episode; the voice reading those "
                                    f"lines is {renames[old_name]}", old_name, None))
    out = tidy(out)

    # Then the second pass, on whatever now names one person twice. Only that name is passed in, so
    # a line repeating somebody else's name is left alone.
    for name in double_named(out, set(renames.values())):
        # Drop occurrences until exactly ONE naming survives, not once per rule. Letting all seven
        # fire independently stripped BOTH names out of 2026-08-05 seg7, which has three, so the
        # line stopped addressing anyone at all. The name is the thing being preserved here; only
        # the repetition is the bug.
        guard = 0
        while len(re.findall(rf"\b{re.escape(name)}\b", out)) > 1 and guard < 8:
            guard += 1
            for j, (pat, repl, why) in enumerate(DEDUPE_RULES):
                before = out
                out = re.sub(pat.format(n=re.escape(name)), repl, out, count=1)
                if out != before:
                    fired.append((f"dedupe{j}", why, name, None))
                    break
            else:
                break  # nothing matched; leave it for the flag rather than mangling it
    out = tidy(out)
    # A line can change on spacing alone: 2026-08-13 seg8 had a stray " ," in the ORIGINAL, nothing
    # to do with the recast. Labelled rather than left blank, so nothing on the review page is
    # unexplained -- an unexplained edit is the one a reviewer cannot check.
    if out != text and not fired:
        fired.append(("tidy", "spacing and punctuation only; no word changed", "", None))
    return out, fired


def tidy(text: str) -> str:
    """Close the gaps a removed clause leaves. Never changes a word, only spacing and punctuation."""
    out = re.sub(r"\s{2,}", " ", text)
    out = out.replace(" ,", ",").replace(" .", ".").replace(" :", ":").replace(" ?", "?")
    out = re.sub(r",\s*,", ",", out)
    return out.strip()


def double_named(text: str, names: set) -> list:
    """Names this sentence says more than once. The 'thanks you, then introduces you' shape."""
    if not names:
        return []
    hits = re.findall(r"\b(" + "|".join(sorted(map(re.escape, names))) + r")\b", text)
    return [n for n, c in Counter(hits).items() if c > 1]


def recast_commenters(segs: list) -> list:
    """Give every quoted comment the voice that did NOT set it up. Returns the changes.

    THE PROBLEM THIS SOLVES IS AUDIBLE AND THE WORD FIX CANNOT TOUCH IT. After the two-person
    recast every old correspondent collapsed onto one co-host, and the archive's comment block
    alternates `desk` (the co-host introducing a quote) with `commenter` (the quote itself). Both
    ended up the same voice, so the co-host introduces a commenter and then performs that commenter
    with no vocal change:

        Gemma  desk       And then kelnos does the thing where you read the same essay twice...
        Gemma  commenter  I don't really understand the author's conclusions about cost...

    A listener cannot hear where the quote starts. Measured before this fix: 17 of 19 episodes end
    on a 5-to-6 segment run in a single voice, mean longest run 4.7.

    The rule is "not whoever just spoke", not "always the host", because that is what alternating
    means and it survives an episode where the host sets a quote up themselves. 36 commenter
    segments across the archive, so this costs 36 extra Flux calls and nothing else.

    This is the shape the live show already uses: comments are read by the two regulars,
    alternating. It makes the archive match the format rather than inventing one for it.
    """
    voices = [s["voice_id"] for s in segs]
    regulars = list(dict.fromkeys(voices))
    changes = []
    for i, seg in enumerate(segs):
        if seg.get("role") != "commenter":
            continue
        prev = next((voices[j] for j in range(i - 1, -1, -1)
                     if segs[j].get("role") != "commenter"), None)
        other = next((v for v in regulars if v != prev), None)
        if other and other != seg["voice_id"]:
            changes.append({"index": i, "from": seg["voice_id"], "to": other})
    return changes


def process(episode: str) -> dict:
    vmap = voice_map(episode)
    renames = {}
    for old, new in vmap.items():
        a, b = display_name(old), display_name(new)
        if a != b:
            renames[a] = b
    segs = json.loads((config.EPISODES_DIR / episode / "script.json").read_text())
    new_names = {display_name(v) for v in vmap.values()}

    changes = []
    for i, seg in enumerate(segs):
        before = seg["text"]
        after, fired = rewrite(before, renames)
        if after == before:
            continue
        changes.append({
            "index": i,
            "speaker": display_name(seg["voice_id"]),
            "before": before,
            "after": after,
            "rules": [(str(f), w) for f, w, _, _ in fired],
            "still_double": double_named(after, new_names),
        })
    return {"episode": episode, "renames": renames, "segments": segs, "changes": changes,
            "commenters": recast_commenters(segs)}


def review_page(results: list) -> str:
    tpl = pathlib.Path.home() / "Developer/gutils/templates/print-ready-html-doc.html"
    total = sum(len(r["changes"]) for r in results)
    dbl = [(r["episode"], c) for r in results for c in r["changes"] if c["still_double"]]
    rows = []
    for r in results:
        if not r["changes"]:
            continue
        ren = ", ".join(f"{a} &rarr; {b}" for a, b in sorted(r["renames"].items()))
        rows.append(f'<h2>{r["episode"]}</h2>')
        rows.append(f'<p class="ren">Renames: {ren or "none"}</p>')
        for c in r["changes"]:
            flag = (' <span class="flag">STILL NAMES '
                    + ", ".join(c["still_double"]).upper() + ' TWICE</span>') if c["still_double"] else ""
            why = "; ".join(sorted({w for _, w in c["rules"]}))
            rows.append(
                f'<div class="chg"><p class="meta">segment {c["index"]} &middot; '
                f'{html.escape(c["speaker"])}{flag}</p>'
                f'<p class="before">{html.escape(c["before"])}</p>'
                f'<p class="after">{html.escape(c["after"])}</p>'
                f'<p class="why">{html.escape(why)}</p></div>')
    body = f"""
<h1>Archive word fix</h1>
<p class="lede">The two-person recast changed every voice and no words. This is the proposed
rewrite: {total} lines across {len([r for r in results if r['changes']])} episodes. Every edit is a
deterministic rule, listed under the line that it changed. Nothing here is written by a model.</p>
<p class="lede"><strong>{len(dbl)} lines still name one person twice</strong> after the rewrite.
Those are the ones that need an ear rather than a rule; they are flagged in red below.</p>
{''.join(rows)}
"""
    if tpl.exists():
        # The canonical template's markers are `<!-- ===== CONTENT START ===== -->`, so match the
        # inner words and walk out to the comment delimiters rather than assuming the exact bar
        # count. Only the title and the content region are touched: the style block, the theme
        # toggle and its script are copied verbatim, which is the rule for every doc in this repo.
        page = tpl.read_text()
        page = re.sub(r"<title>[^<]*</title>", "<title>Archive word fix</title>", page, count=1)
        m_a = re.search(r"<!--[^>]*CONTENT START[^>]*-->", page)
        m_b = re.search(r"<!--[^>]*CONTENT END[^>]*-->", page)
        if m_a and m_b:
            return page[:m_a.end()] + body + page[m_b.start():]
    # No template on this machine: a plain readable page rather than nothing.
    return ("<!doctype html><meta charset=utf-8><title>Archive word fix</title>"
            "<style>body{font-family:Georgia,serif;max-width:52rem;margin:2rem auto;padding:0 1rem}"
            ".chg{margin:1.1rem 0;padding-left:.8rem;border-left:3px solid #ccc}"
            ".before{color:#777;text-decoration:line-through} .after{font-weight:600}"
            ".why{font:.8rem/1.4 ui-monospace,monospace;color:#666}"
            ".meta{font:.72rem/1 ui-sans-serif,sans-serif;letter-spacing:.08em;"
            "text-transform:uppercase;color:#888}"
            ".flag{color:#b42318;font-weight:700}.ren{font-size:.9rem;color:#555}"
            "h2{border-bottom:1px solid #ddd;padding-bottom:.2rem;margin-top:2rem}</style>"
            + body)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fix_archive_words")
    ap.add_argument("--apply", action="store_true", help="rewrite episodes/*/script.json in place")
    ap.add_argument("--review", action="store_true", help="write the HTML review page")
    ap.add_argument("--episode", default="", help="one episode id, for a spot check")
    args = ap.parse_args(argv)

    ids = [args.episode] if args.episode else sorted(
        p.name for p in BACKUP.iterdir() if p.is_dir())
    results = [process(e) for e in ids]

    total = sum(len(r["changes"]) for r in results)
    dbl = [(r["episode"], c["index"], c["after"]) for r in results
           for c in r["changes"] if c["still_double"]]
    for r in results:
        if r["changes"]:
            print(f"{r['episode']}: {len(r['changes']):2d} lines rewritten, "
                  f"renames {r['renames'] or '{}'}")
    comm = sum(len(r["commenters"]) for r in results)
    print(f"\n{total} lines rewritten across {len(ids)} episodes.")
    print(f"{comm} quoted comments reassigned to the voice that did not set them up.")
    print(f"{len(dbl)} still name one person twice and need an ear:")
    for ep, i, text in dbl:
        print(f"  [{ep} seg{i}] {text[:110]}")

    if args.review:
        REVIEW.parent.mkdir(parents=True, exist_ok=True)
        REVIEW.write_text(review_page(results))
        print(f"\nreview page: {REVIEW}")

    if args.apply:
        for r in results:
            if not (r["changes"] or r["commenters"]):
                continue
            segs = r["segments"]
            for c in r["changes"]:
                segs[c["index"]]["text"] = c["after"]
            for c in r["commenters"]:
                segs[c["index"]]["voice_id"] = c["to"]
            path = config.EPISODES_DIR / r["episode"] / "script.json"
            path.write_text(json.dumps(segs, indent=2) + "\n")
            print(f"  wrote {path}  ({len(r['changes'])} lines, "
                  f"{len(r['commenters'])} commenter voices)")
        print("\nApplied. The pristine pre-recast text is still in .recast-backup/.")
    else:
        print("\nNothing written. --review for a page to read, --apply to commit the rewrite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
