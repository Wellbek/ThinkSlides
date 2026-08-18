"""
Catalogue of connections an organisation can configure.

The single source of truth for what the Connections panel offers and what the
connections API will accept, in the same shape and for the same reason as
``app/llm/catalog.py``: the frontend fetches it rather than keeping its own
copy, so adding a connection here makes it configurable everywhere.

Four kinds, each unlocking something specific:

======== =====================================================================
``mcp``    An MCP server. Its tools are discovered at run start and registered
           namespaced, ``mcp__<server>__<tool>``.
``image``  Text-to-image. Unlocks ``generate_image``; with no image connection
           the tool is not registered at all.
``figure`` Publication-grade figure rendering, offered as a ``renderer``
           preference on ``build_chart``.
``data``   A queryable source offered during the ``research`` phase alongside
           ``web_search``.
======== =====================================================================

The existing Tavily and Firecrawl search backends appear here as ``data``
connections so the interface shows one list rather than two. They keep their
current home in ``organisation_ai_settings`` - ``ConnectionSpec.storage`` says
which table a given connection is really read from. This is a presentation-level
union, not a migration.

MCP scope, decided in review
----------------------------

**Remote HTTP endpoints only, plus the vetted presets below.**

There is deliberately no way for an organisation to supply a command to run.
The AI service is shared and multi-tenant, so letting a tenant name a process
to execute is arbitrary code execution in a process that is holding other
tenants' data and decrypted API keys. ``stdio`` transport is therefore not
modelled at all - not disabled behind a flag, not present. Sandboxed ``stdio``
is a separate infrastructure project if a customer ever needs it.

``check_mcp_url`` is the enforcement, and it is stricter than "must be a URL":
a URL an organisation controls is a URL that can point back into the cluster,
so loopback, link-local and private ranges are refused. That check sees only
what was typed - a hostname that *resolves* to a private address gets past it,
which is why the connect path in #182 pins the resolved address as well.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

KIND_MCP = "mcp"
KIND_IMAGE = "image"
KIND_FIGURE = "figure"
KIND_DATA = "data"

KINDS: tuple[str, ...] = (KIND_MCP, KIND_IMAGE, KIND_FIGURE, KIND_DATA)

# Which table a connection's credentials actually live in. The search backends
# were configured long before this catalogue existed and keep their column, so
# the catalogue has to be able to say "listed here, stored there".
STORAGE_CONNECTIONS = "connections"
STORAGE_AI_SETTINGS = "ai_settings"


@dataclass(frozen=True)
class CredentialField:
    """One value the user has to supply, and whether it is a secret.

    ``secret`` is not cosmetic: it decides whether the value is encrypted with
    per-column AAD and read back as a last-four hint, or stored in the clear
    and shown as itself. An endpoint URL has to be readable to be editable; a
    token must never be readable at all.
    """

    name: str
    label: str
    secret: bool
    required: bool = True
    placeholder: str = ""
    help: str = ""


@dataclass(frozen=True)
class ConnectionSpec:
    """Everything the UI and the connections API need about one connection."""

    id: str
    kind: str
    label: str
    description: str
    fields: tuple[CredentialField, ...] = ()
    docs_url: str = ""
    # What configuring this makes possible, in the words the panel shows. Left
    # empty for ``mcp``, where the tool list is discovered rather than fixed.
    unlocks: str = ""
    storage: str = STORAGE_CONNECTIONS
    # Whether this can be configured today. False is for a connection the
    # catalogue can describe but the service cannot yet complete - the honest
    # state, and the same one ``deepThinkingUnavailableReason`` reports.
    available: bool = True
    unavailable_reason: str = ""


@dataclass(frozen=True)
class McpServerPreset:
    """A vetted remote MCP server: its endpoint, transport and auth method.

    A preset is not a different kind of connection - it is the same remote HTTP
    MCP connection with the endpoint filled in and checked, so a user does not
    have to find the URL and cannot mistype it.

    ``auth`` is the reason this is a dataclass and not a URL list. Most vetted
    servers authenticate by OAuth against the user's own account, which is a
    browser flow this service does not have yet, so those presets are listed
    and marked rather than offered as though pasting a token would work.
    """

    id: str
    label: str
    url: str
    # "http" for streamable HTTP, "sse" for the older server-sent-events
    # transport. Both are remote; neither is stdio.
    transport: str
    auth: str  # "bearer" | "oauth"
    description: str
    docs_url: str = ""
    token_help: str = ""


AUTH_BEARER = "bearer"
AUTH_OAUTH = "oauth"

# No OAuth flow exists in this service yet, so an OAuth-only server cannot be
# completed however it is presented. Saying so is better than a token field
# that silently never authenticates. See #206: the flow is a workstream of its
# own, and it turns on an access-control question - an MCP OAuth grant is the
# *user's* access, so a token stored per organisation would hand every member
# of a workspace one member's permissions.
OAUTH_PENDING = (
    "This server signs in through your own account in a browser, which "
    "ThinkSlides cannot do yet. Configure it as a custom endpoint if you have "
    "a token, or use a server that accepts one directly."
)

MCP_PRESETS: tuple[McpServerPreset, ...] = (
    McpServerPreset(
        id="github",
        label="GitHub",
        url="https://api.githubcopilot.com/mcp/",
        transport="http",
        auth=AUTH_BEARER,
        description="Repositories, issues and pull requests as a source.",
        docs_url="https://github.com/github/github-mcp-server",
        token_help=(
            "A GitHub personal access token. Scope it to the repositories you "
            "want a deck to be able to read."
        ),
    ),
    McpServerPreset(
        id="stripe",
        label="Stripe",
        url="https://mcp.stripe.com",
        transport="http",
        auth=AUTH_BEARER,
        description="Billing and revenue objects, for finance decks.",
        docs_url="https://docs.stripe.com/mcp",
        token_help=(
            "A Stripe restricted key. Give it read access only - a deck has no "
            "reason to be able to write."
        ),
    ),
    McpServerPreset(
        id="notion",
        label="Notion",
        url="https://mcp.notion.com/mcp",
        transport="http",
        auth=AUTH_OAUTH,
        description="Pages and databases from a Notion workspace.",
        docs_url="https://developers.notion.com/guides/mcp/build-mcp-client",
    ),
    McpServerPreset(
        id="linear",
        label="Linear",
        url="https://mcp.linear.app/mcp",
        transport="http",
        auth=AUTH_OAUTH,
        description="Issues, projects and cycles.",
        docs_url="https://linear.app/docs/mcp",
    ),
    McpServerPreset(
        id="sentry",
        label="Sentry",
        url="https://mcp.sentry.dev/mcp",
        transport="http",
        auth=AUTH_OAUTH,
        description="Errors and release health.",
        docs_url="https://mcp.sentry.dev/",
    ),
)

MCP_PRESETS_BY_ID: dict[str, McpServerPreset] = {p.id: p for p in MCP_PRESETS}


MCP_URL_FIELD = CredentialField(
    name="url",
    label="Endpoint URL",
    secret=False,
    placeholder="https://mcp.example.com/mcp",
    help="An https endpoint speaking MCP over streamable HTTP.",
)

BEARER_TOKEN_FIELD = CredentialField(
    name="token",
    label="Bearer token",
    secret=True,
    required=False,
    help="Sent as 'Authorization: Bearer ...'. Leave empty for an open server.",
)


CONNECTIONS: tuple[ConnectionSpec, ...] = (
    ConnectionSpec(
        id="mcp_http",
        kind=KIND_MCP,
        label="MCP server",
        description=(
            "A remote MCP server. Its tools are discovered when a run starts "
            "and offered only in the phases that admit them."
        ),
        fields=(MCP_URL_FIELD, BEARER_TOKEN_FIELD),
        docs_url="https://modelcontextprotocol.io/docs/concepts/transports",
    ),
    ConnectionSpec(
        id="image_openai",
        kind=KIND_IMAGE,
        label="OpenAI images",
        description="Text-to-image through gpt-image-1.",
        fields=(
            CredentialField(
                name="api_key", label="API key", secret=True,
                help="A key from platform.openai.com. Starts with 'sk-'.",
            ),
        ),
        unlocks="generate_image",
    ),
    ConnectionSpec(
        id="image_zhipu",
        kind=KIND_IMAGE,
        label="Zhipu CogView",
        description="Text-to-image through CogView.",
        fields=(
            CredentialField(
                name="api_key", label="API key", secret=True,
                help="The same key form as the GLM chat models: 'id.secret'.",
            ),
        ),
        unlocks="generate_image",
    ),
    ConnectionSpec(
        id="figure_figurelabs",
        kind=KIND_FIGURE,
        label="Figurelabs",
        description=(
            "Publication-grade scientific figures. Offered as a renderer "
            "preference; matplotlib stays the default."
        ),
        fields=(
            CredentialField(name="api_key", label="API key", secret=True),
            CredentialField(
                name="base_url", label="Base URL", secret=False, required=False,
                help="Only needed for a self-hosted deployment.",
            ),
        ),
        unlocks="build_chart(renderer='figure')",
    ),
    # Listed here, stored in organisation_ai_settings. A user configuring
    # "where research comes from" should find one list, not two.
    ConnectionSpec(
        id="tavily",
        kind=KIND_DATA,
        label="Tavily",
        description="Search API built for LLM grounding. Western index.",
        fields=(
            CredentialField(
                name="api_key", label="API key", secret=True,
                help="Create a key at tavily.com. Starts with 'tvly-'.",
            ),
        ),
        unlocks="web_search",
        storage=STORAGE_AI_SETTINGS,
    ),
    ConnectionSpec(
        id="firecrawl",
        kind=KIND_DATA,
        label="Firecrawl",
        description="Search plus full-page scraping, so results carry more than a snippet.",
        fields=(
            CredentialField(
                name="api_key", label="API key", secret=True,
                help="Create a key at firecrawl.dev. Starts with 'fc-'.",
            ),
        ),
        unlocks="web_search",
        storage=STORAGE_AI_SETTINGS,
    ),
)

CONNECTIONS_BY_ID: dict[str, ConnectionSpec] = {c.id: c for c in CONNECTIONS}

CONNECTION_IDS: tuple[str, ...] = tuple(c.id for c in CONNECTIONS)


def connections_of_kind(kind: str) -> tuple[ConnectionSpec, ...]:
    return tuple(c for c in CONNECTIONS if c.kind == kind)


def secret_fields(spec: ConnectionSpec) -> tuple[str, ...]:
    """Field names that must be encrypted rather than stored as themselves."""
    return tuple(f.name for f in spec.fields if f.secret)


def config_fields(spec: ConnectionSpec) -> tuple[str, ...]:
    """Field names that are configuration and can be read back."""
    return tuple(f.name for f in spec.fields if not f.secret)


# ── The MCP scope constraint, enforced ─────────────────────────────────

BLOCKED_URL_SCHEMES = frozenset({"file", "ftp", "gopher", "data", "ws", "wss"})

# The cloud metadata services, which answer on a link-local address and hand
# out instance credentials to anything that asks.
METADATA_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
})


def check_mcp_url(raw: str) -> tuple[bool, str]:
    """Whether this is an endpoint an organisation may point us at.

    Returns ``(ok, reason)``, with ``reason`` written for the user.

    Three things are being refused, in order of how much they matter:

    1. Anything that is not https. A token travels on every request, and the
       whole point of remote-only is that the transport is the boundary.
    2. Loopback, link-local and private addresses. This service runs inside a
       cluster next to a database and a Redis holding other tenants' work, so
       an organisation-supplied URL is a request we make on their behalf into
       our own network. Cloud metadata endpoints are in that same range and
       hand out instance credentials to anything that asks.
    3. Credentials embedded in the URL, which end up in logs.

    A hostname that resolves to a private address defeats a check on the text
    alone, so this is the first of two gates and not the only one - the connect
    path checks the address it actually reached.
    """
    text = (raw or "").strip()
    if not text:
        return False, "Enter the server's endpoint URL."

    parts = urlsplit(text)
    scheme = (parts.scheme or "").lower()

    if scheme in BLOCKED_URL_SCHEMES or not scheme:
        return False, "Use an https:// endpoint. Only remote MCP servers are supported."
    if scheme == "http":
        return False, (
            "Use https. The token is sent on every request, so plain http "
            "would put it on the wire in clear text."
        )
    if scheme != "https":
        return False, f"'{scheme}' is not a supported scheme. Use https."

    if parts.username or parts.password:
        return False, (
            "Put the credential in the token field, not in the URL. URLs are "
            "logged; the token field is encrypted."
        )

    host = (parts.hostname or "").lower()
    if not host:
        return False, "The URL has no host."
    if host in METADATA_HOSTS or host.endswith(".internal"):
        return False, "That address is internal to the hosting environment."

    address = _as_ip(host)
    if address is not None and not _is_public(address):
        return False, (
            "That address is inside the network this service runs in, which is "
            "not somewhere a connection may point."
        )

    return True, ""


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The host as an IP address, or None when it is a name."""
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether an address is one it is reasonable to send a tenant's token to.

    ``is_global`` alone is not enough: it is False for the ranges that matter
    here but the named checks are what a reader can verify, and 0.0.0.0/8 and
    the IPv4-mapped IPv6 forms have caught out enough allowlists to be worth
    stating.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


