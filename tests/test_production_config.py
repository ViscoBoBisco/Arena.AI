"""Production mode must never silently fall back to development credentials."""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unified.gateway import _load_api_keys, validate_production_configuration
from unified.server import _load_web_allowlist


class ProductionConfigurationTests(unittest.TestCase):
    def _env(self, token="a" * 48, projects=None):
        profile = {"projects": projects or ["project-a"], "grants": ["repo.read"]}
        return {
            "UNIFIED_ENV": "production",
            "CALLBACK_SECRET": "b" * 48,
            "UNIFIED_API_KEYS": json.dumps({token: profile}),
        }

    def test_missing_production_keys_do_not_load_the_development_wildcard(self):
        with patch.dict(os.environ, {"UNIFIED_ENV": "production", "UNIFIED_API_KEYS": ""}, clear=False):
            self.assertEqual(_load_api_keys(), {})

    def test_production_requires_explicit_api_key_profiles(self):
        env = {"UNIFIED_ENV": "production", "CALLBACK_SECRET": "b" * 48}
        with patch.dict(os.environ, env, clear=False), patch("unified.gateway.API_KEYS", {}):
            with self.assertRaisesRegex(RuntimeError, "UNIFIED_API_KEYS"):
                validate_production_configuration()

    def test_production_rejects_wildcard_project_scope(self):
        env = self._env(projects=["*"])
        profile = {"projects": {"*"}, "grants": {"repo.read"}}
        with patch.dict(os.environ, env, clear=False), patch("unified.gateway.API_KEYS", {"a" * 48: profile}):
            with self.assertRaisesRegex(RuntimeError, "wildcard project"):
                validate_production_configuration()

    def test_production_accepts_strong_scoped_credentials(self):
        env = self._env()
        profile = {"projects": {"project-a"}, "grants": {"repo.read"}}
        with patch.dict(os.environ, env, clear=False), patch("unified.gateway.API_KEYS", {"a" * 48: profile}):
            validate_production_configuration()

    def test_development_mode_keeps_local_defaults(self):
        with patch.dict(os.environ, {"UNIFIED_ENV": "development"}, clear=False):
            validate_production_configuration()

    def test_web_allowlist_is_empty_unless_explicitly_configured(self):
        with patch.dict(os.environ, {"UNIFIED_WEB_ALLOWLIST": ""}, clear=False):
            self.assertEqual(_load_web_allowlist(), ())
        with patch.dict(os.environ, {"UNIFIED_WEB_ALLOWLIST": '["Example.com"]'}, clear=False):
            self.assertEqual(_load_web_allowlist(), ("example.com",))

    def test_web_allowlist_rejects_wildcards_and_private_ips(self):
        for value in ('["*"]', '["127.0.0.1"]', '"example.com"'):
            with self.subTest(value=value), patch.dict(
                    os.environ, {"UNIFIED_WEB_ALLOWLIST": value}, clear=False):
                with self.assertRaises(RuntimeError):
                    _load_web_allowlist()


if __name__ == "__main__":
    unittest.main()
