"""Deterministic filter for accessibility snapshots.

The raw snapshot of a real page can weigh tens of thousands of tokens. This
module keeps only what the LLM needs to write tests: interactive elements,
headings, the regions that give them context and short texts.

Everything here is a pure function: text in, text out. No browser, no LLM.
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_NAME_LENGTH = 80
MAX_TEXT_LENGTH = 80
MAX_SIMILAR = 3
CHARS_PER_TOKEN = 4  # Rough average for English text and YAML.
ELLIPSIS = "…"

# Elements the user can act on. They are never dropped.
INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "searchbox",
        "checkbox",
        "radio",
        "combobox",
        "listbox",
        "option",
        "switch",
        "slider",
        "spinbutton",
        "tab",
        "menuitem",
    }
)

# Regions kept as context: they tell where an element lives ("the button in the
# login form"). A region left with nothing inside is dropped.
REGION_ROLES = frozenset(
    {
        "form",
        "navigation",
        "main",
        "dialog",
        "alertdialog",
        "search",
        "banner",
        "contentinfo",
        "complementary",
        "region",
    }
)

# Containers that group related elements (a product card, a table row). They
# are kept only when they hold two or more things; otherwise their content
# moves up one level.
GROUP_ROLES = frozenset(
    {
        "list",
        "listitem",
        "article",
        "table",
        "rowgroup",
        "row",
        "cell",
        "gridcell",
        "grid",
        "group",
        "radiogroup",
        "tablist",
        "tabpanel",
        "menu",
        "menubar",
        "toolbar",
        "tree",
        "treeitem",
    }
)

# Never useful as text for the LLM.
IGNORED_ROLES = frozenset({"img", "separator", "presentation", "none", "iframe"})

# Properties of an element (lines like `- /url: ...`) worth keeping.
KEPT_PROPERTIES = frozenset({"/placeholder"})

_LINE = re.compile(r"^(?P<indent> *)- (?P<content>.*)$")
_KEY = re.compile(
    r'^(?P<role>[^\s"]+)'
    r'(?:\s+(?P<name>"(?:[^"\\]|\\.)*"))?'
    r"(?P<attrs>(?:\s+\[[^\]]*\])*)\s*$"
)
_NEEDS_QUOTES = re.compile(r"^[\s\-?:,\[\]{}#&*!|>'\"%@`]|: | #|:$|\s$")
_YAML_KEYWORDS = frozenset({"true", "false", "null", "yes", "no", "on", "off", "~"})


@dataclass
class Node:
    """One line of the snapshot: `- role "name" [attrs]: value`."""

    role: str
    name: str | None = None
    attrs: str = ""
    value: str | None = None
    children: list["Node"] = field(default_factory=list)

    @property
    def is_property(self) -> bool:
        return self.role.startswith("/")

    @property
    def is_interactive(self) -> bool:
        return self.role in INTERACTIVE_ROLES


@dataclass(frozen=True)
class FilterStats:
    """Size of a snapshot before and after filtering."""

    raw_chars: int
    filtered_chars: int

    @property
    def raw_tokens(self) -> int:
        return self.raw_chars // CHARS_PER_TOKEN

    @property
    def filtered_tokens(self) -> int:
        return self.filtered_chars // CHARS_PER_TOKEN

    @property
    def reduction(self) -> float:
        """Fraction removed, between 0 and 1."""
        if self.raw_chars == 0:
            return 0.0
        return 1 - self.filtered_chars / self.raw_chars

    def __str__(self) -> str:
        return (
            f"{self.raw_chars:,} -> {self.filtered_chars:,} chars "
            f"(~{self.raw_tokens:,} -> ~{self.filtered_tokens:,} tokens, "
            f"{self.reduction:.0%} smaller)"
        )


def filter_snapshot(
    snapshot: str,
    *,
    max_name_length: int = MAX_NAME_LENGTH,
    max_text_length: int = MAX_TEXT_LENGTH,
    max_similar: int | None = MAX_SIMILAR,
    keep_urls: bool = False,
) -> str:
    """Reduce an accessibility snapshot to what the LLM needs.

    - Interactive elements and headings are always kept.
    - Regions (form, navigation, main, dialog...) are kept as context.
    - Static text longer than `max_text_length` is dropped.
    - Names longer than `max_name_length` are truncated.
    - Runs of more than `max_similar` similar groups (product cards, table
      rows) keep the first ones plus a note with the total. Pass None to keep
      them all.
    - Link URLs are dropped unless `keep_urls` is set.
    """
    options = _Options(max_name_length, max_text_length, max_similar, keep_urls)
    nodes = [
        kept for node in parse_snapshot(snapshot) for kept in _filter(node, options)
    ]
    return render_snapshot(_tidy(nodes, None, options))


def filter_with_stats(snapshot: str, **options) -> tuple[str, FilterStats]:
    """Filter a snapshot and report (and log) its size before and after."""
    filtered = filter_snapshot(snapshot, **options)
    stats = measure(snapshot, filtered)
    logger.info("Snapshot filtered: %s", stats)
    return filtered, stats


def measure(raw: str, filtered: str) -> FilterStats:
    return FilterStats(raw_chars=len(raw), filtered_chars=len(filtered))


# --- Parsing ----------------------------------------------------------------


def parse_snapshot(snapshot: str) -> list[Node]:
    """Parse the YAML produced by Playwright's `aria_snapshot()` into a tree.

    The format is a small subset of YAML: nested lists where each item is
    `- key`, `- key: text` or `- key:` followed by indented children.
    """
    roots: list[Node] = []
    # Stack of (indentation, node) from the root down to the current parent.
    stack: list[tuple[int, Node]] = []
    for line in snapshot.splitlines():
        match = _LINE.match(line)
        if match is None:
            continue
        indent = len(match["indent"])
        node = _parse_item(match["content"])
        while stack and stack[-1][0] >= indent:
            stack.pop()
        (stack[-1][1].children if stack else roots).append(node)
        stack.append((indent, node))
    return roots


def _parse_item(content: str) -> Node:
    key, rest = _split_key(content)
    value = _unquote(rest[1:].strip()) if rest.startswith(":") else None
    match = _KEY.match(key)
    if match is None:
        return Node(role=key, value=value or None)
    name = match["name"]
    return Node(
        role=match["role"],
        name=_unquote(name) if name else None,
        attrs=match["attrs"].strip(),
        value=value or None,
    )


def _split_key(content: str) -> tuple[str, str]:
    """Split an item into its key and whatever follows it (`: value` or empty)."""
    if content[:1] in ("'", '"'):
        end = _closing_quote(content)
        if end != -1:
            return _unquote(content[: end + 1]), content[end + 1 :]
    in_name = False
    escaped = False
    for index, char in enumerate(content):
        if escaped:
            escaped = False
        elif in_name and char == "\\":
            escaped = True
        elif char == '"':
            in_name = not in_name
        elif (
            char == ":" and not in_name and content[index + 1 : index + 2] in ("", " ")
        ):
            return content[:index], content[index:]
    return content, ""


def _closing_quote(text: str) -> int:
    """Index of the quote that closes the YAML scalar starting at text[0]."""
    quote = text[0]
    index = 1
    while index < len(text):
        char = text[index]
        if quote == '"' and char == "\\":
            index += 2
            continue
        if char == quote:
            if quote == "'" and text[index + 1 : index + 2] == "'":
                index += 2  # '' is an escaped quote.
                continue
            return index
        index += 1
    return -1


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return json.loads(text)
        except ValueError:
            return text[1:-1]
    return text


# --- Filtering --------------------------------------------------------------


@dataclass(frozen=True)
class _Options:
    max_name_length: int
    max_text_length: int
    max_similar: int | None
    keep_urls: bool


def _filter(node: Node, options: _Options, labelled: bool = False) -> list[Node]:
    """Return what is kept of `node`: itself, its content moved up, or nothing.

    `labelled` is True inside an element whose name already describes its
    content (a link, a heading): static text there would only repeat the name.
    """
    if node.is_property or node.role in IGNORED_ROLES:
        return []

    if node.is_interactive:
        return [_keep_interactive(node, options)]

    if node.role == "heading":
        children = _filter_children(node, options, labelled=True)
        return [_copy(node, options, children=children)]

    children = _filter_children(node, options, labelled)
    if not children:
        return _keep_text(node, options, labelled)

    if node.role in REGION_ROLES:
        return [_copy(node, options, children=children)]

    if node.role in GROUP_ROLES and (len(children) > 1 or node.name):
        kept = _copy(node, options, children=children)
        if node.role == "row":
            kept.name = None  # A row's name just repeats the text of its cells.
        return [kept]

    # Any other wrapper (paragraph, generic, a group with one item) adds nothing.
    return children


def _keep_interactive(node: Node, options: _Options) -> Node:
    properties = [
        Node(role=child.role, value=child.value)
        for child in node.children
        if child.role in KEPT_PROPERTIES or (child.role == "/url" and options.keep_urls)
    ]
    children = _filter_children(node, options, labelled=True)
    if not node.name and not children:
        # Without a name, the only way to tell the element apart is its content
        # (an icon's alternative text, for example).
        children = _labels(node, options)
    kept = _copy(node, options, children=children)
    kept.children = properties + kept.children
    if node.value:
        kept.value = _truncate(_one_line(node.value), options.max_text_length)
    return kept


def _filter_children(node: Node, options: _Options, labelled: bool) -> list[Node]:
    return [
        kept for child in node.children for kept in _filter(child, options, labelled)
    ]


def _keep_text(node: Node, options: _Options, labelled: bool) -> list[Node]:
    """Keep a node without children only if it is short static text."""
    text = _one_line(node.value or node.name or "")
    if labelled or not text or len(text) > options.max_text_length:
        return []
    if node.value:
        return [Node(role=node.role, attrs=node.attrs, value=text)]
    return [Node(role=node.role, name=text, attrs=node.attrs)]


def _labels(node: Node, options: _Options) -> list[Node]:
    """Short texts and image names found inside `node`, in document order."""
    labels: list[Node] = []
    for child in node.children:
        if child.is_property:
            continue
        text = _one_line(child.value or child.name or "")
        if text and not child.children:
            labels.append(
                Node(role=child.role, name=_truncate(text, options.max_name_length))
            )
        labels.extend(_labels(child, options))
    return labels


def _copy(node: Node, options: _Options, children: list[Node]) -> Node:
    name = node.name
    if name is not None:
        name = _truncate(name, options.max_name_length)
    children = _tidy(children, node.name, options)
    return Node(role=node.role, name=name, attrs=node.attrs, children=children)


def _tidy(nodes: list[Node], parent_name: str | None, options: _Options) -> list[Node]:
    """Clean a final list of siblings: drop repeated labels, collapse groups."""
    return _collapse(_drop_echoes(nodes, parent_name), options)


def _drop_echoes(nodes: list[Node], parent_name: str | None) -> list[Node]:
    """Drop static text that repeats the name of its parent or of a sibling control.

    A visible label next to its field (`text: Male` beside `radio "Male"`) says
    nothing the control's name does not already say.
    """
    names = {
        _one_line(node.name) for node in nodes if node.is_interactive and node.name
    }
    if parent_name:
        names.add(_one_line(parent_name))
    return [node for node in nodes if not (_is_text(node) and _text(node) in names)]


def _is_text(node: Node) -> bool:
    return not node.is_interactive and not node.children and node.role != "heading"


def _text(node: Node) -> str:
    return _one_line(node.value or node.name or "")


def _collapse(nodes: list[Node], options: _Options) -> list[Node]:
    """Shorten runs of similar groups to the first ones plus a note."""
    if options.max_similar is None:
        return nodes
    result: list[Node] = []
    index = 0
    while index < len(nodes):
        run = _similar_run(nodes, index)
        if len(run) > options.max_similar and _is_repetition(run):
            omitted = len(run) - options.max_similar
            result.extend(run[: options.max_similar])
            result.append(
                Node(
                    role="text",
                    value=(
                        f"({omitted} more similar {run[0].role} elements omitted, "
                        f"{len(run)} in total)"
                    ),
                )
            )
        else:
            result.extend(run)
        index += len(run)
    return result


def _similar_run(nodes: list[Node], start: int) -> list[Node]:
    """Consecutive nodes, from `start`, with the same shape as the first one."""
    first = nodes[start]
    if first.role not in GROUP_ROLES or not first.children:
        return [first]
    shape = _shape(first)
    end = start + 1
    while end < len(nodes) and nodes[end].children and _shape(nodes[end]) == shape:
        end += 1
    return nodes[start:end]


def _shape(node: Node) -> tuple[str, frozenset[str]]:
    """What makes two groups look alike: their role and the controls inside."""
    return node.role, frozenset(role for role, _ in _controls(node))


def _is_repetition(run: list[Node]) -> bool:
    """True for 50 product cards, false for 8 different menu sections.

    Cards repeat the same control in every item ("Add to cart"), or have no
    controls at all (rows of plain data). Menu sections only hold controls
    with different names, and dropping any of them would lose information.
    """
    controls = [set(_controls(node)) for node in run]
    if not any(controls):
        return True
    return bool(set.intersection(*controls))


def _controls(node: Node) -> list[tuple[str, str]]:
    """(role, name) of every interactive element under `node`."""
    found = []
    for child in node.children:
        if child.is_interactive:
            found.append((child.role, child.name or ""))
        found.extend(_controls(child))
    return found


def _one_line(text: str) -> str:
    """Collapse whitespace and remove icon-font glyphs (private-use characters)."""
    visible = "".join(char for char in text if unicodedata.category(char) != "Co")
    return " ".join(visible.split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + ELLIPSIS


# --- Rendering --------------------------------------------------------------


def render_snapshot(nodes: list[Node]) -> str:
    """Write a tree back in the same YAML format that `parse_snapshot` reads."""
    lines: list[str] = []
    _render(nodes, 0, lines)
    return "\n".join(lines)


def _render(nodes: list[Node], depth: int, lines: list[str]) -> None:
    for node in nodes:
        key = node.role
        if node.name is not None:
            key += f" {json.dumps(node.name, ensure_ascii=False)}"
        if node.attrs:
            key += f" {node.attrs}"
        line = f"{'  ' * depth}- {_quote(key)}"
        if node.children:
            line += ":"
        elif node.value is not None:
            line += f": {_quote(node.value, is_value=True)}"
        lines.append(line)
        _render(node.children, depth + 1, lines)


def _quote(text: str, is_value: bool = False) -> str:
    """Quote a YAML scalar only when plain style would change its meaning."""
    plain = not _NEEDS_QUOTES.search(text) and text != ""
    if plain and is_value:
        plain = text.lower() not in _YAML_KEYWORDS and not _is_number(text)
    if plain:
        return text
    if any(ord(char) < 32 for char in text):
        return json.dumps(text, ensure_ascii=False)
    return "'" + text.replace("'", "''") + "'"


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True
