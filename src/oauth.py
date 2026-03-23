# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Provides the OAuth class to handle OAuth relations and state."""

import logging
from urllib.parse import urljoin, urlparse

from charms.hydra.v0.oauth import ClientConfig, OauthProviderConfig, OAuthRequirer
from ops import Object

from exceptions import MediaWikiBlockedStatusException
from state import StatefulCharmBase

logger = logging.getLogger(__name__)


class OAuth(Object):
    """The OAuth relation handler."""

    _base_scope = frozenset({"openid", "profile", "email"})
    _grant_types = frozenset({"authorization_code"})

    def __init__(self, charm: StatefulCharmBase, relation_name: str):
        """Initialize the handler and register event handlers.

        Args:
            charm: The charm instance.
            relation_name: The name of the oauth relation.
        """
        super().__init__(charm, "oauth-observer")

        self._charm = charm
        self.oauth = OAuthRequirer(self._charm, relation_name=relation_name)

    def update_client_config(
        self,
        redirect_path: str = "w/index.php/Special:PluggableAuthLogin",
    ) -> None:
        """Update the client config from the relation.

        Note, if a protocol-relative URL is used, we fall back to HTTP for the redirect URI.

        Args:
            redirect_path: The path to use for the redirect URI, relative to the URL origin.

        Raises:
            MediaWikiBlockedStatusException: If the client config update fails.
        """
        config = self._charm.load_charm_config()
        url_origin = config.url_origin or f"//{self._charm.app.name}"
        parsed_url = urlparse(url_origin, scheme="http://")

        extra_scopes = config.oauth_extra_scopes.split()
        client_config = ClientConfig(
            urljoin(parsed_url.geturl(), redirect_path),
            " ".join(self._base_scope | set(extra_scopes)),
            list(self._grant_types),
        )
        try:
            self.oauth.update_client_config(client_config)
        except Exception as e:
            logger.error("Failed to update OAuth client config: %s", e)
            raise MediaWikiBlockedStatusException("Failed to update OAuth client config") from e

    def get_provider_info(self) -> OauthProviderConfig | None:
        """Get the provider info from the relation."""
        return self.oauth.get_provider_info()
