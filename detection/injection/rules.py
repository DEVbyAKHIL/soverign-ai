"""Rule-based prompt-injection pre-filter.

Text is normalised first (Unicode NFKC, zero-width removal, whitespace collapse, common leetspeak)
so trivial obfuscation does not bypass the rules. Base64 blobs are decoded and scanned as well.
Each hit yields a named signal, the label it implies, and a weight used in the risk score.
"""

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass

VERSION = "injection-rules-1.0.0"

ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060-\u2064\ufeff\u00ad]")
LEET = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)
B64_BLOB = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/])")
I = re.IGNORECASE  # noqa: E741


@dataclass(frozen=True)
class Rule:
    signal: str
    label: str
    weight: float
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class RuleHit:
    signal: str
    label: str
    weight: float


_PREV = r"(?:all |any |the )?(?:previous|prior|above|earlier|preceding|initial|original|system)"
_INSTR = r"(?:instructions?|prompts?|rules?|guidelines?|directives?|context|messages?)"

RULES: tuple[Rule, ...] = (
    # --- instruction override -------------------------------------------------------------
    Rule(
        "instruction_override",
        "prompt_injection",
        0.90,
        re.compile(
            rf"\b(?:ignore|disregard|forget|override|bypass|skip|drop)\s+(?:all\s+|any\s+|every\s+)?"
            rf"(?:of\s+)?(?:your\s+|the\s+|my\s+)?{_PREV}?\s*{_INSTR}",
            I,
        ),
    ),
    Rule(
        "instruction_override",
        "prompt_injection",
        0.85,
        re.compile(
            rf"\b(?:do not|don't|never)\s+(?:follow|obey|listen to)\s+(?:your\s+|the\s+)?{_PREV}?\s*{_INSTR}",
            I,
        ),
    ),
    Rule(
        "instruction_override",
        "prompt_injection",
        0.80,
        re.compile(
            r"\b(?:new|updated|real|actual|revised)\s+(?:system\s+)?instructions?\s*(?::|are|follow)",
            I,
        ),
    ),
    Rule(
        "instruction_override",
        "prompt_injection",
        0.85,
        re.compile(
            r"\bignoriere?\s+(?:alle\s+)?(?:vorherigen|bisherigen)\s+anweisungen|"
            r"\bignorez?\s+(?:toutes\s+)?les\s+instructions\s+(?:pr[ée]c[ée]dentes|ant[ée]rieures)|"
            r"\bignora\s+(?:todas\s+)?las\s+instrucciones\s+anteriores",
            I,
        ),
    ),
    # --- guardrail / redaction bypass -----------------------------------------------------
    Rule(
        "guardrail_bypass",
        "prompt_injection",
        0.90,
        re.compile(
            r"\b(?:disable|turn off|deactivate|switch off|remove|bypass|skip)\s+(?:the\s+|your\s+|all\s+)?"
            r"(?:redaction|filters?|filtering|guardrails?|safety|moderation|content polic(?:y|ies)|"
            r"restrictions?|censorship|pii masking|masking)",
            I,
        ),
    ),
    Rule(
        "guardrail_bypass",
        "prompt_injection",
        0.75,
        re.compile(
            r"\b(?:without|no)\s+(?:any\s+)?(?:redaction|filters?|censorship|restrictions?|masking)\b",
            I,
        ),
    ),
    # --- jailbreak / persona switching ----------------------------------------------------
    Rule(
        "mode_switch",
        "jailbreak_attempt",
        0.90,
        re.compile(
            r"\b(?:you are|you're|act as|enter|enable|activate|switch to|switch into)\s+(?:now\s+)?"
            r"(?:in\s+)?(?:developer|debug|god|admin|root|sudo|unrestricted|jailbreak|dan)\s*mode\b"
            # Not about a device or app ("enable developer mode on my phone / in Chrome").
            r"(?!\s+(?:on|in|for)\s+(?:my|the|your|an?|this)?\s*(?:phone|device|android|iphone|ios|"
            r"mac|windows|laptop|browser|chrome|firefox|settings|app|tv|router|console))",
            I,
        ),
    ),
    Rule(
        "persona_hijack",
        "jailbreak_attempt",
        0.85,
        re.compile(
            r"\b(?:you are now|from now on,? you are|pretend (?:to be|you are)|act as|roleplay as)\s+"
            r"(?:an?\s+)?(?:unrestricted|unfiltered|uncensored|evil|rogue|jailbroken|dan\b|ai without)",
            I,
        ),
    ),
    Rule(
        "persona_hijack",
        "jailbreak_attempt",
        0.85,
        re.compile(
            r"\b(?:do anything now|no (?:ethical|moral) (?:guidelines|constraints)|"
            r"without any (?:ethical|moral) (?:guidelines|limits)|stay in character no matter what)\b",
            I,
        ),
    ),
    Rule(
        "persona_hijack",
        "jailbreak_attempt",
        0.70,
        re.compile(
            r"\bhypothetically,? if you (?:had no|were free of|weren't bound by) (?:rules|restrictions)",
            I,
        ),
    ),
    # --- system prompt extraction ---------------------------------------------------------
    Rule(
        "system_prompt_request",
        "system_prompt_extraction_attempt",
        0.90,
        re.compile(
            r"\b(?:reveal|show|print|display|output|repeat|tell me|give me|leak|dump|share|what (?:is|are))"
            r"\s+(?:me\s+)?(?:your|the)\s+(?:full\s+|exact\s+|original\s+|hidden\s+|initial\s+)?"
            r"(?:system\s+prompt|system\s+message|initial\s+instructions|hidden\s+instructions|"
            r"developer\s+message|pre-?prompt)",
            I,
        ),
    ),
    Rule(
        "system_prompt_request",
        "system_prompt_extraction_attempt",
        0.85,
        re.compile(
            r"\b(?:repeat|print|output|echo)\s+(?:everything|all(?: the)? text|the words?)\s+"
            r"(?:above|before (?:this|my)|from the (?:beginning|start))",
            I,
        ),
    ),
    Rule(
        "system_prompt_request",
        "system_prompt_extraction_attempt",
        0.80,
        re.compile(
            r"\bwhat (?:were|are) you (?:told|instructed|programmed) (?:to do |before|initially)", I
        ),
    ),
    # --- data export / exfiltration -------------------------------------------------------
    Rule(
        "data_export_request",
        "data_exfiltration_attempt",
        0.85,
        re.compile(
            r"\b(?:send|export|dump|list|output|give me|show me|extract|download|leak|email|upload|forward)"
            r"\s+(?:me\s+)?(?:all|every|the (?:entire|whole|full)|complete)\s+(?:of the\s+)?"
            r"(?:customer|user|client|patient|employee|account|credit card|payment|personal)?\s*"
            r"(?:records|data(?:base)?|details|emails|passwords|credentials|tables?|list|accounts|files)",
            I,
        ),
    ),
    Rule(
        "data_export_request",
        "data_exfiltration_attempt",
        0.80,
        re.compile(r"\b(?:select\s+\*\s+from|union\s+select|drop\s+table|;\s*--)", I),
    ),
    Rule(
        "exfil_channel",
        "data_exfiltration_attempt",
        0.90,
        re.compile(r"!\[[^\]]*\]\(\s*https?://[^)\s]*[?&][^)\s]*=", I),
    ),  # markdown image beacon with query
    Rule(
        "exfil_channel",
        "data_exfiltration_attempt",
        0.80,
        re.compile(
            r"\b(?:send|post|upload|forward|transmit|append)\b[^.\n]{0,60}\b(?:to|into)\s+"
            r"(?:https?://|ftp://|webhook|my server|this url|the following url|attacker)",
            I,
        ),
    ),
    # --- structural / delimiter attacks ---------------------------------------------------
    Rule(
        "delimiter_injection",
        "prompt_injection",
        0.75,
        re.compile(
            r"(?:</?\s*(?:system|assistant|im_start|im_end|instructions?)\s*>|<\|im_(?:start|end)\|>|"
            r"^\s*#{2,}\s*(?:system|new instructions?)\b|\[/?(?:INST|SYS)\])",
            I | re.MULTILINE,
        ),
    ),
    Rule(
        "tool_hijack",
        "prompt_injection",
        0.80,
        re.compile(
            r"\b(?:call|invoke|execute|run|use)\s+(?:the\s+)?(?:tool|function|plugin|command)\s+"
            r"[`'\"]?[\w.-]+[`'\"]?\s+(?:with|to)\b[^.\n]{0,80}\b(?:delete|transfer|send|grant|"
            r"disable|export)",
            I,
        ),
    ),
    # --- addressed-to-AI imperatives (weak alone; strong inside documents) ------------------
    Rule(
        "ai_directed_instruction",
        "suspicious",
        0.45,
        re.compile(
            r"\b(?:note to (?:the )?(?:ai|assistant|model|llm|chatbot)|"
            r"(?:ai|assistant|model|llm|chatbot)\s*[:,]\s*(?:you must|please|ignore|do not|always))|"
            r"\bif you are an? (?:ai|language model|assistant)\b|\bwhen (?:the )?(?:ai|assistant|model) "
            r"reads this",
            I,
        ),
    ),
    Rule(
        "urgency_pressure",
        "suspicious",
        0.30,
        re.compile(
            r"\b(?:this is (?:an? )?(?:authorized|official) (?:request|test|override)|"
            r"(?:admin|administrator|developer|openai|anthropic) (?:has )?(?:authorized|approved) this)\b",
            I,
        ),
    ),
)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = ZERO_WIDTH.sub("", text)
    return re.sub(r"\s+", " ", text)


