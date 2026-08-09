"""Deterministic audience-interest gate applied before paid editorial work."""

from __future__ import annotations

import re
from dataclasses import dataclass

from f117.domain import Category, RankedMaterial, StoredMaterial


@dataclass(frozen=True, slots=True)
class EditorialConfig:
    fit_weight: float = 0.65
    minimum_fit: float = 55.0
    minimum_delivery_score: float = 55.0
    github_min_stars: int = 1000
    github_min_star_velocity: float = 25.0
    github_min_forks: int = 100
    github_min_mentions: int = 2
    github_exceptional_fit: float = 88.0
    arxiv_min_fit: float = 72.0
    youtube_min_views: int = 50_000
    youtube_min_view_velocity: float = 2_000.0
    youtube_min_likes: int = 1_000
    youtube_min_mentions: int = 2
    reddit_min_upvotes: int = 250
    reddit_min_comments: int = 40
    reddit_min_velocity: float = 20.0
    reddit_min_mentions: int = 2
    reddit_min_fit: float = 75.0
    reddit_rss_min_fit: float = 80.0
    reddit_rss_exceptional_fit: float = 85.0
    urgent_min_fit: float = 92.0
    urgent_min_delivery_score: float = 76.0


@dataclass(frozen=True, slots=True)
class EditorialAssessment:
    fit: float
    delivery_score: float
    eligible: bool
    urgent: bool
    reasons: list[str]


_MAJOR_ENTITY = re.compile(
    r"\b(openai|anthropic|google(?: deepmind)?|deepmind|xai|meta|microsoft|apple|"
    r"nvidia|tesla|neuralink|figure|boston dynamics|unitree)\b",
    re.IGNORECASE,
)
_MAJOR_EVENT = re.compile(
    r"\b(releas(?:e|es|ed|ing)|launch(?:es|ed|ing)?|unveil(?:s|ed)?|announce[sd]?|"
    r"test(?:s|ing|ed)?|preview(?:s|ed)?|introduc(?:e|es|ed|ing)|"
    r"demonstrat(?:e|es|ed|ing)|first trailer|"
    r"new (?:frontier )?model|major update|breakthrough)\b",
    re.IGNORECASE,
)
_PUBLIC_IMPLICATION = re.compile(
    r"\b(restores?|restore[sd]?|regains?|speech|paralys(?:is|ed)|neural signals?|"
    r"brain signals?|after watching|watching human|(?:learns?|learning) "
    r"(?:(?:a )?task|manipulation)|new capability|"
    r"autonomous(?:ly)? discovers?|discover(?:s|ed)? (?:a )?vulnerabilit|"
    r"real[- ]world|in the wild|humanoid|prosthe(?:tic|sis)|implant|bci)\b",
    re.IGNORECASE,
)
_CULTURAL_EVENT = re.compile(
    r"\b(neuromancer|cyberpunk|transhuman(?:ism|ist)?|dystopi(?:a|an)|"
    r"adaptation|first trailer|science fiction|sci[- ]fi|internet culture|"
    r"digital subculture|tech meme|ai meme)\b",
    re.IGNORECASE,
)
_UNUSUAL = re.compile(
    r"\b(wtf|weird|bizarre|absurd|viral|unexpected|strange|first[- ]ever|"
    r"genuinely new|impossible|meme|fail(?:s|ure)?)\b",
    re.IGNORECASE,
)
_CONSUMER = re.compile(
    r"\b(iphone|android|wearable|headset|glasses|home robot|personal robot|"
    r"consumer|device|gadget|assistant)\b",
    re.IGNORECASE,
)
_ENTERPRISE = re.compile(
    r"\b(enterprise|kubernetes|observability|devops|data pipeline|cloud migration|"
    r"service mesh|distributed tracing|database connector|erp|crm|"
    r"infrastructure update|it operations?|workflow automation)\b",
    re.IGNORECASE,
)
_SPECIALIST = re.compile(
    r"\b(integrated power[- ]system|power grid|stability constraints?|"
    r"constrained (?:spectral )?optimization|mixed[- ]integer|lemma|theorem|"
    r"asymptotic|convex relaxation|optimal power flow|benchmark improvement|"
    r"hyperparameter tuning|ablation stud(?:y|ies)|scheduling formulation|"
    r"prompt[- ]engineer(?:ing)?|benchmark(?:ing)? (?:models?|results?))\b",
    re.IGNORECASE,
)
_OPEN_SOURCE_MOMENTUM = re.compile(
    r"\b(jumps?|surges?|trending|fastest[- ]growing|stars? rapidly|10k stars?|"
    r"major release|widely adopted)\b",
    re.IGNORECASE,
)
_COMMUNITY_CHATTER = re.compile(
    r"\b(thinking about (?:getting|buying)|which (?:model|subscription|tool) should i|"
    r"if you had to choose|what do you (?:think|use|recommend)|anyone else|help me choose|"
    r"really confused|this is why the vast majority|change my mind|unpopular opinion)\b",
    re.IGNORECASE,
)
_FRONTIER_EVENT = re.compile(
    r"\b(frontier model|new model|model capability|reasoning model|multimodal model)\b",
    re.IGNORECASE,
)
_REDDIT_RSS_EVENT = re.compile(
    r"\b(announce[sd]?|releas(?:e|es|ed|ing)|launch(?:es|ed|ing)?|"
    r"unveil(?:s|ed)?|introduc(?:e|es|ed|ing)|demo(?:s|ed|ing)?|"
    r"demonstrat(?:e|es|ed|ing)|test(?:s|ed|ing)|incident|breach|"
    r"vulnerabilit(?:y|ies)|restores?|first trailer|new (?:frontier )?model|"
    r"major update|breakthrough)\b",
    re.IGNORECASE,
)
_REDDIT_GENERIC_QUESTION = re.compile(
    r"\b(which|what|should i|can someone|does anyone|any recommendations?|"
    r"is it worth|how do i|what do you (?:think|use|recommend)|help me choose)\b",
    re.IGNORECASE,
)
_REDDIT_UNSUPPORTED_SPECULATION = re.compile(
    r"\b(rumou?r(?:ed|s)?|reportedly|unconfirmed|allegedly|leak(?:ed|s)?|"
    r"might be|could be|will reportedly|i think|we need to|can't trust)\b",
    re.IGNORECASE,
)


