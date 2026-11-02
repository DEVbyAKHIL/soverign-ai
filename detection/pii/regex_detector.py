"""Layer 1: regular expressions with checksum / context validation for structured data."""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import phonenumbers
import yaml

from detection.types import Entity

VERSION = "regex-1.0.0"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "sensitive_terms.yaml"

# --- validators ----------------------------------------------------------


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


IBAN_LENGTHS = {
    "AT": 20, "BE": 16, "CH": 21, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "EE": 20, "ES": 24,
    "FI": 18, "FR": 27, "GB": 22, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26, "IT": 27,
    "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25,
    "RO": 24, "SE": 24, "SI": 19, "SK": 24,
}  # fmt: skip


def iban_valid(raw: str) -> bool:
    iban = raw.replace(" ", "").upper()
    if IBAN_LENGTHS.get(iban[:2]) != len(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    return int(numeric) % 97 == 1


NINO_BAD_PREFIXES = {"BG", "GB", "NK", "KN", "TN", "NT", "ZZ"}

# --- patterns ------------------------------------------------------------


@dataclass(frozen=True)
class Pattern:
    entity_type: str
    regex: re.Pattern[str]
    confidence: float
    group: int = 0
    validate: Callable[[str], bool] | None = None
    # (context regex, confidence when context appears within CONTEXT_WINDOW chars before)
    context: tuple[re.Pattern[str], float] | None = None


CONTEXT_WINDOW = 60
I = re.IGNORECASE  # noqa: E741

FIN_CONTEXT = re.compile(
    r"\b(balance|salary|invoice|payment|paid|loan|debt|revenue|income|owe[sd]?|credit|"
    r"transfer|bonus|budget|fee|price|amount|account)\b",
    I,
)
MED_CONTEXT = re.compile(r"\b(patient|diagnos\w*|clinic\w*|doctor|hospital|treatment|medical)\b", I)

MEDICAL_TERMS = (
    r"diabetes|diabetic|hiv|cancer|chemotherapy|tumou?r|depression|schizophreni\w*|bipolar|"
    r"pregnan(?:t|cy)|prescription|prescribed|insulin|hepatitis|asthma|epilep\w*|dementia|"
    r"alzheimer'?s|hypertension|biopsy|psychiatric|overdose|antidepressants?|chronic illness|"
    r"blood test|mri scan|medical record|sick leave"
)

PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "EMAIL",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
        0.99,
    ),
    Pattern(
        "CREDIT_CARD",
        re.compile(r"\b[3-6]\d{3}(?:[ -]?\d){9,15}\b"),
        0.97,
        validate=lambda v: 13 <= len(d := re.sub(r"\D", "", v)) <= 19 and luhn_valid(d),
    ),
    Pattern(
        "IBAN",
        re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b"),
        0.98,
        validate=iban_valid,
    ),
    Pattern(
        "NATIONAL_ID",  # US SSN
        re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
        0.85,
        context=(re.compile(r"\b(ssn|social security)\b", I), 0.97),
    ),
    Pattern(
        "NATIONAL_ID",  # UK National Insurance number
        re.compile(r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z] ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b"),
        0.85,
        validate=lambda v: v[:2].upper() not in NINO_BAD_PREFIXES,
        context=(re.compile(r"\b(national insurance|nino|ni number)\b", I), 0.97),
    ),
    Pattern(
        "NATIONAL_ID",  # labelled identifiers of any national format
        re.compile(
            r"\b(?:national id|id number|identity number|personalausweis|aadhaar|tax id|"
            r"steuer-?id|social insurance number|bsn)\s*(?:no\.?|nr\.?|number|#)?\s*[:=]?\s*"
            r"([A-Z0-9][A-Z0-9 -]{4,18}[A-Z0-9])\b",
            I,
        ),
        0.90,
        group=1,
    ),
    Pattern(
        "PASSPORT",
        re.compile(r"\bpassport(?:\s*(?:no\.?|nr\.?|number|#))?\s*[:=]?\s*([A-Z0-9]{6,9})\b", I),
        0.92,
        group=1,
        validate=lambda v: any(c.isdigit() for c in v),
    ),
    # Credentials: well-known formats first, then labelled secrets.
    Pattern("API_KEY", re.compile(r"\bsk-(?:proj-|ant-[a-z0-9]+-)?[A-Za-z0-9_-]{20,}"), 0.97),
    Pattern("API_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0.97),
    Pattern("API_KEY", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), 0.97),
    Pattern("API_KEY", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), 0.97),
    Pattern("API_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), 0.97),
    Pattern(
        "API_KEY",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        0.95,
    ),
    Pattern("API_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), 0.99),
    Pattern(
        "API_KEY",
        re.compile(
            r"\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
            r"bearer)\s*[:=]?\s*['\"]?([A-Za-z0-9_\-./+=]{16,})",
            I,
        ),
        0.90,
        group=1,
    ),
    Pattern(
        "PASSWORD",
        re.compile(
            r"\b(?:password|passwd|pwd|passcode|passphrase)\s*(?:is|[:=])\s*['\"]?([^\s'\",;]{4,64})",
            I,
        ),
        0.92,
        group=1,
    ),
    Pattern(
        "DATE_OF_BIRTH",
        re.compile(
            r"\b(?:born(?: on)?|date of birth|d\.?o\.?b\.?|birthday(?: is)?)\s*[:\-]?\s*"
            r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}(?:st|nd|rd|th)?\s+"
            r"[A-Za-z]{3,9}\.?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})",
            I,
        ),
        0.92,
        group=1,
    ),
    Pattern(
        "ADDRESS",
        re.compile(
            r"\b\d{1,5}[A-Za-z]?\s+(?:[A-Z][a-zA-Z'-]+\s){1,4}(?:Street|St\.?|Road|Rd\.?|"
            r"Avenue|Ave\.?|Lane|Ln\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Way|Court|Ct\.?|Place|"
            r"Pl\.?|Terrace|Close|Crescent)(?=\W|$)"
        ),
        0.85,
    ),
    Pattern(
        "ADDRESS",
        re.compile(
            r"\b[A-ZÄÖÜ][a-zäöüß-]+(?:straße|strasse|str\.|weg|platz|allee|gasse|ring|damm)"
            r"\s+\d{1,4}[a-z]?\b"
        ),
        0.85,
    ),
    Pattern(
        "FINANCIAL",
        re.compile(
            r"(?:[€$£]\s?\d[\d,.]*(?:\s?(?:k|m|bn|million|billion))?|"
            r"\b\d[\d,.]*\s?(?:EUR|USD|GBP|CHF|euros?|dollars?|pounds)\b)",
            I,
        ),
        0.60,
        context=(FIN_CONTEXT, 0.88),
    ),
    Pattern(
        "FINANCIAL",
        re.compile(
            r"\b(?:account|acct)\s*(?:no\.?|nr\.?|number|#)\s*[:=]?\s*(\d[\d -]{5,20}\d)\b", I
        ),
        0.90,
        group=1,
    ),
    Pattern(
        "MEDICAL",
        re.compile(rf"\b(?:{MEDICAL_TERMS})\b", I),
        0.75,
        context=(MED_CONTEXT, 0.90),
    ),
)

