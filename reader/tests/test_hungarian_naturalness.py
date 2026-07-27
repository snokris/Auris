"""Tests for the naturalness round: import text hygiene, oversized-segment
splitting, paragraph-end pauses, ID3 tags and optional mastering."""

import unittest
from unittest.mock import patch

from core import enrichment, exporter
from core.enrichment import (
    MAX_TTS_SEGMENT_CHARS,
    _normalize_source_text,
    _split_oversized_segment,
    enrich_chapter,
)


class SourceTextNormalizationTest(unittest.TestCase):
    def test_invisible_characters_are_removed(self):
        text = "﻿Első mondat.​ Második mondat.�"
        cleaned = _normalize_source_text(text)
        self.assertNotIn("﻿", cleaned)
        self.assertNotIn("​", cleaned)
        self.assertNotIn("�", cleaned)
        self.assertNotIn(" ", cleaned)
        self.assertIn("Második mondat.", cleaned)

    def test_control_characters_become_spaces(self):
        cleaned = _normalize_source_text("Egy\x00kettő\x1fhárom")
        self.assertNotIn("\x00", cleaned)
        self.assertNotIn("\x1f", cleaned)
        self.assertIn("Egy kettő három", cleaned)

    def test_newlines_are_preserved(self):
        cleaned = _normalize_source_text("Első bekezdés.\r\n\r\nMásodik.")
        self.assertEqual(cleaned, "Első bekezdés.\n\nMásodik.")

    def test_readable_hungarian_text_is_untouched(self):
        text = "Az őszi eső – mondta Kovács – 1932. október 5-én esett."
        self.assertEqual(_normalize_source_text(text), text)

    def test_enrich_chapter_cleans_the_source(self):
        segments = enrich_chapter(
            "\ufeffElső mondat sétált a parkban egy szép napon.\u200b",
            {}, single_narrator_mode=True,
        )
        self.assertTrue(segments)
        self.assertNotIn("\ufeff", segments[0]["text"])
        self.assertNotIn("\u200b", segments[0]["text"])


class OversizedSegmentSplitTest(unittest.TestCase):
    def test_short_text_is_returned_as_is(self):
        self.assertEqual(_split_oversized_segment("Rövid mondat."),
                         ["Rövid mondat."])

    def test_long_text_splits_at_clause_boundaries(self):
        clause = "ez itt egy közepesen hosszú tagmondat"
        text = ", ".join([clause] * 30) + "."
        pieces = _split_oversized_segment(text)
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLessEqual(len(piece), MAX_TTS_SEGMENT_CHARS)
        # Nothing is lost: every character survives the split.
        self.assertEqual(
            "".join(pieces).replace(" ", ""),
            text.replace(" ", ""),
        )

    def test_text_without_clauses_splits_at_words(self):
        text = " ".join(["szó"] * 300)
        pieces = _split_oversized_segment(text)
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLessEqual(len(piece), MAX_TTS_SEGMENT_CHARS)

    def test_monster_word_is_hard_wrapped(self):
        pieces = _split_oversized_segment("x" * 1200)
        self.assertEqual(len(pieces), 3)
        self.assertEqual("".join(pieces), "x" * 1200)

    def test_enriched_segments_never_exceed_the_cap(self):
        long_paragraph = ", ".join(
            ["a hosszú őszi délutánokon a kertben sétálgatott"] * 40) + "."
        for single in (True, False):
            segments = enrich_chapter(long_paragraph, {},
                                      single_narrator_mode=single)
            for seg in segments:
                self.assertLessEqual(len(seg["text"]), MAX_TTS_SEGMENT_CHARS)


class ParagraphPauseTest(unittest.TestCase):
    def test_segments_carry_the_paragraph_flag(self):
        text = (
            "Első mondat az első bekezdésben. Második mondat ugyanott.\n\n"
            "A második bekezdés egyetlen mondata."
        )
        segments = enrich_chapter(text, {}, single_narrator_mode=True)
        self.assertGreaterEqual(len(segments), 2)
        self.assertTrue(segments[-1]["ends_paragraph"])
        flags = [s["ends_paragraph"] for s in segments]
        self.assertGreaterEqual(sum(flags), 2)

    def test_paragraph_pause_beats_sentence_and_dialogue_pause(self):
        opts = exporter.default_audio_options()
        seg = {"text": "A bekezdés utolsó mondata.", "ends_paragraph": True,
               "is_dialogue": True}
        nxt = {"text": "Új bekezdés.", "is_dialogue": True}
        self.assertEqual(exporter.pause_after_segment(seg, nxt, opts),
                         opts["pause_paragraph"])

    def test_ellipsis_still_wins_over_paragraph(self):
        opts = exporter.default_audio_options()
        seg = {"text": "És akkor…", "ends_paragraph": True}
        self.assertEqual(exporter.pause_after_segment(seg, None, opts),
                         opts["pause_ellipsis"])

    def test_paragraph_pause_is_configurable(self):
        opts = exporter.audio_options({"pause_paragraph": 1.4})
        seg = {"text": "Vége.", "ends_paragraph": True}
        self.assertEqual(exporter.pause_after_segment(seg, None, opts), 1.4)

    def test_non_paragraph_segment_keeps_the_sentence_pause(self):
        opts = exporter.default_audio_options()
        seg = {"text": "Sima mondat.", "ends_paragraph": False}
        self.assertEqual(exporter.pause_after_segment(seg, None, opts),
                         opts["pause_segment"])


