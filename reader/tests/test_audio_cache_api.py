import os
import tempfile
import time
import unittest

import app as app_module
from core import database
from core import tts_engine


class AudioCacheApiTest(unittest.TestCase):
    """Cache stats and orphan cleanup: only old, unreferenced files go."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        self.original_cache_dir = tts_engine.AUDIO_CACHE_DIR
        database.DB_PATH = os.path.join(self.tmp.name, "reader.db")
        self.cache_dir = os.path.join(self.tmp.name, "audio_cache")
        os.makedirs(self.cache_dir)
        tts_engine.AUDIO_CACHE_DIR = self.cache_dir
        database.init_db()

        self.referenced = os.path.join(self.cache_dir, "referenced.wav")
        self.orphan_old = os.path.join(self.cache_dir, "orphan-old.wav")
        self.orphan_new = os.path.join(self.cache_dir, "orphan-new.wav")
        for path in (self.referenced, self.orphan_old, self.orphan_new):
            with open(path, "wb") as f:
                f.write(b"RIFF-test-audio")
        old = time.time() - 7200
        os.utime(self.orphan_old, (old, old))

        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO books (id, title, file_path, file_type, language) "
                "VALUES (1, 'Test', 'test.txt', 'txt', 'en')"
            )
            conn.execute(
                "INSERT INTO chapters "
                "(id, book_id, title, order_num, content, word_count) "
                "VALUES (2, 1, 'Chapter 1', 0, 'One.', 1)"
            )
            conn.execute(
                "INSERT INTO tts_segments "
                "(book_id, chapter_id, segment_index, text, enriched_text, "
                "instruct, speed, is_dialogue, cache_key, audio_path) "
                "VALUES (1, 2, 0, 'One.', 'One.', 'narrator', 1.0, 0, "
                "'referenced', ?)",
                (self.referenced,),
            )
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        tts_engine.AUDIO_CACHE_DIR = self.original_cache_dir
        self.tmp.cleanup()

    def test_stats_classify_orphans(self):
        stats = self.client.get("/api/cache/audio").get_json()
        self.assertEqual(stats["total_files"], 3)
        self.assertEqual(stats["orphan_files"], 2)
        self.assertNotIn("orphan_paths", stats)

    def test_cleanup_removes_only_old_orphans(self):
        resp = self.client.post("/api/cache/audio/cleanup").get_json()
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["removed_files"], 1)
        self.assertFalse(os.path.exists(self.orphan_old))
        self.assertTrue(os.path.exists(self.orphan_new))   # too fresh
        self.assertTrue(os.path.exists(self.referenced))   # still referenced

    def test_cleanup_blocked_during_export(self):
        app_module._export_exclusive_begin()
        try:
            resp = self.client.post("/api/cache/audio/cleanup")
            self.assertEqual(resp.status_code, 409)
            self.assertTrue(os.path.exists(self.orphan_old))
        finally:
            app_module._export_exclusive_end()

    def test_delete_book_sweeps_orphans(self):
        resp = self.client.delete("/api/books/1").get_json()
        self.assertTrue(resp["ok"])
        # The old orphan goes; the referenced file only became an orphan now
        # and is newer than the age guard, so it survives until a later sweep.
        self.assertFalse(os.path.exists(self.orphan_old))
        self.assertTrue(os.path.exists(self.referenced))


if __name__ == "__main__":
    unittest.main()
