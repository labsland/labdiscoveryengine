
Configuration
==============

In order to host remote laboratories for your institution you will need to configure the
LabDiscoveryEngine instance appropriately. The configuration will define which
laboratories are available, in which local addresses they are hosted, their icons,
their descriptions, their keywords and more.


Files and formats
-----------------

The configuration is stored in various YML (YAML) files. This format was chosen
for its readability and ease of use, and it is somewhat similar to JSON. The
configuration system is designed to be simple for laboratory developers and
administrators to use.

An example of a configuration set can be found in the `tests\deployments\simple`
directory.

In the following sections we will describe the various configuration files.

Configuration.yml
------------------

The `configuration.yml` file contains the main configuration of the LabDiscoveryEngine
instance.

Although it is an YML file, in this case the variables inside are loaded as
environment variables before the instance is run. Therefore, if you observe
the source code, you will see that the environment variables that are accessed
are often present and defined in this file.

The variables that this file contains normally are related to the instance as a whole
or to the institution that is hosting the instance.

The main variables are the following:

.. list-table::
   :widths: 25 50 25
   :header-rows: 1

   * - Variable
     - Description
     - Example
   * - ``SECRET_KEY``
     - The secret key that this instance will use. Should be unique and not shared.
     - ``ivTp3UApt7epzy0YzlNOEGirzKe1gHC4JQJc_rcuS2s``
   * - ``DEFAULT_LAB_VISIBILITY``
     - The default visibility of the laboratories. Can be ``public`` or ``private``.
     - ``public``
   * - ``INSTITUTION_LOGO``
     - The URL to the institution's logo or image.
     - ``https://labsland.com/pub/lde/labsland-logo-long.png``
   * - ``INSTITUTION_NAME``
     - The name of the institution.
     - ``LabsLand``
   * - ``INSTITUTION_DESCRIPTION``
     - A brief description of the institution.
     - ``LabsLand - The remote labs company.``
   * - ``INSTITUTION_URL``
     - The URL to the institution's main page.
     - ``https://labsland.com``                                |



Credentials.yml
----------------

The `credentials.yml` file contains the credentials for administrator and external users.

Administrators are those users that have full privileges over the instance. They
can access any laboratory and have multiple privileges, such as creating new users.
For security reasons administrators must always be defined in this file: administration privileges cannot
be granted through the user's database.

External users are mostly meant to be used by trusted external tools that access
the instance, such as platforms that aggregate laboratories.

For security reasons no passwords are stored in plaintext. There is, however, a tool
to set and change passwords. The tool can be invoked through:
```lde credentials administrators change-password```.


Laboratories.yml
----------------

The ``laboratories.yml`` file contains the configuration of the laboratories that
are provided by the instance. Each laboratory is defined by a unique identifier
and a set of properties.

An example is:

.. code-block:: yaml

    archimedes:
      display_name: Archimedes Lab # optional
      description: The Archimedes lab lets you experiment with buoyancy. # optional
      keywords:
        - physics
        - buoyancy
        - liquids
      category: Physics # optional
      max_time: 300 # optional, maximum time users can use the lab, in seconds
      image: https://labsland.com/pub/labs/file-cd0988-buoyancy_thumb.jpg # optional
      resources: # alternatively you can put them inline: dummy-1, dummy-2
        - arch-1
        - arch-2

Each laboratory has a unique identifier. In this case 'archimedes'.

Then, various parameters:

.. list-table::
   :widths: 25 50
   :header-rows: 1

   * - Variable
     - Description
   * - ``display_name``
     - The name of the laboratory. This is the name that will be displayed to users.
   * - ``keywords``
     - Keywords associated to the laboratory that can be used for search and discovery.
   * - ``category``
     - The labs category to which this lab belongs.
   * - ``max_time``
     - Maximum time that users can use the lab, in seconds, in a single session.
   * - ``image``
     - Thumbnail for the lab.
   * - ``resources``
     - List of resources that provide this laboratory. These are the identifiers of each of the resources, which must match and be
         defined in the ``resources.yml`` file.




Resources.yml
----------------

The ``resources.yml`` file contains the resources that provide the laboratories.
A ``resource`` represents an instance of a laboratory. A laboratory may have
several resources. For example, a FPGA laboratory may have various different
copies of the FPGA remote lab. Each of these copies is a 'resource' and will be
associated to the laboratory.

An example of a resource is:

.. code-block:: yaml

orgchem-1:
  url: http://localhost:5100
  login: lde
  password: password
  features: ['feature2', 'feature4']


A resource is first identified by a unique identifier. In this case 'orgchem-1'.
These identifiers need to match the one in the ``laboratories.yml`` file used
to identify it.

Then, various parameters:

.. list-table::
   :widths: 25 50
   :header-rows: 1

   * - Variable
     - Description
   * - ``url``
     - The URL, often local, in which this laboratory is hosted. The labs are normally hosted separately
using the LabDiscoveryLib.
   * - ``login``
     - This is the internal login that will be used by the LabDiscoveryEngine to communicate with the
       resource. It is not the login that will be used by users. This login and its password
       should be secret and shared only between the LabDiscoveryEngine and the resource (the laboratory).
   * - ``password``
     - Internal password for the specified login.
   * - ``features``
     - List of feature identifiers supported by this resource. Features are specific characteristics
     that are not necessarily supported by all instances (resources) of a laboratory. The ones that this resource
     supports are specified here. An example is a FPGA laboratory for which there are some resources (lab instances) with an
     oscilloscope attached and some without. The ones with the oscilloscope would have an 'oscilloscope' feature specified.





