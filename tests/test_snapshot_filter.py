import logging
from collections import Counter
from pathlib import Path

import pytest

from testwise.explorer.snapshot_filter import (
    INTERACTIVE_ROLES,
    FilterStats,
    Node,
    filter_snapshot,
    filter_with_stats,
    measure,
    parse_snapshot,
    render_snapshot,
)

SNAPSHOTS = Path(__file__).parent / "fixtures" / "snapshots"
FIXTURES = ("login", "listing", "long_form")
LONG_TEXT = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 3


def load(name: str) -> str:
    return (SNAPSHOTS / f"{name}.yaml").read_text(encoding="utf-8")


def snapshot(*lines: str) -> str:
    return "\n".join(lines)


def controls(text: str, name_length: int = 80) -> Counter:
    """(role, name) of every interactive element in a snapshot, with counts."""
    found: Counter = Counter()

    def visit(nodes: list[Node]) -> None:
        for node in nodes:
            if node.role in INTERACTIVE_ROLES:
                found[(node.role, (node.name or "")[:name_length])] += 1
            visit(node.children)

    visit(parse_snapshot(text))
    return found


# --- Parsing ----------------------------------------------------------------


def test_parse_builds_a_tree_from_indentation():
    nodes = parse_snapshot(
        snapshot(
            "- main:",
            '  - heading "Welcome" [level=1]',
            '  - form "Login":',
            '    - textbox "Username": alice',
            "- text: bye",
        )
    )

    main, text = nodes
    heading, form = main.children
    assert (main.role, main.name) == ("main", None)
    assert (heading.role, heading.name, heading.attrs) == (
        "heading",
        "Welcome",
        "[level=1]",
    )
    assert (form.role, form.name) == ("form", "Login")
    textbox = form.children[0]
    assert (textbox.role, textbox.name, textbox.value) == (
        "textbox",
        "Username",
        "alice",
    )
    assert (text.role, text.value) == ("text", "bye")


def test_parse_reads_keys_quoted_by_yaml():
    nodes = parse_snapshot(
        snapshot(
            "- 'link \"Sapiens: A Brief History\"':",
            "  - /url: catalogue/sapiens.html",
            "- 'button \"It''s here\"'",
        )
    )

    link, button = nodes
    assert (link.role, link.name) == ("link", "Sapiens: A Brief History")
    assert link.children[0].role == "/url"
    assert link.children[0].value == "catalogue/sapiens.html"
    assert (button.role, button.name) == ("button", "It's here")


def test_parse_keeps_colons_and_quotes_inside_names_and_values():
    nodes = parse_snapshot(
        snapshot(
            '- link "Say \\"hi\\": now"',
            "- text: 'Total: $10'",
            '- text: "*"',
            "- /url: https://example.com/a?b=1",
        )
    )

    assert nodes[0].name == 'Say "hi": now'
    assert nodes[1].value == "Total: $10"
    assert nodes[2].value == "*"
    assert nodes[3].value == "https://example.com/a?b=1"


@pytest.mark.parametrize("name", FIXTURES)
def test_render_writes_what_parse_reads(name):
    tree = parse_snapshot(load(name))

    assert parse_snapshot(render_snapshot(tree)) == tree


# --- What is kept -----------------------------------------------------------


@pytest.mark.parametrize("role", sorted(INTERACTIVE_ROLES))
def test_every_interactive_role_is_kept(role):
    assert filter_snapshot(f'- {role} "Thing"') == f'- {role} "Thing"'


def test_headings_are_kept_with_their_level():
    raw = snapshot('- heading "Products" [level=2]', f"- paragraph: {LONG_TEXT}")

    assert filter_snapshot(raw) == '- heading "Products" [level=2]'


def test_regions_are_kept_as_context():
    raw = snapshot(
        "- navigation:",
        '  - link "Home"',
        "- main:",
        '  - form "Login":',
        '    - button "Log in"',
        '- dialog "Cookies":',
        '  - button "Accept"',
    )

    assert filter_snapshot(raw) == raw


def test_regions_left_empty_are_dropped():
    raw = snapshot("- navigation:", "  - img", '- button "Go"')

    assert filter_snapshot(raw) == '- button "Go"'


