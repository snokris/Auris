"""Exported file names carry the author as well as the book title."""

import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import numpy as np
import soundfile as sf

from core import exporter


def _source_clip(directory: str) -> str:
    path = os.path.join(directory, 'source.wav')
    sf.write(path, np.zeros(2400, dtype=np.float32), exporter.SAMPLE_RATE)
    return path


class UsableAuthorTests(unittest.TestCase):
    def test_a_real_author_is_kept_with_its_accents(self):
        self.assertEqual(exporter.usable_author('Rejtő Jenő'), 'Rejtő_Jenő')

    def test_missing_or_placeholder_authors_are_dropped(self):
        for value in (None, '', '   ', 'Unknown', 'unknown', '???'):
            self.assertEqual(exporter.usable_author(value), '',
                             f'{value!r} should not become part of a name')


class BookFileStemTests(unittest.TestCase):
    def test_author_comes_first(self):
        self.assertEqual(exporter.book_file_stem('A titkok titka', 'Rejto Jeno'),
                         'Rejto_Jeno_-_A_titkok_titka')

    def test_without_an_author_only_the_title_is_used(self):
        self.assertEqual(exporter.book_file_stem('A titkok titka', None),
                         'A_titkok_titka')
        self.assertEqual(exporter.book_file_stem('A titkok titka', 'Unknown'),
                         'A_titkok_titka')

    def test_unsafe_characters_never_reach_the_file_system(self):
        stem = exporter.book_file_stem('A/B: C?', 'X/Y: Z?')
        for bad in '/:?':
            self.assertNotIn(bad, stem)

    def test_a_long_author_and_title_stay_within_the_name_limit(self):
        stem = exporter.book_file_stem('T' * 200, 'A' * 200)
        self.assertLessEqual(len(stem), exporter.MAX_STEM_LEN)


class PartFileStemWithAuthorTests(unittest.TestCase):
    def test_single_joined_file(self):
        self.assertEqual(
            exporter.part_file_stem('A titkok titka', 1, 1, 'Rejto Jeno'),
            'Rejto_Jeno_-_A_titkok_titka',
        )

    def test_split_joined_files_are_numbered_after_the_author_and_title(self):
        self.assertEqual(
            exporter.part_file_stem('A titkok titka', 2, 3, 'Rejto Jeno'),
            'Rejto_Jeno_-_A_titkok_titka_part2',
        )

    def test_the_author_stays_optional(self):
        self.assertEqual(exporter.part_file_stem('Book', 2, 3), 'Book_part2')


class ChapterFileStemWithBookTests(unittest.TestCase):
    def test_book_stem_precedes_the_chapter_number(self):
        self.assertEqual(
            exporter.chapter_file_stem(3, 'Első fejezet', 2, 'Rejtő_Jenő_-_Könyv'),
            'Rejtő_Jenő_-_Könyv_03_Első_fejezet',
        )

    def test_numbering_still_works_without_a_book_stem(self):
        self.assertEqual(exporter.chapter_file_stem(3, 'Vege', 2), '03_Vege')