def _decoded_payloads(text: str) -> list[str]:
    """Decode base64 blobs that turn into readable text (attackers hide instructions this way)."""
    out = []
    for m in B64_BLOB.finditer(text):
        blob = m.group()
        try:
            raw = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True)
            decoded = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        printable = sum(c.isprintable() for c in decoded) / max(len(decoded), 1)
        if printable > 0.95 and " " in decoded:
            out.append(decoded)
    return out


def scan(text: str) -> list[RuleHit]:
    """Return de-duplicated rule hits (strongest per signal)."""
    variants = {"plain": normalise(text)}
    variants["deleet"] = variants["plain"].translate(LEET)
    obfuscated = bool(ZERO_WIDTH.search(text)) or variants["plain"] != unicodedata.normalize(
        "NFKC", text
    ).replace("\n", " ")
    hits: dict[str, RuleHit] = {}

    def run(candidate: str, prefix: str = "") -> None:
        for rule in RULES:
            if rule.pattern.search(candidate):
                signal = prefix + rule.signal
                if signal not in hits or hits[signal].weight < rule.weight:
                    hits[signal] = RuleHit(signal, rule.label, rule.weight)

    for candidate in variants.values():
        run(candidate)
    for payload in _decoded_payloads(text):
        before = len(hits)
        run(normalise(payload))
        if len(hits) > before:
            hits["encoded_payload"] = RuleHit("encoded_payload", "prompt_injection", 0.85)
    if obfuscated and any(h.label != "suspicious" for h in hits.values()):
        hits["obfuscation"] = RuleHit("obfuscation", "prompt_injection", 0.5)
    return sorted(hits.values(), key=lambda h: -h.weight)
