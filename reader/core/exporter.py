"""
Audio + subtitle exporter.

Modes:
  - chapter_folder : numbered, per-chapter files in a book folder
  - chapter_zip    : zip of per-chapter audio + subtitle files
  - single         : one chapter audio + subtitle

Audio formats:
  - mp3  (via pydub + ffmpeg if available)
  - wav  (always available, soundfile)

Subtitle formats:
  - ass  (Advanced SubStation Alpha — per-character colours/styles)
  - srt  (plain SubRip — universal)
"""

import io
import os
import re
import subprocess
import zipfile
import logging
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
EXPORTS_DIR = str(Path(__file__).resolve().parent.parent / 'exports')
os.makedirs(EXPORTS_DIR, exist_ok=True)


def book_export_dir(book_title: str, author: str | None = None) -> str:
    """Per-book output folder: ``exports/<Author>/<Title>`` (created).

    Unknown/empty authors collapse to ``exports/<Title>`` so the tree stays
    clean for books without metadata.
    """
    parts = [EXPORTS_DIR]
    author_safe = _safe_name(author) if str(author or '').strip() else ''
    if author_safe and author_safe.lower() != 'unknown':
        parts.append(author_safe)
    parts.append(_safe_name(book_title))
    path = os.path.join(*parts)
    os.makedirs(path, exist_ok=True)
    return path

DEFAULT_SEGMENT_PAUSE_SEC = 0.35
DIALOGUE_TURN_PAUSE_SEC = 0.55
ELLIPSIS_PAUSE_SEC = 1.5
# Silence inserted between two chapters when they are joined into one file.
# Longer than any in-chapter pause so the chapter break stays audible.
CHAPTER_BREAK_PAUSE_SEC = 2.0

# Joined output: how many files a book may be split into.
MAX_PART_COUNT = 4


# ── Audio option resolution ───────────────────────────────────────────────────
#
# Everything below is user-configurable in Settings → Export Audio. Callers may
# pass an explicit dict; when they pass nothing the values come from the saved
# settings, so a change on the settings page takes effect on the next export
# without restarting the app.

# libmp3lame VBR quality (-q:a). 0 = best/largest … 9 = smallest.
DEFAULT_MP3_VBR_QUALITY = 7
# Constant bitrate in kbps, used when mode == 'cbr'.
DEFAULT_MP3_BITRATE_KBPS = 48

# Narration source is 24 kHz mono, so MPEG-2 Layer III applies: whatever is
# requested above 160 kbps is silently clamped by the encoder anyway.
MAX_MP3_BITRATE_KBPS = 160


def default_audio_options() -> dict:
    """Encoder + pause defaults, independent of the settings file."""
    return {
        'mp3_mode': 'vbr',
        'mp3_vbr_quality': DEFAULT_MP3_VBR_QUALITY,
        'mp3_bitrate': DEFAULT_MP3_BITRATE_KBPS,
        'pause_segment': DEFAULT_SEGMENT_PAUSE_SEC,
        'pause_dialogue': DIALOGUE_TURN_PAUSE_SEC,
        'pause_ellipsis': ELLIPSIS_PAUSE_SEC,
        'pause_chapter': CHAPTER_BREAK_PAUSE_SEC,
    }


