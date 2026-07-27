import io
import json
import os
import struct
import tempfile
import unittest
import zipfile

import app as app_module
from core import database, voice_preset_file


def make_wav(seconds: float = 0.25, sample_rate: int = 24000) -> bytes:
    """A minimal but genuine 16-bit mono PCM WAV."""
    frames = int(seconds * sample_rate)
    data = b''.join(
        struct.pack('<h', (i * 137) % 6000 - 3000) for i in range(frames)
    )
    header = b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE'
    header += b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 1, sample_rate,
                                    sample_rate * 2, 2, 16)
    header += b'data' + struct.pack('<I', len(data))
    return header + data


class VoicePresetFileTest(unittest.TestCase):
    def test_round_trip_preserves_audio_and_transcript(self):
        wav = make_wav()
        blob = voice_preset_file.build_archive(
            'Kern', wav, ref_text='Ez a referenciaszöveg.',
            source_filename='/tmp/kern voice.wav',
        )
        payload = voice_preset_file.read_archive(blob)
        self.assertEqual(payload.name, 'Kern')
        self.assertEqual(payload.ref_text, 'Ez a referenciaszöveg.')
        self.assertEqual(payload.source_filename, 'kern voice.wav')
        self.assertEqual(payload.audio_bytes, wav)
        self.assertEqual(payload.sample_rate, 24000)
        self.assertAlmostEqual(payload.duration_sec, 0.25, places=2)

    def test_archive_layout_is_the_documented_one(self):
        blob = voice_preset_file.build_archive('Kern', make_wav(), ref_text='x')
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            self.assertEqual(sorted(zf.namelist()), ['audio.wav', 'meta.json'])
            meta = json.loads(zf.read('meta.json').decode('utf-8'))
        self.assertEqual(meta['format'], voice_preset_file.FORMAT_VERSION)
        self.assertEqual(meta['name'], 'Kern')
        self.assertIn('created_at', meta)

    def test_non_zip_input_is_rejected(self):
        with self.assertRaises(voice_preset_file.VoicePresetFileError):
            voice_preset_file.read_archive(b'not a zip at all')

    def test_empty_input_is_rejected(self):
        with self.assertRaises(voice_preset_file.VoicePresetFileError):
            voice_preset_file.read_archive(b'')

    def test_path_traversal_entries_are_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('meta.json', json.dumps({'format': 1, 'name': 'X'}))
            zf.writestr('../../../../etc/passwd', 'pwned')
        with self.assertRaises(voice_preset_file.VoicePresetFileError):
            voice_preset_file.read_archive(buffer.getvalue())

    def test_absolute_path_entry_is_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('meta.json', json.dumps({'format': 1, 'name': 'X'}))
            zf.writestr('/etc/cron.d/evil', 'pwned')
        with self.assertRaises(voice_preset_file.VoicePresetFileError):
            voice_preset_file.read_archive(buffer.getvalue())

    def test_extra_member_is_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('meta.json', json.dumps({'format': 1, 'name': 'X'}))
            zf.writestr('audio.wav', make_wav())
            zf.writestr('payload.sh', 'rm -rf /')
        with self.assertRaises(voice_preset_file.VoicePresetFileError):
            voice_preset_file.read_archive(buffer.getvalue())

    def test_zip_bomb_member_is_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('meta.json', b'{' + b' ' * (voice_preset_file.MAX_META_BYTES * 4))
            zf.writestr('audio.wav', make_wav())
        with self.assertRaises(voice_preset_file.VoicePresetFileError):
            voice_preset_file.read_archive(buffer.getvalue())

    def test_missing_audio_is_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('meta.json', json.dumps({'format': 1, 'name': 'X'}))
        with self.assertRaises(voice_preset_file.VoicePresetFileError):
            voice_preset_file.read_archive(buffer.getvalue())

    def test_audio_that_is_not_a_wav_is_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('meta.json', json.dumps({'format': 1, 'name': 'X'}))
            zf.writestr('audio.wav', b'MZ\x90\x00 this is an executable')
        with self.assertRaisesRegex(voice_preset_file.VoicePresetFileError, 'WAV'):
            voice_preset_file.read_archive(buffer.getvalue())

    def test_newer_format_version_is_refused_with_a_hint(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('meta.json', json.dumps({'format': 99, 'name': 'X'}))
            zf.writestr('audio.wav', make_wav())
        with self.assertRaisesRegex(voice_preset_file.VoicePresetFileError, 'newer'):
            voice_preset_file.read_archive(buffer.getvalue())

    def test_float_wav_is_accepted(self):
        wav = bytearray(make_wav())
        wav[20:22] = struct.pack('<H', 3)  # WAVE_FORMAT_IEEE_FLOAT
        probe = voice_preset_file.probe_wav(bytes(wav))
        self.assertIsNotNone(probe)
        self.assertEqual(probe['sample_rate'], 24000)

    def test_download_name_is_filesystem_safe(self):
        self.assertEqual(
            voice_preset_file.safe_download_name('Kern/András: "meleg"'),
            'Kern András meleg.aurisvoice',
        )
        self.assertTrue(
            voice_preset_file.safe_download_name('...').endswith('.aurisvoice')
        )
        self.assertNotIn('/', voice_preset_file.safe_download_name('a/b'))


class VoicePresetPortabilityApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        self.original_upload_dir = app_module.UPLOAD_DIR
        self.original_preset_dir = app_module.VOICE_PRESET_DIR
        self.original_startup = app_module._startup_complete
        database.DB_PATH = os.path.join(self.tmp.name, 'reader.db')
        app_module.UPLOAD_DIR = self.tmp.name
        app_module.VOICE_PRESET_DIR = os.path.join(self.tmp.name, 'presets')
        os.makedirs(app_module.VOICE_PRESET_DIR, exist_ok=True)
        app_module._startup_complete = True
        database.init_db()
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        app_module.UPLOAD_DIR = self.original_upload_dir
        app_module.VOICE_PRESET_DIR = self.original_preset_dir
        app_module._startup_complete = self.original_startup
        self.tmp.cleanup()

    def _make_preset(self, name='Kern', ref_text='Ez a referenciaszöveg.'):
        wav = make_wav()
        path = os.path.join(app_module.VOICE_PRESET_DIR, f'{name}.wav')
        with open(path, 'wb') as fh:
            fh.write(wav)
        with database.get_conn() as conn:
            cur = conn.execute(
                'INSERT INTO voice_presets (name, ref_audio_path, ref_audio_name, ref_text) '
                'VALUES (?, ?, ?, ?)',
                (name, path, f'{name.lower()}.wav', ref_text),
            )
            return cur.lastrowid, wav

    def test_export_then_import_reproduces_the_preset(self):
        preset_id, wav = self._make_preset()

        response = self.client.get(f'/api/voice-presets/{preset_id}/export')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Kern.aurisvoice', response.headers['Content-Disposition'])
        blob = response.data

        with database.get_conn() as conn:
            conn.execute('DELETE FROM voice_presets')

        response = self.client.post(
            '/api/voice-presets/import',
            data={'file': (io.BytesIO(blob), 'Kern.aurisvoice')},
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['name'], 'Kern')
        self.assertFalse(body['renamed'])

        with database.get_conn() as conn:
            row = conn.execute(
                'SELECT * FROM voice_presets WHERE id=?', (body['id'],)
            ).fetchone()
        self.assertEqual(row['ref_text'], 'Ez a referenciaszöveg.')
        self.assertEqual(row['ref_audio_name'], 'kern.wav')
        with open(row['ref_audio_path'], 'rb') as fh:
            self.assertEqual(fh.read(), wav)

    def test_import_of_a_taken_name_is_suffixed_not_rejected(self):
        preset_id, _ = self._make_preset()
        blob = self.client.get(f'/api/voice-presets/{preset_id}/export').data

        for expected in ('Kern (2)', 'Kern (3)'):
            response = self.client.post(
                '/api/voice-presets/import',
                data={'file': (io.BytesIO(blob), 'Kern.aurisvoice')},
                content_type='multipart/form-data',
            )
            self.assertEqual(response.status_code, 200)
            body = response.get_json()
            self.assertEqual(body['name'], expected)
            self.assertTrue(body['renamed'])

        listing = self.client.get('/api/voice-presets').get_json()
        self.assertEqual([p['name'] for p in listing], ['Kern', 'Kern (2)', 'Kern (3)'])

    def test_import_rejects_a_foreign_extension(self):
        response = self.client.post(
            '/api/voice-presets/import',
            data={'file': (io.BytesIO(b'PK\x03\x04'), 'voice.zip')},
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('.aurisvoice', response.get_json()['error'])

    def test_import_rejects_a_malicious_archive_without_touching_the_db(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('meta.json', json.dumps({'format': 1, 'name': 'Evil'}))
            zf.writestr('../../evil.sh', 'rm -rf /')
        response = self.client.post(
            '/api/voice-presets/import',
            data={'file': (buffer, 'evil.aurisvoice')},
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 400)
        with database.get_conn() as conn:
            count = conn.execute('SELECT COUNT(*) c FROM voice_presets').fetchone()['c']
        self.assertEqual(count, 0)
        self.assertEqual(os.listdir(app_module.VOICE_PRESET_DIR), [])

    def test_export_of_a_missing_preset_is_404(self):
        response = self.client.get('/api/voice-presets/999/export')
        self.assertEqual(response.status_code, 404)

    def test_export_reports_a_missing_wav_instead_of_crashing(self):
        preset_id, _ = self._make_preset()
        with database.get_conn() as conn:
            path = conn.execute(
                'SELECT ref_audio_path FROM voice_presets WHERE id=?', (preset_id,)
            ).fetchone()['ref_audio_path']
        os.remove(path)
        response = self.client.get(f'/api/voice-presets/{preset_id}/export')
        self.assertEqual(response.status_code, 410)

    def test_preset_without_transcript_still_round_trips(self):
        preset_id, wav = self._make_preset(name='Néma', ref_text=None)
        blob = self.client.get(f'/api/voice-presets/{preset_id}/export').data
        with database.get_conn() as conn:
            conn.execute('DELETE FROM voice_presets')
        body = self.client.post(
            '/api/voice-presets/import',
            data={'file': (io.BytesIO(blob), 'Néma.aurisvoice')},
            content_type='multipart/form-data',
        ).get_json()
        self.assertTrue(body['ok'])
        self.assertFalse(body['has_text'])
        self.assertEqual(body['name'], 'Néma')


if __name__ == '__main__':
    unittest.main()
