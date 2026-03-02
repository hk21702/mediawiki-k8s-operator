# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import copy
import dataclasses

import ops
import pytest
import scenario  # testing.Container isn't namespaced correctly, so we import scenario directly
from ops import testing
from pytest_mock import MockerFixture, MockType

from charm import Charm
from exceptions import MediaWikiBlockedStatusException, MediaWikiInstallError


@pytest.fixture(autouse=True)
def mock_mediawiki(mocker: MockerFixture) -> MockType:
    """Base MediaWiki class mock."""
    # We patch where it is imported in the charm.py file, not where it is defined
    mock_mediawiki_cls = mocker.patch("charm.MediaWiki", autospec=True)
    mock_instance = mock_mediawiki_cls.return_value

    # Setup default return values or side effects
    mock_instance.reconciliation.return_value = None
    mock_instance.rotate_root_credentials.return_value = ("mocked-root", "mocked-password")
    mock_instance.update_database_schema.return_value = None
    mock_instance.get_version.return_value = "1.39.0"

    return mock_instance


class TestGeneralEvents:
    def test_invalid_proxy_config(
        self, ctx: testing.Context, active_state: testing.State, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that we block should invalid proxy configurations be provided"""
        monkeypatch.setenv("JUJU_CHARM_HTTP_PROXY", "invalid")
        state_out = ctx.run(ctx.on.update_status(), active_state)
        assert isinstance(state_out.unit_status, ops.BlockedStatus)


class TestPebbleReadyEvent:
    def test_can_connect(
        self,
        ctx: testing.Context,
        base_state: testing.State,
        mediawiki_container: scenario.Container,
    ) -> None:
        """Test that the charm sets the correct status when pebble can connect, but no relations."""
        # With replica secret data
        state_out = ctx.run(ctx.on.pebble_ready(container=mediawiki_container), base_state)
        assert isinstance(state_out.unit_status, ops.BlockedStatus)

        # Without replica secret data
        state_in = dataclasses.replace(base_state, secrets=[])
        state_out = ctx.run(ctx.on.pebble_ready(container=mediawiki_container), state_in)
        assert isinstance(state_out.unit_status, ops.WaitingStatus)

    def test_cannot_connect(
        self,
        ctx: testing.Context,
        mediawiki_container: scenario.Container,
        secrets: list[testing.Secret],
    ) -> None:
        """Test that the charm sets waiting status when pebble cannot connect."""
        mediawiki_container = dataclasses.replace(mediawiki_container, can_connect=False)

        # With replica secrets
        state_in = testing.State(
            containers=[mediawiki_container],
            secrets=secrets,
            leader=True,
        )

        state_out = ctx.run(ctx.on.pebble_ready(container=mediawiki_container), state_in)
        assert isinstance(state_out.unit_status, ops.WaitingStatus)

        # Without replica secrets
        state_in = dataclasses.replace(state_in, secrets=[])
        state_out = ctx.run(ctx.on.pebble_ready(container=mediawiki_container), state_in)
        assert isinstance(state_out.unit_status, ops.WaitingStatus)


class TestLeaderElectedEvent:
    @pytest.fixture
    def state(self, base_state: testing.State) -> testing.State:
        """Provides a base state with no secrets."""
        return dataclasses.replace(base_state, secrets=[])

    def test_leader_elected(self, ctx: testing.Context, state: testing.State) -> None:
        """Test that the charm sets up replica data when leader is elected."""
        state_out = ctx.run(ctx.on.leader_elected(), state)
        assert state_out.get_secret(label=Charm._REPLICA_SECRET_LABEL) is not None

    def test_leader_elected_not_leader(self, ctx: testing.Context, state: testing.State) -> None:
        """Test that the charm does not set up replica data when leader is elected but unit is not leader."""
        state_in = dataclasses.replace(state, leader=False)
        state_out = ctx.run(ctx.on.leader_elected(), state_in)
        with pytest.raises(KeyError, match="secret: not found in the State"):
            state_out.get_secret(label=Charm._REPLICA_SECRET_LABEL)

    def test_leader_elected_with_consensus(
        self, ctx: testing.Context, base_state: testing.State
    ) -> None:
        """Test that the charm does not set up replica data when leader is elected but replica consensus has already been reached."""
        expected_secret = copy.deepcopy(
            base_state.get_secret(label=Charm._REPLICA_SECRET_LABEL).latest_content
        )
        state_out = ctx.run(ctx.on.leader_elected(), base_state)
        assert (
            state_out.get_secret(label=Charm._REPLICA_SECRET_LABEL).latest_content
            == expected_secret
        )


class TestConfigChangedEvent:
    def test_valid_config(
        self,
        ctx: testing.Context,
        configured_state: testing.State,
    ) -> None:
        """Test that the charm sets active status when config is changed with all required relations."""
        state_out = ctx.run(ctx.on.config_changed(), configured_state)
        assert isinstance(state_out.unit_status, ops.ActiveStatus)

    def test_invalid_config(
        self,
        ctx: testing.Context,
        active_state: testing.State,
        populated_config: dict[str, bool | float | int | str],
    ) -> None:
        """Test that the charm sets blocked status when config is changed with invalid config."""
        invalid_config = dict(populated_config)
        invalid_config["composer"] = "invalid-json"

        state_in = dataclasses.replace(active_state, config=invalid_config)
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, ops.BlockedStatus)

    def test_not_ready(
        self,
        ctx: testing.Context,
        active_state: testing.State,
        populated_config: dict[str, bool | float | int | str],
    ) -> None:
        """Test that the charm sets blocked status when config is changed but relations are not ready."""
        state_in = dataclasses.replace(active_state, config=populated_config, relations=[])
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, ops.BlockedStatus)


class TestTraefikRouteRelationEvents:
    @pytest.fixture
    def state(self, base_state, traefik_route_relation) -> testing.State:
        """Provide a state with a traefik route relation."""
        relations = [traefik_route_relation]
        return dataclasses.replace(base_state, relations=relations)

    def test_relation_joined_without_db(
        self, ctx: testing.Context, state: testing.State, traefik_route_relation: testing.Relation
    ) -> None:
        """Test that the charm sets blocked status when traefik route relation is joined, but without a database relation."""
        state_out = ctx.run(
            ctx.on.relation_joined(relation=traefik_route_relation),
            state,
        )
        assert isinstance(state_out.unit_status, ops.BlockedStatus)

    def test_relation_changed_without_db(
        self, ctx: testing.Context, state: testing.State, traefik_route_relation: testing.Relation
    ) -> None:
        """Test that the charm sets blocked status when traefik route relation is changed, but without a database relation."""
        state_out = ctx.run(
            ctx.on.relation_changed(relation=traefik_route_relation),
            state,
        )
        assert isinstance(state_out.unit_status, ops.BlockedStatus)

    def test_relation_broken_without_db(
        self, ctx: testing.Context, state: testing.State, traefik_route_relation: testing.Relation
    ) -> None:
        """Test that the charm sets blocked status when traefik route relation has departed, but without a database relation."""
        state_out = ctx.run(
            ctx.on.relation_broken(relation=traefik_route_relation),
            state,
        )
        assert isinstance(state_out.unit_status, ops.BlockedStatus)

    def test_relation_joined(
        self,
        ctx: testing.Context,
        active_state: testing.State,
        traefik_route_relation: testing.Relation,
    ) -> None:
        """Test that the charm sets active status when traefik route relation is joined with a database relation."""
        state_out = ctx.run(
            ctx.on.relation_joined(relation=traefik_route_relation),
            active_state,
        )
        assert isinstance(state_out.unit_status, ops.ActiveStatus)

    def test_relation_changed(
        self,
        ctx: testing.Context,
        active_state: testing.State,
        traefik_route_relation: testing.Relation,
    ) -> None:
        """Test that the charm sets active status when traefik route relation is changed with a database relation."""
        state_out = ctx.run(
            ctx.on.relation_changed(relation=traefik_route_relation),
            active_state,
        )
        assert isinstance(state_out.unit_status, ops.ActiveStatus)

    def test_relation_broken(
        self,
        ctx: testing.Context,
        active_state: testing.State,
        traefik_route_relation: testing.Relation,
    ) -> None:
        """Test that the charm sets active status when traefik route relation has departed with a database relation."""
        state_out = ctx.run(
            ctx.on.relation_broken(relation=traefik_route_relation),
            active_state,
        )
        assert isinstance(state_out.unit_status, ops.ActiveStatus)


class TestDatabaseEvents:
    pass


class TestRotateRootCredentialsAction:
    def test_success(self, ctx: testing.Context, active_state: testing.State) -> None:
        """Test that the action returns new credentials when rotation is successful."""
        ctx.run(ctx.on.action("rotate-root-credentials"), active_state)

        # no sec B105 is bugged for multi-line dicts https://github.com/PyCQA/bandit/issues/1352
        mocked_password = "mocked-password"  # nosec: B105
        assert ctx.action_results == {"username": "mocked-root", "password": mocked_password}

    def test_failure(
        self, ctx: testing.Context, active_state: testing.State, mock_mediawiki
    ) -> None:
        """Test that the action fails when MediaWiki raises an exception during credential rotation."""
        mock_mediawiki.rotate_root_credentials.side_effect = MediaWikiBlockedStatusException(
            "Mocked blocked status during credential rotation"
        )

        with pytest.raises(
            testing.ActionFailed, match="Mocked blocked status during credential rotation"
        ):
            ctx.run(ctx.on.action("rotate-root-credentials"), active_state)

        assert ctx.action_results is None

        mock_mediawiki.rotate_root_credentials.side_effect = Exception(
            "Mocked exception during credential rotation"
        )

        with pytest.raises(testing.ActionFailed, match="Credential rotation failed"):
            ctx.run(ctx.on.action("rotate-root-credentials"), active_state)

        assert ctx.action_results is None


class TestUpdateDatabaseAction:
    def test_success(self, ctx: testing.Context, active_state: testing.State) -> None:
        """Test that the action completes successfully when database update is successful."""
        ctx.run(ctx.on.action("update-database"), active_state)
        assert ctx.action_results is None

    def test_not_leader(self, ctx: testing.Context, active_state: testing.State) -> None:
        """Test that the action fails when the unit is not the leader."""
        state_in = dataclasses.replace(active_state, leader=False)
        with pytest.raises(testing.ActionFailed, match="leader"):
            ctx.run(ctx.on.action("update-database"), state_in)

        assert ctx.action_results is None

    def test_failure(
        self, ctx: testing.Context, active_state: testing.State, mock_mediawiki
    ) -> None:
        """Test that the action fails when MediaWiki raises an exception during database update."""
        mock_mediawiki.update_database_schema.side_effect = MediaWikiInstallError(
            "Mocked install error during database update"
        )

        with pytest.raises(testing.ActionFailed, match="MediaWiki database schema update failed"):
            ctx.run(ctx.on.action("update-database"), active_state)

        assert ctx.action_results is None
