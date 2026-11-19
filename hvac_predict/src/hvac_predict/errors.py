"""Errors the command line reports without a traceback."""


class HvacPredictError(Exception):
    """Something the operator can fix: configuration, data, or a remote service."""


class ConfigError(HvacPredictError):
    """The configuration or one of the tool's own files is missing, malformed, or unusable."""


class DataError(HvacPredictError):
    """History or forecast data is missing, stale, or unusable."""


class ServiceError(HvacPredictError):
    """VictoriaMetrics or Home Assistant answered with something unexpected."""
