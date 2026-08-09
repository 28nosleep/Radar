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
    reddit_rss_min_fit: float = 65.0
    reddit_rss_exceptional_fit: float = 75.0
    urgent_min_fit: float = 92.0
    urgent_min_delivery_score: float = 76.0


@dataclass(frozen=True, slots=True)
class EditorialAssessment:
    fit: float
    delivery_score: float
    eligible: bool
    urgent: bool
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class _RedditRSSSemantics:
    confirmed_event: bool
    strong_event: bool
    kind: str | None = None
    rejection: str | None = None


_MAJOR_ENTITY = re.compile(
    r"\b(openai|anthropic|google(?: deepmind)?|deepmind|xai|meta|microsoft|apple|"
    r"nvidia|tesla|neuralink|boston dynamics|unitree)\b",
    re.IGNORECASE,
)
_FIGURE_AI_ENTITY = re.compile(
    r"\b(?:figure(?: ai)?\b.{0,45}\b(?:company|robot(?:ics)?|humanoid)|"
    r"(?:company|robot(?:ics)?|humanoid)\b.{0,45}\bfigure(?: ai)?)\b",
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
    r"\b(?:announc(?:e|es|ed)|releas(?:es|ed|ing)|debut(?:s|ed)?|"
    r"launch(?:es|ed|ing)?|roll(?:s|ed|ing)? out|built|shipped|drop(?:s|ped)?|"
    r"unveil(?:s|ed)?|demonstrat(?:e|es|ed|ing)|trained entirely on|"
    r"designed|settles?|solved|open[ -]sources|publish(?:es|ed)|"
    r"introduc(?:e|es|ed|ing)|achiev(?:e|es|ed)|begins? using|will use|"
    r"went live|breaks? out|escapes?)\b",
    re.IGNORECASE,
)
_REDDIT_RESEARCH_RESULT = re.compile(
    r"(?:\b(?:poll|paper|study|research(?:ers)?)\b.{0,140}\b"
    r"(?:finds?|found|shows?|shown|reports?|published|yielded|demonstrates?|achieves?)\b|"
    r"\b(?:finds?|found|solv(?:e|es|ed)|settles?|designed)\b.{0,90}\b"
    r"(?:error|problem|proofs?|virus(?:es)?|genomes?|result|accuracy|patients?)\b|"
    r"\b(?:trained entirely on|compressed)\b|"
    r"\b\d+(?:\.\d+)?%.{0,80}\b(?:workers|articles|humanoids?)\b|"
    r"\b(?:got|scored)\s+100%)",
    re.IGNORECASE,
)
_REDDIT_IMPLEMENTED_DEMO = re.compile(
    r"(?:\b(?:i|we|owner)\s+(?:have\s+)?(?:built|made|compressed|gave)\b|"
    r"^gave my\b|^real-time\b.{0,100}\bsystem\b|"
    r"\brunning\b.{0,100}\b(?:offline|on[- ]device|iphone|android)\b|"
    r"\b(?:tool|engine|app|setup|checkpoint)\b.{0,80}\b"
    r"(?:running|closed[- ]loop|hits?|hitting)\b|"
    r"\bhitting\s+\d+(?:\.\d+)?\s*(?:tok(?:ens?)?/s|tps)\b|"
    r"^open(?:-weight)? model\s*:)",
    re.IGNORECASE,
)
_REDDIT_ACTIVITY_EVENT = re.compile(
    r"\b(?:collecting\b.{0,90}\b(?:data|training)|"
    r"(?:moderators?|systems?|models?)\b.{0,30}\b(?:roll(?:s|ed|ing)? out)|"
    r"(?:model|agent)\b.{0,60}\b(?:breaks? out|escapes?))\b",
    re.IGNORECASE,
)
_REDDIT_CHATTER_LEAD = re.compile(
    r"^(?:need help|help(?: me)?\b|which\b|what(?:'s| is| do)\b|why (?:is|are|do)\b|"
    r"does anyone|can someone|any recommendations?|is it worth|how do i|"
    r"thinking about|if you had to choose|found this\b)",
    re.IGNORECASE,
)
_REDDIT_UNSUPPORTED_SPECULATION = re.compile(
    r"\b(?:rumou?r(?:ed|s)?|reportedly|unconfirmed|allegedly|leak(?:ed|s)?|"
    r"anonymous sources?|sources say|expected to|expects?\b|slated for|plans? to|"
    r"potential new|might be|could be|could literally|will reportedly|spotted on)\b",
    re.IGNORECASE,
)
_REDDIT_TROUBLESHOOTING = re.compile(
    r"\b(?:need help|troubleshoot|setup\b.{0,40}\b(?:problem|issue)|"
    r"running slower|performing slower|slower th[ae]n|doesn['’]?t work|"
    r"which (?:ai )?(?:subscription|model|tool).{0,30}(?:choose|use)|"
    r"help me choose)\b",
    re.IGNORECASE,
)
_REDDIT_BENCHMARK_ONLY = re.compile(
    r"\b(?:affordability|cheapest to run|tops? ai models?|table bench|"
    r"performing slower|slower th[ae]n|benchmarking models?)\b",
    re.IGNORECASE,
)
_REDDIT_INCOMPLETE_PROJECT = re.compile(
    r"\b(?:will share the results later|maybe later i['’]?ll do the (?:code|circuits)|"
    r"showing my .{0,50} update)\b",
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
    source_key = stored.item.source_key.casefold()
    is_reddit_rss = source_key.startswith("reddit-") and _is_reddit_rss(stored)
    reddit_semantics = (
        _classify_reddit_rss_semantics(stored.item.title, stored.item.description)
        if is_reddit_rss
        else None
    )
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

    entity_text = stored.item.title if is_reddit_rss else text
    major_entity = _has_major_entity(entity_text)
    major_event = (
        reddit_semantics.confirmed_event
        if reddit_semantics is not None
        else bool(_MAJOR_EVENT.search(text))
    )
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
    if _COMMUNITY_CHATTER.search(text) and not (
        reddit_semantics is not None and reddit_semantics.confirmed_event
    ):
        fit -= 35
        reasons.append("penalty: low-signal community chatter/advice thread")

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
        if is_reddit_rss:
            assert reddit_semantics is not None
            rss_gate, rss_reason = _assess_reddit_rss_gate(
                stored,
                fit=fit,
                semantics=reddit_semantics,
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


def _has_major_entity(text: str) -> bool:
    """Match Figure only when it denotes the robotics company, not the common noun."""

    return bool(_MAJOR_ENTITY.search(text) or _FIGURE_AI_ENTITY.search(text))


def _reddit_primary_claim(title: str, description: str) -> str:
    """Keep the title and opening factual clauses, excluding Reddit boilerplate."""

    clean_description = re.sub(
        r"\s*submitted by /u/.*$", "", description, flags=re.IGNORECASE | re.DOTALL
    ).strip()
    clauses = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", clean_description)]
    opening = " ".join(part for part in clauses[:4] if part)
    return f"{title.strip()}\n{opening}".strip()


def _classify_reddit_rss_semantics(title: str, description: str) -> _RedditRSSSemantics:
    """Classify the post's main claim instead of counting words in its whole body."""

    title_claim = title.strip()
    primary_claim = _reddit_primary_claim(title, description)
    title_event = bool(
        _REDDIT_RSS_EVENT.search(title_claim)
        or _REDDIT_RESEARCH_RESULT.search(title_claim)
        or _REDDIT_IMPLEMENTED_DEMO.search(title_claim)
        or _REDDIT_ACTIVITY_EVENT.search(title_claim)
        or _is_reddit_flair_research_result(title_claim)
    )
    primary_event = bool(
        title_event
        or _REDDIT_RSS_EVENT.search(primary_claim)
        or _REDDIT_RESEARCH_RESULT.search(primary_claim)
        or _REDDIT_IMPLEMENTED_DEMO.search(primary_claim)
        or _REDDIT_ACTIVITY_EVENT.search(primary_claim)
    )

    # A benchmark can be a research result, but a price/speed/model comparison
    # is not a news event merely because it names models or says "study".
    if _REDDIT_BENCHMARK_ONLY.search(title_claim):
        return _RedditRSSSemantics(False, False, rejection="benchmark/comparison claim")
    if _REDDIT_TROUBLESHOOTING.search(title_claim):
        return _RedditRSSSemantics(False, False, rejection="troubleshooting/advice request")
    if _REDDIT_INCOMPLETE_PROJECT.search(primary_claim):
        return _RedditRSSSemantics(False, False, rejection="unfinished personal project")
    if not title_event and (_REDDIT_CHATTER_LEAD.search(title_claim) or "?" in title_claim):
        return _RedditRSSSemantics(False, False, rejection="discussion/question/advice")

    # Explicit, factual title claims win over a conversational question or a
    # future-looking aside in selftext ("what do you think?", "hopefully...").
    if title_event and not _REDDIT_UNSUPPORTED_SPECULATION.search(title_claim):
        return _RedditRSSSemantics(True, True, kind=_reddit_event_kind(title_claim))

    speculation = _REDDIT_UNSUPPORTED_SPECULATION.search(primary_claim)
    if speculation:
        # A cautious headline about future benefits may still report completed
        # research whose opening clause states measured/promising results.
        research_evidence = bool(
            re.search(
                r"\b(?:research|study|paper|results?)\b.{0,100}\b"
                r"(?:shown?|found|demonstrat(?:e|es|ed)|published|promising)\b",
                primary_claim,
                re.IGNORECASE,
            )
        )
        if research_evidence:
            return _RedditRSSSemantics(True, True, kind="confirmed research result")
        return _RedditRSSSemantics(False, False, rejection="unsupported speculative claim")

    if primary_event:
        return _RedditRSSSemantics(True, True, kind=_reddit_event_kind(primary_claim))
    if _COMMUNITY_CHATTER.search(primary_claim):
        return _RedditRSSSemantics(False, False, rejection="discussion/question/advice")
    return _RedditRSSSemantics(False, False, rejection="no standalone factual event")


def _is_reddit_flair_research_result(title: str) -> bool:
    """Treat declarative research/project posts as results, not flair alone as proof."""

    if not re.search(r"\[(?:r|p)\]\s*$", title, re.IGNORECASE):
        return False
    weak_leads = re.compile(
        r"^(?:improved\b|how\b|why\b)|\bnot a single one\b|\?",
        re.IGNORECASE,
    )
    return not bool(weak_leads.search(title))


def _reddit_event_kind(claim: str) -> str:
    if _REDDIT_RESEARCH_RESULT.search(claim) or _is_reddit_flair_research_result(claim):
        return "confirmed research result"
    if _REDDIT_IMPLEMENTED_DEMO.search(claim):
        return "confirmed build/demo"
    if re.search(r"\b(?:breaks? out|escapes?)\b", claim, re.IGNORECASE):
        return "confirmed incident"
    return "confirmed release/event"


def _assess_reddit_rss_gate(
    stored: StoredMaterial,
    *,
    fit: float,
    semantics: _RedditRSSSemantics,
    config: EditorialConfig,
) -> tuple[bool, str]:
    """Apply an event-aware gate while treating absent RSS metrics as unknown."""

    if not semantics.confirmed_event:
        return False, f"Reddit RSS gate failed: {semantics.rejection}"
    threshold = (
        config.reddit_rss_min_fit if semantics.strong_event else config.reddit_rss_exceptional_fit
    )
    if fit < threshold:
        return (
            False,
            f"Reddit RSS gate failed: fit below {threshold:.0f} confirmed-event threshold",
        )

    signals = set(stored.item.qualitative_signals)
    listing_presence = {
        signal.removeprefix("reddit_seen_")
        for signal in signals
        if signal.startswith("reddit_seen_")
    }
    multi_feed = len(listing_presence) >= 2
    feed_detail = (
        f"; RSS multi-feed presence: {' + '.join(sorted(listing_presence))}" if multi_feed else ""
    )
    return (
        True,
        f"Reddit RSS gate passed: {semantics.kind}; engagement unknown{feed_detail}",
    )
