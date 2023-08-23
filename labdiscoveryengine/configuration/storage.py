import pathlib
import datetime
from functools import partial
from typing import Any, Dict, NamedTuple, Optional, Union

import yaml
from flask import current_app

from werkzeug.security import generate_password_hash

from labdiscoveryengine.configuration.exc import ConfigurationDirectoryNotFoundError, ConfigurationFileNotFoundError, InvalidConfigurationFoundError, InvalidConfigurationValueError, InvalidLaboratoryConfigurationError

from ..data import Administrator, ExternalUser, Laboratory, Resource

class ConfigurationFileNames:
    configuration = 'configuration'
    credentials = 'credentials'
    resources = 'resources'
    laboratories = 'laboratories'

class StoredConfiguration(NamedTuple):
    administrators: Dict[str, Administrator]
    external_users: Dict[str, ExternalUser]
    laboratories: Dict[str, Laboratory]
    resources: Dict[str, Resource]
    variables: Dict[str, Union[str, int]]
    last_check: Dict[str, datetime.datetime]

    def apply_variables(self):
        """
        Given the variables stored in the configuration.yml, apply them everywhere (e.g., change current_app.config)       
        """
        for variable_name, variable_value in self.variables.items():
            current_app.config[variable_name] = variable_value

    @staticmethod
    def create_empty() -> 'StoredConfiguration':
        return StoredConfiguration(
            administrators={},
            external_users={},
            laboratories={},
            resources={},
            variables={},
            last_check={
                ConfigurationFileNames.configuration: datetime.datetime.utcfromtimestamp(0),
                ConfigurationFileNames.resources: datetime.datetime.utcfromtimestamp(0),
                ConfigurationFileNames.credentials: datetime.datetime.utcfromtimestamp(0),
                ConfigurationFileNames.laboratories: datetime.datetime.utcfromtimestamp(0),
            }
        )
    
    def clear(self):
        """
        Clear the configuration.
        """
        self.administrators.clear()
        self.external_users.clear()
        self.laboratories.clear()
        self.resources.clear()
        self.variables.clear()
        self.last_check.clear()

def _generate_files_dict(directory: pathlib.Path) -> Dict[str, pathlib.Path]:
    return {
        ConfigurationFileNames.configuration: directory / "configuration.yml",
        ConfigurationFileNames.credentials: directory / "credentials.yml",
        ConfigurationFileNames.resources: directory / "resources.yml",
        ConfigurationFileNames.laboratories: directory / "laboratories.yml",
    }

