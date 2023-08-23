import pathlib

import click

from labdiscoveryengine import create_app
from labdiscoveryengine.configuration.storage import create_deployment_folder

def _create_app(directory: pathlib.Path):
    create_app('development')
    pass

@click.group('lde')
def lde():
    """
    LabDiscoveryEngine is a Remote Laboratory Management System (RLMS). Use this command to manage a deployment.
    """

@lde.group('deployments')
def deployments():
    """
    Manage an existing deployment
    """

@deployments.command('create')
@click.option('-d', '--directory', type=click.Path(dir_okay=True, file_okay=False), required=True, help="Deployment directory")
@click.option('-f', '--force', is_flag=True, help="Force if the folder already exists / has contents")
def create_deployment(directory: pathlib.Path, force: bool):
    """
    Create a deployment
    """
    directory_path = pathlib.Path(directory)
    create_deployment_folder(directory_path, force)


@lde.group('credentials')
def credentials_group():
    """
    Credentials-related commands
    """

@credentials_group.group('administrators')
def administrator_credentials():
    """
    Credentials-related commands for administrators
    """

@administrator_credentials.command('list')
def list_administrators():
    """
    List the administrators in the system
    """

@administrator_credentials.command('create')
def create_administrator():
    """
    Create an administrator in the system
    """

@administrator_credentials.command('change-password')
def change_administartor_password():
    """
    Change the password of an administrator
    """

@credentials_group.group('external-users')
def external_users_group():
    """
    Credentials-related commands for external users
    """

@external_users_group.command('create')
def create_external_user():
    """
    Create an external user
    """

@external_users_group.command('list')
def list_external_user():
    """
    List the external users
    """

@external_users_group.command('change-password')
def change_external_user_password():
    """
    Change the password of an external user
    """

if __name__ == '__main__':
    lde()    