def test_element_state_is_kept():
    raw = snapshot(
        '- checkbox "Remember me" [checked]',
        '- button "Pay" [disabled]',
    )

    assert filter_snapshot(raw) == raw


def test_field_values_and_placeholders_are_kept():
    raw = snapshot(
        '- textbox "Email": alice@example.com',
        '- textbox "Search":',
        "  - /placeholder: Search products",
    )

    assert filter_snapshot(raw) == raw


# --- What is dropped --------------------------------------------------------


def test_long_static_text_is_dropped_and_short_text_is_kept():
    raw = snapshot(f"- paragraph: {LONG_TEXT}", "- paragraph: $29.99")

    assert filter_snapshot(raw) == "- paragraph: $29.99"


def test_names_longer_than_the_limit_are_truncated():
    long_name = "A" * 120

    filtered = filter_snapshot(f'- link "{long_name}"')

    assert filtered == f'- link "{"A" * 80}…"'


def test_limits_are_configurable():
    raw = snapshot('- button "Add to cart"', "- text: In stock")

    filtered = filter_snapshot(raw, max_name_length=6, max_text_length=5)

    assert filtered == '- button "Add to…"'


def test_link_urls_are_dropped_unless_requested():
    raw = snapshot('- link "Home":', "  - /url: /index.html")

    assert filter_snapshot(raw) == '- link "Home"'
    assert filter_snapshot(raw, keep_urls=True) == raw


def test_images_and_text_inside_a_named_control_are_dropped():
    raw = snapshot(
        '- link "Backpack $29.99":',
        '  - img "Backpack"',
        "  - text: $29.99",
    )

    assert filter_snapshot(raw) == '- link "Backpack $29.99"'


def test_control_without_a_name_keeps_its_content_as_label():
    raw = snapshot("- link:", "  - /url: /cart", '  - img "Shopping cart"')

    assert filter_snapshot(raw) == snapshot("- link:", '  - img "Shopping cart"')


def test_controls_nested_in_other_elements_are_kept():
    raw = snapshot(
        '- heading "Blue shirt" [level=3]:',
        '  - link "Blue shirt"',
        '- combobox "Size":',
        '  - option "Small"',
        '  - option "Large" [selected]',
    )

    assert filter_snapshot(raw) == raw


def test_wrappers_with_a_single_item_are_flattened():
    raw = snapshot(
        "- list:",
        "  - listitem:",
        '    - link "Home"',
        "  - listitem:",
        '    - link "Cart"',
    )

    assert filter_snapshot(raw) == snapshot(
        "- list:",
        '  - link "Home"',
        '  - link "Cart"',
    )


def test_label_text_that_repeats_a_control_name_is_dropped():
    raw = snapshot(
        '- radio "Male"',
        "- text: Male",
        "- text: Gender",
        '- group "Hobbies":',
        "  - text: Hobbies",
        '  - checkbox "Music"',
        '  - checkbox "Sports"',
    )

    assert filter_snapshot(raw) == snapshot(
        '- radio "Male"',
        "- text: Gender",
        '- group "Hobbies":',
        '  - checkbox "Music"',
        '  - checkbox "Sports"',
    )


def test_icon_font_glyphs_are_not_text():
    raw = snapshot("- paragraph:  ", "- paragraph:  In stock")

    assert filter_snapshot(raw) == "- paragraph: In stock"


# --- Repeated groups --------------------------------------------------------


def cards(count: int) -> str:
    lines = ["- list:"]
    for number in range(1, count + 1):
        lines += [
            "  - listitem:",
            f'    - link "Product {number}"',
            f"    - text: ${number}.00",
            '    - button "Add to cart"',
        ]
    return snapshot(*lines)


def test_repeated_cards_collapse_to_three_plus_a_note_with_the_total():
    filtered = filter_snapshot(cards(50))

    assert filtered.count('button "Add to cart"') == 3
    assert 'link "Product 3"' in filtered
    assert 'link "Product 4"' not in filtered
    assert "(47 more similar listitem elements omitted, 50 in total)" in filtered


def test_three_cards_or_fewer_are_left_alone():
    assert filter_snapshot(cards(3)) == cards(3)


def test_collapsing_can_be_tuned_or_turned_off():
    assert filter_snapshot(cards(10), max_similar=None) == cards(10)
    assert filter_snapshot(cards(10), max_similar=5).count("Add to cart") == 5


