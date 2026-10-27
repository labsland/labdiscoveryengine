"""Build the small recovery runtime without importing the LDE web application."""
from pathlib import Path
from setuptools import setup

setup(name='labsland-recovery', version='0.1.0',
      description='Generation-aware laboratory lifecycle coordination',
      packages=['labsland_recovery'],
      package_dir={'labsland_recovery': str(Path(__file__).resolve().parent.parent/'labdiscoveryengine/recovery')},
      python_requires='>=3.9', install_requires=['redis>=3.4,<6','PyYAML>=5,<7'])
