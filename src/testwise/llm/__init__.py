import os

# PydanticAI prints an advertising banner on the first run of an agent. It must
# be turned off before the library is imported; the user can still set it to 0.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from .structure_output import (  # noqa: E402
    MAX_OUTPUT_RETRIES,
    SYSTEM_PROMPT,
    LLMError,
    LocatorResult,
    TokenUsage,
    generate_locators,
)

__all__ = [
    "MAX_OUTPUT_RETRIES",
    "SYSTEM_PROMPT",
    "LLMError",
    "LocatorResult",
    "TokenUsage",
    "generate_locators",
]
