import time
import pathlib
import secrets
import datetime

from collections import OrderedDict
from functools import partial
from typing import Any, Dict, NamedTuple, Optional, Union

import yaml
from flask import current_app

from werkzeug.security import generate_password_hash, check_password_hash

from labdiscoveryengine.configuration.exc import ConfigurationDirectoryNotFoundError, ConfigurationFileNotFoundError, InvalidConfigurationFoundError, InvalidConfigurationValueError, InvalidLaboratoryConfigurationError, InvalidUsernameConfigurationError

from ..data import Administrator, ExternalUser, Laboratory, Resource


# Define a custom representer for OrderedDict
def represent_ordered_dict(dumper, data):
    return dumper.represent_mapping('tag:yaml.org,2002:map', data.items())

# Register the custom representer
yaml.add_representer(OrderedDict, represent_ordered_dict, Dumper=yaml.Dumper)

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

def get_current_deployment_directory() -> pathlib.Path:
    """
    Obtain the current deployment directory
    """
    directory: pathlib.Path = pathlib.Path(current_app.config['LABDISCOVERYENGINE_DIRECTORY'])
    if not directory.exists():
        raise ConfigurationDirectoryNotFoundError(f"Directory does not exist: {directory.absolute()}")
    return directory


def get_latest_configuration(configuration: Optional[StoredConfiguration] = None) -> StoredConfiguration:
    """
    If a configuration is provided, it will modify it if the files have changed. 
    
    If the configuration is not provided, it will create a new one.
    """
    directory: pathlib.Path = get_current_deployment_directory()

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
                
            configuration_values[configuration_type] = file_data or {}
            configuration_checks[configuration_type] = modification_time_before_reading

            modification_time_after_reading = datetime.datetime.utcfromtimestamp(configuration_file_path.stat().st_mtime)
    
    if ConfigurationFileNames.configuration in configuration_values:
        try:
            configuration.variables.update(configuration_values[ConfigurationFileNames.configuration])
            configuration.last_check[ConfigurationFileNames.configuration] = configuration_checks[ConfigurationFileNames.configuration]
            configuration.apply_variables()
        except Exception as err:
            raise InvalidConfigurationValueError(f"Invalid variables in file {configuration_files[ConfigurationFileNames.configuration].absolute()}: {err}")

    if ConfigurationFileNames.resources in configuration_values:
        try:
            added_resources = []
            for resource_identifier, resource_data in configuration_values[ConfigurationFileNames.resources].items():
                resource_url = resource_data.get('url')                
                resource_login = resource_data.get('login') or get_config('DEFAULT_RESOURCE_LOGIN')
                resource_password = resource_data.get('password') or get_config('DEFAULT_RESOURCE_PASSWORD')
                resource_features = resource_data.get('features') or []
                resource_api = resource_data.get('api') or 'labdiscoverylib'

                if resource_login is None:
                    raise InvalidConfigurationValueError(f"Resource {resource_identifier} has no 'login' defined")
                
                if resource_password is None:
                    raise InvalidConfigurationValueError(f"Resource {resource_identifier} has no 'password' defined")
                
                if not resource_url:
                    raise InvalidConfigurationValueError(f"Resource {resource_identifier} has no 'url' defined")
                
                for resource_feature in resource_features:
                    if not isinstance(resource_feature, str):
                        raise InvalidConfigurationValueError(f"Resource {resource_identifier} has an invalid feature (must be str): {resource_feature}")

                configuration.resources[resource_identifier] = Resource(
                    identifier=resource_identifier,
                    url=resource_url,
                    login=resource_login,
                    password=resource_password,
                    features=resource_features,
                    api=resource_api
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
            for identifier, laboratory_data in configuration_values[ConfigurationFileNames.laboratories].items():
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
                    description=laboratory_data.get('description'),
                    keywords=laboratory_data.get('keywords'),
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
                                                            name=external_user_data.get('name') or login,
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
        
    return configuration

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
            result = input(f"[{time.asctime()}] The directory ({directory}) already exists. Do you want to overwrite it? (y/n) ")
            while result not in ('y', 'n'):
                result = input(f"[{time.asctime()}] Invalid answer. Do you want to overwrite it? (y/n) ")
            
            if result == 'n':
                print(f"[{time.asctime()}] Cancelling deployment creation")
                return
    else:
        directory.mkdir()

    print(f"[{time.asctime()}] Creating configuration files in {directory.absolute()}...")
    
    filenames = _generate_files_dict(directory)

    with filenames[ConfigurationFileNames.configuration].open('w') as f:
        f.write('\n'.join([
            "# In this file you store the standard configuration variables",
            "# ",
            "# DEFAULT_MAX_TIME: 300 # So you do not need to put max_time: 300 in each laboratory",
            "# DEFAULT_RESOURCE_LOGIN: lde # If you have the same login in all the laboratories",
            "# DEFAULT_RESOURCE_PASSWORD: lde # If you have the same password in all the laboratories",
            "# ",
            "# REDIS_URL: redis://localhost:6379/0",
            "# MONGO_URL: mongodb://localhost:27017/lde",
            "# MYSQL_DATABASE_URL: mysql+pymysql://lde:password@localhost:3306/lde # Optional",
            "",
            f"SECRET_KEY: {secrets.token_urlsafe()}",
            "",
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
            "  features: [feature1, feature2] # So we can reserve with a particular feature",
            "dummy-2:",
            "  # Include the URL and credentials of the remote laboratory",
            "  url: http://localhost:5001",
            "  login: lde",
            "  password: password",
            "  features: [feature1, feature3] # So we can reserve with a particular feature",
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
    print(f"[{time.asctime()}] Deployment directory {directory.absolute()} properly created")

def _add_user_to_yaml(field: str, login: str, data: Dict[str, str]):
    directory: pathlib.Path = get_current_deployment_directory()

    configuration_files: Dict[str, pathlib.Path] = _generate_files_dict(directory)

    configuration = get_latest_configuration()
    if login in configuration.administrators:
        raise InvalidUsernameConfigurationError(f"{login} already used by an administrator")
    
    if login in configuration.external_users:
        raise InvalidUsernameConfigurationError(f"{login} already used by an external user")

    # Read the YAML file as a list of lines
    with open(configuration_files[ConfigurationFileNames.credentials], 'r') as credentials_file:
        lines = credentials_file.readlines()

    # Find the line index after the "field:" (e.g., administrators:) section and determine the indentation
    idx = None
    indentation = ""
    field_found = False
    for i, line in enumerate(lines):
        if line.startswith(f'{field}:'):
            field_found = True
            idx = i + 1
            # Find the indentation of the first non-empty line
            while not lines[idx].strip() and not lines[idx].strip().startswith('#'):
                idx += 1
            indentation = lines[idx][:-len(lines[idx].lstrip())]
            break

    if not indentation:
        # For example, 'external:' might not exist, so we copy the administrators one
        idx = None
        indentation = ""
        for i, line in enumerate(lines):
            if line.startswith(f'administrators:'):
                idx = i + 1
                # Find the indentation of the first non-empty line
                while not lines[idx].strip() and not lines[idx].strip().startswith('#'):
                    idx += 1
                indentation = lines[idx][:-len(lines[idx].lstrip())]
                break
        
        idx = len(lines)

    yaml_user_content: str = '\n'.join([ indentation + line for line in yaml.dump(data, indent=len(indentation)).split('\n') ])

    # Add the new user after the last existing user in the "administrators" section
    while idx < len(lines) and lines[idx].strip() != '':
        idx += 1

    if not field_found:
        lines.insert(idx, "\n")
        lines.insert(idx+1, field + ':\n')

    lines.insert(idx+2, yaml_user_content + '\n')

    with open(configuration_files[ConfigurationFileNames.credentials], 'w') as credentials_file:
        credentials_file.write("".join(lines))

def create_admin_user(login: str, name: Optional[str], email: Optional[str], password: str):
    """
    Create a username in the file
    """
    data = {
        login: OrderedDict()
    }
    if name:
        data[login]['name'] = name
    if email:
        data[login]['email'] = email

    data[login]['password'] = generate_password_hash(password)
    
    _add_user_to_yaml('administrators', login, data)

    print(f"Administrator {login} added")

def create_external_user(login: str, name: Optional[str], email: Optional[str], password: str):
    """
    Create a username in the file
    """
    data = {
        login: OrderedDict()
    }
    if name:
        data[login]['name'] = name
    if email:
        data[login]['email'] = email

    data[login]['password'] = generate_password_hash(password)
    data[login]['laboratories'] = 'all'

    _add_user_to_yaml('external', login, data)

    print(f"External user {login} added")

def list_users(administrators: bool = False, external_users: bool = False):
    """
    List the users in the file
    """
    configuration = get_latest_configuration()

    if administrators:
        print("Administrators:")
        if configuration.administrators:
            for login in configuration.administrators:
                print(f" - {login}")
                print(f"   - name: {configuration.administrators[login].name}")
                if configuration.administrators[login].email:
                    print(f"   - email: {configuration.administrators[login].email}")
        else:
            print("No administrator in the system")

    if external_users:
        print("External users:")
        if configuration.external_users:
            for login in configuration.external_users:
                print(f" - {login}")
                print(f"   - name: {configuration.external_users[login].name}")
                if configuration.external_users[login].email:
                    print(f"   - email: {configuration.external_users[login].email}")
                print(f"   - laboratories: {configuration.external_users[login].laboratories}")
        else:
            print("No external user in the system")

def change_credentials_password(field: str, login: str, password: str):
    """
    Change the password of an admin or user
    """
    hashed_password = generate_password_hash(password)
    directory: pathlib.Path = get_current_deployment_directory()

    configuration_files: Dict[str, pathlib.Path] = _generate_files_dict(directory)

    with open(configuration_files[ConfigurationFileNames.credentials], 'r') as file:
        lines = file.readlines()

    # Variables to track the indentation levels
    login_indentation = password_indentation = -1

    # Flags to determine if we are inside the correct sections
    inside_field = inside_login = False
    found = False

    for i, line in enumerate(lines):
        stripped_line = line.lstrip()

        if not stripped_line or stripped_line.startswith("#"):
            continue

        indentation = len(line) - len(stripped_line)

        if inside_field:
            if login_indentation == -1:
                login_indentation = indentation
            
            if inside_login:
                if password_indentation == -1:
                    password_indentation = indentation

        if line.startswith(field + ":"):
            inside_field = True

        if inside_field and login_indentation != -1 and line.startswith(' ' * login_indentation + login + ":"):
            inside_login = True

        if inside_field and inside_login and line.startswith(' ' * password_indentation + "password:"):
            lines[i] = " " * indentation + f"password: {hashed_password}\n"
            found = True
            break

        if inside_field and indentation == 0 and not line.startswith(field + ":"):
            inside_field = False
            login_indentation = password_indentation = -1

        if inside_field and indentation == login_indentation and not stripped_line.startswith(login + ":"):
            inside_login = False
            password_indentation = -1

    with open(configuration_files[ConfigurationFileNames.credentials], 'w') as file:
        file.writelines(lines)

    if found:
        print(f"Password updated successfully for field: {field} and login: {login}")
    else:
        print(f"login: {login} not found for field: {field}")

def check_credentials_password(field: str, login: str, password: str):
    """
    Given a field and a username, check if the password is correct or not.
    """
    configuration = get_latest_configuration()

    if field == 'administrators':
        if login in configuration.administrators:
            if check_password_hash(configuration.administrators[login].hashed_password, password):
                print(f"That password is correct for user {login}")
                return True
            else:
                print(f"That password is INCORRECT for user {login}")
        else:
            print(f"login: {login} not found for field: {field}")
    elif field == 'external':
        if login in configuration.external_users:
            if check_password_hash(configuration.external_users[login].hashed_password, password):
                print(f"That password is correct for user {login}")
                return True
            else:
                print(f"That password is INCORRECT for user {login}")
        else:
            print(f"login: {login} not found for field: {field}")
    return False
