"""report-skill — external skill layer over ReportArchive."""
from importlib.metadata import version as _pkg_version, PackageNotFoundError as _PackageNotFoundError

try:
    __version__ = _pkg_version("report-skill")
except _PackageNotFoundError:
    # Package not installed (eg. running from source tree without `pip install -e .`).
    __version__ = "0.0.0+unknown"