def assess_editorial_fit(
    stored: StoredMaterial,
    ranked: RankedMaterial,
    *,
    config: EditorialConfig,
) -> EditorialAssessment:
    """Estimate whether id:28's audience would actually open the material."""

    categories = set(stored.item.categories)
    text = f"{stored.item.title}\n{stored.item.description}"
    reasons: list[str] = []

    category_baselines = {
        Category.BRAIN_INTERFACE: 76.0,
        Category.CYBERCULTURE: 74.0,
        Category.ROBOTICS: 66.0,
        Category.LLM: 63.0,
        Category.AI: 58.0,
        Category.WTF: 56.0,
        Category.FUNNY: 50.0,
        Category.HARDWARE: 47.0,
        Category.OPEN_SOURCE: 44.0,
        Category.RESEARCH: 38.0,
        Category.OTHER: 20.0,
    }
    fit = max((category_baselines[category] for category in categories), default=20.0)
    reasons.append(f"category baseline: {fit:.0f}")

    major_entity = bool(_MAJOR_ENTITY.search(text))
    major_event = bool(_MAJOR_EVENT.search(text))
    public_implication = bool(_PUBLIC_IMPLICATION.search(text))
    cultural_event = bool(_CULTURAL_EVENT.search(text))
    unusual = bool(_UNUSUAL.search(text))

    if major_entity and major_event:
        fit += 22
        reasons.append("major recognizable company/product event")
    elif major_entity:
        fit += 8
        reasons.append("recognizable company/product")
    elif major_event and categories & {Category.AI, Category.LLM, Category.ROBOTICS}:
        fit += 9
        reasons.append("clear new AI/robotics event")
    if public_implication:
        fit += 20
        reasons.append("understandable broader consequence or striking capability")
    if major_entity and _FRONTIER_EVENT.search(text):
        fit += 18
        reasons.append("major frontier-model signal")
    if cultural_event and Category.CYBERCULTURE in categories:
        fit += 18
        reasons.append("cyberculture significance")
    if unusual:
        fit += 13
        reasons.append("unusual/viral/WTF appeal")
    if _CONSUMER.search(text):
        fit += 8
        reasons.append("consumer-tech relevance")
    if stored.independent_mentions >= 2:
        fit += min(12.0, 4.0 * (stored.independent_mentions - 1))
        reasons.append(f"independent mentions: {stored.independent_mentions}")
    if ranked.rising or ranked.hidden_gem or _OPEN_SOURCE_MOMENTUM.search(text):
        fit += 20
        reasons.append("strong current momentum")

    if _ENTERPRISE.search(text):
        fit -= 42
        reasons.append("penalty: enterprise/infrastructure minutiae")
    specialist = bool(_SPECIALIST.search(text))
    if specialist:
        fit -= 42
        reasons.append("penalty: specialist-only technical work")
    if _COMMUNITY_CHATTER.search(text):
        fit -= 35
        reasons.append("penalty: low-signal community chatter/advice thread")

    source_key = stored.item.source_key.casefold()
    github_gate = True
    if source_key.startswith("github-"):
        metrics = stored.item.popularity
        stars = float(metrics.get("github_stars", metrics.get("stars", 0.0)))
        velocity = float(metrics.get("github_stars_per_hour", metrics.get("stars_per_hour", 0.0)))
        forks = float(metrics.get("forks", 0.0))
        known_team_release = major_entity and bool(metrics.get("releases", 0.0))
        github_gate = (
            stars >= config.github_min_stars
            or velocity >= config.github_min_star_velocity
            or forks >= config.github_min_forks
            or stored.independent_mentions >= config.github_min_mentions
            or known_team_release
        )
        # An exceptional concept remains useful for discovery, but delivery
        # still requires adoption, momentum, a known-team release, or an
        # independent source.  Otherwise a keyword-rich tiny repository can
        # buy its way into Telegram without any corroboration.
        exceptional = fit >= config.github_exceptional_fit and (unusual or public_implication)
        if github_gate:
            reasons.append(
                f"GitHub gate passed: {stars:.0f} stars, {velocity:.1f}/h, {forks:.0f} forks"
            )
        else:
            fit -= 28
            detail = (
                f"GitHub gate failed: {stars:.0f} stars, {velocity:.1f}/h, "
                f"{forks:.0f} forks, no independent signal"
            )
            if exceptional:
                detail += "; exceptional concept retained for discovery only"
            reasons.append(detail)

    arxiv_gate = True
    if source_key.startswith("arxiv"):
        arxiv_gate = public_implication and not specialist
        if arxiv_gate:
            reasons.append("arXiv public-interest gate passed")
        else:
            fit -= 28
            reasons.append("arXiv gate failed: no clear public-facing implication")

    youtube_gate = True
    if source_key.startswith("youtube-"):
        metrics = stored.item.popularity
        views = float(metrics.get("youtube_views", metrics.get("views", 0.0)))
        velocity = float(metrics.get("youtube_views_per_hour", metrics.get("views_per_hour", 0.0)))
        likes = float(metrics.get("youtube_likes", metrics.get("likes", 0.0)))
        youtube_gate = (
            views >= config.youtube_min_views
            or velocity >= config.youtube_min_view_velocity
            or likes >= config.youtube_min_likes
            or stored.independent_mentions >= config.youtube_min_mentions
        )
        if youtube_gate:
            reasons.append(
                f"YouTube gate passed: {views:.0f} views, {velocity:.1f}/h, {likes:.0f} likes"
            )
        else:
            fit -= 45
            reasons.append(
                f"YouTube gate failed: {views:.0f} views, {velocity:.1f}/h, "
                f"{likes:.0f} likes, no independent signal"
            )

    reddit_gate = True
    if source_key.startswith("reddit-"):
        metrics = stored.item.popularity
        upvotes = float(metrics.get("reddit_upvotes", metrics.get("upvotes", 0.0)))
        comments = float(metrics.get("reddit_comments", metrics.get("comments", 0.0)))
        velocity = max(
            float(metrics.get("reddit_upvotes_per_hour", 0.0)),
            float(metrics.get("reddit_comments_per_hour", 0.0)),
        )
        independent = stored.independent_mentions >= config.reddit_min_mentions
        engagement = (
            upvotes >= config.reddit_min_upvotes
            or comments >= config.reddit_min_comments
            or velocity >= config.reddit_min_velocity
        )
        is_rss = _is_reddit_rss(stored)
        if is_rss:
            rss_gate, rss_reason = _assess_reddit_rss_gate(
                stored,
                fit=fit,
                independent=independent,
                major_entity=major_entity,
                public_implication=public_implication,
                unusual=unusual,
                text=text,
                config=config,
            )
            reddit_gate = rss_gate
            reasons.append(rss_reason)
        else:
            reddit_gate = independent or (engagement and fit >= config.reddit_min_fit)
            if reddit_gate:
                reasons.append(
                    f"Reddit API gate passed: {upvotes:.0f} upvotes, {comments:.0f} comments, "
                    f"{velocity:.1f}/h"
                )
            else:
                fit -= 25
                reasons.append(
                    f"Reddit API gate failed: weak engagement/cross-source or fit below "
                    f"{config.reddit_min_fit:.0f}"
                )

    fit = round(min(100.0, max(0.0, fit)), 2)
    delivery_score = round(
        min(100.0, max(0.0, ranked.score * (1.0 - config.fit_weight) + fit * config.fit_weight)),
        2,
    )
    eligible = (
        fit >= config.minimum_fit
        and delivery_score >= config.minimum_delivery_score
        and github_gate
        and arxiv_gate
        and youtube_gate
        and reddit_gate
        and (not source_key.startswith("arxiv") or fit >= config.arxiv_min_fit)
    )
    urgent_source_gate = (
        not source_key.startswith("arxiv") or (stored.independent_mentions >= 2 and ranked.rising)
    ) and (
        not source_key.startswith("github-")
        or ranked.rising
        or stored.independent_mentions >= config.github_min_mentions
    )
    urgent = (
        eligible
        and urgent_source_gate
        and fit >= config.urgent_min_fit
        and delivery_score >= config.urgent_min_delivery_score
        and (major_entity or public_implication)
        and (major_event or unusual or ranked.rising)
    )
    reasons.append(
        f"editorial delivery score: {delivery_score:.1f} "
        f"(importance {ranked.score:.1f}, fit {fit:.1f})"
    )
    if not eligible:
        reasons.append("rejected by minimum editorial delivery threshold")
    elif urgent:
        reasons.append("urgent: exceptional event qualifies outside digest windows")
    return EditorialAssessment(fit, delivery_score, eligible, urgent, reasons)


