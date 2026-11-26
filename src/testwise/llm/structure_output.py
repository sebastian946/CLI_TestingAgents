"""The only place that calls the LLM: turns a filtered snapshot into locators.

The model is the one the user configured (BYOK). Its answer is validated
against the `Locator` model, so what comes out is data ready for a page.json,
never free text.
"""

import logging
from dataclasses import dataclass

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.capabilities import RetryOnModelErrors
from pydantic_ai.exceptions import (
    AgentRunError,
    ModelAPIError,
    UnexpectedModelBehavior,
    UserError,
)
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from ..locators.models import Locator, LocatorStatus, Strategy
from ..settings import get_settings

logger = logging.getLogger(__name__)

# How many times a badly formed answer is sent back to the model to be fixed.
MAX_OUTPUT_RETRIES = 2
# How many times a request is attempted when the provider fails (rate limit,
# overload, network). These failures are not the model's fault.
MAX_REQUEST_ATTEMPTS = 2

# Fixed on purpose: it never includes anything about the page. Everything that
# changes between calls goes in the user message, so the provider can cache
# this prefix.
SYSTEM_PROMPT = """\
You are a QA engineer who writes Playwright tests. You receive the \
accessibility snapshot of a web page and return the locators of its elements.

Return one locator for each element a test would interact with or check: \
buttons, links, form fields and the headings or messages that confirm a result.

Rules:

1. Choose the first strategy of this list that identifies the element:
   1. test_id: only when the snapshot shows a test id for the element.
   2. role: the ARIA role in "role" and the accessible name in "value".
   3. label: the text of the label of a form field.
   4. placeholder: the placeholder of a form field.
   5. text: the visible text of the element.
   6. css: a CSS selector, only as a last resort.
   No other strategy exists.

2. Name every locator in snake_case and describe what the element is for: \
login_button, not button_1. Names must be unique.

3. Never invent elements. Every locator must refer to an element that appears \
in the snapshot, and every "value" must be copied from the snapshot exactly \
as it is written there.

4. Write "description" as one short sentence about the purpose of the element.

5. Leave "status" as "unverified" and do not set "last_verified".
"""

# Strategies whose value is text that must be visible in the snapshot.
_TEXT_STRATEGIES = frozenset(
    {Strategy.ROLE, Strategy.LABEL, Strategy.PLACEHOLDER, Strategy.TEXT}
)


class LLMError(Exception):
    """The LLM could not produce valid locators.

    `usage` holds the tokens spent before failing: they are billed anyway.
    """

    def __init__(self, message: str, usage: "TokenUsage | None" = None) -> None:
        super().__init__(message)
        self.usage = usage or TokenUsage()


@dataclass(frozen=True)
class TokenUsage:
    """Tokens spent by one call to `generate_locators`, retries included."""

    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __str__(self) -> str:
        return (
            f"{self.input_tokens:,} input + {self.output_tokens:,} output tokens "
            f"in {self.requests} request(s)"
        )


@dataclass(frozen=True)
class LocatorResult:
    """Locators returned by the model and what the call cost."""

    locators: list[Locator]
    usage: TokenUsage
    model: str


def generate_locators(
    snapshot: str,
    *,
    url: str | None = None,
    model: Model | str | None = None,
) -> LocatorResult:
    """Ask the LLM for the locators of the page described by `snapshot`.

    `snapshot` is the filtered accessibility snapshot (see `filter_snapshot`).
    `model` defaults to TESTWISE_MODEL from the user's configuration; tests
    pass a fake model so that no real API is called.

    Raises LLMError with a user-facing message if the model fails or keeps
    returning invalid locators. Raises SettingsError if the configuration is
    missing; in that case no request is made.
    """
    if not snapshot.strip():
        raise LLMError("The snapshot is empty: there is nothing to find locators for.")

    model = model if model is not None else get_settings().testwise_model
    model_name = model if isinstance(model, str) else model.model_name
    usage = RunUsage()

    try:
        agent = _build_agent(model)
        result = agent.run_sync(_user_prompt(snapshot, url), deps=snapshot, usage=usage)
    except UnexpectedModelBehavior:
        spent = _log_usage(model_name, usage)
        raise LLMError(
            f"The model {model_name} did not return valid locators after "
            f"{MAX_OUTPUT_RETRIES} retries. Run the command again or set a more "
            "capable model in TESTWISE_MODEL.",
            spent,
        ) from None
    except ModelAPIError as error:
        spent = _log_usage(model_name, usage)
        raise LLMError(
            f"The request to {model_name} failed: {_first_line(error)}", spent
        ) from None
    except (AgentRunError, UserError) as error:
        spent = _log_usage(model_name, usage)
        raise LLMError(
            f"Could not use the model {model_name}: {_first_line(error)}", spent
        ) from None

    spent = _log_usage(model_name, usage)
    return LocatorResult(
        locators=[_as_new(locator) for locator in result.output],
        usage=spent,
        model=model_name,
    )


def _build_agent(model: Model | str) -> Agent[str, list[Locator]]:
    """Agent whose answer must be a list of valid locators.

    The dependency of a run is the snapshot, so that the answer can be checked
    against it.
    """
    agent = Agent(
        model,
        deps_type=str,
        output_type=list[Locator],
        instructions=SYSTEM_PROMPT,
        retries={"output": MAX_OUTPUT_RETRIES},
        capabilities=[RetryOnModelErrors(max_attempts=MAX_REQUEST_ATTEMPTS)],
    )

    @agent.output_validator
    def check_locators(context: RunContext[str], locators: list[Locator]):
        problems = _duplicated_names(locators) + _invented(locators, context.deps)
        if problems:
            # Sent back to the model, which gets another try to fix them.
            raise ModelRetry("Fix these problems:\n- " + "\n- ".join(problems))
        return locators

    return agent


def _user_prompt(snapshot: str, url: str | None) -> str:
    header = f"Page URL: {url}\n\n" if url else ""
    return f"{header}Accessibility snapshot:\n\n{snapshot}"


def _duplicated_names(locators: list[Locator]) -> list[str]:
    seen: set[str] = set()
    problems = []
    for locator in locators:
        if locator.name in seen:
            problems.append(f"More than one locator is named '{locator.name}'.")
        seen.add(locator.name)
    return problems


def _invented(locators: list[Locator], snapshot: str) -> list[str]:
    """Locators whose text does not appear anywhere in the snapshot."""
    visible = _comparable(snapshot)
    return [
        f"Locator '{locator.name}': \"{locator.value}\" does not appear in the "
        "snapshot. Copy the text exactly or remove the locator."
        for locator in locators
        if locator.strategy in _TEXT_STRATEGIES
        and locator.value
        and _comparable(locator.value) not in visible
    ]


def _comparable(text: str) -> str:
    """Text without the escaping and spacing that the snapshot format adds."""
    return " ".join(text.replace("\\", "").split())


def _as_new(locator: Locator) -> Locator:
    """A locator from the model has never been checked against the real page."""
    return locator.model_copy(
        update={"status": LocatorStatus.UNVERIFIED, "last_verified": None}
    )


def _log_usage(model_name: str, usage: RunUsage) -> TokenUsage:
    spent = TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        requests=usage.requests,
    )
    logger.info("LLM call to %s: %s", model_name, spent)
    return spent


def _first_line(error: Exception) -> str:
    lines = str(error).strip().splitlines()
    return lines[0] if lines else type(error).__name__