def get_latest_configuration(configuration: Optional[StoredConfiguration] = None) -> StoredConfiguration:
    """
    If a configuration is provided, it will modify it if the files have changed. 
    
    If the configuration is not provided, it will create a new one.
    """
    directory: pathlib.Path = pathlib.Path(current_app.config['LABDISCOVERYENGINE_DIRECTORY'])
    if not directory.exists():
        raise ConfigurationDirectoryNotFoundError(f"Directory does not exist: {directory.absolute()}")
    
    configuration_files: Dict[str, pathlib.Path] = _generate_files_dict(directory)

    if configuration is None:
        configuration = StoredConfiguration.create_empty()

    get_config = partial(_get_config, configuration=configuration)

    configuration_values: Dict[str, Dict] = {}
    configuration_checks: Dict[str, datetime.datetime] = {}

    for configuration_type, configuration_file_path in configuration_files.items():
        if not configuration_file_path.exists():
            raise ConfigurationFileNotFoundError(f"File does not exist: {configuration_file_path.absolute()}")
        
        modification_time_before_reading = datetime.datetime.utcfromtimestamp(configuration_file_path.stat().st_mtime)
        if configuration.last_check[configuration_type] >= modification_time_before_reading:
            continue

        # By default one day in the future
        modification_time_after_reading = datetime.datetime.utcnow() + datetime.timedelta(days=1)

        # If the the file has potentially been modified after it was read, read again just in case.
        while modification_time_after_reading > modification_time_before_reading:

            modification_time_before_reading = datetime.datetime.utcfromtimestamp(configuration_file_path.stat().st_mtime)
            with configuration_file_path.open() as f:
                try:
                    file_data = yaml.safe_load(f)
                except Exception as err:
                    raise InvalidConfigurationFoundError(f"Invalid configuration in file {configuration_file_path.absolute()}: {err}")
                
            configuration_values[configuration_type] = file_data
            configuration_checks[configuration_type] = modification_time_before_reading

            modification_time_after_reading = datetime.datetime.utcfromtimestamp(configuration_file_path.stat().st_mtime())
    
    if ConfigurationFileNames.configuration in configuration_values:
        try:
            configuration.variables.update(configuration_values[ConfigurationFileNames.configuration]['variables'])
            configuration.last_check[ConfigurationFileNames.configuration] = configuration_checks[ConfigurationFileNames.configuration]
            configuration.apply_variables()
        except Exception as err:
            raise InvalidConfigurationValueError(f"Invalid variables in file {configuration_files[ConfigurationFileNames.configuration].absolute()}: {err}")

    if ConfigurationFileNames.resources in configuration_values:
        try:
            added_resources = []
            for resource_identifier, resource_data in configuration_values[ConfigurationFileNames.resources]['resources'].items():
                resource_url = resource_data.get('url')                
                resource_login = resource_data.get('login') or get_config('DEFAULT_RESOURCE_LOGIN')
                resource_password = resource_data.get('password') or get_config('DEFAULT_RESOURCE_PASSWORD')

                if resource_login is None:
                    raise InvalidConfigurationValueError(f"Resource {resource_identifier} has no 'login' defined")
                
                if resource_password is None:
                    raise InvalidConfigurationValueError(f"Resource {resource_identifier} has no 'password' defined")
                
                if not resource_url:
                    raise InvalidConfigurationValueError(f"Resource {resource_identifier} has no 'url' defined")

                configuration.resources[resource_identifier] = Resource(
                    identifier=resource_identifier,
                    url=resource_url,
                    login=resource_login,
                    password=resource_password
                )
                
                added_resources.append(resource_identifier)
            
            for resource_identifier in configuration.resources:
                if resource_identifier not in added_resources:
                    configuration.resources.pop(resource_identifier)

            configuration.last_check[ConfigurationFileNames.resources] = configuration_checks[ConfigurationFileNames.resources]
        except Exception as err:
            raise InvalidConfigurationValueError(f"Invalid resources in file {configuration_files[ConfigurationFileNames.resources].absolute()}: {err}")

    if ConfigurationFileNames.laboratories in configuration_values:
        try:
            laboratories = {

            }
            for identifier, laboratory_data in configuration_values[ConfigurationFileNames.laboratories]['laboratories'].items():
                raw_resources = laboratory_data.get('resources', [])
                resources = set()
                for raw_resource in raw_resources:
                    if raw_resource not in configuration.resources:
                        raise InvalidLaboratoryConfigurationError(f"Resource {raw_resource} listed in laboratory {identifier} not found in resources {list(configuration.resources.keys())}")
                    resources.add(raw_resource)

                laboratories[identifier] = Laboratory(
                    identifier=identifier,
                    display_name=laboratory_data.get('display_name') or identifier,
                    category=laboratory_data.get('category'),
                    max_time=laboratory_data.get('max_time') or get_config('DEFAULT_MAX_TIME'),
                    resources=resources,
                    image=laboratory_data.get('image', '')
                )
                configuration.laboratories[identifier] = laboratories[identifier]
                
            configuration.last_check[ConfigurationFileNames.laboratories] = configuration_checks[ConfigurationFileNames.laboratories]
        except Exception as err:
            raise InvalidConfigurationValueError(f"Invalid laboratories in file {configuration_files[ConfigurationFileNames.laboratories].absolute()}: {err}")

    if ConfigurationFileNames.credentials in configuration_values:
        try:
            added_administrators = []
            for login, administrator_data in configuration_values[ConfigurationFileNames.credentials].get('administrators', {}).items():
                if 'password' not in administrator_data:
                    raise InvalidConfigurationValueError(f"Missing password in administrator {login} in file {configuration_files[ConfigurationFileNames.credentials].absolute()}")
                
                configuration.administrators[login] = Administrator(
                                                            login=login,
                                                            name=administrator_data.get('name') or login,
                                                            email=administrator_data.get('email'),
                                                            hashed_password=administrator_data['password'],
                                                    )
                added_administrators.append(login)

            for login in configuration.administrators:
                if login not in added_administrators:
                    configuration.administrators.pop(login)

            added_external_users = []
            for login, external_user_data in configuration_values[ConfigurationFileNames.credentials].get('external', {}).items():
                if 'password' not in external_user_data:
                    raise InvalidConfigurationValueError(f"Missing password in external user {login} in file {configuration_files[ConfigurationFileNames.credentials].absolute()}")
                
                external_user_laboratories = set()
                if external_user_data.get('laboratories') in ('all', 'ALL'):
                    external_user_laboratories = list(configuration.laboratories.keys())
                else:
                    for laboratory in external_user_data.get('laboratories', []):
                        if laboratory not in configuration.laboratories:
                            raise InvalidConfigurationValueError(f"External user {login} has a laboratory {laboratory} that is not in the configuration")
                        external_user_laboratories.add(laboratory)

                configuration.external_users[login] = ExternalUser(
                                                            login=login,
                                                            email=external_user_data.get('email'),
                                                            hashed_password=external_user_data['password'],
                                                            laboratories=external_user_laboratories
                                                    )
                added_external_users.append(login)
            
            for login in configuration.external_users:
                if login not in added_external_users:
                    configuration.external_users.pop(login)

            configuration.last_check[ConfigurationFileNames.credentials] = configuration_checks[ConfigurationFileNames.credentials]
        except Exception as err:
            raise InvalidConfigurationValueError(f"Invalid credentials in file {configuration_files[ConfigurationFileNames.credentials].absolute()}: {err}")