def audio_options(overrides: dict | None = None) -> dict:
    """Resolve export audio options: defaults ← saved settings ← overrides.

    Resolved once per export call and then passed down, so a whole chapter is
    encoded and timed with one consistent set of values even if the user
    changes a setting mid-export.
    """
    opts = default_audio_options()
    try:
        from core import settings as _app_settings
        saved = _app_settings.load()
    except Exception:                                    # settings are optional
        saved = {}
    for key, saved_key in (
        ('mp3_mode', 'mp3_mode'),
        ('mp3_vbr_quality', 'mp3_vbr_quality'),
        ('mp3_bitrate', 'mp3_bitrate'),
        ('pause_segment', 'export_pause_segment'),
        ('pause_dialogue', 'export_pause_dialogue'),
        ('pause_ellipsis', 'export_pause_ellipsis'),
        ('pause_chapter', 'export_pause_chapter'),
    ):
        if saved.get(saved_key) is not None:
            opts[key] = saved[saved_key]
    for key, value in (overrides or {}).items():
        if value is not None:
            opts[key] = value

    mode = str(opts.get('mp3_mode') or 'vbr').strip().lower()
    opts['mp3_mode'] = mode if mode in ('vbr', 'cbr') else 'vbr'
    opts['mp3_vbr_quality'] = _clamp_int(
        opts.get('mp3_vbr_quality'), DEFAULT_MP3_VBR_QUALITY, 0, 9)
    opts['mp3_bitrate'] = _clamp_int(
        opts.get('mp3_bitrate'), DEFAULT_MP3_BITRATE_KBPS, 8, MAX_MP3_BITRATE_KBPS)
    for key, fallback in (
        ('pause_segment', DEFAULT_SEGMENT_PAUSE_SEC),
        ('pause_dialogue', DIALOGUE_TURN_PAUSE_SEC),
        ('pause_ellipsis', ELLIPSIS_PAUSE_SEC),
    ):
        opts[key] = _clamp_float(opts.get(key), fallback, 0.0, 5.0)
    # A chapter break may legitimately be longer than an in-sentence pause.
    opts['pause_chapter'] = _clamp_float(
        opts.get('pause_chapter'), CHAPTER_BREAK_PAUSE_SEC, 0.0, 10.0)
    return opts


