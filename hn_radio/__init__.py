"""HN Radio: turn the Hacker News front page into a produced daily podcast, using Deepgram Flux TTS.

Pipeline stages (see pipeline.py):
    ingest -> script_assembly -> voices -> render -> stitch -> publish
"""

__version__ = "0.1.0"
