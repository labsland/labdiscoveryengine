#-*-*- encoding: utf-8 -*-*-
import os
import platform
import subprocess
from collections import OrderedDict

from setuptools import setup, Command,  find_packages
from setuptools.command.sdist import sdist
from setuptools.command.install import install

classifiers=[
    "Development Status :: 1 - Planning",
    "Environment :: Web Environment",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: GNU Affero General Public License v3",
    "Operating System :: OS Independent",
    "Programming Language :: Python",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Topic :: Education",
    "Topic :: Internet :: WWW/HTTP",
]

cp_license="GNU AGPL v3"

with open("README.md", "r") as fh:
    long_description = fh.read()

with open('requirements.txt') as f:
    requirements = [line.strip() for line in f if not line.startswith('#') and line.strip() != '']

class NpmInstall(Command):
    description = 'Install npm dependencies and build assets'
    user_options = [
        ('force', None, 'Force npm install and flask assets build'),
    ]

    def initialize_options(self):
        self.force = False

    def finalize_options(self):
        pass

    def run(self):
        if self.force or not os.path.exists('labdiscoveryengine/static/node_modules') or not os.path.exists('labdiscoveryengine/static/gen'):
            try:
                subprocess.check_call(['npm', 'install'], cwd='labdiscoveryengine/static')
                if platform.system() in ['Windows', 'Microsoft']:
                    subprocess.check_call("devrc & flask assets build", shell=True)
                else:
                    subprocess.check_call('. ./devrc && flask assets build', shell=True)
            except subprocess.CalledProcessError as e:
                print(f"The command '{' '.join(e.cmd)}' failed with error code {e.returncode}")
                raise

class CustomSDistCommand(sdist):
    def run(self):
        npm_install_cmd = self.distribution.get_command_obj('npm_install')
        npm_install_cmd.force = True
        self.run_command('npm_install')
        super().run()

class CustomInstallCommand(install):
    def run(self):
        self.run_command('npm_install')
        super().run()

setup(name='labdiscoveryengine',
      version='0.6.0',
      description="Remote Laboratory Management System for creating laboratories (replacement of WebLab-Deusto)",
      long_description=long_description,
      long_description_content_type="text/markdown",
      project_urls=OrderedDict((
            ('Documentation', 'https://developers.labsland.com/labdiscoveryengine/en/stable/'),
            ('Code', 'https://github.com/labsland/labdiscoveryengine'),
            ('Issue tracker', 'https://github.com/labsland/labdiscoveryengine/issues'),
      )),
      classifiers=classifiers,
      zip_safe=False,
      author='LabsLand',
      author_email='dev@labsland.com',
      url='https://developers.labsland.com/labdiscoveryengine/',
      license=cp_license,
      packages=find_packages(exclude=['tests**']),
      install_requires=requirements,
      include_package_data=True,
      cmdclass={
        'npm_install': NpmInstall,
        'sdist': CustomSDistCommand,
        'install': CustomInstallCommand,
      },
      entry_points='''
        [console_scripts]
        lde=labdiscoveryengine.cli:lde
      '''
)
