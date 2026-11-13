"""Event validation used by the Spark job (as a UDF) - the same JSON Schema the producers use.

Reusing events.schema keeps one contract: an event the gateway or producer considers valid is
exactly what the processor accepts. Reasons are short codes without event values, so they are safe
to put on the dead-letter topic and in logs.
"""

import json

from events.schema import errors, parse_ts, validator

ALL_AGENTS = "00000000-0000-0000-0000-000000000000"


def invalid_reason(value: bytes | bytearray | None) -> str | None:
    """None for a valid event, otherwise the first violation, e.g. 'decision:enum'."""
    if value is None:
        return "empty"
    try:
        event = json.loads(bytes(value))
    except (ValueError, UnicodeDecodeError):
        return "not_json"
    if not isinstance(event, dict):
        return "<root>:type"
    if not validator().is_valid(event):
        return (errors(event) or ["<root>:invalid"])[0]
    try:
        parse_ts(event["event_timestamp"])
    except ValueError:
        return "event_timestamp:not_utc"
    return None
