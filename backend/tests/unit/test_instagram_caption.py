"""Instagram caption rules: length, hashtags and mentions (D49)."""

import pytest

from social_media_auto_poster.platforms.instagram.rules import (
    MAX_CAPTION_CHARS,
    MAX_HASHTAGS,
    MAX_MENTIONS,
    caption_lengths,
    caption_problems,
    count_hashtags,
    count_mentions,
)

EMOJI = "\U0001f600"  # one code point, two UTF-16 units


def test_limits_match_the_documented_numbers():
    assert (MAX_CAPTION_CHARS, MAX_HASHTAGS, MAX_MENTIONS) == (2200, 30, 20)


# Length


def test_caption_lengths_counts_code_points_and_utf16_units():
    assert caption_lengths("ab" + EMOJI) == (3, 4)


def test_2200_characters_is_accepted():
    assert caption_problems("a" * 2200) == []


def test_2201_characters_is_refused():
    assert caption_problems("a" * 2201) == ["caption is 2,201 characters; Instagram allows at most 2,200"]


def test_1100_emoji_is_accepted():
    assert caption_lengths(EMOJI * 1100) == (1100, 2200)
    assert caption_problems(EMOJI * 1100) == []


def test_1101_emoji_is_refused_by_utf16_units():
    assert caption_problems(EMOJI * 1101) == ["caption is 2,202 characters; Instagram allows at most 2,200"]


def test_code_points_under_but_utf16_over_is_refused():
    text = "a" * 2000 + EMOJI * 101
    assert caption_lengths(text) == (2101, 2202)
    assert caption_problems(text) == ["caption is 2,202 characters; Instagram allows at most 2,200"]


# Hashtags


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("C#", 0),
        ("C#1", 1),
        ("#tag", 1),
        ("##tag", 1),
        ("# tag", 0),
        ("#café", 1),
        ("#_x", 1),
        ("#a #b,#c", 3),
        ("", 0),
    ],
)
def test_count_hashtags(text, expected):
    assert count_hashtags(text) == expected


def test_30_hashtags_is_accepted():
    assert caption_problems(" ".join(f"#t{i}" for i in range(30))) == []


def test_31_hashtags_is_refused():
    text = " ".join(f"#t{i}" for i in range(31))
    assert caption_problems(text) == ["caption has 31 hashtags; Instagram allows at most 30"]


# Mentions


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("me@example.com", 1),
        ("@ x", 0),
        ("@@user", 1),
        ("@user", 1),
        ("@.x @_y", 2),
        ("@é", 0),
        ("", 0),
    ],
)
def test_count_mentions(text, expected):
    assert count_mentions(text) == expected


def test_20_mentions_is_accepted():
    assert caption_problems(" ".join(f"@u{i}" for i in range(20))) == []


def test_21_mentions_is_refused():
    text = " ".join(f"@u{i}" for i in range(21))
    assert caption_problems(text) == [
        "caption has 21 @mentions (e-mail addresses count too); Instagram allows at most 20"
    ]


# Together


def test_several_problems_come_back_in_order():
    text = " ".join(f"#t{i}" for i in range(31)) + " " + " ".join(f"@u{i}" for i in range(21))
    text += " " + "a" * 2200
    assert caption_problems(text) == [
        f"caption is {len(text):,} characters; Instagram allows at most 2,200",
        "caption has 31 hashtags; Instagram allows at most 30",
        "caption has 21 @mentions (e-mail addresses count too); Instagram allows at most 20",
    ]


@pytest.mark.parametrize("text", ["", "Hello world", "Launch day (beta) | 50% off"])
def test_plain_caption_has_no_problems(text):
    assert caption_problems(text) == []