def _is_reddit_rss(stored: StoredMaterial) -> bool:
    """Recognize both newly tagged RSS rows and historical fallback observations."""

    signals = set(stored.item.qualitative_signals)
    if "reddit_rss" in signals:
        return True
    api_metric_keys = {
        "reddit_upvotes",
        "reddit_comments",
        "reddit_upvotes_per_hour",
        "reddit_comments_per_hour",
    }
    return not bool(api_metric_keys.intersection(stored.item.popularity))


def _assess_reddit_rss_gate(
    stored: StoredMaterial,
    *,
    fit: float,
    independent: bool,
    major_entity: bool,
    public_implication: bool,
    unusual: bool,
    text: str,
    config: EditorialConfig,
) -> tuple[bool, str]:
    """Apply an event-first gate when public RSS cannot expose engagement."""

    if _COMMUNITY_CHATTER.search(text) or _REDDIT_GENERIC_QUESTION.search(text):
        return False, "Reddit RSS gate failed: community chatter/question"
    if _REDDIT_UNSUPPORTED_SPECULATION.search(text):
        return False, "Reddit RSS gate failed: unsupported speculation or opinion"
    if not _REDDIT_RSS_EVENT.search(text):
        return False, "Reddit RSS gate failed: no standalone news, release, demo, or incident"
    if fit < config.reddit_rss_min_fit:
        return (
            False,
            f"Reddit RSS gate failed: fit below {config.reddit_rss_min_fit:.0f} event threshold",
        )

    signals = set(stored.item.qualitative_signals)
    listing_presence = {
        signal.removeprefix("reddit_seen_")
        for signal in signals
        if signal.startswith("reddit_seen_")
    }
    multi_feed = len(listing_presence) >= 2
    exceptional_fit = fit >= config.reddit_rss_exceptional_fit
    strong_event = (major_entity and bool(_MAJOR_EVENT.search(text))) or (
        public_implication and unusual
    )
    if exceptional_fit or independent or multi_feed or strong_event:
        strength = (
            "exceptional fit"
            if exceptional_fit
            else "independent cross-source mention"
            if independent
            else f"RSS multi-feed presence: {' + '.join(sorted(listing_presence))}"
            if multi_feed
            else "strong recognizable event signal"
        )
        return True, f"Reddit RSS gate passed: event-first path; {strength}"
    return False, "Reddit RSS gate failed: no strong qualitative signal"
