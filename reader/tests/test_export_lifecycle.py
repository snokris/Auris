import os
import tempfile
import unittest

import app as app_module
from core import database


class _ReadyTTS:
    def status(self):
        return {"state": "ready"}

    def load_async(self):
        return None


class ExportLifecycleTest(unittest.TestCase):
    """Single active export, pause/stop, and restart = paused semantics."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db = database.DB_PATH
        self.orig_startup = app_module._startup_complete
        self.orig_tts = app_module.tts
        database.DB_PATH = os.path.join(self.tmp.name, "reader.db")
        app_module._startup_complete = True
        app_module.tts = _ReadyTTS()
        database.init_db()
        with database.get_conn() as conn:
            for bid, title in ((1, "Book One"), (2, "Book Two")):
                conn.execute(
                    "INSERT INTO books (id, title, file_path, file_type, language) "
                    "VALUES (?, ?, 'x.txt', 'txt', 'en')",
                    (bid, title),
                )
        app_module._export_jobs.clear()
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        database.DB_PATH = self.orig_db
        app_module._startup_complete = self.orig_startup
        app_module.tts = self.orig_tts
        app_module._export_jobs.clear()
        self.tmp.cleanup()

    def _mark_running(self, book_id, mode="chapterwise", chapters="all"):
        app_module._save_export_prefs(book_id, mode, chapters, "wav", "srt")
        app_module._set_export_status(book_id, "running")

    def test_second_book_export_is_blocked(self):
        self._mark_running(1)
        r = self.client.post(
            "/api/books/2/export/chapterwise",
            json={"audio_fmt": "wav", "sub_fmt": "srt", "chapters": "all"},
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["active_book_id"], 1)

    def test_state_reports_lock_for_other_book(self):
        self._mark_running(1)
        st = self.client.get("/api/books/2/export/state").get_json()
        self.assertIsNotNone(st["locked_by"])
        self.assertEqual(st["locked_by"]["book_id"], 1)

    def test_running_without_live_job_becomes_paused(self):
        # Simulates a crash/restart: status='running' persisted, no job object.
        self._mark_running(1)
        st = self.client.get("/api/books/1/export/state").get_json()
        self.assertEqual(st["status"], "paused")
        with database.get_conn() as conn:
            row = conn.execute(
                "SELECT status FROM export_prefs WHERE book_id=1"
            ).fetchone()
        self.assertEqual(row["status"], "paused")

    def test_pause_without_live_job_marks_paused(self):
        self._mark_running(1)
        r = self.client.post("/api/books/1/export/pause").get_json()
        self.assertEqual(r["state"], "paused")

    def test_stop_clears_status(self):
        self._mark_running(1)
        self.client.post("/api/books/1/export/stop")
        with database.get_conn() as conn:
            row = conn.execute(
                "SELECT status FROM export_prefs WHERE book_id=1"
            ).fetchone()
        self.assertIsNone(row["status"])
        # Now book 2 can export (slot freed).
        self.assertIsNone(app_module._active_export_row())

    def test_pause_signals_live_job(self):
        job_id, job = app_module._make_export_job(1)
        job["state"] = "running"
        r = self.client.post("/api/books/1/export/pause").get_json()
        self.assertEqual(r["state"], "pausing")
        self.assertEqual(job["control"], "pause")

    def test_library_exposes_export_status(self):
        self._mark_running(1)
        books = self.client.get("/api/books").get_json()
        by_id = {b["id"]: b for b in books}
        # book 1 running (persisted), book 2 has no export row
        self.assertEqual(by_id[1]["export_status"], "running")
        self.assertIsNone(by_id[2]["export_status"])


if __name__ == "__main__":
    unittest.main()
