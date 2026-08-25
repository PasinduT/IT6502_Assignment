from google.genai import errors

from app.services.gemini_errors import (
    TIMEOUT_MESSAGE,
    USAGE_LIMIT_MESSAGE,
    translate_gemini_error,
)


def test_translates_gemini_429_to_usage_limit_error():
    provider_error = errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Quota exceeded",
                "status": "RESOURCE_EXHAUSTED",
            }
        },
    )

    error = translate_gemini_error(provider_error, "FALLBACK", "Fallback message")

    assert error.code == "GEMINI_USAGE_LIMIT"
    assert error.message == USAGE_LIMIT_MESSAGE
    assert error.status_code == 429


def test_preserves_generic_handling_for_other_errors():
    error = translate_gemini_error(ValueError("bad response"), "FALLBACK", "Fallback message")

    assert error.code == "FALLBACK"
    assert error.message == "Fallback message"
    assert error.status_code == 503


def test_translates_gemini_timeout_error():
    provider_error = errors.ServerError(
        504,
        {
            "error": {
                "code": 504,
                "message": "Request timed out",
                "status": "DEADLINE_EXCEEDED",
            }
        },
    )

    error = translate_gemini_error(provider_error, "FALLBACK", "Fallback message")

    assert error.code == "GEMINI_TIMEOUT"
    assert error.message == TIMEOUT_MESSAGE
    assert error.status_code == 504
