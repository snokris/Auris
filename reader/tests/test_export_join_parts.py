"""Joining chapters into 1-4 whole files instead of one file per chapter."""

import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from core import exporter


def _write_wav(path: str, seconds: float, value: float = 0.5) -> None:
    samples = int(exporter.SAMPLE_RATE * seconds)
    sf.write(path, np.full(samples, value, dtype=np.float32), exporter.SAMPLE_RATE)


class SplitChaptersTests(unittest.TestCase):
    def test_no_chapters_yields_no_parts(self):
        self.assertEqual(exporter.split_chapters_into_parts([], 3), [])

    def test_one_part_keeps_every_chapter_together(self):
        self.assertEqual(
            exporter.split_chapters_into_parts([1.0, 2.0, 3.0], 1),
            [[0, 1, 2]],
        )

    def test_parts_are_contiguous_and_in_reading_order(self):
        groups = exporter.split_chapters_into_parts([5, 1, 1, 5, 1, 1], 3)
        flat = [i for group in groups for i in group]
        self.assertEqual(flat, list(range(6)))
        for group in groups:
            self.assertEqual(group, list(range(group[0], group[-1] + 1)))

    def test_equal_chapters_split_evenly(self):
        groups = exporter.split_chapters_into_parts([10.0] * 8, 4)
        self.assertEqual([len(g) for g in groups], [2, 2, 2, 2])

    def test_uneven_chapters_minimise_the_longest_part(self):
        durations = [7.0, 2.0, 2.0, 2.0]
        groups = exporter.split_chapters_into_parts(durations, 2)
        longest = max(sum(durations[i] for i in g) for g in groups)
        self.assertEqual(groups, [[0], [1, 2, 3]])
        self.assertEqual(longest, 7.0)

    def test_more_parts_than_chapters_is_clamped(self):
        groups = exporter.split_chapters_into_parts([1.0, 1.0], 4)
        self.assertEqual(len(groups), 2)

    def test_part_count_is_capped_at_the_maximum(self):
        groups = exporter.split_chapters_into_parts([1.0] * 12, 99)
        self.assertEqual(len(groups), exporter.MAX_PART_COUNT)

    def test_unusable_part_count_falls_back_to_one_file(self):
        self.assertEqual(len(exporter.split_chapters_into_parts([1.0, 2.0], 0)), 1)


class PartFileStemTests(unittest.TestCase):
    def test_single_part_keeps_the_plain_book_title(self):
        self.assertEqual(exporter.part_file_stem('A titkok titka', 1, 1),
                         exporter._safe_name('A titkok titka'))

    def test_multiple_parts_are_numbered(self):
        self.assertEqual(exporter.part_file_stem('Book', 2, 3), 'Book_part2')

    def test_unsafe_characters_are_stripped(self):
        stem = exporter.part_file_stem('A/B: C?', 1, 2)
        for bad in '/:?':
            self.assertNotIn(bad, stem)


class ConcatWavsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_offsets_and_length_match_the_written_samples(self):
        paths = []
        for i, seconds in enumerate((1.0, 0.5, 2.0)):
            p = os.path.join(self.dir, f'ch{i}.wav')
            _write_wav(p, seconds)
            paths.append(p)
        out = os.path.join(self.dir, 'joined.wav')

        offsets = exporter.concat_wavs(paths, out, gap_sec=0.25)

        self.assertEqual(len(offsets), 3)
        self.assertAlmostEqual(offsets[0], 0.0, places=6)
        self.assertAlmostEqual(offsets[1], 1.25, places=3)
        self.assertAlmostEqual(offsets[2], 2.0, places=3)

        data, rate = sf.read(out)
        self.assertEqual(rate, exporter.SAMPLE_RATE)
        # 3.5 s of audio + 2 gaps of 0.25 s
        self.assertAlmostEqual(len(data) / rate, 4.0, places=3)

    def test_gap_is_actually_silent(self):
        paths = []
        for i in range(2):
            p = os.path.join(self.dir, f'ch{i}.wav')
            _write_wav(p, 0.2, value=0.8)
            paths.append(p)
        out = os.path.join(self.dir, 'joined.wav')
        exporter.concat_wavs(paths, out, gap_sec=0.5)

        data, _ = sf.read(out)
        gap_start = int(0.2 * exporter.SAMPLE_RATE) + 10
        gap_end = int(0.7 * exporter.SAMPLE_RATE) - 10
        self.assertLess(float(np.abs(data[gap_start:gap_end]).max()), 1e-4)

    def test_zero_gap_joins_seamlessly(self):
        paths = []
        for i in range(2):
            p = os.path.join(self.dir, f'ch{i}.wav')
            _write_wav(p, 0.5)
            paths.append(p)
        out = os.path.join(self.dir, 'joined.wav')
        offsets = exporter.concat_wavs(paths, out, gap_sec=0.0)
        self.assertAlmostEqual(offsets[1], 0.5, places=3)

    def test_single_input_gets_no_leading_gap(self):
        p = os.path.join(self.dir, 'only.wav')
        _write_wav(p, 0.4)
        out = os.path.join(self.dir, 'joined.wav')
        offsets = exporter.concat_wavs([p], out, gap_sec=2.0)
        self.assertEqual(offsets, [0.0])
        data, _ = sf.read(out)
        self.assertAlmostEqual(len(data) / exporter.SAMPLE_RATE, 0.4, places=3)


class ShiftTimelineTests(unittest.TestCase):
    def test_every_timing_moves_by_the_offset(self):
        timeline = [
            {'text': 'a', 't_start': 0.0, 't_end': 1.0},
            {'text': 'b', 't_start': 1.35, 't_end': 2.0},
        ]
        shifted = exporter.shift_timeline(timeline, 10.0)
        self.assertEqual([s['t_start'] for s in shifted], [10.0, 11.35])
        self.assertEqual([s['t_end'] for s in shifted], [11.0, 12.0])

    def test_other_fields_survive_and_the_input_is_untouched(self):
        timeline = [{'text': 'a', 'character_name': 'Anna',
                     't_start': 0.0, 't_end': 1.0}]
        shifted = exporter.shift_timeline(timeline, 5.0)
        self.assertEqual(shifted[0]['character_name'], 'Anna')
        self.assertEqual(timeline[0]['t_start'], 0.0)


class ExportJoinedPartTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _chapter(self, name: str, seconds: float) -> dict:
        wav = os.path.join(self.dir, f'{name}.wav')
        _write_wav(wav, seconds)
        return {
            'chapter_title': name,
            'wav_path': wav,
            'timeline': [{'text': f'{name} mondat.', 'character_name': None,
                          't_start': 0.0, 't_end': seconds}],
            'duration_sec': seconds,
        }

    def test_subtitles_are_retimed_to_their_place_in_the_joined_file(self):
        part = [self._chapter('egy', 1.0), self._chapter('ketto', 1.0)]
        result = exporter.export_joined_part(
            'Konyv', part, {}, output_dir=self.dir, file_stem='Konyv_part1',
            audio_fmt='wav', sub_fmt='srt', opts={'pause_chapter': 2.0},
        )
        with open(result['subtitle_path'], encoding='utf-8') as f:
            srt = f.read()
        # Second chapter starts after 1 s of audio + a 2 s chapter break.
        self.assertIn('00:00:00,000', srt)
        self.assertIn('00:00:03,000', srt)
        self.assertIn('egy mondat.', srt)
        self.assertIn('ketto mondat.', srt)

    def test_wav_output_is_one_file_with_the_summed_length(self):
        part = [self._chapter('a', 0.5), self._chapter('b', 0.5)]
        result = exporter.export_joined_part(
            'Konyv', part, {}, output_dir=self.dir, file_stem='Konyv',
            audio_fmt='wav', sub_fmt='srt', opts={'pause_chapter': 1.0},
        )
        self.assertEqual(result['audio_fmt'], 'wav')
        self.assertEqual(result['chapter_count'], 2)
        data, _ = sf.read(result['audio_path'])
        self.assertAlmostEqual(len(data) / exporter.SAMPLE_RATE, 2.0, places=3)

    def test_mp3_is_encoded_once_and_the_scratch_wav_is_removed(self):
        part = [self._chapter('a', 0.3)]
        calls = []

        def fake_encode(wav_path, out_path, opts=None):
            calls.append((wav_path, out_path))
            with open(out_path, 'wb') as f:
                f.write(b'ID3')
            return True

        with patch.object(exporter, 'encode_wav_file', side_effect=fake_encode):
            result = exporter.export_joined_part(
                'Konyv', part, {}, output_dir=self.dir, file_stem='Konyv',
                audio_fmt='mp3', sub_fmt='srt',
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['audio_fmt'], 'mp3')
        self.assertTrue(result['audio_path'].endswith('.mp3'))
        self.assertFalse(os.path.exists(calls[0][0]))

    def test_failed_encode_keeps_the_lossless_wav(self):
        part = [self._chapter('a', 0.3)]
        with patch.object(exporter, 'encode_wav_file', return_value=False):
            result = exporter.export_joined_part(
                'Konyv', part, {}, output_dir=self.dir, file_stem='Konyv',
                audio_fmt='mp3', sub_fmt='srt',
            )
        self.assertEqual(result['audio_fmt'], 'wav')
        self.assertTrue(os.path.exists(result['audio_path']))

    def test_ass_subtitles_are_written_when_requested(self):
        part = [self._chapter('a', 0.3)]
        result = exporter.export_joined_part(
            'Konyv', part, {}, output_dir=self.dir, file_stem='Konyv',
            audio_fmt='wav', sub_fmt='ass',
        )
        self.assertTrue(result['subtitle_path'].endswith('.ass'))
        with open(result['subtitle_path'], encoding='utf-8') as f:
            self.assertIn('[Script Info]', f.read())


class ChapterPauseOptionTests(unittest.TestCase):
    def test_default_chapter_pause_is_longer_than_the_in_chapter_pauses(self):
        opts = exporter.default_audio_options()
        self.assertEqual(opts['pause_chapter'], exporter.CHAPTER_BREAK_PAUSE_SEC)
        self.assertGreater(opts['pause_chapter'], opts['pause_ellipsis'])

    def test_saved_chapter_pause_is_used(self):
        with patch('core.settings.load', return_value={'export_pause_chapter': 4.5}):
            self.assertEqual(exporter.audio_options()['pause_chapter'], 4.5)

    def test_chapter_pause_may_exceed_the_five_second_sentence_cap(self):
        with patch('core.settings.load', return_value={'export_pause_chapter': 8.0}):
            self.assertEqual(exporter.audio_options()['pause_chapter'], 8.0)

    def test_chapter_pause_is_clamped_and_never_raises(self):
        with patch('core.settings.load', return_value={'export_pause_chapter': 99}):
            self.assertEqual(exporter.audio_options()['pause_chapter'], 10.0)
        with patch('core.settings.load', return_value={'export_pause_chapter': 'x'}):
            self.assertEqual(exporter.audio_options()['pause_chapter'],
                             exporter.CHAPTER_BREAK_PAUSE_SEC)


class RenderChapterWavTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_rendered_chapter_carries_its_wav_timeline_and_duration(self):
        clip = os.path.join(self.dir, 'seg.wav')
        _write_wav(clip, 1.0)
        segments = [
            {'text': 'Első.', 'audio_path': clip, 'duration_sec': 1.0},
            {'text': 'Második.', 'audio_path': clip, 'duration_sec': 1.0},
        ]
        out = exporter.render_chapter_wav(
            segments, self.dir, 'ch1', {'pause_segment': 0.5})

        self.assertTrue(out['wav_path'].endswith('ch1.wav'))
        self.assertTrue(os.path.exists(out['wav_path']))
        self.assertAlmostEqual(out['duration_sec'], 2.5, places=2)
        self.assertAlmostEqual(out['timeline'][1]['t_start'], 1.5, places=3)


if __name__ == '__main__':
    unittest.main()