PHONE_REGIONS = ("US", "GB", "DE", "FR", "IN", "NL", "ES", "IT")
PHONE_CANDIDATE = re.compile(r"(?:\+|\(?\b\d)[\d ().\-/]{6,22}\d\b")


class RegexDetector:
    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        self.version = f"{VERSION}+terms-{config.get('version', '0')}"
        projects = [re.escape(p) for p in config.get("internal_projects", [])]
        self.extra: list[Pattern] = []
        if projects:
            self.extra.append(
                Pattern(
                    "INTERNAL_PROJECT",
                    re.compile(rf"\b(?:{'|'.join(projects)})\b", I),
                    0.95,
                )
            )
        for rx in config.get("customer_id_patterns", []):
            compiled = re.compile(rx, I)
            self.extra.append(
                Pattern("CUSTOMER_ID", compiled, 0.90, group=1 if compiled.groups else 0)
            )

    def detect(self, text: str, part: int = 0) -> list[Entity]:
        found = list(self._patterns(text, part))
        found.extend(self._phones(text, part))
        return found

    def _patterns(self, text: str, part: int) -> Iterator[Entity]:
        for p in (*PATTERNS, *self.extra):
            for m in p.regex.finditer(text):
                start, end = m.span(p.group)
                value = text[start:end].rstrip(".")
                end = start + len(value)
                if not value or (p.validate and not p.validate(value)):
                    continue
                confidence = p.confidence
                if p.context:
                    window = text[max(0, start - CONTEXT_WINDOW) : start]
                    if p.context[0].search(window):
                        confidence = p.context[1]
                yield Entity(
                    p.entity_type, start, end, confidence, "regex", self.version, value, part
                )

    def _phones(self, text: str, part: int) -> Iterator[Entity]:
        # libphonenumber is slow on long text; only parse digit runs that could be numbers.
        seen: set[tuple[int, int]] = set()
        for cand in PHONE_CANDIDATE.finditer(text):
            chunk, offset = cand.group(), cand.start()
            # International numbers parse the same in any region; national ones need each region.
            regions = PHONE_REGIONS[:1] if chunk.startswith("+") else PHONE_REGIONS
            for region in regions:
                for m in phonenumbers.PhoneNumberMatcher(
                    chunk, region, phonenumbers.Leniency.VALID
                ):
                    span = (offset + m.start, offset + m.end)
                    if span in seen:
                        continue
                    seen.add(span)
                    confidence = 0.95 if m.raw_string.startswith("+") else 0.85
                    yield Entity(
                        "PHONE", *span, confidence, "regex", self.version, m.raw_string, part
                    )
