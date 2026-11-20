"""A segment is one sentence of the video: its words with their times, the source text ("src"),
and once translated the dub text ("tgt"; "model_tgt" is the model's own version, before review).

Runs made before 0.9.0 stored those texts under the language names "bg", "en" and "model_en";
upgrade() renames them when a run's files are read, so old runs continue where they stopped.
"""

from __future__ import annotations

OLD_KEYS = {"bg": "src", "en": "tgt", "model_en": "model_tgt"}


def upgrade(seg: dict) -> dict:
    """The same dict with the old key names renamed (in place); new ones are left alone."""
    for old, new in OLD_KEYS.items():
        if old in seg and new not in seg:
            seg[new] = seg.pop(old)
    return seg


def upgrade_all(segs: list[dict]) -> list[dict]:
    return [upgrade(s) for s in segs]
