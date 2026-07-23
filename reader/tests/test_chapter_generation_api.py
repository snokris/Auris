import os
import tempfile
import time
import unittest

import app as app_module
from core import database


class _FakeTTS:
    engine_name = "higgs"

    def __init__(self, audio_dir):
        self.audio_dir = audio_dir

    def status(self):
        return {"state": "ready"}

    def load_async(self):
        return None

    def generate_many(
        self,
        items,
        num_step=None,
        batch_size=None,
        on_item=None,
        on_status=None,
    ):
        results = []
        for index, _item in enumerate(items):
            path = os.path.join(self.audio_dir, f"generated-{index}.wav")
            with open(path, "wb") as audio_file:
                audio_file.write(b"RIFF-test")
            result = {
                "audio_path": path,
                "duration_sec": 1.0,
                "cache_hit": False,
                "cache_key": f"generated-{index}",
            }
            results.append(result)
            if on_item:
                on_item(index, result)
        return results

    def set_dedicated_cuda_stream(self, enabled):
        return None


class ChapterGenerationApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        self.original_startup = app_module._startup_complete
        self.original_tts = app_module.tts
        database.DB_PATH = os.path.join(self.tmp.name, "reader.db")
        app_module._startup_complete = True
        app_module.tts = _FakeTTS(self.tmp.name)
        database.init_db()
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO books (id, title, file_path, file_type, language) "
                "VALUES (1, 'Test', 'test.txt', 'txt', 'en')"
            )
            conn.execute(
                "INSERT INTO chapters "
                "(id, book_id, title, order_num, content, word_count) "
                "VALUES (2, 1, 'Chapter 1', 0, 'One. Two. Three.', 3)"
            )
            for index, text in enumerate(("One.", "Two.", "Three.")):
                conn.execute(
                    "INSERT INTO tts_segments "
                    "(book_id, chapter_id, segment_index, text, enriched_text, "
                    "instruct, speed, is_dialogue, cache_key) "
                    "VALUES (1, 2, ?, ?, ?, 'narrator', 1.0, 0, ?)",
                    (index, text, text, f"pending-{index}"),
                )
        with app_module._chapter_generation_lock:
            app_module._chapter_generation_jobs.clear()
            app_module._chapter_generation_by_chapter.clear()
            app_module._chapter_generation_active_job_id = None
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        app_module._startup_complete = self.original_startup
        app_module.tts = self.original_tts
        with app_module._chapter_generation_lock:
            app_module._chapter_generation_jobs.clear()
            app_module._chapter_generation_by_chapter.clear()
            app_module._chapter_generation_active_job_id = None
        self.tmp.cleanup()

    def test_whole_chapter_generation_reports_progress_and_ready(self):
        page = self.client.get("/reader/1")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'id="chapter-generate-btn"', page.data)
        self.assertIn(b'id="chapter-generate-percent"', page.data)

        initial = self.client.get(
            "/api/books/1/chapters/2/generate"
        ).get_json()
        self.assertEqual(initial["state"], "idle")
        self.assertEqual((initial["done"], initial["total"]), (0, 3))

        started = self.client.post(
            "/api/books/1/chapters/2/generate"
        )
        self.assertEqual(started.status_code, 200)
        job_id = started.get_json()["job_id"]

        final = None
        for _ in range(100):
            final = self.client.get(
                f"/api/chapter-generation/status/{job_id}"
            ).get_json()
            if final["state"] in ("complete", "failed"):
                break
            time.sleep(0.01)

        self.assertEqual(final["state"], "complete")
        self.assertEqual((final["done"], final["total"]), (3, 3))
        self.assertEqual(final["percent"], 100)

        status = self.client.get(
            "/api/books/1/chapters/2/generate"
        ).get_json()
        self.assertEqual(status["state"], "complete")
        self.assertEqual(status["percent"], 100)

        chapters = self.client.get("/api/books/1/chapters").get_json()
        self.assertEqual(chapters[0]["audio_ready"], 3)
        self.assertEqual(chapters[0]["audio_total"], 3)


class _CancelRecordingTTS:
    def __init__(self):
        self.cancel_calls = 0

    def status(self):
        return {"state": "ready"}

    def cancel(self):
        self.cancel_calls += 1
        return True


class TtsCancelExportGatingTest(unittest.TestCase):
    """/api/tts/cancel must not kill the engine while a bulk job owns it.

    Higgs cancel() terminates the worker subprocess; the reader UI fires
    cancel on every chapter switch, which used to abort a running export.
    """

    def setUp(self):
        self.original_tts = app_module.tts
        self.fake = _CancelRecordingTTS()
        app_module.tts = self.fake
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.tts = self.original_tts

    def test_cancel_ignored_while_export_exclusive(self):
        app_module._export_exclusive_begin()
        try:
            resp = self.client.post("/api/tts/cancel").get_json()
            self.assertEqual(resp.get("busy"), "export")
            self.assertFalse(resp["cancel_requested"])
            self.assertEqual(self.fake.cancel_calls, 0)
        finally:
            app_module._export_exclusive_end()

    def test_cancel_passes_through_when_idle(self):
        resp = self.client.post("/api/tts/cancel").get_json()
        self.assertTrue(resp["cancel_requested"])
        self.assertEqual(self.fake.cancel_calls, 1)


if __name__ == "__main__":
    unittest.main()
