# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Provides the OAuth class to handle OAuth relations and state."""

import logging
from typing import Iterable
from urllib.parse import urljoin

from charms.hydra.v0.oauth import ClientConfig, OauthProviderConfig, OAuthRequirer
from ops import CharmBase, Object

logger = logging.getLogger(__name__)


class OAuth(Object):
    """The OAuth relation handler."""

    def __init__(self, charm: CharmBase, relation_name: str):
        """Initialize the handler and register event handlers.

        Args:
            charm: The charm instance.
            relation_name: The name of the oauth relation.
        """
        super().__init__(charm, "oauth-observer")

        self._base_scope = {"openid", "profile", "email"}
        self.oauth = OAuthRequirer(charm, relation_name=relation_name)

    def update_client_config(
        self,
        external_hostname: str,
        redirect_path: str = "w/index.php/Special:PluggableAuthLogin",
        extra_scope: Iterable[str] = (),
    ) -> None:
        """Update the client config from the relation."""
        client_config = ClientConfig(
            urljoin(external_hostname, redirect_path),
            " ".join(self._base_scope | set(extra_scope)),
            ["authorization_code"],
        )
        self.oauth.update_client_config(client_config)

    def get_provider_info(self) -> OauthProviderConfig | None:
        """Get the provider info from the relation."""
        return self.oauth.get_provider_info()
