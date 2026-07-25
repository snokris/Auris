"""Configurable MP3 encoding and spoken-pause options."""

import unittest
from unittest.mock import patch

from core import exporter


class AudioOptionResolutionTests(unittest.TestCase):
    def test_defaults_are_the_narration_preset(self):
        opts = exporter.default_audio_options()
        self.assertEqual(opts['mp3_mode'], 'vbr')
        self.assertEqual(opts['mp3_vbr_quality'], exporter.DEFAULT_MP3_VBR_QUALITY)
        self.assertEqual(opts['mp3_bitrate'], exporter.DEFAULT_MP3_BITRATE_KBPS)
        self.assertEqual(opts['pause_segment'], exporter.DEFAULT_SEGMENT_PAUSE_SEC)
        self.assertEqual(opts['pause_dialogue'], exporter.DIALOGUE_TURN_PAUSE_SEC)
        self.assertEqual(opts['pause_ellipsis'], exporter.ELLIPSIS_PAUSE_SEC)

    def test_saved_settings_override_defaults(self):
        saved = {
            'mp3_mode': 'cbr',
            'mp3_bitrate': 64,
            'mp3_vbr_quality': 5,
            'export_pause_segment': 0.2,
            'export_pause_dialogue': 0.9,
            'export_pause_ellipsis': 2.0,
        }
        with patch('core.settings.load', return_value=saved):
            opts = exporter.audio_options()
        self.assertEqual(opts['mp3_mode'], 'cbr')
        self.assertEqual(opts['mp3_bitrate'], 64)
        self.assertEqual(opts['mp3_vbr_quality'], 5)
        self.assertEqual(opts['pause_segment'], 0.2)
        self.assertEqual(opts['pause_dialogue'], 0.9)
        self.assertEqual(opts['pause_ellipsis'], 2.0)

    def test_explicit_overrides_beat_saved_settings(self):
        with patch('core.settings.load', return_value={'mp3_vbr_quality': 2}):
            opts = exporter.audio_options({'mp3_vbr_quality': 9})
        self.assertEqual(opts['mp3_vbr_quality'], 9)

    def test_resolution_is_idempotent(self):
        """A whole-book export resolves once and passes the dict down."""
        with patch('core.settings.load', return_value={'mp3_vbr_quality': 3}):
            once = exporter.audio_options({'mp3_mode': 'cbr', 'mp3_bitrate': 40})
            twice = exporter.audio_options(once)
        self.assertEqual(once, twice)

    def test_unusable_values_fall_back_instead_of_raising(self):
        saved = {
            'mp3_mode': 'flac',
            'mp3_vbr_quality': 'nope',
            'mp3_bitrate': None,
            'export_pause_segment': 'x',
        }
        with patch('core.settings.load', return_value=saved):
            opts = exporter.audio_options()
        self.assertEqual(opts['mp3_mode'], 'vbr')
        self.assertEqual(opts['mp3_vbr_quality'], exporter.DEFAULT_MP3_VBR_QUALITY)
        self.assertEqual(opts['mp3_bitrate'], exporter.DEFAULT_MP3_BITRATE_KBPS)
        self.assertEqual(opts['pause_segment'], exporter.DEFAULT_SEGMENT_PAUSE_SEC)

    def test_out_of_range_values_are_clamped(self):
        saved = {
            'mp3_vbr_quality': 42,
            'mp3_bitrate': 320,          # 24 kHz mono cannot exceed 160 kbps
            'export_pause_segment': -1,
            'export_pause_ellipsis': 99,
        }
        with patch('core.settings.load', return_value=saved):
            opts = exporter.audio_options()
        self.assertEqual(opts['mp3_vbr_quality'], 9)
        self.assertEqual(opts['mp3_bitrate'], exporter.MAX_MP3_BITRATE_KBPS)
        self.assertEqual(opts['pause_segment'], 0.0)
        self.assertEqual(opts['pause_ellipsis'], 5.0)

    def test_missing_settings_module_is_not_fatal(self):
        with patch('core.settings.load', side_effect=RuntimeError('no settings')):
            opts = exporter.audio_options()
        self.assertEqual(opts, exporter.default_audio_options())


