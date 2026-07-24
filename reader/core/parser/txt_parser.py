import re

from core.parser.language import detect_language
from core.parser.sections import (
    EXPLICIT_MARKER_THRESHOLD as _EXPLICIT_MARKER_THRESHOLD,
    HU_ORDINAL as _HU_ORDINAL,
    NUMBER_WORDS as _NUMBER_WORDS,
    is_explicit_section as _is_explicit_section,
    looks_like_lead_in_prose as _looks_like_lead_in_prose,
    split_front_matter as _split_front_matter,
)
_SKIP_SECTION_RE = re.compile(
    r'^(?:table\s+of\s+contents|contents|copyright\b|other\s+books\s+by\b|'
    r'tartalom(?:jegyzék)?\b)$',
    re.IGNORECASE,
)
_BACKMATTER_RE = re.compile(
    r'^(?:you\s+have\s+just\s+finished\s+reading\b|about\s+the\s+author\b|'
    r'acknowledgements?\b|a\s+szerzőről\b)',
    re.IGNORECASE,
)
_COPYRIGHT_RE = re.compile(
    r'\bcopyright\b|all rights reserved|licensed for your enjoyment only|'
    r'please buy an additional copy|'
    r'minden\s+jog\s+fenntartva',
    re.IGNORECASE,
)
_TOC_CHAPTER_RE = re.compile(
    rf'\b(?:chapter|fejezet)\s+(?:\d+|[ivxlcdm]+|{_NUMBER_WORDS})\b|'
    rf'\b(?:\d+|[ivxlcdm]+)\.?\s*fejezet\b|'
    rf'\b(?:{_HU_ORDINAL})\s+fejezet\b',
    re.IGNORECASE,
)
# Pure roman-numeral sub-section markers: I, II, III., XIV
_ROMAN_ONLY_RE = re.compile(r'^[IVXLCDM]+\.?$', re.IGNORECASE)
# Initials like "B. L." / "A. B. C."
_INITIALS_RE = re.compile(r'^(?:[A-Z]\.\s*)+[A-Z]\.?$')
# Dialogue / script speaker labels: "VERDIER:", "BALUKHIN :"
_SPEAKER_LABEL_RE = re.compile(r'^[A-Z][A-Z0-9 .\'-]{0,40}:\s*$')

def _is_all_caps_heading(line: str) -> bool:
    """Conservative all-caps heading heuristic for books without Chapter N labels."""
    line = (line or '').strip()
    if not line:
        return False
    if not (3 < len(line) < 80):
        return False
    if not line.isupper():
        return False
    # Speaker labels and short roman-numeral scene markers are not chapters.
    if line.endswith(':'):
        return False
    if _SPEAKER_LABEL_RE.match(line):
        return False
    if _ROMAN_ONLY_RE.match(line):
        return False
    if _INITIALS_RE.match(line):
        return False
    # Need at least one real word (2+ letters), not just punctuation/digits.
    if not re.search(r'[A-ZÁÉÍÓÖŐÚÜŰ]{2,}', line):
        return False
    return True


def _looks_like_heading(line: str, allow_all_caps: bool = True) -> bool:
    if _is_explicit_section(line):
        return True
    if allow_all_caps and _is_all_caps_heading(line):
        return True
    return False


def _section_disposition(title, content, started_story):
    """'drop' (junk), 'exclude' (keep hidden), or 'keep' (normal chapter).

    Front matter before the first chapter that is not flowing prose is
    preserved as an excluded section instead of being discarded, so nothing
    is lost — the reader's chapter editor can reveal, rename, or delete it.
    """
    title = (title or '').strip()
    content = (content or '').strip()
    lowered = content.lower()

    if not content:
        return 'drop'
    if _SKIP_SECTION_RE.match(title) or _BACKMATTER_RE.match(title):
        return 'drop'
    if _COPYRIGHT_RE.search(content):
        return 'drop'
    if 'table of contents' in lowered and len(_TOC_CHAPTER_RE.findall(content)) >= 3:
        return 'drop'
    if 'tartalom' in lowered and len(_TOC_CHAPTER_RE.findall(content)) >= 3:
        return 'drop'
    if (
        not started_story
        and not _is_explicit_section(title)
        and not _looks_like_lead_in_prose(content)
    ):
        return 'exclude'
    return 'keep'


def parse(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        raw = f.read()

    # Form-feed page breaks are common in plain-text book dumps.
    raw = raw.replace('\x0c', '\n')
    lines = raw.splitlines()

    # Try to extract title from first non-empty lines
    title = 'Unknown Title'
    author = 'Unknown Author'
    for line in lines[:20]:
        line = line.strip()
        if line and len(line) < 120:
            title = line
            break

    # Detect "by Author" pattern
    by_match = re.search(r'\bby\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', raw[:500])
    if by_match:
        author = by_match.group(1)

    # If the document already has several explicit Chapter/Fejezet markers,
    # ignore all-caps scene titles so they don't pollute the TOC.
    explicit_count = sum(1 for line in lines if _is_explicit_section(line))
    allow_all_caps = explicit_count < _EXPLICIT_MARKER_THRESHOLD

    chapters = []
    current_title = title
    current_lines = []
    order = 0
    started_story = False

    def _append(ch_title, content, excluded):
        nonlocal order, started_story
        floor = 40 if excluded else 100
        if len(content) <= floor:
            return
        chapters.append({
            'title': ch_title,
            'order_num': order,
            'content': content,
            'word_count': len(content.split()),
            'excluded': excluded,
            'section_type': 'frontmatter' if excluded else None,
        })
        order += 1
        if not excluded:
            started_story = True

    def _emit(ch_title, content, is_front_matter=False):
        disp = _section_disposition(ch_title, content, started_story)
        if disp == 'drop':
            return
        if is_front_matter:
            # The block before the very first heading: split a leading
            # title-page/credits block off (excluded) from the motto/intro.
            for seg_text, seg_excl in _split_front_matter(content):
                _append('Bevezető' if not seg_excl else ch_title, seg_text, seg_excl)
            return
        _append(ch_title, content, disp == 'exclude')

    seen_heading = False
    for line in lines:
        stripped = line.strip()
        if _BACKMATTER_RE.match(stripped):
            break
        if _looks_like_heading(stripped, allow_all_caps=allow_all_caps):
            _emit(current_title, '\n'.join(current_lines).strip(),
                  is_front_matter=not seen_heading)
            seen_heading = True
            current_title = stripped
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        _emit(current_title, '\n'.join(current_lines).strip())

    if not chapters:
        chapters = [{
            'title': title,
            'order_num': 0,
            'content': raw.strip(),
            'word_count': len(raw.split()),
        }]

    return {
        'title': title,
        'author': author,
        'language': detect_language(raw),
        'cover_b64': None,
        'chapters': chapters,
    }
