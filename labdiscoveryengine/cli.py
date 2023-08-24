import os
import getpass
import pathlib
import functools
import sys

from typing import Callable, Optional

import click

from labdiscoveryengine import create_app
from labdiscoveryengine.configuration.exc import InvalidUsernameConfigurationError
from labdiscoveryengine.configuration.storage import change_credentials_password, create_admin_user, create_deployment_folder, create_external_user as storage_create_external_user, list_users, check_credentials_password

def with_app(func: Callable):
    """
    Run function in the Flask app context
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        app = create_app()
        with app.app_context():
            return func(*args, **kwargs)

    return wrapper

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
@with_app
def list_administrators():
    """
    List the administrators in the system
    """
    list_users(administrators=True)

@administrator_credentials.command('create')
@click.option('-l', '--login', type=str, required=True, help="login")
@click.option('-n', '--name', type=str, required=False, help="name")
@click.option('-e', '--email', type=str, required=False, help="e-mail")
@click.option('-p', '--password', type=str, required=False, help="password")
@with_app
def create_administrator(login: str, name: Optional[str] = None, email: Optional[str] = None, password: Optional[str] = None):
    """
    Create an administrator in the system
    """
    login = login.strip().lower()
    while not password:
        password = getpass.getpass(f"Select a password for {login}: ")
    
    password = password.strip()
    try:
        create_admin_user(login, name, email, password)
    except InvalidUsernameConfigurationError as err:
        click.echo(click.style(err, fg='red'))
        sys.exit(1)

@administrator_credentials.command('change-password')
@click.option('-l', '--login', type=str, required=True, help="login")
@click.option('-p', '--password', type=str, required=False, help="password")
@with_app
def change_administartor_password(login: str, password: Optional[str] = None):
    """
    Change the password of an administrator
    """
    login = login.strip().lower()
    while not password:
        password = getpass.getpass(f"Select a password for {login}: ")
    
    password = password.strip()
    change_credentials_password('administrators', login, password)

@administrator_credentials.command('check-password')
@click.option('-l', '--login', type=str, required=True, help="login")
@click.option('-p', '--password', type=str, required=False, help="password")
@with_app
def check_administartor_password(login: str, password: Optional[str] = None):
    """
    Check the password of an administrator
    """
    login = login.strip().lower()
    while not password:
        password = getpass.getpass(f"Select a password for {login}: ")
    
    password = password.strip()
    if not check_credentials_password('administrators', login, password):
        sys.exit(1)

@credentials_group.group('external-users')
def external_users_group():
    """
    Credentials-related commands for external users
    """

@external_users_group.command('create')
@click.option('-l', '--login', type=str, required=True, help="login")
@click.option('-n', '--name', type=str, required=False, help="name")
@click.option('-e', '--email', type=str, required=False, help="e-mail")
@click.option('-p', '--password', type=str, required=False, help="password")
@with_app
def create_external_user(login: str, name: Optional[str] = None, email: Optional[str] = None, password: Optional[str] = None):
    """
    Create an external user
    """
    login = login.strip().lower()
    while not password:
        password = getpass.getpass(f"Select a password for external user {login}: ")
    
    password = password.strip()
    try:
        storage_create_external_user(login, name, email, password)
    except InvalidUsernameConfigurationError as err:
        click.echo(click.style(err, fg='red'))
        sys.exit(1)


@external_users_group.command('list')
@with_app
def list_external_user():
    """
    List the external users
    """
    list_users(external_users=True)

@external_users_group.command('change-password')
@click.option('-l', '--login', type=str, required=True, help="login")
@click.option('-p', '--password', type=str, required=False, help="password")
@with_app
def change_external_user_password(login: str, password: Optional[str] = None):
    """
    Change the password of an external user
    """
    login = login.strip().lower()
    while not password:
        password = getpass.getpass(f"Select a password for {login}: ")
    
    password = password.strip()
    change_credentials_password('external', login, password)

@external_users_group.command('check-password')
@click.option('-l', '--login', type=str, required=True, help="login")
@click.option('-p', '--password', type=str, required=False, help="password")
@with_app
def check_external_user_password(login: str, password: Optional[str] = None):
    """
    Check the password of an external_user
    """
    login = login.strip().lower()
    while not password:
        password = getpass.getpass(f"Select a password for {login}: ")
    
    password = password.strip()
    if not check_credentials_password('external', login, password):
        sys.exit(1)

if __name__ == '__main__':
    lde()