class EstimatedBitrateTests(unittest.TestCase):
    def test_cbr_estimate_is_the_configured_bitrate(self):
        kbps = exporter.estimated_mp3_kbps({'mp3_mode': 'cbr', 'mp3_bitrate': 96})
        self.assertEqual(kbps, 96)

    def test_vbr_estimate_decreases_as_quality_number_grows(self):
        rates = [
            exporter.estimated_mp3_kbps({'mp3_mode': 'vbr', 'mp3_vbr_quality': q})
            for q in range(10)
        ]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_recommended_preset_is_far_below_the_old_hardcoded_160(self):
        kbps = exporter.estimated_mp3_kbps(exporter.default_audio_options())
        self.assertLess(kbps, exporter.MAX_MP3_BITRATE_KBPS / 3)


class EncoderParameterTests(unittest.TestCase):
    """The encoder must ask ffmpeg for exactly what the settings say."""

    def _export_call(self, opts):
        captured = {}

        class FakeSegment:
            @staticmethod
            def from_wav(path):
                return FakeSegment()

            def export(self, buf, **kwargs):
                captured.update(kwargs)
                buf.write(b'ID3')
                return buf

        fake_pydub = type('m', (), {'AudioSegment': FakeSegment})
        with patch.dict('sys.modules', {'pydub': fake_pydub}), \
                patch.object(exporter, '_ffmpeg_available', return_value=True):
            data = exporter._wav_to_mp3_bytes('ignored.wav', opts)
        return captured, data

    def test_vbr_uses_q_a_and_no_fixed_bitrate(self):
        kwargs, data = self._export_call({'mp3_mode': 'vbr', 'mp3_vbr_quality': 7,
                                          'mp3_bitrate': 48})
        self.assertEqual(data, b'ID3')
        self.assertEqual(kwargs['format'], 'mp3')
        self.assertEqual(kwargs['parameters'], ['-q:a', '7'])
        self.assertNotIn('bitrate', kwargs)

    def test_cbr_uses_bitrate_and_no_q_a(self):
        kwargs, _ = self._export_call({'mp3_mode': 'cbr', 'mp3_vbr_quality': 7,
                                       'mp3_bitrate': 64})
        self.assertEqual(kwargs['bitrate'], '64k')
        self.assertNotIn('parameters', kwargs)

    def test_without_ffmpeg_the_caller_gets_none_and_keeps_the_wav(self):
        with patch.object(exporter, '_ffmpeg_available', return_value=False):
            self.assertIsNone(exporter._wav_to_mp3_bytes('ignored.wav'))


class ConfigurablePauseTests(unittest.TestCase):
    OPTS = {
        'pause_segment': 0.1,
        'pause_dialogue': 0.2,
        'pause_ellipsis': 3.0,
    }

    def test_pause_after_segment_honours_configured_values(self):
        self.assertEqual(
            exporter.pause_after_segment({'text': 'Egy mondat.'}, None, self.OPTS), 0.1)
        self.assertEqual(
            exporter.pause_after_segment({'text': '– Igen.', 'is_dialogue': True},
                                         {'text': '– Nem.', 'is_dialogue': True},
                                         self.OPTS), 0.2)
        self.assertEqual(
            exporter.pause_after_segment({'text': 'Hát...'}, None, self.OPTS), 3.0)

    def test_timeline_gaps_follow_the_configured_pauses(self):
        segments = [
            {'text': 'Első mondat.', 'duration_sec': 1.0},
            {'text': 'Második mondat.', 'duration_sec': 1.0},
        ]
        timeline = exporter.build_timeline(segments, self.OPTS)
        gap = timeline[1]['t_start'] - timeline[0]['t_end']
        self.assertAlmostEqual(gap, 0.1, places=3)

    def test_timeline_without_options_uses_the_defaults(self):
        segments = [
            {'text': 'Első mondat.', 'duration_sec': 1.0},
            {'text': 'Második mondat.', 'duration_sec': 1.0},
        ]
        timeline = exporter.build_timeline(segments)
        gap = timeline[1]['t_start'] - timeline[0]['t_end']
        self.assertAlmostEqual(gap, exporter.DEFAULT_SEGMENT_PAUSE_SEC, places=3)


if __name__ == '__main__':
    unittest.main()
