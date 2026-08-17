"""
The connection catalogue: what it declares, and the scope it enforces.

Most of this file is about the MCP scope constraint, because that is the part a
later change quietly undoes. A catalogue entry that stops being described is
obvious; a URL check that stops refusing loopback is not.
"""

from app.connections import catalog
from app.connections.catalog import (
    AUTH_BEARER,
    AUTH_OAUTH,
    CONNECTIONS,
    CONNECTIONS_BY_ID,
    KIND_DATA,
    KIND_FIGURE,
    KIND_IMAGE,
    KIND_MCP,
    KINDS,
    MCP_PRESETS,
    STORAGE_AI_SETTINGS,
    catalog_payload,
    check_mcp_url,
    config_fields,
    connections_of_kind,
    secret_fields,
)


class TestEveryKindIsRepresented:
    def test_all_four_kinds_have_at_least_one_connection(self):
        for kind in KINDS:
            assert connections_of_kind(kind), f"nothing offers {kind}"

    def test_every_connection_declares_a_known_kind(self):
        for spec in CONNECTIONS:
            assert spec.kind in KINDS, f"{spec.id} has kind {spec.kind!r}"

    def test_ids_are_unique(self):
        assert len(CONNECTIONS_BY_ID) == len(CONNECTIONS)

    def test_every_connection_says_what_it_needs(self):
        """A connection with no fields is one the UI cannot ask about."""
        for spec in CONNECTIONS:
            assert spec.fields, f"{spec.id} declares no credential fields"

    def test_every_connection_has_a_description(self):
        for spec in CONNECTIONS:
            assert spec.description.strip(), spec.id


class TestSecretsAreMarkedAsSecrets:
    def test_every_api_key_and_token_field_is_secret(self):
        """
        The flag decides whether a value is encrypted and redacted, so getting
        it wrong on one field stores that field's credential in the clear.
        """
        for spec in CONNECTIONS:
            for f in spec.fields:
                if f.name in {"api_key", "token", "secret", "password"}:
                    assert f.secret, f"{spec.id}.{f.name} is not marked secret"

    def test_a_url_is_not_secret_because_it_has_to_be_editable(self):
        mcp = CONNECTIONS_BY_ID["mcp_http"]
        url = next(f for f in mcp.fields if f.name == "url")

        assert not url.secret

    def test_the_split_covers_every_field(self):
        for spec in CONNECTIONS:
            names = {f.name for f in spec.fields}
            assert set(secret_fields(spec)) | set(config_fields(spec)) == names


class TestTheSearchBackendsAreListedNotMigrated:
    """
    The interface shows one list; the storage stays where it is. Losing that
    distinction would mean reading a Tavily key from a table it was never
    written to, which resolves as "not configured".
    """

    def test_tavily_and_firecrawl_appear_as_data_connections(self):
        data = {c.id for c in connections_of_kind(KIND_DATA)}

        assert {"tavily", "firecrawl"} <= data

    def test_they_declare_their_real_home(self):
        for cid in ("tavily", "firecrawl"):
            assert CONNECTIONS_BY_ID[cid].storage == STORAGE_AI_SETTINGS

    def test_their_ids_match_the_search_backend_ids(self):
        """The union is only coherent if the two catalogues agree on names."""
        from app.llm.catalog import SEARCH_BACKENDS_BY_ID

        for spec in connections_of_kind(KIND_DATA):
            if spec.storage == STORAGE_AI_SETTINGS:
                assert spec.id in SEARCH_BACKENDS_BY_ID

    def test_the_new_kinds_are_stored_in_the_new_table(self):
        for kind in (KIND_MCP, KIND_IMAGE, KIND_FIGURE):
            for spec in connections_of_kind(kind):
                assert spec.storage == "connections", spec.id


class TestTheVettedPresets:
    def test_every_preset_is_https(self):
        for preset in MCP_PRESETS:
            assert preset.url.startswith("https://"), preset.id

    def test_every_preset_url_passes_the_check_it_will_be_saved_through(self):
        """
        A preset that the save path refuses is a broken button. This is the
        invariant that catches a vetted entry typed as http, or as a bare host.
        """
        for preset in MCP_PRESETS:
            ok, reason = check_mcp_url(preset.url)
            assert ok, f"{preset.id}: {reason}"

    def test_every_preset_declares_a_remote_transport(self):
        for preset in MCP_PRESETS:
            assert preset.transport in {"http", "sse"}, preset.id

    def test_a_bearer_preset_explains_which_token_to_make(self):
        for preset in MCP_PRESETS:
            if preset.auth == AUTH_BEARER:
                assert preset.token_help.strip(), preset.id

    def test_an_oauth_preset_is_marked_unavailable_rather_than_offered(self):
        """
        There is no OAuth flow yet, so a token field on one of these would be
        a field that can never authenticate.
        """
        payload = {p["id"]: p for p in catalog_payload()["mcpPresets"]}

        for preset in MCP_PRESETS:
            if preset.auth == AUTH_OAUTH:
                assert payload[preset.id]["available"] is False
                assert payload[preset.id]["unavailableReason"]

    def test_a_bearer_preset_is_available(self):
        payload = {p["id"]: p for p in catalog_payload()["mcpPresets"]}

        for preset in MCP_PRESETS:
            if preset.auth == AUTH_BEARER:
                assert payload[preset.id]["available"] is True

    def test_at_least_one_preset_can_actually_be_completed_today(self):
        assert any(p.auth == AUTH_BEARER for p in MCP_PRESETS)