def test_plain_lists_of_different_links_are_not_collapsed():
    lines = ["- navigation:"] + [f'  - link "Category {n}"' for n in range(30)]
    raw = snapshot(*lines)

    assert filter_snapshot(raw) == raw


def test_menu_sections_with_different_controls_are_not_collapsed():
    lines = ["- list:"]
    for section in range(8):
        lines += [
            "  - listitem:",
            f'    - link "Section {section}"',
            f'    - link "Subsection {section}"',
        ]
    raw = snapshot(*lines)

    assert filter_snapshot(raw) == raw


def test_rows_of_plain_data_are_collapsed():
    lines = ["- table:"]
    for number in range(40):
        lines += [
            f'  - row "Order {number} Paid":',
            f'    - cell "Order {number}"',
            '    - cell "Paid"',
        ]

    filtered = filter_snapshot(snapshot(*lines))

    assert filtered.count("- row:") == 3
    assert "(37 more similar row elements omitted, 40 in total)" in filtered


# --- Fixtures from real pages ----------------------------------------------


def test_login_page():
    assert filter_snapshot(load("login")) == snapshot(
        "- text: Swag Labs",
        "- main:",
        '  - form "Login":',
        '    - textbox "Username"',
        '    - textbox "Password"',
        '    - button "Login"',
        '  - heading "Accepted usernames are:" [level=4]',
        '  - heading "Password for all users:" [level=4]',
        "  - text: secret_sauce",
    )


@pytest.mark.parametrize("name", FIXTURES)
def test_no_interactive_element_is_lost(name):
    raw = load(name)

    assert controls(filter_snapshot(raw, max_similar=None)) == controls(raw)


@pytest.mark.parametrize("name", FIXTURES)
def test_collapsing_only_removes_elements_covered_by_a_note(name):
    raw = load(name)
    filtered = filter_snapshot(raw)

    missing = controls(raw) - controls(filtered)

    assert not missing or "more similar" in filtered
    if name != "listing":
        assert not missing


def test_listing_page_is_reduced_by_at_least_70_percent():
    raw = load("listing")
    filtered = filter_snapshot(raw)

    assert measure(raw, filtered).reduction >= 0.70
    assert filtered.count('button "Add to basket"') == 3
    assert "(17 more similar article elements omitted, 20 in total)" in filtered
    # Navigation links are all different, so every one of them is kept.
    assert filtered.count("- link") >= 50


def test_long_form_keeps_every_field_and_gets_smaller():
    raw = load("long_form")
    filtered = filter_snapshot(raw)

    for field in ("First name:", "Last name:", "Email:", "Confirm password:"):
        assert f'textbox "{field}"' in filtered
    assert 'radio "Male"' in filtered
    assert 'button "Register"' in filtered
    assert "/url" not in filtered
    assert measure(raw, filtered).reduction >= 0.40


@pytest.mark.parametrize("name", FIXTURES)
def test_filter_is_deterministic_and_stable(name):
    raw = load(name)
    filtered = filter_snapshot(raw)

    assert filter_snapshot(raw) == filtered
    # Filtering an already filtered snapshot changes nothing.
    assert filter_snapshot(filtered) == filtered


def test_empty_snapshot():
    assert filter_snapshot("") == ""


# --- Size -------------------------------------------------------------------


def test_stats_measure_size_before_and_after():
    stats = measure("x" * 1000, "x" * 250)

    assert stats == FilterStats(raw_chars=1000, filtered_chars=250)
    assert stats.raw_tokens == 250
    assert stats.filtered_tokens == 62
    assert stats.reduction == 0.75
    assert str(stats) == "1,000 -> 250 chars (~250 -> ~62 tokens, 75% smaller)"


def test_stats_of_an_empty_snapshot():
    assert measure("", "").reduction == 0.0


def test_filter_with_stats_logs_the_size(caplog):
    raw = load("listing")

    with caplog.at_level(logging.INFO, logger="testwise.explorer.snapshot_filter"):
        filtered, stats = filter_with_stats(raw)

    assert filtered == filter_snapshot(raw)
    assert stats == measure(raw, filtered)
    assert f"Snapshot filtered: {stats}" in caplog.text
