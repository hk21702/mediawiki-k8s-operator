#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm Operator for mediawiki-k8s."""

import logging
import typing
from urllib.parse import urlparse

import ops
from charms.traefik_k8s.v0.traefik_route import TraefikRouteRequirer
from ops import (
    ActionEvent,
    ActiveStatus,
    BlockedStatus,
    EventBase,
    MaintenanceStatus,
    SecretNotFoundError,
    WaitingStatus,
    pebble,
)

from database import Database
from exceptions import (
    CharmConfigInvalidError,
    MediaWikiBlockedStatusException,
    MediaWikiInstallError,
    MediaWikiStatusException,
    MediaWikiWaitingStatusException,
)
from mediawiki import MediaWiki, MediaWikiSecrets
from state import StatefulCharmBase

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class Charm(StatefulCharmBase):
    """Charm for MediaWiki on Kubernetes."""

    _CONTAINER_NAME = "mediawiki"
    _SERVICE_NAME = "mediawiki"

    _DATABASE_RELATION_NAME = "database"
    _DATABASE_NAME = "mediawiki"

    _INGRESS_RELATION_NAME = "traefik-route"

    _REPLICA_SECRET_LABEL = "replica-secret"  # nosec: B105

    def __init__(self, *args: typing.Any):
        super().__init__(*args)

        self._database = Database(self, self._DATABASE_RELATION_NAME, self._DATABASE_NAME)
        self._mediawiki = MediaWiki(self, self._database)

        self.framework.observe(self.on.leader_elected, self._setup_replica_data)
        self.framework.observe(self.on.mediawiki_pebble_ready, self._reconciliation)
        self.framework.observe(self._database.db.on.database_created, self._reconciliation)
        self.framework.observe(self._database.db.on.endpoints_changed, self._reconciliation)
        self.framework.observe(
            self.on[self._DATABASE_RELATION_NAME].relation_broken, self._reconciliation
        )
        self.framework.observe(self.on.traefik_route_relation_joined, self._reconciliation)
        self.framework.observe(self.on.traefik_route_relation_changed, self._reconciliation)
        self.framework.observe(self.on.traefik_route_relation_broken, self._reconciliation)
        self.framework.observe(self.on.config_changed, self._reconciliation)
        self.framework.observe(self.on.secret_changed, self._reconciliation)

        self.framework.observe(
            self.on.rotate_root_credentials_action, self._on_rotate_root_credentials
        )
        self.framework.observe(self.on.update_database_action, self._on_update_database)

    @property
    def _container(self) -> ops.Container:
        """Return the MediaWiki container."""
        return self.unit.get_container(self._CONTAINER_NAME)

    def _init_pebble_layer(self) -> None:
        """Initialize the Pebble layer for the MediaWiki container."""
        health_check_timeout = 5
        layer: pebble.LayerDict = {
            "summary": "mediawiki layer",
            "description": "Pebble layer configuration for MediaWiki",
            "services": {
                self._SERVICE_NAME: {
                    "override": "replace",
                    "summary": "MediaWiki service (apache)",
                    "command": "/usr/sbin/apache2ctl -D FOREGROUND",
                }
            },
            "checks": {
                "mediawiki-api-ready": {
                    "override": "replace",
                    "level": "ready",
                    "http": {
                        "url": "http://localhost/w/api.php?action=query&format=json&prop=&meta=siteinfo&formatversion=2"
                    },
                    "period": f"{max(10, health_check_timeout * 2)}s",
                    "timeout": f"{health_check_timeout}s",
                },
                "mediawiki-api-alive": {
                    "override": "replace",
                    "level": "alive",
                    "http": {"url": "http://localhost/w/api.php"},
                    "period": f"{max(10, health_check_timeout * 2)}s",
                    "timeout": f"{health_check_timeout}s",
                },
            },
        }

        self._container.add_layer(self._SERVICE_NAME, layer, combine=True)

    def _replica_consensus_reached(self) -> bool:
        """Check if the necessary minimal data and secrets shared with MediaWiki peers (replicas) has been initialized and synchronized."""
        try:
            self.model.get_secret(label=self._REPLICA_SECRET_LABEL)
        except SecretNotFoundError:
            return False

        return True

    def _setup_replica_data(self, _event: EventBase) -> None:
        """Initialize the synchronized data required for MediaWiki replication.

        The relation data content object is used to share (read and write) necessary secret data
        used by MediaWiki to enhance security and must be synchronized.

        Only the leader can update the data shared with all replicas.
        """
        if self._replica_consensus_reached() or not self.unit.is_leader():
            return

        logger.info("Creating replica data due to event %s", _event)
        content = MediaWikiSecrets.generate().to_juju_secret()
        self.app.add_secret(content, label=self._REPLICA_SECRET_LABEL)

    def _configure_ingress(self) -> None:
        """Configure the Traefik ingress relation.

        TODO: Switch to ingress once gateway-api-integrator supports an upstream ingress, or once connecting directly to HAProxy is viable.
        """
        route_relation = self.model.get_relation(self._INGRESS_RELATION_NAME)
        if route_relation is None:
            return
        self._ingress_requirer = TraefikRouteRequirer(
            self, route_relation, relation_name=self._INGRESS_RELATION_NAME
        )

        if not self.unit.is_leader():
            return

        if self._ingress_requirer.is_ready():
            config = self.load_charm_config()
            hostname = config.hostname or self.app.name
            parsed_hostname = urlparse(f"http://{hostname}").hostname
            traefik_hostname = parsed_hostname or hostname

            self._ingress_requirer.submit_to_traefik(
                config={
                    "http": {
                        "routers": {
                            f"{self.app.name}-router": {
                                "rule": f"Host(`{traefik_hostname}`)",
                                "service": f"{self.app.name}-service",
                            },
                        },
                        "services": {
                            f"{self.app.name}-service": {
                                "loadBalancer": {
                                    "servers": [
                                        {
                                            "url": f"http://{self.app.name}-endpoints.{self.model.name}.svc.cluster.local:80"
                                        }
                                    ]
                                },
                            },
                        },
                    },
                }
            )
        else:
            raise MediaWikiWaitingStatusException(
                f"Waiting for {self._INGRESS_RELATION_NAME} relation to be ready"
            )

    def _start_service(self) -> None:
        """Start the MediaWiki service (apache)."""
        container = self._container
        if not container.can_connect():
            raise MediaWikiWaitingStatusException("Waiting for pebble")

        self._init_pebble_layer()
        if not self._container.get_service(self._SERVICE_NAME).is_running():
            self._container.start(self._SERVICE_NAME)

        self.unit.set_workload_version(self._mediawiki.get_version())

    def _stop_service(self) -> None:
        """Stop the MediaWiki service (apache)."""
        container = self._container
        if not container.can_connect():
            logger.info("Cannot connect to pebble to stop service; pebble is not ready")
            return

        if (
            self._SERVICE_NAME in container.get_plan().services
            and container.get_service(self._SERVICE_NAME).is_running()
        ):
            container.stop(self._SERVICE_NAME)

    def _reconciliation(self, _event: EventBase) -> None:
        """Reconcile the charm state.

        This method will move the charm towards an active and correct state.

        If the container or database relation is not ready, it will not proceed.
        Additionally, if the database relation is not present, it will stop the apache web server to prevent any chance of data getting written unintentionally.

        Otherwise, if prerequisite criteria is met, the following actions are attempted:
        - Configure ingress.
        - Trigger the MediaWiki workload reconciliation process.
        - Start the MediaWiki service if it is not running.
        - Set the unit status to an appropriate state depending on the outcome of the above actions.

        Args:
            _event: The event that triggered the reconciliation.
        """
        self.unit.status = MaintenanceStatus("Reconciling charm state")
        logger.info("Starting reconciliation due to event: %s", _event)
        if not self._container.can_connect():
            logger.info("Reconciliation process terminated early, pebble is not ready")
            self.unit.status = WaitingStatus("Waiting for pebble")
            return

        # Early stop service cases
        try:
            if not self._replica_consensus_reached():
                raise MediaWikiWaitingStatusException(
                    "Replica consensus has not been reached; waiting for leader"
                )
            if not self._database.has_relation():
                # To prevent the any chance of data getting written unintentionally, we have to check for this case
                raise MediaWikiBlockedStatusException(
                    f"Waiting for relation {self._DATABASE_RELATION_NAME}"
                )
        except MediaWikiStatusException as e:
            logger.info(
                "Reconciliation process terminated early, stopping service, status exception raised: %s",
                e,
            )
            self.unit.status = e.status
            self._stop_service()
            return

        secrets = self.model.get_secret(label=self._REPLICA_SECRET_LABEL).get_content(refresh=True)

        try:
            self._configure_ingress()
            self._mediawiki.reconciliation(MediaWikiSecrets(**secrets))
            self._start_service()
        except MediaWikiStatusException as e:
            logger.info("Reconciliation process terminated early, status exception raised: %s", e)
            self.unit.status = e.status
            return
        except CharmConfigInvalidError as e:
            logger.info(
                "Reconciliation process terminated early, invalid charm configuration: %s", e
            )
            self.unit.status = BlockedStatus(str(e))
            return

        logger.info("Reconciliation process complete.")
        self.unit.status = ActiveStatus()

    def _on_rotate_root_credentials(self, event: ActionEvent) -> None:
        """Handle the rotate-root-credentials action.

        Rotate the root bureaucrat user's credentials and ensure that it is in the bureaucrat group.
        If the user does not exist, it will be created.

        This user should only be used to assign permissions to real users, not for regular use.

        Args:
            event: The event that triggered the credential rotation.
        """
        logger.info("Rotating root bureaucrat credentials due to event: %s", event)

        try:
            new_username, new_password = self._mediawiki.rotate_root_credentials()
            event.log("Root bureaucrat user credentials rotated successfully")
            event.set_results({"username": new_username, "password": new_password})
        except MediaWikiStatusException as e:
            event.fail(f"Credential rotation failed: {e.status.message}")
        except Exception as e:
            logger.error("Credential rotation process failed with unexpected error: %s", e)
            event.fail("Credential rotation failed due to an unexpected error")

    def _on_update_database(self, event: ActionEvent) -> None:
        """Handle the update-database action.

        Update the MediaWiki database schema with the current database configuration.

        Args:
            event: The event that triggered the database update.
        """
        logger.info("Updating the MediaWiki database schema due to event: %s", event)

        if not self.unit.is_leader():
            event.fail("Only the leader unit can update the MediaWiki database schema")
            return

        try:
            self._mediawiki.update_database_schema()
            event.log("MediaWiki database schema updated successfully")
        except MediaWikiInstallError:
            event.fail("MediaWiki database schema update failed; check logs for details")


if __name__ == "__main__":  # pragma: nocover
    ops.main(Charm)
