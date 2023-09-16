import os
import sys
import time
import getpass
import pathlib
import functools
import multiprocessing
import asyncio

from typing import Callable, Optional

import click

import labdiscoveryengine
from labdiscoveryengine import create_app
from labdiscoveryengine.configuration.exc import InvalidUsernameConfigurationError
from labdiscoveryengine.configuration.storage import change_credentials_password, create_admin_user, create_deployment_folder, create_external_user as storage_create_external_user, list_users, check_credentials_password
from labdiscoveryengine.queues.runner import main as runner_main

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
def create_deployment(directory: str, force: bool):
    """
    Create a deployment
    """
    directory_path = pathlib.Path(directory)
    create_deployment_folder(directory_path, force)

    os.makedirs(directory_path / "logs", exist_ok=True)
    os.makedirs(directory_path / "scripts", exist_ok=True)

    worker_script_lines = [
        _generate_running_script(directory_path),
    ]
    worker_script_lines.append("# Run the worker")
    worker_script_lines.append("exec lde worker run")

    directory_path.joinpath("scripts", "worker_script.sh").write_text("\n".join(worker_script_lines) + "\n")
    if hasattr(os, 'chmod'):
        os.chmod(directory_path.joinpath("scripts", "worker_script.sh"), 0o755)


    print(f"[{time.asctime()}]")
    print(f"[{time.asctime()}] If you want to set up the gunicorn scripts, run:")
    print(f"[{time.asctime()}] $ lde deployments add-gunicorn-script -d {directory}")
    print(f"[{time.asctime()}] or:")
    print(f"[{time.asctime()}] $ lde deployments add-gunicorn-script -d {directory} --help (for seeing all the options: gevent, workers, port, etc.)")
    print(f"[{time.asctime()}]")
    print(f"[{time.asctime()}] If you want to set up the supervisor configuration, run:")
    print(f"[{time.asctime()}] $ lde deployments add-supervisor-config -d {directory}")
    print(f"[{time.asctime()}] or:")
    print(f"[{time.asctime()}] $ lde deployments add-supervisor-config -d {directory} --help (for more options)")


def is_virtual_environment() -> bool:
    "Are we in a virtual environment or not?"
    # Outside a virtual environment, sys.base_prefix is the same as sys.prefix (e.g., '/usr/')
    # In virtualenv, sys.real_prefix is added
    # In venv, sys.base_prefix is the original one (e.g., '/usr/') and sys.prefix is the venv one (e.g., /home/user/.virtualenvs/module)
    return hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)

def _generate_running_script(directory_path: pathlib.Path):
    lines = [
        "#!/usr/bin/env bash",
        "",
    ]
    if is_virtual_environment():
        lines.append("# Import the virtual environment")
        lines.append(f"source {sys.prefix}/bin/activate")
        lines.append("")
    
    if not labdiscoveryengine.__file__.startswith(sys.prefix):
        # If there is /home/user/foo/labdiscoveryengine/__init__.py
        # we are looking for /home/user/foo/
        labdiscoveryengine_location: str = os.path.dirname(os.path.dirname(labdiscoveryengine.__file__))
        lines.append("# Add labdiscoveryengine to the path")
        lines.append(f"export PYTHONPATH=$PYTHONPATH:{labdiscoveryengine_location}")
        lines.append("")

    lines.append("# Change directory to the deployment directory")
    lines.append(f"cd {directory_path.absolute()}")
    lines.append("")

    lines.append("# Import prodrc configuration (if it exists)")
    lines.append("if [ -f prodrc ]; then")
    lines.append("    source prodrc")
    lines.append("fi")
    lines.append("")

    return '\n'.join(lines)