def _get_config(configuration: StoredConfiguration, key: str) -> Any:
    if key in configuration.variables:
        return configuration.variables[key]
    return current_app.config.get(key)

def create_deployment_folder(directory: pathlib.Path, force: bool = False):
    """
    Create a new deployment folder
    """
    if directory.exists():
        if not force:
            result = input(f"The directory ({directory}) already exists. Do you want to overwrite it? (y/n) ")
            while result not in ('y', 'n'):
                result = input(f"Invalid answer. Do you want to overwrite it? (y/n) ")
            
            if result == 'n':
                print(f"Cancelling deployment creation")
                return
    else:
        directory.mkdir()

    print(f"Creating configuration files in {directory.absolute()}...")
    
    filenames = _generate_files_dict(directory)

    with filenames[ConfigurationFileNames.configuration].open('w') as f:
        f.write('\n'.join([
            "# In this file you store the standard configuration variables",
            "# ",
            "# DEFAULT_MAX_TIME=300 # So you do not need to put max_time: 300 in each laboratory",
            "# DEFAULT_RESOURCE_LOGIN=lde # If you have the same login in all the laboratories",
            "# DEFAULT_RESOURCE_PASSWORD=lde # If you have the same password in all the laboratories",
            "# ",
            "# REDIS_URL = 'redis://localhost:6379/0'",
            "# MONGO_URL = 'mongodb://localhost:27017/lde'",
            "# MYSQL_DATABASE_URL = 'mysql+pymysql://lde:password@localhost:3306/lde' # Optional",
            ""
        ]))

    with filenames[ConfigurationFileNames.resources].open('w') as f:
        f.write('\n'.join([
            "# This file contains the laboratory resources. For example, you may have 3 copies of",
            "# the same laboratory. In LDE, we call this 3 resources of one laboratory. Therefore,",
            "# here you can specify and document the particular resources (the URL, login, password,",
            "# healthchecks of the resource, etc). Later, in the laboratories.yml file you will be",
            "# able to specify the name of the laboratory, which resources are assigned to what",
            "# laboratory, and information that the user should see (e.g., the image, category, etc.)",
            "",
            "dummy-1:",
            "  # Include the URL and credentials of the remote laboratory",
            "  url: http://localhost:5000",
            "  login: lde # If always the same use DEFAULT_RESOURCE_LOGIN in credentials.yml",
            "  password: password # If always the same use DEFAULT_RESOURCE_PASSWORD in credentials.yml",
            "dummy-2:",
            "  # Include the URL and credentials of the remote laboratory",
            "  url: http://localhost:5001",
            "  login: lde",
            "  password: password",
            ""
        ]))

    with filenames[ConfigurationFileNames.laboratories].open('w') as f:
        f.write('\n'.join([
            "# This file contains the laboratories, and associate them to the resources. In LDE, you can",
            "# have multiple remote laboratories, and multiple copies of each of them. You might even have",
            "# resources shared among two laboratories. For example, in LabsLand, the Arduino Robot is a remote",
            "# laboratory and you can select any robot, or robots that have one type of path or another type",
            "# of path. If we had 4 robots (2 with one path and 2 with the other), we can have 3 laboratories: one",
            "# with two resources, the other one with the other 2 resources and a general one with all 4 resources",
            "# ",
            "# This file is focused on information that the user should be aware: name of the lab, category, image, etc.",
            "",
            "dummy:",
            "  display_name: Dummy Lab # optional",
            "  category: Dummy laboratories # optional",
            "  max_time: 300 # optional, maximum time users can use the lab, in seconds",
            "  image: https://example.com/wherever/is/your/logo.jpg # optional",
            "  resources: # alternatively you can put them inline: dummy-1, dummy-2",
            "  - dummy-1", 
            "  - dummy-2",
            ""
        ]))

    with filenames[ConfigurationFileNames.credentials].open('w') as f:
        f.write('\n'.join([
            "# This file contains the credentials of the administrators and external users.",
            "# For security reasons. Additionally, this removes the requirement of a SQL database.",
            "",
            "administrators:",
            "  admin: ",
            "    name: Admin User # optional",
            "    # email: (optional)",
            "    # The following is a salted hash for 'password'. ",
            "    # Please use the command 'lde credentials administrators change-password' to change it.",
            f"    password: {generate_password_hash('password')}",
            ""
        ]))
    print("Deployment directory properly created")
