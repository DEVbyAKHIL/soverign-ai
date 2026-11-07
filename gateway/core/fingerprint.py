"""Keyed fingerprints for correlating content without storing it.

A plain SHA-256 of a prompt or model answer can be confirmed by anyone who guesses the text (short
or templated prompts are guessable). An HMAC under FINGERPRINT_HMAC_KEY cannot be computed without
the key, while equal inputs still give equal fingerprints (repeat detection, deduplication).
Purpose-specific subkeys keep a content fingerprint from ever matching an output fingerprint.
The key itself is never logged, returned or stored.
"""

import hashlib
import hmac


def _subkey(master: str, purpose: str) -> bytes:
    return hmac.new(master.encode(), f"fingerprint:{purpose}".encode(), hashlib.sha256).digest()


def fingerprint(master: str, purpose: str, text: str) -> str:
    """Hex HMAC-SHA256 of `text` under the purpose-derived subkey of `master`."""
    return hmac.new(_subkey(master, purpose), text.encode("utf-8"), hashlib.sha256).hexdigest()
