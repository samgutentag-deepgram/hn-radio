# HN Radio glossary

Shared vocabulary for talking about the show. Written 2026-08-08 because three words were being
used two ways each, and the ambiguity was costing real time in review.

Where a term maps to code, the code name is given. Where a word is genuinely ambiguous, the
ambiguity is called out rather than quietly resolved, because both meanings are in circulation.

Trued up against the code on 2026-08-20, when the show went from three themed voices to two. Some
words below survived that change with a narrower meaning rather than dying, and where they did, the
entry says which surface still uses them. A term that no longer describes anything is marked
retired instead of being deleted, because the archive is full of episodes it does describe.

---

## The three collisions

These are the ones that caused actual confusion. Fixing them is the point of this document.

### "Segment"

| Sense | Meaning | Use instead |
|---|---|---|
| Code | ONE line spoken by ONE voice. `ScriptSegment`. The 2026-08-07 episode has 29. | **line** |
| Podcast | A portion of the show, e.g. "the comment segment". | **section** |

The class stays `ScriptSegment`. In conversation, say **line** for the object and **section**
for a part of the show. A sentence like "the segment is too long" is unanswerable without this.

### "Beat"

| Sense | Meaning | Use instead |
|---|---|---|
| Editorial | What a seat covers. `Desk.beat` in code: "Second chair. Takes every story with the host." | **beat** (keep) |
| Timing | A pause, as in "a story change gets a real beat." | **pause** or **gap** |

Beat keeps the editorial meaning because the code owns that word. It used to name a desk's SUBJECT
area, and it no longer does: the daily show's two seats are not themed, so a beat is now one line on
what a seat does. The subject-area sense moved to **topic** (see Production). For timing, say
**gap**, which is also what the code calls it.

### "Sting" vs "stinger"

The same thing. Broadcast uses both. The code says `STING_SECONDS`, so the house word is
**sting**.

---

## Music

| Term | Meaning |
|---|---|
| **Cue** | Any placed piece of music. The umbrella term: the intro cue, a sting, the outro cue. |
| **Sting** | A short cue punctuating a transition. `STING_SECONDS = 2.0`, one per story at its first mention. |
| **Bed** | Music running UNDER speech at low level. Ours covers the fixed intro and the cold open, the first two lines, 26 dB below the voice. |
| **Interstitial** | Informal, for cues between spoken parts. Same family as sting. |
| **Cut point** | Where a cue is lifted from in the source track. Chosen from the track's own decays, not arbitrarily; the busy regions are unusable because a cue cut from them starts and ends mid-phrase. |

Levels are set **relative to the episode's own speech**, never as a fixed multiplier. Two tracks
are never the same loudness, so a gain chosen against one means nothing against another.
`STING_DB = -6.0`, `BED_DB = -26.0`.

---

## Show structure

In broadcast order:

| Term | Meaning |
|---|---|
| **Intro** | The show's FIXED signature line. "From Deepgram, this is Hacker News Radio..." Written once in `pipeline._INTRO`, added by the pipeline, never by the LLM. |
| **Cold open** | The host's rapid preview of every story, AFTER the intro, before any story is covered. One sentence per story, ten words maximum each, the last beginning with "And". Borrowed from the NBR model. ONE segment and one TTS call; see Cold-open pause. |
| **Take** | A substantive contribution on one story, from either regular. |
| **Follow-up** | The other regular's question after a take. One per story, and it must be a real question about the substance. |
| **Callback** | A later line referring to an earlier story in the same episode. Permitted, at most one, never required. |
| **Comment theater** | Real Hacker News comments performed on air. No longer a section: since 2026-08-20 the comments play INSIDE the coverage of the thread they came from, read by the two regulars, who name the commenter before each quote. The old end-of-show block, and the separate guest voice that read it, survive only in the archive and in a custom edition. |
| **Outro** | The show's FIXED sign-off, like the intro. |

**Intro and cold open are different things.** This is the pair most likely to be conflated: the
intro is the same every day, the cold open is that day's headlines.

---

## Casting

This trio is load-bearing. Conflating character with voice produced a real bug: a 403 on one
voice id was generalised into "Alexis does not exist on production", which was false.

| Term | Meaning | Example |
|---|---|---|
| **Role** | A stable slot in the show. | `anchor` (the host) and `cohost`; `ai`, `maker`, `security`, `drama` in the archive and in custom editions |
| **Character** | A person with a name the audience hears. | Alexis, Cole, Jack |
| **Voice** | A Flux model id. | `flux-alexis-en` |

Character and voice are one-to-one in the show as it ships, but the code does not assume it. A
name is resolved from the voice id at cast time, so a voice leaving the catalog changes who the
listener hears and the show says so rather than silently sounding different.

