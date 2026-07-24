import os
import tempfile
import unittest

import app as app_module
from core import database


class ChapterEditorApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db = database.DB_PATH
        database.DB_PATH = os.path.join(self.tmp.name, "reader.db")
        database.init_db()
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO books (id, title, file_path, file_type, language, total_chapters) "
                "VALUES (1, 'Book', 'x.txt', 'txt', 'hu', 3)"
            )
            for cid, order, title, excluded in (
                (10, 0, "Bevezető", 1),
                (11, 1, "ELSŐ FEJEZET", 0),
                (12, 2, "MÁSODIK FEJEZET", 0),
            ):
                conn.execute(
                    "INSERT INTO chapters (id, book_id, title, order_num, section_type, content, word_count, excluded) "
                    "VALUES (?, 1, ?, ?, 'chapter', ?, 5, ?)",
                    (cid, title, order, f"{title} tartalma hosszabb szöveggel.", excluded),
                )
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        database.DB_PATH = self.orig_db
        self.tmp.cleanup()

    def _titles(self):
        return [c["title"] for c in self.client.get("/api/books/1/chapters").get_json()]

    def test_list_exposes_excluded_flag(self):
        rows = self.client.get("/api/books/1/chapters").get_json()
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id[10]["excluded"], 1)
        self.assertEqual(by_id[11]["excluded"], 0)

    def test_rename_chapter(self):
        r = self.client.patch("/api/books/1/chapters/11", json={"title": "1. fejezet"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("1. fejezet", self._titles())

    def test_rename_rejects_empty(self):
        r = self.client.patch("/api/books/1/chapters/11", json={"title": "   "})
        self.assertEqual(r.status_code, 400)

    def test_toggle_excluded_includes_front_matter(self):
        r = self.client.patch("/api/books/1/chapters/10", json={"excluded": False})
        self.assertEqual(r.status_code, 200)
        rows = self.client.get("/api/books/1/chapters").get_json()
        self.assertEqual({r["id"]: r["excluded"] for r in rows}[10], 0)

    def test_delete_chapter_renumbers(self):
        r = self.client.delete("/api/books/1/chapters/11")
        self.assertEqual(r.status_code, 200)
        rows = self.client.get("/api/books/1/chapters").get_json()
        self.assertEqual([c["title"] for c in rows], ["Bevezető", "MÁSODIK FEJEZET"])
        self.assertEqual([c["order_num"] for c in rows], [0, 1])

    def test_cannot_delete_last_chapter(self):
        self.client.delete("/api/books/1/chapters/10")
        self.client.delete("/api/books/1/chapters/11")
        r = self.client.delete("/api/books/1/chapters/12")
        self.assertEqual(r.status_code, 400)

    def test_merge_up_combines_into_previous(self):
        r = self.client.post("/api/books/1/chapters/12/merge-up")
        self.assertEqual(r.status_code, 200)
        rows = self.client.get("/api/books/1/chapters").get_json()
        self.assertEqual([c["title"] for c in rows], ["Bevezető", "ELSŐ FEJEZET"])
        first = next(c for c in rows if c["title"] == "ELSŐ FEJEZET")
        self.assertGreater(first["word_count"], 5)

    def test_merge_up_first_chapter_fails(self):
        r = self.client.post("/api/books/1/chapters/10/merge-up")
        self.assertEqual(r.status_code, 400)

    def test_edits_blocked_during_export(self):
        app_module._export_exclusive_begin()
        try:
            self.assertEqual(
                self.client.patch("/api/books/1/chapters/11", json={"title": "x"}).status_code,
                409,
            )
            self.assertEqual(self.client.delete("/api/books/1/chapters/11").status_code, 409)
        finally:
            app_module._export_exclusive_end()


if __name__ == "__main__":
    unittest.main()
