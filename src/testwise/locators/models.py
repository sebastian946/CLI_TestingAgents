"""Schema of the page.json: one file per page with its locators.

The page.json is the memory of the product. A locator is stored as data (a
strategy plus its arguments), never as code to run: `build_locator` is the only
place that turns it into a Playwright locator.
"""

from enum import StrEnum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# Version of the format written by this version of testwise. Raise it when the
# format changes in a way that old files need to be migrated.
SCHEMA_VERSION = 1

# Names become file names and identifiers in generated tests.
PAGE_NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
LOCATOR_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"
ROLE_PATTERN = r"^[a-z]+$"


class Strategy(StrEnum):
    """How an element is found. Each one maps to a Playwright method."""

    TEST_ID = "test_id"  # page.get_by_test_id(value)
    ROLE = "role"  # page.get_by_role(role, name=value)
    LABEL = "label"  # page.get_by_label(value)
    PLACEHOLDER = "placeholder"  # page.get_by_placeholder(value)
    TEXT = "text"  # page.get_by_text(value)
    CSS = "css"  # page.locator(value)


class LocatorStatus(StrEnum):
    UNVERIFIED = "unverified"  # Never checked against the real page.
    OK = "ok"  # Found exactly one element the last time it was checked.
    BROKEN = "broken"  # No longer finds its element.


class Locator(BaseModel):
    """One element of a page and how to find it."""

    # A typo in a field name is an error instead of being silently dropped.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=LOCATOR_NAME_PATTERN)
    description: str = ""
    strategy: Strategy
    # ARIA role of the element. Only used, and required, with the role strategy.
    role: str | None = Field(default=None, pattern=ROLE_PATTERN)
    # Argument of the strategy: the test id, the accessible name, the label, the
    # placeholder, the text or the CSS selector.
    value: str | None = None
    status: LocatorStatus = LocatorStatus.UNVERIFIED
    last_verified: AwareDatetime | None = None

    @model_validator(mode="after")
    def _check_strategy_arguments(self) -> "Locator":
        if self.strategy is Strategy.ROLE:
            if self.role is None:
                raise ValueError(
                    "strategy 'role' needs a 'role' (for example \"button\")"
                )
        else:
            if self.role is not None:
                raise ValueError(
                    f"'role' is only used with strategy 'role', not with "
                    f"'{self.strategy.value}'"
                )
            if not (self.value or "").strip():
                raise ValueError(
                    f"strategy '{self.strategy.value}' needs a non-empty 'value'"
                )
        return self


class PageContext(BaseModel):
    """Everything testwise remembers about one page."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    name: str = Field(pattern=PAGE_NAME_PATTERN)
    url: str
    captured_at: AwareDatetime
    locators: list[Locator] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, version: int) -> int:
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"this file uses schema version {version}, but this version of "
                f"testwise only understands up to {SCHEMA_VERSION}. Update testwise"
            )
        if version < 1:
            raise ValueError("must be 1 or greater")
        return version

    @field_validator("url")
    @classmethod
    def _check_url(cls, url: str) -> str:
        if not url.startswith(("http://", "https://", "file://")):
            raise ValueError("must start with http:// or https://")
        return url

    @model_validator(mode="after")
    def _check_unique_names(self) -> "PageContext":
        seen: set[str] = set()
        for locator in self.locators:
            if locator.name in seen:
                raise ValueError(f"two locators are named '{locator.name}'")
            seen.add(locator.name)
        return self

    def locator(self, name: str) -> Locator:
        """The locator called `name`. Raises KeyError if there is none."""
        for locator in self.locators:
            if locator.name == name:
                return locator
        raise KeyError(name)
