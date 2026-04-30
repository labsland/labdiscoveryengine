import tempfile
import unittest
from pathlib import Path

from flask import Flask

from labdiscoveryengine.data import RobotcheckerHealthcheck
from labdiscoveryengine.configuration.storage import get_latest_configuration


class ConfigurationStorageTestCase(unittest.TestCase):
    def test_get_latest_configuration_uses_defaults_from_configuration_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            deployment_dir = Path(tmpdir)
            (deployment_dir / "configuration.yml").write_text(
                "\n".join(
                    [
                        "DEFAULT_MAX_TIME: 180",
                        "DEFAULT_RESOURCE_LOGIN: default-user",
                        "DEFAULT_RESOURCE_PASSWORD: default-pass",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (deployment_dir / "resources.yml").write_text(
                "\n".join(
                    [
                        "resource-1:",
                        "  url: http://example.invalid/lab",
                        "  features: ['boolean']",
                        "  healthchecks:",
                        "    checker:",
                        "      type: robotchecker",
                        "      url: https://checker.example/status/robot-1/",
                        "      timeout: 25",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (deployment_dir / "laboratories.yml").write_text(
                "\n".join(
                    [
                        "boolean-lab:",
                        "  display_name: Boolean lab",
                        "  bypass_resource_health: true",
                        "  resources:",
                        "    - resource-1",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (deployment_dir / "credentials.yml").write_text(
                "\n".join(
                    [
                        "administrators:",
                        "  admin:",
                        "    password: hash",
                        "external:",
                        "  tester:",
                        "    password: hash",
                        "    laboratories: all",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            app = Flask(__name__)
            app.config["LABDISCOVERYENGINE_DIRECTORY"] = str(deployment_dir)

            with app.app_context():
                config = get_latest_configuration()

            self.assertEqual("default-user", config.resources["resource-1"].login)
            self.assertEqual("default-pass", config.resources["resource-1"].password)
            self.assertEqual(180, config.laboratories["boolean-lab"].max_time)
            self.assertTrue(config.laboratories["boolean-lab"].bypass_resource_health)
            self.assertIsInstance(config.resources["resource-1"].healthchecks[0], RobotcheckerHealthcheck)
            self.assertEqual(25, config.resources["resource-1"].healthchecks[0].timeout)
