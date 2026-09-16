# Up First style corpus

A measurement pass, not a style transfer. Up First is the closest published show to HN Radio's
shape: a cold open of one-sentence headlines, then three stories covered by two people. Every
number in `hn_radio/writers.py` that governs that shape was picked by ear. This finds out what
the range actually is on a produced show, so those rules can cite a measurement.

## Run it

```bash
python3 fetch.py -n 30          # briefing episodes only; Sunday is a different show
export DEEPGRAM_API_KEY=...
python3 transcribe.py           # nova-3, diarize + utterances + filler_words
python3 segment.py --report     # cut sponsor reads and credits; eyeball the cuts
python3 analyze.py              # the numbers
```

Every stage is resumable and skips work already on disk.

## What this is not

Up First's per-story device is a host handing off to a correspondent who reports. HN Radio
deliberately rejected that: `writers.py` says the co-host "is not a beat reporter being handed a
topic and is never introduced as one." So the handoff mechanics here are the one thing NOT to
copy. What transfers is timing, turn length, headline construction and follow-up density.

Third-party audio, pulled from a public feed for private structural analysis. Nothing from it is
reproduced in the show, and none of it is committed.
