.. meta::
   :description: How to configure MediaWiki using LocalSettings.php.

.. _how_to_configure_mediawiki:

How to configure MediaWiki
==========================

MediaWiki's basic configuration settings are managed using the ``LocalSettings.php`` file. While the MediaWiki charm will provide some sensible defaults and also generate some necessary secrets, you may want to customize the configuration of your MediaWiki deployment by providing your own ``LocalSettings.php`` file.

.. warning::

   Certain configuration settings, such as database credentials and secrets like ``$wgSecretKey``, are :ref:`handled entirely by the MediaWiki charm <reference_charm_managed_settings>`. The settings are applied in such a way that the Charm's management has greater priority over most manually configured settings.

The MediaWiki charm allows you to configure an arbitrary ``LocalSettings.php`` file using ``juju config``.

For example, with a local ``LocalSettings.php`` file, you can run the following command to update the charm's configuration:

.. code-block:: bash

   juju config mediawiki-k8s local-settings="$(cat ${PATH_TO_LOCAL_SETTINGS_FILE})"

.. note::

   The user-configured ``LocalSettings.php`` contents are stored outside the webroot and are not world-readable. This is to minimize the risk of exposing sensitive information. Refer to the :ref:`security overview <explanation_security>` for more details.

Configure TLS certificates
--------------------------

To configure Apache to serve HTTPS, integrate the charm with a provider on the :ref:`certificates relation <reference_relation_endpoints_certificates>`:

.. code-block:: bash

   juju integrate mediawiki-k8s:certificates self-signed-certificates:certificates

The charm requests a certificate for its Kubernetes service hostname. This certificate secures the connection between Traefik and Apache; the certificate provider controls issuance policy and optional certificate subject metadata.

Apache continues to serve HTTP on port 80 after TLS is enabled. When a ``traefik-route`` relation is present, the charm routes Traefik to Apache over HTTPS on port 443. Configure Traefik to trust the issuing CA through its own certificate and CA transfer relations.

.. seealso::

   Read more about the ``LocalSettings.php`` file from the official `MediaWiki documentation <https://www.mediawiki.org/wiki/Manual:LocalSettings.php>`__.