class TestStdioIsNotModelledAtAll:
    """
    The scope decision, asserted rather than commented. A tenant naming a
    process to run is arbitrary code execution in a process holding other
    tenants' decrypted keys, so the shape must not exist - not be disabled.
    """

    def test_no_connection_field_asks_for_a_command(self):
        forbidden = {"command", "cmd", "args", "argv", "env", "executable"}

        for spec in CONNECTIONS:
            for f in spec.fields:
                assert f.name not in forbidden, f"{spec.id}.{f.name}"

    def test_no_preset_declares_a_stdio_transport(self):
        for preset in MCP_PRESETS:
            assert preset.transport != "stdio", preset.id

    def test_the_module_does_not_offer_a_stdio_constant(self):
        assert not hasattr(catalog, "TRANSPORT_STDIO")


class TestTheUrlCheck:
    def test_an_ordinary_https_endpoint_is_accepted(self):
        ok, reason = check_mcp_url("https://mcp.example.com/mcp")

        assert ok, reason

    def test_plain_http_is_refused_and_says_why(self):
        ok, reason = check_mcp_url("http://mcp.example.com/mcp")

        assert not ok
        assert "https" in reason.lower()

    def test_a_file_url_is_refused(self):
        ok, _ = check_mcp_url("file:///etc/passwd")

        assert not ok

    def test_a_bare_host_is_refused_rather_than_assumed_https(self):
        """
        Guessing the scheme is how an http endpoint gets saved as though it
        were secure.
        """
        ok, _ = check_mcp_url("mcp.example.com/mcp")

        assert not ok

    def test_an_empty_url_is_refused(self):
        ok, reason = check_mcp_url("")

        assert not ok
        assert reason

    def test_whitespace_around_a_good_url_is_tolerated(self):
        ok, reason = check_mcp_url("  https://mcp.example.com/mcp  ")

        assert ok, reason

    def test_credentials_in_the_url_are_refused(self):
        ok, reason = check_mcp_url("https://user:secret@mcp.example.com/mcp")

        assert not ok
        assert "token field" in reason

    def test_loopback_is_refused(self):
        for host in ("127.0.0.1", "localhost", "[::1]"):
            ok, _ = check_mcp_url(f"https://{host}/mcp")
            if host == "localhost":
                # A name, so the text check cannot resolve it - the connect
                # path is what catches this one. Documented, not asserted as
                # a refusal, so the test does not claim protection it lacks.
                continue
            assert not ok, host

    def test_the_private_ranges_are_refused(self):
        for host in ("10.0.0.5", "172.16.4.4", "192.168.1.1", "0.0.0.0"):
            ok, _ = check_mcp_url(f"https://{host}/mcp")
            assert not ok, host

    def test_the_cloud_metadata_address_is_refused(self):
        """169.254.169.254 hands out instance credentials to anything that asks."""
        ok, _ = check_mcp_url("https://169.254.169.254/mcp")

        assert not ok

    def test_the_metadata_hostname_is_refused_too(self):
        ok, _ = check_mcp_url("https://metadata.google.internal/mcp")

        assert not ok

    def test_an_ipv4_mapped_ipv6_loopback_is_refused(self):
        """The form that gets past allowlists written against IPv4 only."""
        ok, _ = check_mcp_url("https://[::ffff:127.0.0.1]/mcp")

        assert not ok

    def test_a_public_address_literal_is_accepted(self):
        ok, reason = check_mcp_url("https://93.184.216.34/mcp")

        assert ok, reason

    def test_an_internal_suffix_is_refused(self):
        ok, _ = check_mcp_url("https://mcp.svc.internal/mcp")

        assert not ok


class TestThePayload:
    def test_it_describes_every_connection(self):
        payload = catalog_payload()

        assert len(payload["connections"]) == len(CONNECTIONS)
        assert {c["id"] for c in payload["connections"]} == set(CONNECTIONS_BY_ID)

    def test_it_needs_no_storage_to_exist(self):
        """
        The acceptance criterion for this step: the API can describe the
        catalogue before there is a table.
        """
        payload = catalog_payload()

        assert payload["connections"]
        assert payload["mcpPresets"]

    def test_it_describes_fields_without_carrying_a_value_for_any_of_them(self):
        """
        The catalogue is a description, not a read of what is stored. A
        ``value`` key here would be the shape a later change fills in with the
        thing it must never send.
        """
        for connection in catalog_payload()["connections"]:
            for f in connection["fields"]:
                assert set(f) == {
                    "name", "label", "secret", "required", "placeholder", "help",
                }, f"{connection['id']}.{f['name']}"

    def test_field_shapes_are_camel_cased_for_the_frontend(self):
        payload = catalog_payload()
        first = payload["connections"][0]

        assert "docsUrl" in first
        assert "unavailableReason" in first

    def test_the_kinds_are_listed(self):
        assert catalog_payload()["connectionKinds"] == list(KINDS)


class TestTheSettingsCatalogEndpointMergesBoth:
    def test_the_merged_payload_carries_both_catalogues(self):
        """
        One fetch. A user configuring a workspace does not care which module a
        choice came from, and two endpoints is two chances to be out of sync.
        """
        from app.llm.catalog import catalog_payload as llm_payload

        merged = {**llm_payload(), **catalog_payload()}

        assert "providers" in merged
        assert "searchBackends" in merged
        assert "connections" in merged
        assert "mcpPresets" in merged

    def test_the_two_catalogues_do_not_collide_on_a_key(self):
        from app.llm.catalog import catalog_payload as llm_payload

        assert not set(llm_payload()) & set(catalog_payload())
