"""Explainable keyword classification without external models."""

from __future__ import annotations

import re
from collections.abc import Iterable

from f117.domain import Category, NormalizedItem

_CATEGORY_PATTERNS: dict[Category, tuple[re.Pattern[str], ...]] = {
    Category.AI: tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"(?<![\w])a\.?i\.?(?![\w])",
            r"\bartificial intelligence\b",
            r"\bmachine learning\b",
            r"\bdeep learning\b",
            r"\bneural networks?\b",
            r"\bgenerative ai\b",
            r"\b(?:openai|anthropic|deepmind|xai)\b",
            r"\bai safety\b",
            r"\balignment\b",
            r"\bloss of control\b",
            r"\bискусственн(?:ый|ого|ому|ым) интеллект\w*\b",
            r"\bбезопасност[ьи] (?:ии|ai)\b",
            r"\bмашинн(?:ое|ого|ому|ым) обучени\w*\b",
        )
    ),
    Category.LLM: tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"(?<![\w])llms?(?![\w])",
            r"\blarge language models?\b",
            r"(?<![\w])gpt[-\s]?\d*[a-z]?(?![\w])",
            r"\bchatgpt\b",
            r"\bclaude(?:\s+\d+(?:\.\d+)?)?\b",
            r"\bgemini(?:\s+\d+(?:\.\d+)?)?\b",
            r"\blanguage model\b",
            r"\bязыков(?:ая|ой|ую|ые) модел\w*\b",
        )
    ),
    Category.ROBOTICS: tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\brobot(?:s|ics|ic)?\b",
            r"\bhumanoids?\b",
            r"\bquadruped\b",
            r"\bphysical ai\b",
            r"\bробот\w*\b",
            r"\bгуманоид\w*\b",
        )
    ),
    Category.RESEARCH: tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\barxiv\b",
            r"\bresearch(?:ers?)?\b",
            r"\bscientists?\b",
            r"\bpeer[-\s]?reviewed\b",
            r"\b(?:new |research )?paper\b",
            r"\bstudy (?:finds|shows|reveals)\b",
            r"\bисследовани\w*\b",
            r"\bнаучн(?:ая|ой|ое|ые) работ\w*\b",
        )
    ),
    Category.OPEN_SOURCE: tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\bopen[-\s]?source\b",
            r"\bsource code\b",
            r"\bgithub(?:\.com)?\b",
            r"\bcode repository\b",
            r"\bapache[-\s]?2(?:\.0)? license\b",
            r"\bmit license\b",
            r"\bоткрыт(?:ый|ого|ым) (?:исходн(?:ый|ого) )?код\w*\b",
        )
    ),
    Category.HARDWARE: tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"(?<![\w])gpus?(?![\w])",
            r"\bgraphics processing units?\b",
            r"\bai (?:chip|accelerator)s?\b",
            r"\bsemiconductors?\b",
            r"\b(?:nvidia|amd) (?:h\d{2,3}|b\d{2,3}|mi\d{2,3})\b",
            r"\bcuda\b",
            r"\bвычислительн(?:ый|ого|ым) ускорител\w*\b",
            r"\bграфическ(?:ий|ого|им) процессор\w*\b",
        )
    ),
    Category.BRAIN_INTERFACE: tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\bbrain[-\s](?:computer|machine) interface\b",
            r"(?<![\w])bci(?![\w])",
            r"\bneuralink\b",
            r"\bneural implants?\b",
            r"\bbrain implants?\b",
            r"\bneurointerfaces?\b",
            r"\bнейроинтерфейс\w*\b",
            r"\bмозгов(?:ой|ого|ым) имплант\w*\b",
        )
    ),
    Category.CYBERCULTURE: tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\bcyberpunk\b",
            r"\bcyberculture\b",
            r"\bneuromancer\b",
            r"\bblade[ -]runner\b",
            r"\balien\b",
            r"\b(?:science[ -]fiction|sci[ -]fi)\b",
            r"\btranshuman(?:ism|ist)?\b",
            r"\bdigital subcultures?\b",
            r"\binternet culture\b",
            r"\bhacker culture\b",
            r"\b(?:digital identity|synthetic media|deepfakes?|virtual worlds?)\b",
            r"\b(?:surveillance|privacy|dystopian technology)\b",
            r"\bai[- ]generated (?:culture|media|music|film|video|art)\b",
            r"\btech(?:nology)? memes?\b",
            r"\b(?:ai|robot(?:ics)?) memes?\b",
            r"\b(?:ai|robot|hacking) (?:film|movie|series|show|game|adaptation)\b",
            r"\b(?:film|movie|series|show|game|adaptation) "
            r"(?:about|featuring) (?:ai|robots?|hacking)\b",
            r"\bкиберпанк\w*\b",
            r"\bтрансгуманизм\w*\b",
            r"\bинтернет.культур\w*\b",
        )
    ),
    Category.FUNNY: tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\bfunny\b",
            r"\bhilarious\b",
            r"\b(?:ai|robot) fails?\b",
            r"\b(?:ai|robot) bloopers?\b",
            r"\bmemes?\b",
            r"\bзабавн\w*\b",
            r"\bсмешн\w*\b",
            r"\bмем\w*\b",
        )
    ),
    Category.WTF: tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"(?<![\w])wtf(?![\w])",
            r"\bbizarre\b",
            r"\babsurd\b",
            r"\bweird (?:ai|robot|experiment|research|project)\b",
            r"\bstrange (?:ai|robot|experiment|research|project)\b",
            r"\bбезумн\w* (?:ии|робот|эксперимент|исследовани|проект)\w*\b",
            r"\bстранн\w* (?:ии|робот|эксперимент|исследовани|проект)\w*\b",
        )
    ),
}


def classify_text(title: str, description: str = "") -> list[Category]:
    """Return every matching category in stable enum order."""

    text = f"{title}\n{description}"
    matched = {
        category
        for category, patterns in _CATEGORY_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    }

    # An LLM is a kind of AI even when a headline only names the model family.
    if Category.LLM in matched:
        matched.add(Category.AI)

    if not matched:
        return [Category.OTHER]
    return [category for category in Category if category in matched]


def classify_item(
    item: NormalizedItem,
    default_categories: Iterable[Category] = (),
) -> NormalizedItem:
    """Classify an item, preserving explicit source defaults as extra labels."""

    categories = set(item.source_categories)
    categories.update(default_categories)
    categories.update(classify_text(item.title, item.description))
    categories.update(item.categories)
    if len(categories) > 1:
        categories.discard(Category.OTHER)
    ordered_categories = [category for category in Category if category in categories]
    return item.model_copy(update={"categories": ordered_categories})
