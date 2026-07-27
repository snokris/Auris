"""Word-timestamp based splitting of coalesced TTS audio.

When consecutive same-voice segments are merged into one synthesis call
(``tts_coalesce_chars``), the combined waveform must be cut back into
per-segment clips. Proportional character-weight cuts drift with speaking
rate, which clips segment endings, leaks neighboring word fragments across
boundaries, and can swallow very short segments entirely.

This module places each cut inside the pause between the last word of one
segment and the first word of the next, using word-level timestamps from
the Whisper ASR pipeline that ships with OmniVoice.

Every entry point degrades gracefully: on any failure it returns ``None``
and the caller falls back to character-weight splitting.
"""

from __future__ import annotations

import logging
import re

import numpy as np

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Whisper language hints for the languages Auris Studio detects. Anything else is
# left to Whisper's own language detection.
_WHISPER_LANGUAGES = {
    "hu": "hungarian",
    "hun": "hungarian",
    "en": "english",
    "eng": "english",
}


def _norm_words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _word_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    import difflib

    return difflib.SequenceMatcher(None, a, b).ratio()


_GAP_PENALTY = -0.45


def _align_word_sequences(
    expected: list[str], recognized: list[str]
) -> list[int | None]:
    """Needleman–Wunsch alignment of recognized words onto expected words.

    Returns, for every recognized word, the expected word index it maps to
    (``None`` for insertions). Robust to the ASR merging or splitting words
    ("végigsétált" → "végig sétált", "Ki jár ott" → "Kiárót").
    """
    n, m = len(expected), len(recognized)
    score = np.zeros((n + 1, m + 1), dtype=np.float32)
    trace = np.zeros((n + 1, m + 1), dtype=np.int8)  # 0=diag 1=up 2=left
    score[:, 0] = np.arange(n + 1, dtype=np.float32) * _GAP_PENALTY
    score[0, :] = np.arange(m + 1, dtype=np.float32) * _GAP_PENALTY
    trace[1:, 0] = 1
    trace[0, 1:] = 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = score[i - 1, j - 1] + (
                _word_similarity(expected[i - 1], recognized[j - 1]) * 2.0 - 1.0
            )
            up = score[i - 1, j] + _GAP_PENALTY
            left = score[i, j - 1] + _GAP_PENALTY
            best = max(diag, up, left)
            score[i, j] = best
            trace[i, j] = 0 if best == diag else (1 if best == up else 2)

    mapping: list[int | None] = [None] * m
    i, j = n, m
    while i > 0 or j > 0:
        step = trace[i, j]
        if i > 0 and j > 0 and step == 0:
            mapping[j - 1] = i - 1
            i -= 1
            j -= 1
        elif i > 0 and (j == 0 or step == 1):
            i -= 1
        else:
            j -= 1
    return mapping


def _nearest_zero_crossing(audio: np.ndarray, index: int, window: int) -> int:
    """Move ``index`` to the closest zero crossing within ±``window`` samples.

    Cutting at non-zero amplitude produces an audible click; a zero
    crossing makes the seam silent.
    """
    n = int(audio.shape[0])
    index = min(max(index, 1), n - 1)
    lo = max(1, index - window)
    hi = min(n - 1, index + window)
    if hi <= lo:
        return index
    section = audio[lo - 1 : hi + 1]
    flips = np.nonzero(np.signbit(section[:-1]) != np.signbit(section[1:]))[0]
    if flips.size == 0:
        return index
    candidates = flips + lo
    return int(candidates[np.argmin(np.abs(candidates - index))])


def declick_clips(
    clips: list[np.ndarray], sample_rate: int, fade_ms: float = 6.0
) -> list[np.ndarray]:
    """Apply short raised-cosine fades on every cut edge to remove clicks.

    The first clip keeps its natural start and the last keeps its natural
    end; only the artificial seams between clips are faded.
    """
    fade = max(8, int(sample_rate * fade_ms / 1000.0))
    ramp_in = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, fade, dtype=np.float32))
    ramp_out = ramp_in[::-1]
    out: list[np.ndarray] = []
    last = len(clips) - 1
    for i, clip in enumerate(clips):
        clip = np.array(clip, dtype=np.float32, copy=True)
        if i > 0 and clip.shape[0] > fade:
            clip[:fade] *= ramp_in
        if i < last and clip.shape[0] > fade:
            clip[-fade:] *= ramp_out
        out.append(clip)
    return out