@deployments.command('add-gunicorn-script')
@click.option('-d', '--directory', type=click.Path(dir_okay=True, exists=True, file_okay=False), required=True, help="Deployment directory")
@click.option('-f', '--force', is_flag=True, help="Force if the folder already exists / has contents")
@click.option('--keep-alive', type=int, default=75, help="Keep alive time in seconds")
@click.option('--port', type=int, default=8080, help="Port used")
@click.option('--workers', type=int, default=None, help="Number of workers used (if not gevent). By default, workers * 2 + 1")
@click.option('--use-gevent', is_flag=True, help="Use gunicorn with gevent (monkeypatching everything)")
def deployments_add_gunicorn_script(directory: str, force: bool, keep_alive: int, port: int, workers: int, use_gevent: bool):
    if os.name != 'posix':
        print("Warning: supervisor is only available on POSIX systems. Consider using WSL or a UNIX system")

    directory_path = pathlib.Path(directory)

    if directory_path.joinpath("scripts", "gunicorn_script.sh").exists() and not force:
        print(f"[{time.asctime()}] Error: gunicorn_script.sh already exists in {directory_path.absolute()}. Add --force")
        return

    wsgi_app_content = [
        "#!/usr/bin/env python\n",
        ""
    ]
    wsgi_app_content.extend([
        "from labdiscoveryengine import create_app\n",
        "application = create_app('production')\n"
    ])

    print(f"[{time.asctime()}] Writing wsgi_app to {directory_path.absolute()}/wsgi_app.py")
    directory_path.joinpath("scripts", "wsgi_app.py").write_text('\n'.join(wsgi_app_content))

    lines = [
        _generate_running_script(directory_path),
        "# Locate scripts/wsgi_app.py",
        "export PYTHONPATH=$PYTHONPATH:scripts",
        "",
    ]
    
    additional_args = ""
    if use_gevent:
        additional_args += "--worker-class gevent"
    else:
        if workers is None:
            # The recommended number of workers is 2 * the number of CPUs + 1.
            workers = 2 * multiprocessing.cpu_count() + 1

        additional_args += f"--workers {workers}"

    lines.append("# Run gunicorn")
    lines.append(f"exec gunicorn {additional_args} --keep-alive {keep_alive} --bind 0.0.0.0:{port} wsgi_app:application")

    print(f"[{time.asctime()}] Writing gunicorn script to {directory_path.absolute()}/gunicorn_script.sh")
    directory_path.joinpath("scripts", "gunicorn_script.sh").write_text("\n".join(lines) + "\n")
    os.chmod(directory_path.joinpath("scripts", "gunicorn_script.sh"), 0o755)
    print(f"[{time.asctime()}]")
    print(f"[{time.asctime()}] If you want to set up the supervisor configuration, run:")
    print(f"[{time.asctime()}] $ lde deployments add-supervisor-config -d {directory}")
    print(f"[{time.asctime()}] or:")
    print(f"[{time.asctime()}] $ lde deployments add-supervisor-config -d {directory} --help (for more options)")


