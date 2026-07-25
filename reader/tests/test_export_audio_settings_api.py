"""/api/settings must expose and guard the new audio options."""

import tempfile
import unittest
from pathlib import Path

import app as app_module
from core import exporter
from core import settings as app_settings


class ExportAudioSettingsApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_file = app_settings.SETTINGS_FILE
        app_settings.SETTINGS_FILE = Path(self.tmp.name) / 'settings.json'
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_settings.SETTINGS_FILE = self.original_file
        self.tmp.cleanup()

    def _post(self, payload):
        return self.client.post('/api/settings', json=payload)

    def test_get_reports_the_audio_capabilities_the_ui_needs(self):
        info = self.client.get('/api/settings').get_json()['_audio_info']
        self.assertEqual(info['sample_rate'], exporter.SAMPLE_RATE)
        self.assertEqual(info['channels'], 1)
        self.assertEqual(info['max_bitrate'], exporter.MAX_MP3_BITRATE_KBPS)
        self.assertIn('ffmpeg', info)
        self.assertEqual(len(info['kbps_by_vbr_quality']), 10)
        self.assertEqual(info['kbps_by_vbr_quality']['7'],
                         exporter.estimated_mp3_kbps(
                             {'mp3_mode': 'vbr', 'mp3_vbr_quality': 7}))

    def test_audio_options_round_trip(self):
        self._post({'mp3_mode': 'cbr', 'mp3_bitrate': 64, 'mp3_vbr_quality': 5,
                    'export_pause_segment': 0.4})
        saved = app_settings.load()
        self.assertEqual(saved['mp3_mode'], 'cbr')
        self.assertEqual(saved['mp3_bitrate'], 64)
        self.assertEqual(saved['mp3_vbr_quality'], 5)
        self.assertEqual(saved['export_pause_segment'], 0.4)

    def test_hostile_values_are_clamped_not_stored_raw(self):
        self._post({'mp3_mode': 'opus', 'mp3_bitrate': 320, 'mp3_vbr_quality': -3,
                    'export_pause_ellipsis': 900, 'export_pause_segment': 'x'})
        saved = app_settings.load()
        self.assertEqual(saved['mp3_mode'], 'vbr')
        self.assertEqual(saved['mp3_bitrate'], exporter.MAX_MP3_BITRATE_KBPS)
        self.assertEqual(saved['mp3_vbr_quality'], 0)
        self.assertEqual(saved['export_pause_ellipsis'], 5.0)
        self.assertEqual(saved['export_pause_segment'], 0.35)

    def test_unknown_keys_are_still_rejected(self):
        self._post({'mp3_bitrate': 96, 'evil_key': 'boom'})
        self.assertNotIn('evil_key', app_settings.load())

    def test_saved_settings_reach_the_exporter(self):
        self._post({'mp3_mode': 'cbr', 'mp3_bitrate': 32,
                    'export_pause_dialogue': 0.8})
        opts = exporter.audio_options()
        self.assertEqual(opts['mp3_mode'], 'cbr')
        self.assertEqual(opts['mp3_bitrate'], 32)
        self.assertEqual(opts['pause_dialogue'], 0.8)


if __name__ == '__main__':
    unittest.main()
