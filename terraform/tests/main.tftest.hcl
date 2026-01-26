# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

variables {
  channel = "latest/edge"
  # renovate: depName="MediaWiki"
  revision = 1
}

run "basic_deploy" {
  assert {
    condition     = module.MediaWiki.app_name == "MediaWiki"
    error_message = "MediaWiki app_name did not match expected"
  }
}