@deployments.command('add-supervisor-config')
@click.option('-d', '--directory', type=click.Path(dir_okay=True, exists=True, file_okay=False), required=True, help="Deployment directory")
@click.option('-f', '--force', is_flag=True, help="Force if the folder already exists / has contents")
@click.option('-n', '--name', default=None, type=str, help="Name for the application. By default the name of the directory")
@click.option('-u', '--user', default=None, type=str, help="System username. By default whoever is calling this script.")
@click.option('--log-maxbytes', default='20MB', type=str, help="Size of each log file.")
@click.option('--log-backups', default=2, type=int, help="Number of backups of each log file.")
def deployments_add_supervisor(directory: str, force: bool, name: str, user: str, log_maxbytes: int, log_backups: int):
    if os.name != 'posix':
        print("Warning: supervisor is only available on POSIX systems. Consider using WSL or a UNIX system")

    if user is None:
        user = getpass.getuser()

    directory_path = pathlib.Path(directory)

    if directory_path.joinpath("scripts", "supervisor.conf").exists() and not force:
        print(f"[{time.asctime()}] Supervisor configuration already exists. Use --force to overwrite.")
        return
    
    if name is None:
        name = os.path.basename(directory_path.absolute())

    gunicorn_supervisor_config = [
        f"[program:{name}-gunicorn]",
        f"command={directory_path.absolute()}/scripts/gunicorn_script.sh",
        f"directory={directory_path.absolute()}",
        f"user={user}",
        f"stdout_logfile={directory_path.absolute()}/logs/gunicorn.out",
        f"stdout_logfile_maxbytes={log_maxbytes}",
        f"stdout_logfile_backups={log_backups}",
        f"stderr_logfile={directory_path.absolute()}/logs/gunicorn.err",
        f"stderr_logfile_maxbytes={log_maxbytes}",
        f"stderr_logfile_backups={log_backups}",
        "autostart=true",
        "autorestart=true",
        "stopasgroup=true",
        "killasgroup=true",
    ]

    worker_supervisor_config = [
        f"[program:{name}-worker]",
        f"command={directory_path.absolute()}/scripts/worker_script.sh",
        f"directory={directory_path.absolute()}",
        f"user={user}",
        f"stdout_logfile={directory_path.absolute()}/logs/worker.out",
        f"stdout_logfile_maxbytes={log_maxbytes}",
        f"stdout_logfile_backups={log_backups}",
        f"stderr_logfile={directory_path.absolute()}/logs/worker.err",
        f"stderr_logfile_maxbytes={log_maxbytes}",
        f"stderr_logfile_backups={log_backups}",
        "autostart=true",
        "autorestart=true",
        "stopasgroup=true",
        "killasgroup=true",
    ]

    supervisor_config = ''.join([
        '# Gunicorn configuration\n',
        '\n'.join(gunicorn_supervisor_config),
        '\n',
        '\n',
        '\n# Worker configuration\n',
        '\n'.join(worker_supervisor_config),
        '\n',
        '\n',
        '\n',
        '\n# Group configuration\n',
        f'[group:{name}]\n',
        f'programs={name}-gunicorn,{name}-worker\n',
        '\n'
    ])

    print(f"[{time.asctime()}] Writing supervisor configuration to {directory_path.absolute()}/scripts/supervisor.conf")
    directory_path.joinpath("scripts", "supervisor.conf").write_text(supervisor_config)
    print(f"[{time.asctime()}] done")
    print(f"[{time.asctime()}]")
    print(f"[{time.asctime()}] To add this configuration to supervisor (if you haven't done it yet), run (as root):")
    print(f"[{time.asctime()}] # ln -s {directory_path.absolute()}/scripts/supervisor.conf /etc/supervisor/conf.d/{name}.conf")
    print(f"[{time.asctime()}] # supervisorctl update")
    print(f"[{time.asctime()}]")
    print(f"[{time.asctime()}] Then to start it / stop it / restart it, run:")
    print(f"[{time.asctime()}] # supervisorctl status {name}:*")
    print(f"[{time.asctime()}] # supervisorctl start {name}:*")
    print(f"[{time.asctime()}] # supervisorctl stop {name}:*")
    print(f"[{time.asctime()}] # supervisorctl restart {name}:*")
    print(f"[{time.asctime()}]")
    print(f"[{time.asctime()}] or a particular process:")
    print(f"[{time.asctime()}] # supervisorctl status {name}:{name}-gunicorn")
    print(f"[{time.asctime()}] # supervisorctl status {name}:{name}-worker")
    print(f"[{time.asctime()}]")



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
def change_administrator_password(login: str, password: Optional[str] = None):
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
def check_administrator_password(login: str, password: Optional[str] = None):
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

@lde.group('worker')
def worker_group():
    """
    Worker-related commands
    """

@worker_group.command('run')
@with_app
def worker_run():
    """
    Run the worker
    """
    print(f"[{time.asctime()}] Starting worker...", flush=True)
    print(f"[{time.asctime()}] Starting worker...", file=sys.stderr, flush=True)

    asyncio.run(runner_main())

if __name__ == '__main__':
    lde()