
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