class Id3TagTest(unittest.TestCase):
    def test_empty_tag_values_are_dropped(self):
        cleaned = exporter._clean_id3_tags({
            "title": "Fejezet", "artist": "", "album": None, "track": "3",
        })
        self.assertEqual(cleaned, {"title": "Fejezet", "track": "3"})

    def test_ffmpeg_encode_receives_metadata_arguments(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd

            class R:
                returncode = 0
                stderr = ""
            return R()

        with patch.object(exporter.subprocess, "run", side_effect=fake_run), \
                patch.object(exporter, "_ffmpeg_available", return_value=True):
            ok = exporter.encode_wav_file(
                "in.wav", "out.mp3",
                exporter.default_audio_options(),
                tags={"title": "Rész 1", "artist": "Dan Brown",
                      "album": "A titkok titka", "track": "1"},
            )
        self.assertTrue(ok)
        cmd = captured["cmd"]
        self.assertIn("-metadata", cmd)
        self.assertIn("title=Rész 1", cmd)
        self.assertIn("artist=Dan Brown", cmd)
        self.assertIn("album=A titkok titka", cmd)
        self.assertIn("track=1", cmd)
        self.assertIn("-id3v2_version", cmd)


class MasteringTest(unittest.TestCase):
    def test_mastering_defaults_to_off(self):
        self.assertFalse(exporter.default_audio_options()["mastering"])

    def test_loudnorm_measurement_parsing(self):
        stderr = (
            "noise\n"
            '{ "input_i" : "-23.5", "input_tp" : "-6.1", "input_lra" : "4.2", '
            '"input_thresh" : "-34.2", "output_i" : "-19.0", '
            '"target_offset" : "0.3" }\n'
        )
        measured = exporter._extract_loudnorm_measurements(stderr)
        self.assertEqual(measured["input_i"], "-23.5")

    def test_invalid_measurements_are_rejected(self):
        with self.assertRaises(ValueError):
            exporter._extract_loudnorm_measurements("no json here")
        with self.assertRaises(ValueError):
            exporter._extract_loudnorm_measurements(
                '{ "input_i" : "-inf", "input_tp" : "-6", "input_lra" : "4", '
                '"input_thresh" : "-34", "target_offset" : "0" }'
            )

    def test_mastering_failure_falls_back_to_raw_render(self):
        import numpy as np
        import os
        import tempfile

        segments = []
        with tempfile.TemporaryDirectory() as tmp:
            seg_wav = os.path.join(tmp, "seg.wav")
            import soundfile as sf
            sf.write(seg_wav, np.zeros(2400), exporter.SAMPLE_RATE)
            segments = [{
                "text": "Egy mondat.", "audio_path": seg_wav,
                "duration_sec": 0.1, "is_dialogue": 0,
            }]
            with patch.object(exporter, "_master_wav",
                              return_value=(False, "boom")):
                rendered = exporter.render_chapter_wav(
                    segments, tmp, "chapter",
                    {**exporter.default_audio_options(), "mastering": True},
                )
            self.assertTrue(os.path.isfile(rendered["wav_path"]))
            self.assertFalse(rendered["mastering_applied"])
            self.assertFalse(
                [f for f in os.listdir(tmp) if "premaster" in f])

    def test_mastering_disabled_writes_plain_wav(self):
        import numpy as np
        import os
        import tempfile
        import soundfile as sf

        with tempfile.TemporaryDirectory() as tmp:
            seg_wav = os.path.join(tmp, "seg.wav")
            sf.write(seg_wav, np.zeros(2400), exporter.SAMPLE_RATE)
            segments = [{
                "text": "Egy mondat.", "audio_path": seg_wav,
                "duration_sec": 0.1, "is_dialogue": 0,
            }]
            with patch.object(exporter, "_master_wav") as master:
                rendered = exporter.render_chapter_wav(
                    segments, tmp, "chapter",
                    exporter.default_audio_options(),
                )
            master.assert_not_called()
            self.assertTrue(os.path.isfile(rendered["wav_path"]))


if __name__ == "__main__":
    unittest.main()
