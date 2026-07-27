"""Multi-voice narration is disabled: the app must never take those paths."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from core import database
from core import settings as app_settings


class SingleVoiceOnlyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        self.original_settings_file = app_settings.SETTINGS_FILE
        self.original_upload_dir = app_module.UPLOAD_DIR
        self.original_startup = app_module._startup_complete
        database.DB_PATH = os.path.join(self.tmp.name, 'reader.db')
        app_settings.SETTINGS_FILE = Path(self.tmp.name) / 'settings.json'
        app_module.UPLOAD_DIR = self.tmp.name
        app_module._startup_complete = True
        database.init_db()
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        app_settings.SETTINGS_FILE = self.original_settings_file
        app_module.UPLOAD_DIR = self.original_upload_dir
        app_module._startup_complete = self.original_startup
        self.tmp.cleanup()

    def test_flag_is_off(self):
        self.assertFalse(app_module.MULTI_VOICE_NARRATION)

    def test_every_book_reads_as_single_narrator(self):
        # Even a book stored with the mode off behaves as single narrator.
        self.assertTrue(app_module._book_single_narrator_mode(
            {'single_narrator_mode': 0}))
        self.assertTrue(app_module._book_single_narrator_mode(None))

    def test_import_skips_character_analysis(self):
        with patch.object(app_module.threading, 'Thread') as thread:
            response = self.client.post(
                '/api/books/import',
                data={'file': (
                    io.BytesIO(
                        'Tesztkönyv\n\nElső fejezet\nEgy rövid magyar mondat '
                        'a próbához.'.encode('utf-8')),
                    'teszt.txt',
                )},
                content_type='multipart/form-data',
            )
        self.assertEqual(response.status_code, 200)
        thread.assert_not_called()
        with database.get_conn() as conn:
            book = conn.execute('SELECT * FROM books').fetchone()
        self.assertEqual(book['single_narrator_mode'], 1)
        self.assertEqual(book['character_analysis_status'], 'complete')

    def test_single_narrator_cannot_be_turned_off(self):
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO books (id, title, author, file_path, file_type, "
                "single_narrator_mode) VALUES (1, 'T', 'A', 't.txt', 'txt', 1)"
            )
        response = self.client.put(
            '/api/books/1/single-narrator',
            json={'enabled': False},
        )
        self.assertEqual(response.status_code, 400)
        with database.get_conn() as conn:
            book = conn.execute('SELECT * FROM books WHERE id=1').fetchone()
        self.assertEqual(book['single_narrator_mode'], 1)

    def test_voice_studio_page_shows_no_multi_voice_ui(self):
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO books (id, title, author, file_path, file_type, "
                "single_narrator_mode) VALUES (1, 'T', 'A', 't.txt', 'txt', 0)"
            )
        page = self.client.get('/voice-studio/1').data
        self.assertNotIn(b'Characters', page)
        self.assertNotIn(b'single-narrator-mode', page)
        self.assertNotIn(b'Narration mode', page)
        # The narrator card itself must stay.
        self.assertIn(b'Narrator', page)
        self.assertIn(b'Saved voices', page)

    def test_settings_page_shows_no_character_detection(self):
        page = self.client.get('/settings').data
        self.assertNotIn(b'Character &amp; Dialogue Speaker Detection', page)
        self.assertNotIn(b'character-detection-mode', page)
        self.assertNotIn(b'default-single-narrator-mode', page)

    def test_docs_page_shows_no_llm_detection_section(self):
        page = self.client.get('/docs').data
        self.assertNotIn('Helyi LLM beállítása'.encode('utf-8'), page)
        self.assertNotIn('Karakterfelismerés'.encode('utf-8'), page)


if __name__ == '__main__':
    unittest.main()
