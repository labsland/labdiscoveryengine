import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

from labdiscoveryengine.data import RobotcheckerHealthcheck
from labdiscoveryengine.configuration.storage import ConfigurationFileNames, get_latest_configuration


class ConfigurationStorageTestCase(unittest.TestCase):
    def test_initial_load_reads_reproducible_epoch_timestamp_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir)
            self._write_environment_credential_configuration(directory)
            (directory / 'credentials.yml').write_text(
                'administrators: {}\nexternal:\n  tester:\n    password: hash\n    laboratories: [lab-1]\n')
            for path in directory.glob('*.yml'):
                os.utime(path, (0, 0))
            app = Flask(__name__)
            app.config['LABDISCOVERYENGINE_DIRECTORY'] = str(directory)
            with mock.patch.dict(os.environ, {'TEST_RESOURCE_LOGIN':'user', 'TEST_RESOURCE_PASSWORD':'pass'}):
                with app.app_context():
                    config = get_latest_configuration()
                    self.assertEqual(app.config['DEFAULT_MAX_TIME'], 180)
                    self.assertIn('resource-1', config.resources)
                    self.assertIn('lab-1', config.laboratories)
                    self.assertIn('tester', config.external_users)
                    self.assertIs(get_latest_configuration(config), config)

    def _write_environment_credential_configuration(self, deployment_dir):
        (deployment_dir / "configuration.yml").write_text(
            "DEFAULT_MAX_TIME: 180\n",
            encoding="utf-8",
        )
        (deployment_dir / "resources.yml").write_text(
            "\n".join(
                [
                    "resource-1:",
                    "  url: http://example.invalid/lab",
                    "  login_env: TEST_RESOURCE_LOGIN",
                    "  password_env: TEST_RESOURCE_PASSWORD",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (deployment_dir / "laboratories.yml").write_text(
            "lab-1:\n  resources: [resource-1]\n",
            encoding="utf-8",
        )
        (deployment_dir / "credentials.yml").write_text(
            "administrators: {}\nexternal: {}\n",
            encoding="utf-8",
        )

    def test_get_latest_configuration_resolves_resource_credentials_from_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            deployment_dir = Path(tmpdir)
            self._write_environment_credential_configuration(deployment_dir)
            app = Flask(__name__)
            app.config["LABDISCOVERYENGINE_DIRECTORY"] = str(deployment_dir)

            with mock.patch.dict(
                os.environ,
                {
                    "TEST_RESOURCE_LOGIN": "environment-user",
                    "TEST_RESOURCE_PASSWORD": "environment-pass",
                },
            ):
                with app.app_context():
                    config = get_latest_configuration()

            self.assertEqual("environment-user", config.resources["resource-1"].login)
            self.assertEqual("environment-pass", config.resources["resource-1"].password)

    def test_get_latest_configuration_rejects_missing_resource_credential_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            deployment_dir = Path(tmpdir)
            self._write_environment_credential_configuration(deployment_dir)
            app = Flask(__name__)
            app.config["LABDISCOVERYENGINE_DIRECTORY"] = str(deployment_dir)

            with mock.patch.dict(os.environ, {}, clear=True):
                with app.app_context():
                    with self.assertRaisesRegex(
                        Exception,
                        "TEST_RESOURCE_LOGIN",
                    ):
                        get_latest_configuration()

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

    def test_get_latest_configuration_prunes_removed_entries_on_reload(self):
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
                        "  url: http://example.invalid/lab1",
                        "resource-2:",
                        "  url: http://example.invalid/lab2",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (deployment_dir / "laboratories.yml").write_text(
                "\n".join(
                    [
                        "lab-1:",
                        "  resources: [resource-1]",
                        "lab-2:",
                        "  resources: [resource-2]",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (deployment_dir / "credentials.yml").write_text(
                "\n".join(
                    [
                        "administrators:",
                        "  admin-1:",
                        "    password: hash",
                        "  admin-2:",
                        "    password: hash",
                        "external:",
                        "  tester-1:",
                        "    password: hash",
                        "    laboratories: [lab-1]",
                        "  tester-2:",
                        "    password: hash",
                        "    laboratories: [lab-2]",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            app = Flask(__name__)
            app.config["LABDISCOVERYENGINE_DIRECTORY"] = str(deployment_dir)

            with app.app_context():
                config = get_latest_configuration()
                self.assertIn("resource-2", config.resources)
                self.assertIn("lab-2", config.laboratories)
                self.assertIn("admin-2", config.administrators)
                self.assertIn("tester-2", config.external_users)

                (deployment_dir / "resources.yml").write_text(
                    "\n".join(
                        [
                            "resource-1:",
                            "  url: http://example.invalid/lab1",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )
                (deployment_dir / "laboratories.yml").write_text(
                    "\n".join(
                        [
                            "lab-1:",
                            "  resources: [resource-1]",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )
                (deployment_dir / "credentials.yml").write_text(
                    "\n".join(
                        [
                            "administrators:",
                            "  admin-1:",
                            "    password: hash",
                            "external:",
                            "  tester-1:",
                            "    password: hash",
                            "    laboratories: [lab-1]",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )
                for configuration_type in (
                    ConfigurationFileNames.configuration,
                    ConfigurationFileNames.resources,
                    ConfigurationFileNames.credentials,
                    ConfigurationFileNames.laboratories,
                ):
                    config.last_check[configuration_type] = datetime.datetime.utcfromtimestamp(0)

                config = get_latest_configuration(config)

            self.assertNotIn("resource-2", config.resources)
            self.assertNotIn("lab-2", config.laboratories)
            self.assertNotIn("admin-2", config.administrators)
            self.assertNotIn("tester-2", config.external_users)
