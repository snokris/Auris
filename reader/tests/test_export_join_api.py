"""The join switch and the 1-4 slider on the settings and export APIs."""

import tempfile
import unittest
from pathlib import Path

import app as app_module
from core import exporter
from core import settings as app_settings


class JoinSettingsApiTest(unittest.TestCase):
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

    def test_defaults_keep_the_per_chapter_behaviour(self):
        saved = app_settings.load()
        self.assertFalse(saved['export_join_parts'])
        self.assertEqual(saved['export_part_count'], 1)

    def test_join_settings_round_trip(self):
        self._post({'export_join_parts': True, 'export_part_count': 3,
                    'export_pause_chapter': 3.5})
        saved = app_settings.load()
        self.assertTrue(saved['export_join_parts'])
        self.assertEqual(saved['export_part_count'], 3)
        self.assertEqual(saved['export_pause_chapter'], 3.5)

    def test_part_count_is_clamped_to_the_supported_range(self):
        self._post({'export_part_count': 99})
        self.assertEqual(app_settings.load()['export_part_count'],
                         exporter.MAX_PART_COUNT)
        self._post({'export_part_count': 0})
        self.assertEqual(app_settings.load()['export_part_count'], 1)
        self._post({'export_part_count': 'three'})
        self.assertEqual(app_settings.load()['export_part_count'], 1)

    def test_chapter_pause_may_be_longer_than_a_sentence_pause(self):
        self._post({'export_pause_chapter': 8})
        self.assertEqual(app_settings.load()['export_pause_chapter'], 8.0)
        self._post({'export_pause_chapter': 99})
        self.assertEqual(app_settings.load()['export_pause_chapter'], 10.0)
        self._post({'export_pause_chapter': 'x'})
        self.assertEqual(app_settings.load()['export_pause_chapter'],
                         exporter.CHAPTER_BREAK_PAUSE_SEC)

    def test_join_switch_accepts_only_a_boolean(self):
        self._post({'export_join_parts': 'yes'})
        self.assertIs(app_settings.load()['export_join_parts'], True)
        self._post({'export_join_parts': 0})
        self.assertIs(app_settings.load()['export_join_parts'], False)

    def test_saved_chapter_pause_reaches_the_exporter(self):
        self._post({'export_pause_chapter': 4.0})
        self.assertEqual(exporter.audio_options()['pause_chapter'], 4.0)


class JoinRequestResolutionTest(unittest.TestCase):
    """The export request wins; Settings are the fallback."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_file = app_settings.SETTINGS_FILE
        app_settings.SETTINGS_FILE = Path(self.tmp.name) / 'settings.json'

    def tearDown(self):
        app_settings.SETTINGS_FILE = self.original_file
        self.tmp.cleanup()

    def test_empty_body_uses_the_saved_defaults(self):
        app_settings.save({'export_join_parts': True, 'export_part_count': 4})
        self.assertEqual(app_module._read_join_request({}), (True, 4))

    def test_request_overrides_the_saved_defaults(self):
        app_settings.save({'export_join_parts': True, 'export_part_count': 4})
        self.assertEqual(
            app_module._read_join_request({'join_parts': False, 'part_count': 1}),
            (False, 1),
        )

    def test_request_values_are_clamped(self):
        self.assertEqual(
            app_module._read_join_request({'join_parts': True, 'part_count': 77}),
            (True, exporter.MAX_PART_COUNT),
        )
        self.assertEqual(
            app_module._read_join_request({'join_parts': True, 'part_count': None}),
            (True, 1),
        )

    def test_a_fresh_install_exports_per_chapter(self):
        self.assertEqual(app_module._read_join_request({}), (False, 1))


class PartCountClampTest(unittest.TestCase):
    def test_slider_positions_map_to_file_counts(self):
        for value, expected in ((1, 1), (2, 2), (3, 3), (4, 4), (5, 4),
                                (0, 1), (-2, 1), ('2', 2), (None, 1), ('x', 1)):
            self.assertEqual(app_module._clamp_part_count(value), expected)


if __name__ == '__main__':
    unittest.main()