def get_word_timestamps(
    asr_pipe,
    audio: np.ndarray,
    sample_rate: int,
    language: str | None = None,
) -> list[tuple[float, float, str]]:
    """Return ``(start_sec, end_sec, word)`` tuples for the waveform.

    Uses chunked long-form decoding so units longer than Whisper's 30 s
    window are handled correctly.
    """
    payload = {
        "array": np.asarray(audio, dtype=np.float32),
        "sampling_rate": int(sample_rate),
    }
    kwargs: dict = {"return_timestamps": "word", "chunk_length_s": 30}
    hint = _WHISPER_LANGUAGES.get(str(language or "").strip().lower())
    if hint:
        kwargs["generate_kwargs"] = {"language": hint}
    result = asr_pipe(payload, **kwargs)
    words: list[tuple[float, float, str]] = []
    for chunk in result.get("chunks") or []:
        start, end = chunk.get("timestamp") or (None, None)
        if start is None:
            continue
        if end is None:
            end = start
        text = (chunk.get("text") or "").strip()
        if text:
            words.append((float(start), float(end), text))
    return words


def split_by_word_alignment(
    audio: np.ndarray,
    sample_rate: int,
    member_texts: list[str],
    asr_pipe,
    language: str | None = None,
) -> list[np.ndarray] | None:
    """Split ``audio`` into one clip per member text, cutting between words.

    Member boundaries are mapped into the recognized word sequence: exactly
    when the ASR word count matches the expected count, proportionally
    otherwise (robust to ASR merges/splits). Each cut lands at the midpoint
    of the inter-word gap, i.e. in silence.

    Returns ``None`` whenever the alignment cannot be trusted, so the
    caller can fall back to character-weight splitting.
    """
    n = int(audio.shape[0])
    if len(member_texts) <= 1 or n <= 0:
        return None
    try:
        words = get_word_timestamps(asr_pipe, audio, sample_rate, language)
    except Exception as exc:
        log.warning("Word-timestamp ASR failed (%s); falling back.", exc)
        return None
    if len(words) < len(member_texts):
        return None

    # Which member owns each expected word.
    expected_words: list[str] = []
    owner_of_expected: list[int] = []
    for member_index, text in enumerate(member_texts):
        member_words = _norm_words(text) or [""]
        expected_words.extend(member_words)
        owner_of_expected.extend([member_index] * len(member_words))

    recognized_words = [w[2].lower() for w in words]
    mapping = _align_word_sequences(expected_words, recognized_words)
    n_words = len(words)

    # Which member owns each recognized word; fill unmatched insertions
    # from the previous known owner (then from the next).
    owner_of_recognized: list[int | None] = [
        owner_of_expected[e] if e is not None else None for e in mapping
    ]
    last_seen: int | None = None
    for idx, owner in enumerate(owner_of_recognized):
        if owner is None:
            owner_of_recognized[idx] = last_seen
        else:
            last_seen = owner
    next_seen: int | None = None
    for idx in range(n_words - 1, -1, -1):
        if owner_of_recognized[idx] is None:
            owner_of_recognized[idx] = next_seen
        else:
            next_seen = owner_of_recognized[idx]
    if any(owner is None for owner in owner_of_recognized):
        return None

    # Cut BEFORE the first recognized word of each subsequent member.
    cuts_at_word: list[int] = []
    for boundary in range(len(member_texts) - 1):
        k = next(
            (
                idx
                for idx, owner in enumerate(owner_of_recognized)
                if owner is not None and owner > boundary
            ),
            None,
        )
        if k is None:
            return None
        cuts_at_word.append(min(max(k, 1), n_words - 1))
    for i in range(1, len(cuts_at_word)):
        if cuts_at_word[i] <= cuts_at_word[i - 1]:
            cuts_at_word[i] = cuts_at_word[i - 1] + 1
    if cuts_at_word and cuts_at_word[-1] > n_words - 1:
        return None

    duration = n / float(sample_rate)
    cut_samples: list[int] = []
    for k in cuts_at_word:
        prev_end = min(words[k - 1][1], duration)
        next_start = max(words[k][0], prev_end)
        midpoint = (prev_end + next_start) / 2.0
        cut = _nearest_zero_crossing(
            audio, int(midpoint * sample_rate), int(0.010 * sample_rate)
        )
        cut_samples.append(cut)

    clips: list[np.ndarray] = []
    previous = 0
    for cut in cut_samples:
        cut = min(max(cut, previous + 1), n - 1)
        clips.append(audio[previous:cut])
        previous = cut
    clips.append(audio[previous:])
    if len(clips) != len(member_texts) or any(c.shape[0] <= 0 for c in clips):
        return None
    return clips
