"""
Per-run context for the agent loop.

The registry invokes tools with only the arguments the LLM produced, so there
is no parameter through which the loop can hand a tool the organisation's
resolved configuration. Context variables carry it instead: the loop sets them
for the duration of one run, and any tool executing inside that run reads them.

Two things travel this way:

- the **search configuration**, read by the web_search tool;
- the **citation settings** - whether the user asked for source references in
  the document, and what the run has researched - read by
  finalize_and_render_pptx when it puts the trail into the .pptx itself;
- the **chat provider**, read by every tool that makes its own LLM call.
  Without it those tools would quietly fall back to the process-wide provider
  built from environment variables, so choosing Claude in Settings would change
  the agent's reasoning but not the model that actually writes the slides;
- the run's **design system** and the **material behind each slide**, read by
  validate_slide_json when it checks a slide against its density budget. Both
  have to travel this way rather than as tool arguments: the design system is
  too large to make the model copy, and the material score exists precisely so
  that a model is not the one judging whether it had enough to say.

Using ``contextvars`` rather than module globals keeps concurrent runs -
several organisations generating at once in one worker - from seeing each
other's configuration.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from app.ai_settings import SearchConfig
from app.llm.provider import AINotConfiguredError, LLMProvider

_search_config: ContextVar[SearchConfig | None] = ContextVar(
    "thinkslides_search_config", default=None
)

_image_connection: ContextVar = ContextVar("image_connection", default=None)
_figure_connection: ContextVar = ContextVar("figure_connection", default=None)
_vision_provider: ContextVar[object | None] = ContextVar(
    "thinkslides_vision_provider", default=None
)
_chat_provider: ContextVar[LLMProvider | None] = ContextVar(
    "thinkslides_chat_provider", default=None
)

# Whether the user asked for source references in the rendered document, and
# the findings to draw them from. Both default to "no citations", so a tool
# running outside a configured run renders exactly what it always did.
_include_sources: ContextVar[bool] = ContextVar(
    "thinkslides_include_sources", default=False
)

_research_findings: ContextVar[list | None] = ContextVar(
    "thinkslides_research_findings", default=None
)

# The run's resolved design system, and how much material stood behind each
# slide when it was designed. Outside a run both are empty, and every consumer
# treats that as "no budget to hold this slide to" rather than as zero.
_design_system: ContextVar[dict | None] = ContextVar(
    "thinkslides_design_system", default=None
)

_slide_material: ContextVar[dict | None] = ContextVar(
    "thinkslides_slide_material", default=None
)

# The design language pack this run composes against, when it has one. A run
# on an uploaded template has none and keeps the template-faithful path; a run
# with no template has one, and every page is laid out by `app/design` rather
# than by filling a stock Office layout's placeholders. Carried here for the
# same reason the design system is: it is far too large to make the model
# copy back, and nothing about it is the model's to decide.
_pack: ContextVar[object | None] = ContextVar("thinkslides_pack", default=None)

# Per slide index, the archetypes it has already been composed on. The second
# rung of the revision ladder: a page whose arrangement came out wrong is
# placed again from the same material rather than redrafted from scratch.
_avoid_arrangements: ContextVar[dict | None] = ContextVar(
    "thinkslides_avoid_arrangements", default=None)

# Where this run's rendered deck is on disk. A path rather than the bytes:
# `finalize_and_render_pptx` writes a real file that the download, the vision
# check and `edit_deck_with_code` all read, and a second copy in memory would
# be a second source of truth for the same deck.
_rendered_path: ContextVar[str | None] = ContextVar(
    "thinkslides_rendered_path", default=None)

# Pictures the user supplied this session, by the name a script finds them
# under. Carried here, not as a tool parameter: the model names an asset, it
# never supplies the bytes or a path.
_assets: ContextVar[dict | None] = ContextVar("thinkslides_assets", default=None)


def set_include_sources(enabled: bool) -> Token:
    """Install whether this run cites its sources in the document."""
    return _include_sources.set(bool(enabled))


def get_include_sources() -> bool:
    """Whether the current run cites its sources; False outside a run."""
    return _include_sources.get()


def reset_include_sources(token: Token) -> None:
    _include_sources.reset(token)


def set_research_findings(findings: list | None) -> Token:
    """Install the findings this run has gathered, for the reference list."""
    return _research_findings.set(findings)


def get_research_findings() -> list | None:
    """The current run's research findings, or None outside a run."""
    return _research_findings.get()


def reset_research_findings(token: Token) -> None:
    _research_findings.reset(token)


def set_design_system(design_system: dict | None) -> Token:
    """Install the run's resolved design system for the tools to read."""
    return _design_system.set(design_system)


def get_design_system() -> dict | None:
    """The current run's design system, or None outside a configured run."""
    return _design_system.get()


def reset_design_system(token: Token) -> None:
    _design_system.reset(token)


def set_pack(pack: object | None) -> Token:
    """Install the design language pack this run composes against."""
    return _pack.set(pack)


def get_pack():
    """The run's pack, or None when this run is on an uploaded template."""
    return _pack.get()


def reset_pack(token: Token) -> None:
    _pack.reset(token)