| Term | Meaning |
|---|---|
| **Host** | The permanent first chair. Alexis every episode, `flux-alexis-en`. Role key `anchor`, which is not a rename waiting to happen: it is written into every `script.json` on disk and read by `custom.py`, `recast.py` and the site's segment colors. |
| **Co-host** | The second chair, re-cast every episode from the whole Flux catalog. Role key `cohost`. |
| **Desk** | A seat plus its beat and persona. In the daily show there are two and neither is themed. A themed desk, with routing keywords attached, now exists only in a custom edition and in the archive. |
| **Correspondent** | RETIRED. Named the one themed desk covering stories in an episode, chosen by how well the day's news matched its keywords. Nothing routes a story to a speaker any more. Say **co-host**. |
| **Regular** | A character appearing in every episode. There are two: the host and the co-host, and they cover the stories and read the comments between them. |
| **Guest voice** | A voice used to perform a quoted commenter, distinct from the regulars. Off the daily show path since 2026-08-20; still what a recast's Guest host preset and a custom edition's comment theater put on quoted lines. |
| **Seating** | Who fills each role right now, given the active catalog. |
| **Substitution** | When a preferred character is unavailable and another covers, announced in-fiction ("Alexis is out today"). Reaches the host in practice, since the co-host's candidates are built FROM the live catalog and so its first choice is always available. |
| **Recency window** | How many recent episodes block a voice from taking the second chair again. `cast.COHOST_RECENCY_WINDOW = 14`, read off the episodes directory rather than a state file. A voice inside the window goes to the back of the list rather than being dropped, so a catalog smaller than the window still casts a show. |

---

## Timing and pacing

| Term | Meaning |
|---|---|
| **Boundary** | The join between two consecutive lines. Gaps and stings both attach here, indexed by position. N lines have N-1 boundaries. |
| **Gap** | Silence inserted at a boundary. |
| **Boundary kind** | How a boundary is classified, which decides its gap. |
| **Pacing policy** | A named set of gaps, one per boundary kind. Ours is `CONVERSATIONAL`. |
| **Edge normalization** | Trimming the silence Flux bakes into each rendered line, then padding back a known amount, so the inserted gap IS the pause a listener hears. |
| **Cold-open pause** | The silence between two headlines *inside* the cold open. Not a gap and not a boundary: the cold open is one segment and one TTS call, so `stitch` never inserts anything there and only Flux's own silence is available to change. Set to one fixed, even 0.55s by `pacing.set_internal_pauses`. |

The six boundary kinds and their current gaps:

| Kind | Gap | When |
|---|---|---|
| `exchange` | 0.16s | Two different speakers, same story |
| `same_speaker` | 0.22s | One speaker continuing |
| `into_comment` | 0.40s | A regular hands into a performed comment |
| `out_of_comment` | 0.30s | A performed comment lands |
| `story_change` | 0.85s | The show moves to a new story |
| `show_boundary` | 0.90s | After the fixed intro, before the fixed outro |

**These are the knobs for "the cadence feels off."** They live in `hn_radio/pacing.py`, and
`scripts/pace_experiment.py` can A/B any change against a past episode from cached audio at zero
API cost.

---

## Production

| Term | Meaning |
|---|---|
| **Edition** | A story-selection filter. Four: `makers` (the default), `ai`, `security`, `frontpage`. |
| **Topic** | A subject a story can be about, with the keywords that say so. `editions.TOPICS`: `ai`, `maker`, `security`. Names a subject, never a character; nothing in the audio corresponds to one. This is the word the editorial sense of "beat" used to carry. |
| **Recast** | Re-rendering an existing episode with different voices, reusing the cached script. Two roles, **Showrunner** (the `anchor` slot, plus any quote pinned to that voice) and **Guest host** (everything else, including the archive's themed desks), and one voice cannot take both. |
| **Custom / build-your-own edition** | A listener-configured episode: their desks, their voices. The one surface where a themed desk is still a real seat, because picking one both casts a voice and filters the topic. |
| **Chapter** | A podcast chapter marker. One per story, at its first mention. |
| **The cache** | Per-line raw PCM under `episodes/<id>/segments/`, staged during a render and deleted once the MP3 exists. The content-addressed cache `scripts/local_episode.py` keeps under `.render-cache/` is the one that persists. |
| **Dry** | Audio without pacing or music applied. What the raw cache sounds like. |

---

## Two words to avoid

**"Segment"** on its own. Say line or section.

**"Slow."** It has meant three different things in this project: the music cues lingering, the
gaps between lines being long, and the speech itself being unhurried. Only the first two are
tunable here; the third is a property of the Flux voice. Name which one.
