"""jira-timer package. Version is resolved from installed package metadata."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("jira-timer")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = ["__version__"]
