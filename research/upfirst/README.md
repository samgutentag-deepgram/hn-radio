# Up First style corpus

A measurement pass, not a style transfer. Up First is the closest published show to HN Radio's
shape: a cold open of one-sentence headlines, then three stories covered by two people. Every
number in `hn_radio/writers.py` that governs that shape was picked by ear. This finds out what
the range actually is on a produced show, so those rules can cite a measurement.

## Run it

```bash
python3 fetch.py -n 30 --manifest-only   # weekdays only; Sat and Sun are different shows
python3 pull_transcripts.py              # NPR's own transcripts: named speakers + roles
python3 analyze.py                       # the numbers
```

Every stage is resumable and skips work already on disk. No audio and no API key needed for any
of it.

### Why NPR's transcripts and not STT

NPR publishes a full transcript per episode at `npr.org/transcripts/<story_id>`, and every turn
carries a NAME and a ROLE -- `LEILA FADEL, HOST` vs `SCOTT HORSLEY, BYLINE`. That is the one
distinction this whole exercise turns on: host copy is written and read, correspondent copy is
reported, and HN Radio is entirely the former. Diarization returns "speaker 0" and leaves that
unresolved, so STT would have been strictly worse here as well as slower and metered.

`(SOUNDBITE OF MUSIC)` delimits the blocks, which is the same device HN Radio uses for story
changes, so the structures line up without any inference.

The one thing NPR transcripts do NOT carry is timestamps. Anything measured in seconds still
needs the audio, which is what `fetch.py` (without `--manifest-only`) and `transcribe.py` are
for, on a handful of episodes rather than all of them.

## What this is not

Up First's per-story device is a host handing off to a correspondent who reports. HN Radio
deliberately rejected that: `writers.py` says the co-host "is not a beat reporter being handed a
topic and is never introduced as one." So the handoff mechanics here are the one thing NOT to
copy. What transfers is timing, turn length, headline construction and follow-up density.

Third-party audio, pulled from a public feed for private structural analysis. Nothing from it is
reproduced in the show, and none of it is committed.
