from google.genai import errors

from app.errors import AppError

USAGE_LIMIT_MESSAGE = (
    "The assistant has temporarily reached its Gemini usage limit. Please try again later."
)
TIMEOUT_MESSAGE = "Gemini took too long to respond. Please try again."


def translate_gemini_error(exc: Exception, fallback_code: str, fallback_message: str) -> AppError:
    if isinstance(exc, errors.APIError) and exc.code == 429:
        return AppError("GEMINI_USAGE_LIMIT", USAGE_LIMIT_MESSAGE, 429)
    if isinstance(exc, errors.APIError) and exc.code in {408, 504}:
        return AppError("GEMINI_TIMEOUT", TIMEOUT_MESSAGE, 504)
    return AppError(fallback_code, fallback_message, 503)
