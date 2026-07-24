"""Shared section / chapter heading detection for text and PDF parsers."""

import re

# Shared number words for English chapter/part labels.
NUMBER_WORDS = (
    r'one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|'
    r'thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|'
    r'twenty(?:\s*-\s*\w+)?|thirty|forty|fifty|sixty|seventy|eighty|'
    r'ninety|hundred'
)

# Spelled-out Hungarian ordinals 1–99 ("ELSŐ", "MÁSODIK", … "HUSZONKETTEDIK").
# Vowel classes stay lenient (ő/o, á/a, ö/o) so PDF glyph damage still matches.
HU_ORDINAL = (
    r'(?:tizen|huszon|harminc|negyven|[öo]tven|hatvan|hetven|nyolcvan|kilencven)'
    r'(?:egyedik|kettedik|harmadik|negyedik|[öo]t[öo]dik|hatodik|hetedik|nyolcadik|kilencedik)'
    r'|els[őo]|m[áa]sodik|harmadik|negyedik|[öo]t[öo]dik|hatodik|hetedik|nyolcadik|kilencedik'
    r'|tizedik|huszadik|harmincadik|negyvenedik|[öo]tvenedik|hatvanadik|hetvenedik'
    r'|nyolcvanadik|kilencvenedik|sz[áa]zadik'
)

# Hungarian named front/back matter (lenient vowels for PDF glyph damage).
# Each maps to an English section type in ``core.structure``.
HU_NAMED_SECTIONS = (
    r'el[őo]sz[óo]'                     # Előszó   → foreword
    r'|ut[óo]sz[óo]'                    # Utószó   → afterword
    r'|bevezet[ée]s|bevezet[őo]'        # Bevezetés/Bevezető → introduction
    r'|pr[óo]l[óo]gus'                  # Prológus → prologue
    r'|epil[óo]gus'                     # Epilógus → epilogue
    r'|f[üu]ggel[ée]k'                  # Függelék → appendix
    r'|k[öo]sz[öo]netnyilv[áa]n[íi]t[áa]s'  # Köszönetnyilvánítás → afterword
    r'|a\s+szerz[őo]r[őo]l'             # A szerzőről → afterword
)

# Explicit section markers (English + Hungarian). High-confidence chapter boundaries.
SECTION_RE = re.compile(
    r'^(?:'
    # English: Chapter 1 / Ch. I / Chapter Twenty-one
    rf'(?:chapter|ch\.?)\s+(?:\d+|[ivxlcdm]+|{NUMBER_WORDS})\b'
    # English: Part 1 / Part II
    rf'|part\s+(?:\d+|[ivxlcdm]+|{NUMBER_WORDS})\b'
    # Named front/back matter (English + Hungarian)
    r'|prologue|epilogue|foreword|preface|introduction|afterword|appendix|interlude'
    rf'|(?:{HU_NAMED_SECTIONS})\b'
    # Hungarian: "1. fejezet", "Fejezet 1", "I. FEJEZET", "ELSŐ FEJEZET"
    r'|(?:\d+|[ivxlcdm]+)\.?\s*fejezet\b'
    r'|fejezet\s+(?:\d+|[ivxlcdm]+)\b'
    rf'|(?:{HU_ORDINAL})\s+fejezet\b'
    # Hungarian: "1. rész", "II. rész", "Rész 3", "ELSŐ RÉSZ"
    r'|(?:\d+|[ivxlcdm]+)\.?\s*r[eé]sz\b'
    r'|r[eé]sz\s+(?:\d+|[ivxlcdm]+)\b'
    rf'|(?:{HU_ORDINAL})\s+r[eé]sz\b'
    r').*$',
    re.IGNORECASE,
)

# Prefer explicit markers once we see this many in the whole document.
EXPLICIT_MARKER_THRESHOLD = 2


def is_explicit_section(line: str, max_len: int = 150) -> bool:
    """True when a line is a high-confidence chapter/section heading."""
    line = (line or '').strip()
    if not line or len(line) > max_len:
        return False
    return bool(SECTION_RE.match(line))


def looks_like_lead_in_prose(content: str) -> bool:
    """True when pre-first-chapter text is real prose worth keeping.

    Distinguishes an author's note / motto / opening paragraph (flowing
    sentences) from a title page or byline block (short lines, a name, a
    translator credit — no sentence flow). Used to decide whether the
    content before the first chapter heading is read or dropped.
    """
    content = (content or '').strip()
    words = content.split()
    if len(words) < 15:
        return False
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    longest_line_words = max((len(ln.split()) for ln in lines), default=0)
    sentence_marks = len(re.findall(r'[.!?…]', content))
    # Prose either flows on a long line or has several sentence endings.
    return longest_line_words >= 12 or sentence_marks >= 2
