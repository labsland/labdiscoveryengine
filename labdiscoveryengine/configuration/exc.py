from labdiscoveryengine.exc import LabDiscoveryEngineError


class ConfigurationError(LabDiscoveryEngineError):
    pass

class ConfigurationDirectoryNotFoundError(ConfigurationError):
    pass

class ConfigurationFileNotFoundError(ConfigurationError):
    pass

class InvalidConfigurationFoundError(ConfigurationError):
    pass

class InvalidConfigurationValueError(ConfigurationError):
    pass

class InvalidLaboratoryConfigurationError(ConfigurationError):
    pass