def set_arrangements_to_avoid(avoid: dict | None) -> Token:
    """Which arrangements each slide has already been placed on and failed.

    Carried here rather than as a tool parameter for the same reason the pack
    is: it is the *loop's* bookkeeping, and a model that could set it could
    talk the deck out of an arrangement nobody complained about.
    """
    return _avoid_arrangements.set(avoid or {})


def get_arrangements_to_avoid() -> dict:
    return _avoid_arrangements.get() or {}


def reset_arrangements_to_avoid(token: Token) -> None:
    _avoid_arrangements.reset(token)


def set_rendered_path(path: str | None) -> Token:
    """Record where the deck this run has rendered actually is."""
    return _rendered_path.set(path)


def get_rendered_path() -> str | None:
    """The rendered deck's path, or None before anything has been rendered."""
    return _rendered_path.get()


def reset_rendered_path(token: Token) -> None:
    _rendered_path.reset(token)


def set_assets(assets: dict | None) -> Token:
    return _assets.set(assets or {})


def get_assets() -> dict:
    return _assets.get() or {}


def set_slide_material(material: dict | None) -> Token:
    """
    Install the per-slide material map for this run.

    A plain dict, mutated in place by ``record_slide_material`` as each slide
    is designed. It is per-run because the ContextVar is set fresh at the top
    of a run, which is what keeps concurrent runs from reading each other's.
    """
    return _slide_material.set({} if material is None else material)


def record_slide_material(slide_index: int, score: float) -> None:
    """Note how much material stood behind one slide when it was written."""
    material = _slide_material.get()
    if material is None:
        return
    try:
        material[int(slide_index)] = float(score)
    except (TypeError, ValueError):
        return


def get_slide_material(slide_index: int) -> float | None:
    """
    How much material one slide had, or None when it was never recorded.

    None is not zero. A slide validated outside a run, or one written before
    this existed, has no measurement behind it, and holding it to a budget on
    that basis would be inventing the evidence the budget exists to require.
    """
    material = _slide_material.get() or {}
    try:
        return material.get(int(slide_index))
    except (TypeError, ValueError):
        return None


def reset_slide_material(token: Token) -> None:
    _slide_material.reset(token)


def set_search_config(config: SearchConfig | None) -> Token:
    """Install the search configuration for the current run."""
    return _search_config.set(config)


def get_search_config() -> SearchConfig | None:
    """The current run's search configuration, or None outside a run."""
    return _search_config.get()


def reset_search_config(token: Token) -> None:
    """Restore whatever configuration was in place before ``set_search_config``."""
    _search_config.reset(token)


def set_chat_provider(provider: LLMProvider | None) -> Token:
    """Install the organisation's chat provider for the current run."""
    return _chat_provider.set(provider)


def get_chat_provider() -> LLMProvider | None:
    """The current run's chat provider, or None outside a configured run."""
    return _chat_provider.get()


def require_chat_provider() -> LLMProvider:
    """The current run's chat provider, or fail saying it is not configured.

    Every tool that makes its own LLM call used to write
    ``get_chat_provider() or get_llm_provider()``, and the right-hand side is a
    process-wide provider built from environment variables. That turned a
    workspace with no model configured into a workspace silently running on
    somebody else's key - and, when that key was out of credit, into a run that
    reported a billing error nobody had any way to connect to their own
    Settings page.
    """
    provider = _chat_provider.get()
    if provider is None:
        raise AINotConfiguredError()
    return provider


def reset_chat_provider(token: Token) -> None:
    """Restore whatever provider was in place before ``set_chat_provider``."""
    _chat_provider.reset(token)


def set_vision_provider(provider: object | None) -> Token:
    """Install the organisation's vision provider for the current run."""
    return _vision_provider.set(provider)


def get_vision_provider() -> object | None:
    """The current run's vision provider, or None when it has none.

    Same rule as the chat provider, for the same reason: a tool that reached
    for the process-wide, environment-built provider instead turned a
    workspace with no model configured into one silently running on somebody
    else's key. Returns None rather than raising, because every caller so far
    is a best-effort qualitative pass that must degrade rather than fail a run.
    """
    return _vision_provider.get()


def reset_vision_provider(token: Token) -> None:
    """Restore whatever provider was in place before ``set_vision_provider``."""
    _vision_provider.reset(token)


def set_image_connection(connection: object | None) -> Token:
    """Install the organisation's image provider for the current run.

    Published on the context rather than passed as a tool argument, for the same
    reason the chat provider is: a tool cannot be handed a connection through
    the registry, and routing a decrypted credential through an LLM response is
    not a thing to do.
    """
    return _image_connection.set(connection)


def get_image_connection():
    """The current run's image provider, or None when none is configured.

    None is the ordinary case and is not an error. ``generate_image`` is only
    registered when this is set, so a tool that finds it empty means the
    registry and the run have disagreed.
    """
    return _image_connection.get()


def reset_image_connection(token: Token) -> None:
    _image_connection.reset(token)


def set_figure_connection(connection: object | None) -> Token:
    """Install the organisation's figure provider for the current run.

    Unlike the image connection, this does not gate a tool. ``build_chart``
    always exists; the connection only decides whether its ``renderer='figure'``
    preference can be honoured or falls back to matplotlib.
    """
    return _figure_connection.set(connection)


def get_figure_connection():
    """The current run's figure provider, or None - which means matplotlib."""
    return _figure_connection.get()


def reset_figure_connection(token: Token) -> None:
    _figure_connection.reset(token)
