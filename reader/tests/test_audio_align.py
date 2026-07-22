import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.audio_align import (  # noqa: E402
    declick_clips,
    split_by_word_alignment,
)


SR = 24000


class FakeASR:
    """Mimics the HF ASR pipeline word-timestamp output."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    def __call__(self, payload, **kwargs):
        self.calls.append(kwargs)
        return {"chunks": self._chunks}


def word(start, end, text):
    return {"timestamp": (start, end), "text": text}


def make_audio(seconds):
    return np.arange(int(seconds * SR), dtype=np.float32)


class AlignedSplitTests(unittest.TestCase):
    def test_exact_word_count_cuts_in_inter_word_gap(self):
        # Two members: "Jó reggelt." (2 words) + "Ki jár ott?" (3 words).
        asr = FakeASR([
            word(0.10, 0.40, "Jó"),
            word(0.45, 0.90, "reggelt."),
            word(1.50, 1.70, "Ki"),
            word(1.75, 2.00, "jár"),
            word(2.05, 2.40, "ott?"),
        ])
        audio = make_audio(2.6)
        parts = split_by_word_alignment(
            audio, SR, ["Jó reggelt.", "Ki jár ott?"], asr, "hu"
        )
        self.assertIsNotNone(parts)
        self.assertEqual(len(parts), 2)
        # Cut must land in the 0.90–1.50 s pause (midpoint = 1.20 s).
        cut = parts[0].shape[0]
        self.assertGreater(cut, int(0.90 * SR))
        self.assertLess(cut, int(1.50 * SR))
        self.assertEqual(parts[0].shape[0] + parts[1].shape[0], audio.shape[0])

    def test_one_word_member_keeps_its_own_clip(self):
        asr = FakeASR([
            word(0.10, 0.50, "Igen."),
            word(1.20, 1.45, "A"),
            word(1.50, 1.90, "vár"),
            word(1.95, 2.30, "üres."),
        ])
        audio = make_audio(2.5)
        parts = split_by_word_alignment(
            audio, SR, ["Igen.", "A vár üres."], asr, "hu"
        )
        self.assertIsNotNone(parts)
        # The one-word member must get a non-trivial clip that covers its word.
        self.assertGreater(parts[0].shape[0], int(0.5 * SR))

    def test_mismatched_word_count_uses_proportional_mapping(self):
        # ASR merged two words ("jó reggelt" -> "jóreggelt"): 4 expected, 3 found.
        asr = FakeASR([
            word(0.10, 0.80, "jóreggelt."),
            word(1.40, 1.60, "szia"),
            word(1.65, 2.00, "uram."),
        ])
        audio = make_audio(2.2)
        parts = split_by_word_alignment(
            audio, SR, ["Jó reggelt.", "Szia uram."], asr, "hu"
        )
        self.assertIsNotNone(parts)
        self.assertEqual(len(parts), 2)
        cut = parts[0].shape[0]
        self.assertGreater(cut, int(0.80 * SR))
        self.assertLess(cut, int(1.40 * SR))

    def test_single_member_returns_none(self):
        asr = FakeASR([word(0.1, 0.4, "Szia.")])
        self.assertIsNone(
            split_by_word_alignment(make_audio(1.0), SR, ["Szia."], asr, "hu")
        )

    def test_fewer_words_than_members_falls_back(self):
        asr = FakeASR([word(0.1, 0.4, "Szia.")])
        parts = split_by_word_alignment(
            make_audio(1.0), SR, ["Szia.", "Ki az?"], asr, "hu"
        )
        self.assertIsNone(parts)

    def test_asr_exception_falls_back(self):
        class Boom:
            def __call__(self, *a, **k):
                raise RuntimeError("asr down")

        parts = split_by_word_alignment(
            make_audio(1.0), SR, ["Egy.", "Kettő."], Boom(), "hu"
        )
        self.assertIsNone(parts)

    def test_hungarian_language_hint_is_passed(self):
        asr = FakeASR([
            word(0.1, 0.3, "Egy."),
            word(0.9, 1.2, "Kettő."),
        ])
        split_by_word_alignment(make_audio(1.5), SR, ["Egy.", "Kettő."], asr, "hu")
        self.assertEqual(
            asr.calls[0].get("generate_kwargs"), {"language": "hungarian"}
        )
        self.assertEqual(asr.calls[0].get("return_timestamps"), "word")


class DeclickTests(unittest.TestCase):
    def test_seam_edges_are_faded_to_silence(self):
        clips = [np.ones(SR, dtype=np.float32), np.ones(SR, dtype=np.float32)]
        out = declick_clips(clips, SR)
        # End of clip 1 and start of clip 2 must approach zero (no step).
        self.assertLess(abs(out[0][-1]), 1e-3)
        self.assertLess(abs(out[1][0]), 1e-3)
        # Natural outer edges stay untouched.
        self.assertAlmostEqual(float(out[0][0]), 1.0, places=5)
        self.assertAlmostEqual(float(out[1][-1]), 1.0, places=5)

    def test_lengths_and_middle_content_preserved(self):
        clips = [np.ones(1000, dtype=np.float32), np.ones(1000, dtype=np.float32)]
        out = declick_clips(clips, SR)
        self.assertEqual([len(c) for c in out], [1000, 1000])
        self.assertAlmostEqual(float(out[0][500]), 1.0, places=5)

    def test_tiny_clip_is_left_intact(self):
        clips = [np.ones(2000, dtype=np.float32), np.ones(5, dtype=np.float32)]
        out = declick_clips(clips, SR)
        self.assertEqual(len(out[1]), 5)
        # Too short to fade — must not be zeroed out.
        self.assertAlmostEqual(float(out[1][2]), 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
