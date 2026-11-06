import pytest

from detection.pii.regex_detector import RegexDetector, iban_valid, luhn_valid


@pytest.fixture(scope="module")
def det() -> RegexDetector:
    return RegexDetector()


def _types(det, text):
    return {(e.entity_type, text[e.start : e.end]) for e in det.detect(text)}


@pytest.mark.parametrize(
    "text,etype,value",
    [
        (
            "mail me at jane.doe+test@mail.example.co.uk today",
            "EMAIL",
            "jane.doe+test@mail.example.co.uk",
        ),
        ("card 4111 1111 1111 1111 exp 12/29", "CREDIT_CARD", "4111 1111 1111 1111"),
        ("card 5555-5555-5555-4444", "CREDIT_CARD", "5555-5555-5555-4444"),
        ("IBAN DE89 3704 0044 0532 0130 00 please", "IBAN", "DE89 3704 0044 0532 0130 00"),
        ("pay GB82WEST12345698765432.", "IBAN", "GB82WEST12345698765432"),
        ("my SSN is 123-45-6789", "NATIONAL_ID", "123-45-6789"),
        ("NI number JG 10 37 63 A", "NATIONAL_ID", "JG 10 37 63 A"),
        ("Personalausweis Nr: T22000129", "NATIONAL_ID", "T22000129"),
        ("passport number C01X00T47 expires", "PASSPORT", "C01X00T47"),
        (
            "key sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
            "API_KEY",
            "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
        ),
        ("aws AKIAIOSFODNN7EXAMPLE here", "API_KEY", "AKIAIOSFODNN7EXAMPLE"),
        ("token ghp_" + "a" * 36, "API_KEY", "ghp_" + "a" * 36),
        ("client_secret = 'Zm9vYmFyYmF6cXV4MTIzNDU2'", "API_KEY", "Zm9vYmFyYmF6cXV4MTIzNDU2"),
        ("-----BEGIN RSA PRIVATE KEY-----", "API_KEY", "-----BEGIN RSA PRIVATE KEY-----"),
        ("the password is Hunter2!x so", "PASSWORD", "Hunter2!x"),
        ("pwd=s3cr3tPass;", "PASSWORD", "s3cr3tPass"),
        ("date of birth: 04/07/1985", "DATE_OF_BIRTH", "04/07/1985"),
        ("born on 12 March 1990 in Rome", "DATE_OF_BIRTH", "12 March 1990"),
        ("ship to 221B Baker Street, London", "ADDRESS", "221B Baker Street"),
        ("wohnt in der Hauptstraße 12 in Köln", "ADDRESS", "Hauptstraße 12"),
        ("the balance is €12,400.", "FINANCIAL", "€12,400"),
        ("account number: 12345678", "FINANCIAL", "12345678"),
        ("diagnosed with diabetes in 2019", "MEDICAL", "diabetes"),
        ("ticket from CUST-0012345", "CUSTOMER_ID", "CUST-0012345"),
        ("customer id: AB-77821", "CUSTOMER_ID", "AB-77821"),
        ("update on Project Nightingale timeline", "INTERNAL_PROJECT", "Project Nightingale"),
        ("call +44 20 7946 0958 now", "PHONE", "+44 20 7946 0958"),
    ],
)
def test_detects(det, text, etype, value):
    assert (etype, value) in _types(det, text)


@pytest.mark.parametrize(
    "text",
    [
        "Reference 4111 1111 1111 1112 failed validation",  # Luhn fails
        "IBAN DE00 3704 0044 0532 0130 00",  # checksum fails
        "Python 3.12.4 and release v2.10.3",
        "ISBN 978-0-306-40615-7",
        "SSN 000-12-3456 and 666-12-3456 are invalid",
        "the meeting room on floor 4 seats 12 people",
        "passport photo booth on level 2",
    ],
)
def test_no_false_positive_for_near_misses(det, text):
    found = {t for t, _ in _types(det, text)} - {"PHONE"}
    assert found == set(), found


def test_context_raises_confidence(det):
    plain = next(e for e in det.detect("total €500 today") if e.entity_type == "FINANCIAL")
    ctx = next(e for e in det.detect("the salary is €500") if e.entity_type == "FINANCIAL")
    assert ctx.confidence > plain.confidence
    weak = next(e for e in det.detect("I like asthma research") if e.entity_type == "MEDICAL")
    strong = next(e for e in det.detect("the patient has asthma") if e.entity_type == "MEDICAL")
    assert strong.confidence > weak.confidence


def test_part_index_and_version(det):
    [e] = [x for x in det.detect("x@example.com", part=2) if x.entity_type == "EMAIL"]
    assert e.part == 2 and e.detector == "regex" and e.model_version.startswith("regex-1.0.0")


def test_validators():
    assert luhn_valid("4111111111111111") and not luhn_valid("4111111111111112")
    assert iban_valid("DE89370400440532013000") and not iban_valid("DE89370400440532013001")
    assert not iban_valid("XX89370400440532013000")


def test_value_not_in_repr(det):
    [e] = [x for x in det.detect("password: Sup3rSecret!") if x.entity_type == "PASSWORD"]
    assert "Sup3rSecret" not in repr(e)
