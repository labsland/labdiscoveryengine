# LabDiscoveryEngine


[![CircleCI](https://circleci.com/gh/labsland/labdiscoveryengine.svg?style=svg)](https://circleci.com/gh/labsland/labdiscoveryengine)
[![Supported Versions](https://img.shields.io/pypi/pyversions/labdiscoveryengine.svg)](https://pypi.org/project/labdiscoveryengine)
[![pypi](https://img.shields.io/pypi/v/labdiscoveryengine.svg)](https://pypi.org/project/labdiscoveryengine)

LabDiscoveryEngine is an evolved and modern RLMS building upon WebLabDeusto's experience.

The official website of the LabDiscoveryEngine project is https://labdiscoveryengine.labsland.com/


## First steps

### Installation

```
$ pip install labdiscoveryengine
```

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

## Full documentation

https://developers.labsland.com/labdiscoveryengine/en/stable/

## Development

When developing LabDiscoveryEngine (not a remote laboratory, but when developing the RLMS itself), the easiest steps are:


* Start the web server in debug mode:
```
$ . devrc
$ flask run
```

* Start the worker:
```
$ python labdiscoveryengine/cli.py worker run
```

(this is the equivalent to running ```lde worker run``` when the labdiscoveryengine package is installed)

* In the folder tools there are scripts to test.



## Example Reference Remote Labs

As part of the LabDiscoveryEngine project, two reference remote labs have been developed using the LabDiscoveryEngine to serve as examples:

### FPGA remote lab

The FPGA remote lab provides control over a FPGA device and is oriented towards computer vision and other applications. It is developed and hosted by H-BRS (Germany).
The repository is found in: https://github.com/Andrea-Schwandt/LabDiscoverEngine-FPGA-Lab
Andrea Schanwdt, its main developer, presents it here: https://youtu.be/2Da-6_kJjmI

### Buck and Bust Converters remote lab

The buck and bust converters remote labs allow students to experiment with these circuits. They are developed and hosted by CPNU (Ukraine).
The repositories are found in:
  - Hardware & Embedded Software: https://github.com/RTESdepartmentCPNU/LabDiscoveryCPNU/
  - Web Application: https://github.com/vtinkerer/cnpu-remote-lab


## Funding

The LabDiscoveryEngine project operates under a cascade funding model, provided by the European Union-backed initiative, NGI Search. This funding approach not only empowers us financially but also aligns our objective to revolutionize the accessibility and discovery of educational labs with the broader goals of NGI Search.

Funded by the European Union. Views and opinions expressed are however those of the author(s) only and do not necessarily reflect those of the European Union or European Commission. Neither the European Union nor the granting authority can be held responsible for them. Funded within the framework of the NGI Search project under grant agreement No 101069364.

<img src="https://labsland.com/images/supportedby/eu-emblem.jpeg" style="width: 200px">