def _clamp_int(value, fallback: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return fallback


def _clamp_float(value, fallback: float, low: float, high: float) -> float:
    try:
        return max(low, min(float(value), high))
    except (TypeError, ValueError):
        return fallback


def estimated_mp3_kbps(opts: dict | None = None) -> int:
    """Rough average bitrate of the produced MP3, for size estimates in the UI.

    VBR averages are measured on 24 kHz mono narration; real speech with its
    inter-segment silence usually lands a little below these numbers.
    """
    opts = opts or audio_options()
    if opts['mp3_mode'] == 'cbr':
        return int(opts['mp3_bitrate'])
    return {0: 96, 1: 88, 2: 80, 3: 70, 4: 62, 5: 55,
            6: 50, 7: 44, 8: 43, 9: 34}.get(int(opts['mp3_vbr_quality']), 44)


# ── ffmpeg / pydub detection ──────────────────────────────────────────────────

def _ffmpeg_available() -> bool:
    return shutil.which('ffmpeg') is not None


def _mp3_encoder_args(opts: dict) -> list[str]:
    """ffmpeg arguments for the configured MP3 mode."""
    if opts['mp3_mode'] == 'cbr':
        return ['-c:a', 'libmp3lame', '-b:a', f"{opts['mp3_bitrate']}k"]
    return ['-c:a', 'libmp3lame', '-q:a', str(opts['mp3_vbr_quality'])]


def encode_wav_file(wav_path: str, out_path: str, opts: dict | None = None) -> bool:
    """Encode a WAV file to MP3 on disk, streaming through ffmpeg.

    Used for joined parts, which can be many hours long: unlike the pydub
    path, this never holds the decoded audio in memory.
    """
    if not _ffmpeg_available():
        return False
    opts = opts or audio_options()
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', wav_path]
    cmd += _mp3_encoder_args(opts)
    cmd += [out_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        log.warning(f'MP3 encoding failed to start: {e}')
        return False
    if result.returncode != 0:
        log.warning(f'MP3 encoding failed: {result.stderr.strip()[:400]}')
        return False
    return True


def _wav_to_mp3_bytes(wav_path: str, opts: dict | None = None) -> bytes | None:
    """Encode the merged chapter WAV to MP3 with the configured settings.

    The source is 24 kHz mono speech, so the useful range is far below music
    bitrates. VBR is the default because the inter-segment silences cost almost
    nothing, while CBR pays full price for them.
    """
    if not _ffmpeg_available():
        return None
    opts = opts or audio_options()
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_wav(wav_path)
        buf = io.BytesIO()
        if opts['mp3_mode'] == 'cbr':
            seg.export(buf, format='mp3', bitrate=f"{opts['mp3_bitrate']}k")
        else:
            seg.export(buf, format='mp3',
                       parameters=['-q:a', str(opts['mp3_vbr_quality'])])
        return buf.getvalue()
    except Exception as e:
        log.warning(f'MP3 conversion failed: {e}')
        return None


# ── Time formatting ───────────────────────────────────────────────────────────

def _fmt_ass(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f'{h}:{m:02d}:{s:05.2f}'


def _fmt_srt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


# ── ASS subtitle builder ──────────────────────────────────────────────────────

_ASS_HEADER = """\
[Script Info]
Title: {title}
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Narrator,Arial,28,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1
{char_styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{events}"""

_ASS_CHAR_STYLE = (
    'Style: {name},Arial,28,&H00{color},&H000000FF,&H00000000,'
    '&H80000000,0,-1,0,0,100,100,0,0,1,2,1,2,10,10,30,1'
)


def _hex_to_ass(hex_color: str) -> str:
    """Convert #RRGGBB → AABBGGRR (ASS BGR order, alpha=00)."""
    h = hex_color.lstrip('#')
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f'{b}{g}{r}'.upper()


def build_ass(segments: list[dict], character_colors: dict, title: str) -> str:
    char_styles = []
    seen = set()
    for name, color in character_colors.items():
        safe = name.replace(' ', '_')
        if safe in seen:
            continue
        seen.add(safe)
        char_styles.append(_ASS_CHAR_STYLE.format(
            name=safe, color=_hex_to_ass(color)
        ))

    events = []
    for seg in segments:
        start = _fmt_ass(seg['t_start'])
        end = _fmt_ass(seg['t_end'])
        char = seg.get('character_name') or 'Narrator'
        style = char.replace(' ', '_') if char in character_colors else 'Narrator'
        text = seg['text'].replace('\n', '\\N')
        if seg.get('is_dialogue'):
            text = '{\\i1}' + text + '{\\i0}'
        events.append(
            f'Dialogue: 0,{start},{end},{style},{char},0000,0000,0000,,{text}'
        )

    return _ASS_HEADER.format(
        title=title,
        char_styles='\n'.join(char_styles),
        events='\n'.join(events),
    )


def build_srt(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _fmt_srt(seg['t_start'])
        end = _fmt_srt(seg['t_end'])
        lines.append(f'{i}\n{start} --> {end}\n{seg["text"]}\n')
    return '\n'.join(lines)


# ── Audio merge ───────────────────────────────────────────────────────────────

def pause_after_segment(
    segment: dict,
    next_segment: dict | None = None,
    opts: dict | None = None,
) -> float:
    """Return the spoken-program pause after a segment.

    Ellipses represent an intentional trailing-off pause. Consecutive dialogue
    segments get a slightly longer beat so a new voice does not cut in
    unnaturally fast. All three lengths are configurable in Settings.
    """
    opts = opts or default_audio_options()
    text = str(segment.get('text') or '').rstrip()
    text = text.rstrip('"\'”’»').rstrip()
    if text.endswith(('...', '…')):
        return opts['pause_ellipsis']
    if (
        next_segment
        and segment.get('is_dialogue')
        and next_segment.get('is_dialogue')
    ):
        return opts['pause_dialogue']
    return opts['pause_segment']


def _merge_wavs(segments: list[dict], opts: dict | None = None) -> np.ndarray:
    arrays = []
    playable = [
        seg
        for seg in segments
        if seg.get('audio_path') and os.path.exists(seg['audio_path'])
    ]
    for idx, seg in enumerate(playable):
        p = seg['audio_path']
        if p and os.path.exists(p):
            data, _ = sf.read(p)
            if data.ndim > 1:
                data = data.mean(axis=1)
            arrays.append(data)
            if idx + 1 < len(playable):
                pause = pause_after_segment(seg, playable[idx + 1], opts)
                arrays.append(np.zeros(int(SAMPLE_RATE * pause)))
    return np.concatenate(arrays) if arrays else np.zeros(SAMPLE_RATE)


# ── Segment timeline builder ──────────────────────────────────────────────────

def build_timeline(segments_db: list[dict], opts: dict | None = None) -> list[dict]:
    """
    segments_db: rows from tts_segments with audio_path + duration_sec.
    Returns same list enriched with t_start / t_end fields.

    Uses the same resolved options as the merge, so subtitle timings never
    drift from the audio.
    """
    timeline = []
    cursor = 0.0
    for idx, seg in enumerate(segments_db):
        dur = seg.get('duration_sec') or 0.0
        timeline.append({**seg, 't_start': cursor, 't_end': cursor + dur})
        next_seg = segments_db[idx + 1] if idx + 1 < len(segments_db) else None
        cursor += dur
        if next_seg is not None:
            cursor += pause_after_segment(seg, next_seg, opts)
    return timeline


# ── Chapter rendering ─────────────────────────────────────────────────────────

def render_chapter_wav(
    segments: list[dict],
    output_dir: str,
    file_stem: str,
    opts: dict | None = None,
) -> dict:
    """Write one chapter's merged WAV and return it with its segment timeline.

    Shared by the per-chapter export and the joined export, so both produce
    bit-identical audio and identical subtitle timings.
    """
    os.makedirs(output_dir, exist_ok=True)
    opts = audio_options(opts)
    timeline = build_timeline(segments, opts)
    merged = _merge_wavs(timeline, opts)
    wav_path = os.path.join(output_dir, f'{_safe_name(file_stem)}.wav')
    sf.write(wav_path, merged, SAMPLE_RATE)
    return {
        'wav_path': wav_path,
        'timeline': timeline,
        'duration_sec': len(merged) / SAMPLE_RATE,
    }


# ── Joining chapters into parts ───────────────────────────────────────────────

def split_chapters_into_parts(
    durations: list[float],
    part_count: int,
) -> list[list[int]]:
    """Split chapters into ``part_count`` contiguous groups of similar length.

    Chapters keep their reading order — only the cut points are chosen, so the
    listener never gets a shuffled book. The cuts minimise the longest part,
    which is what makes 2–4 files feel like equal halves/quarters rather than
    one huge file plus scraps.
    """
    n = len(durations)
    if n == 0:
        return []
    k = max(1, min(int(part_count or 1), MAX_PART_COUNT, n))
    if k == 1:
        return [list(range(n))]

    # prefix[i] = total duration of the first i chapters
    prefix = [0.0]
    for d in durations:
        prefix.append(prefix[-1] + max(0.0, float(d or 0.0)))

    # best[j][i] = smallest achievable "longest part" when the first i chapters
    # are split into j parts; cut[j][i] remembers where the last part started.
    inf = float('inf')
    best = [[inf] * (n + 1) for _ in range(k + 1)]
    cut = [[0] * (n + 1) for _ in range(k + 1)]
    best[0][0] = 0.0
    for j in range(1, k + 1):
        for i in range(j, n + 1):           # each part needs at least 1 chapter
            for start in range(j - 1, i):
                candidate = max(best[j - 1][start], prefix[i] - prefix[start])
                if candidate < best[j][i]:
                    best[j][i] = candidate
                    cut[j][i] = start

    bounds = [n]
    for j in range(k, 0, -1):
        bounds.append(cut[j][bounds[-1]])
    bounds.reverse()
    return [list(range(bounds[j], bounds[j + 1])) for j in range(k)]


def part_file_stem(book_title: str, index: int, total: int) -> str:
    """``Book`` for a single file, ``Book_part1`` … when split."""
    safe = _safe_name(book_title)
    return safe if total <= 1 else f'{safe}_part{index}'


def concat_wavs(
    wav_paths: list[str],
    out_path: str,
    gap_sec: float = CHAPTER_BREAK_PAUSE_SEC,
) -> list[float]:
    """Join WAVs into one file with a silent gap between them.

    Streams block by block, so a many-hour part never has to fit in memory.
    Returns each input's start offset in seconds, measured from the real sample
    counts written — that is what keeps the subtitles in sync no matter how the
    individual chapter lengths round.
    """
    gap_samples = int(SAMPLE_RATE * max(0.0, float(gap_sec)))
    silence = np.zeros(gap_samples, dtype=np.float32)
    offsets: list[float] = []
    written = 0

    with sf.SoundFile(out_path, 'w', samplerate=SAMPLE_RATE,
                      channels=1, subtype='PCM_16') as out:
        for idx, path in enumerate(wav_paths):
            if idx and gap_samples:
                out.write(silence)
                written += gap_samples
            offsets.append(written / SAMPLE_RATE)
            with sf.SoundFile(path, 'r') as src:
                for block in src.blocks(blocksize=SAMPLE_RATE * 30, dtype='float32'):
                    if block.ndim > 1:
                        block = block.mean(axis=1)
                    out.write(block)
                    written += len(block)
    return offsets


def shift_timeline(timeline: list[dict], offset_sec: float) -> list[dict]:
    """Move a chapter's subtitle timings to their position inside a part."""
    return [
        {**seg,
         't_start': seg['t_start'] + offset_sec,
         't_end': seg['t_end'] + offset_sec}
        for seg in timeline
    ]


def export_joined_part(
    book_title: str,
    part: list[dict],
    character_colors: dict,
    output_dir: str,
    file_stem: str,
    audio_fmt: str = 'mp3',
    sub_fmt: str = 'ass',
    opts: dict | None = None,
) -> dict:
    """Join already-rendered chapter WAVs into one audio file plus subtitles.

    ``part`` is an ordered list of ``{'chapter_title', 'wav_path', 'timeline'}``
    as returned by :func:`render_chapter_wav`. The chapter WAVs are joined
    losslessly and encoded exactly once, so a joined MP3 is not a re-compressed
    copy of the per-chapter MP3s.
    """
    opts = audio_options(opts)
    os.makedirs(output_dir, exist_ok=True)
    safe_stem = _safe_name(file_stem)
    wav_path = os.path.join(output_dir, f'{safe_stem}.wav')

    offsets = concat_wavs(
        [ch['wav_path'] for ch in part], wav_path, opts['pause_chapter'])

    combined: list[dict] = []
    for offset, chapter in zip(offsets, part):
        combined.extend(shift_timeline(chapter['timeline'], offset))

    out_audio = wav_path
    actual_fmt = 'wav'
    if audio_fmt == 'mp3':
        mp3_path = os.path.join(output_dir, f'{safe_stem}.mp3')
        if encode_wav_file(wav_path, mp3_path, opts):
            out_audio = mp3_path
            actual_fmt = 'mp3'
            os.remove(wav_path)

    sub_content = (
        build_ass(combined, character_colors, book_title)
        if sub_fmt == 'ass'
        else build_srt(combined)
    )
    sub_ext = 'ass' if sub_fmt == 'ass' else 'srt'
    sub_path = os.path.join(output_dir, f'{safe_stem}.{sub_ext}')
    with open(sub_path, 'w', encoding='utf-8') as f:
        f.write(sub_content)

    return {
        'audio_path': out_audio,
        'subtitle_path': sub_path,
        'audio_fmt': actual_fmt,
        'sub_fmt': sub_ext,
        'chapter_count': len(part),
        'duration_sec': (offsets[-1] + part[-1]['timeline'][-1]['t_end']
                         if offsets and part[-1]['timeline'] else 0.0),
    }


# ── Public export functions ───────────────────────────────────────────────────

def export_single_chapter(
    chapter_title: str,
    book_title: str,
    segments: list[dict],
    character_colors: dict,
    audio_fmt: str = 'wav',
    sub_fmt: str = 'ass',
    output_dir: str | None = None,
    file_stem: str | None = None,
    author: str | None = None,
    opts: dict | None = None,
) -> dict:
    """Returns {'audio_path': ..., 'subtitle_path': ..., 'audio_fmt': ..., 'sub_fmt': ...}"""
    output_dir = output_dir or book_export_dir(book_title, author)
    safe_title = _safe_name(file_stem or chapter_title)
    # Idempotent: an already-resolved dict passed by the caller wins over the
    # saved settings, so a whole book export keeps one consistent set.
    opts = audio_options(opts)
    rendered = render_chapter_wav(segments, output_dir, safe_title, opts)
    timeline = rendered['timeline']
    wav_path = rendered['wav_path']

    out_audio = wav_path
    actual_fmt = 'wav'
    if audio_fmt == 'mp3':
        mp3 = _wav_to_mp3_bytes(wav_path, opts)
        if mp3:
            out_audio = wav_path.replace('.wav', '.mp3')
            with open(out_audio, 'wb') as f:
                f.write(mp3)
            actual_fmt = 'mp3'
            os.remove(wav_path)

    sub_content = (
        build_ass(timeline, character_colors, f'{book_title} — {chapter_title}')
        if sub_fmt == 'ass'
        else build_srt(timeline)
    )
    sub_ext = 'ass' if sub_fmt == 'ass' else 'srt'
    sub_path = os.path.join(output_dir, f'{safe_title}.{sub_ext}')
    with open(sub_path, 'w', encoding='utf-8') as f:
        f.write(sub_content)

    return {'audio_path': out_audio, 'subtitle_path': sub_path,
            'audio_fmt': actual_fmt, 'sub_fmt': sub_ext}


def export_chapter_zip(
    book_title: str,
    chapters_data: list[dict],
    character_colors: dict,
    audio_fmt: str = 'wav',
    sub_fmt: str = 'ass',
    author: str | None = None,
    opts: dict | None = None,
) -> str:
    """chapters_data: list of {chapter_title, segments}. Returns zip file path."""
    safe_book = _safe_name(book_title)
    zip_path = os.path.join(book_export_dir(book_title, author), f'{safe_book}_chapters.zip')
    opts = audio_options(opts)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for ch in chapters_data:
            result = export_single_chapter(
                ch['chapter_title'], book_title, ch['segments'],
                character_colors, audio_fmt, sub_fmt, author=author, opts=opts,
            )
            ch_safe = _safe_name(ch['chapter_title'])
            ext = result['audio_fmt']
            zf.write(result['audio_path'], f'{ch_safe}.{ext}')
            zf.write(result['subtitle_path'], f'{ch_safe}.{result["sub_fmt"]}')

    return zip_path


def chapter_number_width(chapters_data: list[dict]) -> int:
    """Zero-pad width for numbered chapter file names."""
    max_number = max(
        (int(ch.get('chapter_number', 0)) for ch in chapters_data),
        default=len(chapters_data),
    )
    return max(2, len(str(max_number)))


def chapter_file_stem(number: int, title: str, number_width: int) -> str:
    return f'{number:0{number_width}d}_{_safe_name(title)}'


def export_chapter_folder(
    book_title: str,
    chapters_data: list[dict],
    character_colors: dict,
    audio_fmt: str = 'wav',
    sub_fmt: str = 'ass',
    author: str | None = None,
    opts: dict | None = None,
) -> dict:
    """Write numbered chapter files beneath ``exports/<Author>/<Title>``."""
    output_dir = book_export_dir(book_title, author)
    number_width = chapter_number_width(chapters_data)
    opts = audio_options(opts)
    files = []

    for fallback_number, chapter in enumerate(chapters_data, 1):
        number = int(chapter.get('chapter_number') or fallback_number)
        title = chapter['chapter_title']
        stem = chapter_file_stem(number, title, number_width)
        files.append(export_single_chapter(
            title,
            book_title,
            chapter['segments'],
            character_colors,
            audio_fmt,
            sub_fmt,
            output_dir=output_dir,
            file_stem=stem,
            opts=opts,
        ))

    return {'directory_path': output_dir, 'chapters': files}


def parse_chapter_selection(selection: str | None, chapter_count: int) -> list[int]:
    """Parse print-style chapter numbers such as ``1,3,5-8``."""
    if chapter_count < 1:
        raise ValueError('This book has no chapters to export.')

    value = (selection or '').strip().lower()
    if value in ('', '*', 'all', 'mind', 'összes'):
        return list(range(1, chapter_count + 1))

    chosen: set[int] = set()
    for item in value.split(','):
        item = item.strip()
        if not item:
            raise ValueError('Empty item in chapter selection.')
        match = re.fullmatch(r'(\d+)\s*-\s*(\d+)', item)
        if match:
            start, end = (int(part) for part in match.groups())
            if start > end:
                raise ValueError(f'Invalid descending chapter range: {item}')
            chosen.update(range(start, end + 1))
        elif item.isdigit():
            chosen.add(int(item))
        else:
            raise ValueError(
                'Use chapter numbers, commas and ranges, for example: 1,3,5-8.'
            )

    invalid = sorted(number for number in chosen if not 1 <= number <= chapter_count)
    if invalid:
        raise ValueError(
            f'Chapter number out of range: {invalid[0]} '
            f'(valid range: 1-{chapter_count}).'
        )
    return sorted(chosen)


def _safe_name(name: str) -> str:
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:80] or 'export'
