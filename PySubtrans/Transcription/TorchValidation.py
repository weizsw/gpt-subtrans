"""Shared validation logic for external Torch installations.

Used by TorchRuntime (runtime loader), TorchSetupDialog (GUI wizard), and
prepare_external_torch.py (CLI/build-time tool).  Pure validation — no Torch
imports, no Qt imports, no sys.path manipulation.
"""

import json
import platform
import struct
import sys
import sysconfig
from pathlib import Path

from PySubtrans.Helpers.Localization import _
from PySubtrans.Helpers.Resources import GetResourcePath


METADATA_FILENAME = "frozen-python-compatibility.json"
OFFICIAL_PYTORCH_SELECTOR = "https://pytorch.org/get-started/locally/"

_METADATA_KIND = "llm-subtrans-frozen-python-compatibility"
_METADATA_SCHEMA_VERSION = 1

_COMPATIBILITY_FIELDS = (
    "python_implementation", "python_abi", "python_version",
    "os", "architecture",
)

# Union of aliases from TorchRuntime and prepare_external_torch
_ARCHITECTURE_ALIASES : dict[str, str] = {
    "amd64": "x8664",
    "x8664": "x8664",
    "x86_64": "x8664",
    "aarch64": "arm64",
    "i386": "x86",
    "i486": "x86",
    "i586": "x86",
    "i686": "x86",
}


def normalise_architecture(value : object) -> str:
    """Normalize common architecture spellings for comparison."""
    normalized = str(value or "").casefold().replace("-", "").replace("_", "")
    return _ARCHITECTURE_ALIASES.get(normalized, normalized)


def candidate_site_packages_paths(root : Path) -> list[Path]:
    """Return standard venv site-packages directories to probe for packages."""
    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return [
        root,
        root / "site-packages",
        root / "Lib" / "site-packages",
        root / "lib" / python_version / "site-packages",
    ]


def find_torch_site_packages(root : Path) -> Path|None:
    """Return the first candidate path containing a ``torch`` package, or None."""
    for candidate in candidate_site_packages_paths(root):
        if candidate.is_dir() and (candidate / "torch").is_dir():
            return candidate.resolve()
    return None


def has_torch_package(root : Path) -> bool:
    """Whether any standard venv layout under *root* contains a torch package."""
    return find_torch_site_packages(root) is not None


def get_python_abi() -> str:
    """Return the interpreter ABI tag used by compatible extension wheels."""
    cache_tag = sys.implementation.cache_tag
    if cache_tag:
        return cache_tag

    soabi = sysconfig.get_config_var("SOABI")
    if soabi:
        return str(soabi)

    return f"{sys.implementation.name}{sys.version_info.major}{sys.version_info.minor}"


def build_current_compatibility() -> dict[str, object]:
    """Build the 6-field compatibility dict from the running interpreter."""
    version = sys.version_info
    return {
        "python_implementation": sys.implementation.name,
        "python_abi": get_python_abi(),
        "python_version": f"{version.major}.{version.minor}",
        "os": platform.system() or sys.platform,
        "architecture": platform.machine() or "unknown",
        "pointer_bits": struct.calcsize("P") * 8,
    }


def find_compatibility_metadata() -> Path|None:
    """Locate the frozen application's compatibility metadata file.

    Uses ``GetResourcePath`` so the file is found via ``sys._MEIPASS`` in
    frozen builds and via ``./assets/`` in development.  Returns None when
    the file does not exist (normal for non-frozen runs).
    """
    metadata_path = Path(GetResourcePath("assets", METADATA_FILENAME))
    return metadata_path if metadata_path.is_file() else None


def read_compatibility_metadata(
    metadata_path : str|Path,
    *,
    error_type : type[Exception] = ValueError,
) -> dict[str, object]:
    """Read and validate frozen-build compatibility metadata.

    Raises *error_type* (default ``ValueError``) on any problem so that
    each consumer can raise its own exception hierarchy.
    """
    resolved = Path(metadata_path).expanduser().resolve()

    try:
        loaded = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise error_type(
            _("Torch compatibility metadata could not be read from '{}': {}").format(resolved, error)
        ) from error

    if not isinstance(loaded, dict):
        raise error_type(
            _("Torch compatibility metadata has an invalid format: {}").format(resolved)
        )

    if loaded.get("kind") != _METADATA_KIND:
        raise error_type(
            _("Torch compatibility metadata has an unknown kind: {}").format(resolved)
        )

    if loaded.get("schema_version") != _METADATA_SCHEMA_VERSION:
        raise error_type(
            _("Torch compatibility metadata schema is unsupported: {}").format(resolved)
        )

    compatibility = loaded.get("compatibility")
    if not isinstance(compatibility, dict) or any(
        not isinstance(compatibility.get(key), str) or not compatibility[key]
        for key in _COMPATIBILITY_FIELDS
    ) or type(compatibility.get("pointer_bits")) is not int or compatibility["pointer_bits"] not in (32, 64):
        raise error_type(
            _("Torch compatibility metadata has missing or invalid compatibility fields: {}").format(resolved)
        )

    return loaded


def check_compatibility(
    actual : dict[str, object],
    expected : dict[str, object],
    *,
    error_type : type[Exception] = RuntimeError,
) -> None:
    """Compare two compatibility dicts and raise *error_type* on mismatch."""
    mismatches : list[str] = []

    for key in (*_COMPATIBILITY_FIELDS, "pointer_bits"):
        observed = actual.get(key)
        required = expected.get(key)

        if key == "architecture":
            matches = normalise_architecture(observed) == normalise_architecture(required)
        else:
            matches = observed == required

        if not matches:
            mismatches.append(f"{key}: external={observed!r}, frozen={required!r}")

    if mismatches:
        raise error_type(
            _("The external Torch installation is incompatible with this application: {}. "
              "Install a complete Torch environment matching the application's Python ABI, "
              "OS, architecture, and pointer width, then restart.").format("; ".join(mismatches))
        )
