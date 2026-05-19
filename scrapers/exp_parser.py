import re
from html.parser import HTMLParser


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        return " ".join(self._parts)


def strip_html(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text()


_SENIOR_TITLE = re.compile(
    r'\b(senior|sr\.?|lead|principal|staff|distinguished|fellow|'
    r'architect|manager|director|head\s+of|vp|vice[\s-]?president|'
    r'svp|evp|managing)\b',
    re.IGNORECASE,
)
_JUNIOR_TITLE = re.compile(
    r'\b(junior|jr\.?|entry[\s-]?level?|associate|apprentice|early[\s-]?career)\b',
    re.IGNORECASE,
)
_ROMAN_III_IV = re.compile(r'\b(iii|iv|v)\b', re.IGNORECASE)
_ROMAN_II = re.compile(r'\bii\b', re.IGNORECASE)

_EXP_PATTERNS = [
    # "3+ years of experience", "5+ years experience"
    re.compile(r'(\d+)\+\s*years?\s+(?:of\s+)?(?:\w+\s+){0,3}experience', re.IGNORECASE),
    # "3-5 years of experience", "2-4 years experience"
    re.compile(r'(\d+)\s*[-–]\s*\d+\s+years?\s+(?:of\s+)?(?:\w+\s+){0,3}experience', re.IGNORECASE),
    # "minimum 3 years", "minimum of 2 years"
    re.compile(r'minimum\s+(?:of\s+)?(\d+)\s+years?', re.IGNORECASE),
    # "at least 3 years"
    re.compile(r'at\s+least\s+(\d+)\s+years?', re.IGNORECASE),
    # "3 or more years"
    re.compile(r'(\d+)\s+or\s+more\s+years?', re.IGNORECASE),
    # "3 years of experience" (generic)
    re.compile(r'(\d+)\s+years?\s+of\s+(?:\w+\s+){0,2}experience', re.IGNORECASE),
]


def exp_from_title(title: str) -> int | None:
    if _SENIOR_TITLE.search(title):
        return 6
    if _ROMAN_III_IV.search(title):
        return 6
    if _ROMAN_II.search(title):
        return 3
    if _JUNIOR_TITLE.search(title):
        return 0
    return None


def exp_from_jd(html: str | None) -> int | None:
    if not html:
        return None
    text = strip_html(html)
    nums = []
    for pat in _EXP_PATTERNS:
        for m in pat.finditer(text):
            try:
                n = int(m.group(1))
                if 0 <= n <= 20:
                    nums.append(n)
            except (IndexError, ValueError):
                pass
    return min(nums) if nums else None


def infer_exp(title: str, jd_html: str | None = None) -> int:
    """Return minimum years of experience required. 0 = entry-level or unknown."""
    from_jd = exp_from_jd(jd_html)
    if from_jd is not None:
        return from_jd
    from_title = exp_from_title(title)
    if from_title is not None:
        return from_title
    return 0
