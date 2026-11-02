import pytest
from pydantic import ValidationError

from gateway.config import Settings

SECRET = "s" * 40


def test_cors_csv_parsed():
    s = Settings(jwt_secret=SECRET, cors_origins="https://a.example, https://b.example")
    assert s.cors_origins == ["https://a.example", "https://b.example"]


def test_wildcard_cors_rejected():
    with pytest.raises(ValidationError):
        Settings(jwt_secret=SECRET, cors_origins="*")


@pytest.mark.parametrize("secret", ["short", "CHANGE_ME_AT_LEAST_32_CHARS_xxxxxxxxxx"])
def test_weak_or_placeholder_secret_rejected(secret):
    with pytest.raises(ValidationError):
        Settings(jwt_secret=secret)