class NamedOutputOnDiskTests(unittest.TestCase):
    """The names the listener actually sees in their player."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.clip = _source_clip(self.dir)
        self.segments = [{'audio_path': self.clip, 'duration_sec': 0.1,
                          'text': 'Egy mondat.'}]

    def tearDown(self):
        self.tmp.cleanup()

    def _chapters(self):
        return [
            {'chapter_number': 1, 'chapter_title': 'Elso',
             'segments': list(self.segments)},
            {'chapter_number': 2, 'chapter_title': 'Masodik',
             'segments': list(self.segments)},
        ]

    def test_chapter_files_carry_the_author_and_title(self):
        with patch.object(exporter, 'EXPORTS_DIR', self.dir):
            result = exporter.export_chapter_folder(
                'Konyv', self._chapters(), {}, audio_fmt='wav', sub_fmt='srt',
                author='Rejto Jeno',
            )
        names = sorted(os.listdir(result['directory_path']))
        self.assertIn('Rejto_Jeno_-_Konyv_01_Elso.wav', names)
        self.assertIn('Rejto_Jeno_-_Konyv_01_Elso.srt', names)
        self.assertIn('Rejto_Jeno_-_Konyv_02_Masodik.wav', names)

    def test_a_single_chapter_export_is_named_after_the_book_too(self):
        with patch.object(exporter, 'EXPORTS_DIR', self.dir):
            result = exporter.export_single_chapter(
                'Elso fejezet', 'Konyv', self.segments, {},
                audio_fmt='wav', sub_fmt='srt', author='Rejto Jeno',
            )
        self.assertEqual(os.path.basename(result['audio_path']),
                         'Rejto_Jeno_-_Konyv_Elso_fejezet.wav')

    def test_an_explicit_stem_from_the_caller_still_wins(self):
        with patch.object(exporter, 'EXPORTS_DIR', self.dir):
            result = exporter.export_single_chapter(
                'Elso fejezet', 'Konyv', self.segments, {},
                audio_fmt='wav', sub_fmt='srt', author='Rejto Jeno',
                file_stem='sajat_nev',
            )
        self.assertEqual(os.path.basename(result['audio_path']), 'sajat_nev.wav')

    def test_zip_and_its_entries_are_named_after_the_author_and_title(self):
        with patch.object(exporter, 'EXPORTS_DIR', self.dir):
            zip_path = exporter.export_chapter_zip(
                'Konyv', self._chapters(), {}, audio_fmt='wav', sub_fmt='srt',
                author='Rejto Jeno',
            )
        self.assertEqual(os.path.basename(zip_path),
                         'Rejto_Jeno_-_Konyv_chapters.zip')
        with zipfile.ZipFile(zip_path) as zf:
            names = sorted(zf.namelist())
        self.assertIn('Rejto_Jeno_-_Konyv_Elso.wav', names)
        self.assertIn('Rejto_Jeno_-_Konyv_Masodik.srt', names)

    def test_joined_parts_are_named_after_the_author_and_title(self):
        rendered = []
        for name in ('Elso', 'Masodik'):
            wav = os.path.join(self.dir, f'{name}.wav')
            sf.write(wav, np.zeros(2400, dtype=np.float32), exporter.SAMPLE_RATE)
            rendered.append({
                'chapter_title': name, 'wav_path': wav, 'duration_sec': 0.1,
                'timeline': [{'text': f'{name}.', 'character_name': None,
                              't_start': 0.0, 't_end': 0.1}],
            })
        stem = exporter.part_file_stem('Konyv', 1, 2, 'Rejto Jeno')
        result = exporter.export_joined_part(
            'Konyv', rendered, {}, output_dir=self.dir, file_stem=stem,
            audio_fmt='wav', sub_fmt='srt',
        )
        self.assertEqual(os.path.basename(result['audio_path']),
                         'Rejto_Jeno_-_Konyv_part1.wav')
        self.assertEqual(os.path.basename(result['subtitle_path']),
                         'Rejto_Jeno_-_Konyv_part1.srt')

    def test_the_book_folder_is_one_flat_author_and_title_folder(self):
        with patch.object(exporter, 'EXPORTS_DIR', self.dir):
            path = exporter.book_export_dir('Konyv', 'Rejto Jeno')
        self.assertEqual(path, os.path.join(self.dir, 'Rejto_Jeno_-_Konyv'))

    def test_a_book_without_an_author_gets_a_title_only_folder(self):
        with patch.object(exporter, 'EXPORTS_DIR', self.dir):
            path = exporter.book_export_dir('Konyv', None)
        self.assertEqual(path, os.path.join(self.dir, 'Konyv'))

    def test_the_folder_and_its_files_share_one_name(self):
        with patch.object(exporter, 'EXPORTS_DIR', self.dir):
            result = exporter.export_chapter_folder(
                'Konyv', self._chapters(), {}, audio_fmt='wav', sub_fmt='srt',
                author='Rejto Jeno',
            )
        folder = result['directory_path']
        for name in os.listdir(folder):
            self.assertTrue(name.startswith(os.path.basename(folder)), name)


if __name__ == '__main__':
    unittest.main()