# ── API payload ────────────────────────────────────────────────────────


def _field_payload(f: CredentialField) -> dict:
    return {
        "name": f.name,
        "label": f.label,
        "secret": f.secret,
        "required": f.required,
        "placeholder": f.placeholder,
        "help": f.help,
    }


def _connection_payload(spec: ConnectionSpec) -> dict:
    return {
        "id": spec.id,
        "kind": spec.kind,
        "label": spec.label,
        "description": spec.description,
        "fields": [_field_payload(f) for f in spec.fields],
        "docsUrl": spec.docs_url,
        "unlocks": spec.unlocks,
        "storage": spec.storage,
        "available": spec.available,
        "unavailableReason": spec.unavailable_reason,
    }


def _preset_payload(preset: McpServerPreset) -> dict:
    usable = preset.auth == AUTH_BEARER
    return {
        "id": preset.id,
        "label": preset.label,
        "url": preset.url,
        "transport": preset.transport,
        "auth": preset.auth,
        "description": preset.description,
        "docsUrl": preset.docs_url,
        "tokenHelp": preset.token_help,
        "available": usable,
        "unavailableReason": "" if usable else OAUTH_PENDING,
    }


def catalog_payload() -> dict:
    """Serialise the catalogue for the connections API and the frontend."""
    return {
        "connections": [_connection_payload(c) for c in CONNECTIONS],
        "connectionKinds": list(KINDS),
        "mcpPresets": [_preset_payload(p) for p in MCP_PRESETS],
    }
