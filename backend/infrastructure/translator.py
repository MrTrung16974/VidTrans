"""Language-neutral translation boundary.

The implementation remains in ``vietnamese_translator`` for one compatibility
cycle because deployed jobs and tests may still import that module directly.
New code should import this module.
"""

from infrastructure.vietnamese_translator import (
    GoogleVietnameseTranslator,
    MultilingualTranslator,
    TranslationRateLimitedError,
    TranslationServiceError,
    failure_code,
)

__all__ = [
    "MultilingualTranslator",
    "GoogleVietnameseTranslator",
    "TranslationRateLimitedError",
    "TranslationServiceError",
    "failure_code",
]

