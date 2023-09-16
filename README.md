# LabDiscoveryEngine


[![CircleCI](https://circleci.com/gh/labsland/labdiscoveryengine.svg?style=svg)](https://circleci.com/gh/labsland/labdiscoveryengine)
[![Supported Versions](https://img.shields.io/pypi/pyversions/labdiscoveryengine.svg)](https://pypi.org/project/labdiscoveryengine)
[![pypi](https://img.shields.io/pypi/v/labdiscoveryengine.svg)](https://pypi.org/project/labdiscoveryengine)


LabDiscoveryEngine is an evolved and modern RLMS building upon WebLabDeusto's experience

## First steps

### Installation

$ pip install labdiscoveryengine

### Creating an LDE deployment

First, you have to create an LDE deployment directory, where you will store the configuration. To create a simple example, run the following:

```
$ lde deployments create -d /path/to/mydeployment
```

If you want to use gunicorn (recommended), then run this:
```
$ lde deployments add-gunicorn-script -d /path/to/mydeployment
```

And if you want to manage it with supervisor (recommended), run this and follow the installation instructions:

```
$ lde deployments add-supervisor-config -d /path/to/mydeployment
```

From that moment, you will have a setup up and running, with four configuration files.

## Full documentation:

https://developers.labsland.com/labdiscoveryengine/en/stable/
