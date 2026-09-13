"""Runtime support for loading an optional external Torch installation."""

import importlib.util
import json
import logging
import os
import platform
import struct
import sys
from importlib.machinery import PathFinder
from pathlib import Path

from PySubtrans.Helpers.Localization import _


class TorchRuntimeError(ImportError):
    """Raised when the configured external Torch runtime cannot be used."""


METADATA_FILENAME = "frozen-python-compatibility.json"


_configured_path: Path|None = None
_dll_directories: list[object] = []


def _candidate_installation_paths(installation_directory: str) -> list[Path]:
    """Return supported layouts without inspecting or executing configuration files."""
    root = Path(installation_directory).expanduser()
    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return [
        root,
        root / "site-packages",
        root / "Lib" / "site-packages",
        root / "lib" / python_version / "site-packages",
    ]


def _is_package(path: Path, package_name: str) -> bool:
    """Check for a regular package or module directory."""
    return (path / package_name).is_dir() or (path / f"{package_name}.py").is_file()


def _select_installation_path(installation_directory: str) -> Path:
    """Validate and resolve the configured site-packages directory."""
    candidates = _candidate_installation_paths(installation_directory)
    for candidate in candidates:
        if candidate.is_dir() and _is_package(candidate, "torch"):
            return candidate.resolve()

    raise TorchRuntimeError(_(
        "The configured Torch installation directory is invalid. Select the external "
        "site-packages directory containing the 'torch' package."
    ))


def _validate_process_compatibility(installation_path: Path) -> None:
    """Reject plainly incompatible installations before changing import state."""
    if platform.architecture()[0] not in ("32bit", "64bit"):
        raise TorchRuntimeError(_("Unable to determine the Python process architecture for the external Torch installation."))

    # PathFinder performs an absolute, path-scoped lookup without consulting the
    # ambient sys.path or importing Torch from the frozen application.
    torch_spec = PathFinder.find_spec("torch", [str(installation_path)])
    if torch_spec is None:
        raise TorchRuntimeError(_("The external Torch installation cannot be inspected by this Python interpreter."))


def _metadata_candidates(installation_path: Path) -> list[Path]:
    """Return likely locations for the frozen application's compatibility metadata."""
    candidates = [
        installation_path / METADATA_FILENAME,
        installation_path.parent / METADATA_FILENAME,
        installation_path.parent.parent / METADATA_FILENAME,
        installation_path.parent.parent.parent / METADATA_FILENAME,
    ]
    if bool(getattr(sys, "frozen", False)):
        candidates.append(Path(sys.executable).resolve().parent / METADATA_FILENAME)

    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _normalise_architecture(value: object) -> str:
    """Normalize common architecture spellings before comparing metadata."""
    normalized = str(value or "").casefold().replace("-", "").replace("_", "")
    aliases = {
        "amd64": "x8664",
        "x8664": "x8664",
        "x86_64": "x8664",
        "i386": "x86",
        "i486": "x86",
        "i586": "x86",
        "i686": "x86",
    }
    return aliases.get(normalized, normalized)


def _read_metadata_object(metadata_path: Path) -> dict[str, object]:
    """Read and minimally type-check compatibility metadata without executing it."""
    try:
        loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise TorchRuntimeError(_(
            "The frozen application's Torch compatibility metadata could not be read from '{}': {}"
        ).format(metadata_path, error)) from error

    if not isinstance(loaded, dict):
        raise TorchRuntimeError(_(
            "The frozen application's Torch compatibility metadata has an invalid format: {}"
        ).format(metadata_path))
    return loaded


def _validate_frozen_compatibility(installation_path: Path) -> None:
    """Validate the frozen Python target before loading external Torch."""
    metadata_path = next((candidate for candidate in _metadata_candidates(installation_path)
                          if candidate.is_file()), None)
    if metadata_path is None:
        if bool(getattr(sys, "frozen", False)):
            raise TorchRuntimeError(_(
                "This frozen application is missing '{}'. Repair or reinstall the distribution "
                "before configuring an external Torch installation."
            ).format(METADATA_FILENAME))
        return

    metadata = _read_metadata_object(metadata_path)
    if metadata.get("kind") != "llm-subtrans-frozen-python-compatibility":
        raise TorchRuntimeError(_(
            "The frozen application's Torch compatibility metadata has an unknown kind: {}"
        ).format(metadata_path))
    if metadata.get("schema_version") != 1:
        raise TorchRuntimeError(_(
            "The frozen application's Torch compatibility metadata schema is unsupported: {}"
        ).format(metadata_path))

    compatibility = metadata.get("compatibility")
    if not isinstance(compatibility, dict):
        raise TorchRuntimeError(_(
            "The frozen application's Torch compatibility metadata is missing its compatibility section: {}"
        ).format(metadata_path))

    version = sys.version_info
    expected = {
        "python_implementation": sys.implementation.name,
        "python_abi": sys.implementation.cache_tag or "",
        "python_version": f"{version.major}.{version.minor}",
        "os": platform.system() or sys.platform,
        "architecture": platform.machine() or "unknown",
        "pointer_bits": struct.calcsize("P") * 8,
    }
    mismatches: list[str] = []
    for key, actual in expected.items():
        declared = compatibility.get(key)
        if key == "architecture":
            matches = _normalise_architecture(declared) == _normalise_architecture(actual)
        else:
            matches = declared == actual
        if not matches:
            mismatches.append(f"{key}={declared!r} (requires {actual!r})")

    if mismatches:
        raise TorchRuntimeError(_(
            "The external Torch installation is incompatible with this frozen application "
            "according to '{}': {}. Install a complete Torch environment matching the "
            "application's Python ABI, OS, architecture, and pointer width, then restart."
        ).format(metadata_path, "; ".join(mismatches)))


def _register_native_libraries(installation_path: Path) -> None:
    """Register Torch's native library directory on platforms that support it."""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return

    torch_lib = installation_path / "torch" / "lib"
    if not torch_lib.is_dir():
        raise TorchRuntimeError(_("The external Torch installation is missing its native library directory: {}.").format(torch_lib))

    try:
        _dll_directories.append(os.add_dll_directory(str(torch_lib)))
    except OSError as error:
        raise TorchRuntimeError(_("Unable to register Torch native libraries from '{}': {}.").format(torch_lib, error)) from error


def PrepareTorchRuntime(installation_directory: str|None) -> None:
    """Prepare an optional external Torch path before deferred Torch imports.

    The external path is appended, preserving bundled-module precedence. Python's
    normal site initialization is deliberately not used, so external ``.pth`` files
    are never executed. A changed runtime after Torch was imported requires restart.
    """
    global _configured_path

    requested = (installation_directory or "").strip()
    if not requested:
        if _configured_path is not None:
            raise TorchRuntimeError(_(
                "The external Torch installation setting was cleared after Torch runtime setup. "
                "Restart the application to apply that change."
            ))
        return

    installation_path = _select_installation_path(requested)
    _validate_frozen_compatibility(installation_path)
    _validate_process_compatibility(installation_path)

    loaded_torch = sys.modules.get("torch")
    if loaded_torch is not None:
        loaded_path = Path(str(getattr(loaded_torch, "__file__", ""))).resolve()
        if installation_path not in loaded_path.parents:
            raise TorchRuntimeError(_(
                "Torch is already loaded from '{}'. Changing the Torch installation requires restarting the application."
            ).format(loaded_path))

    if _configured_path is not None and _configured_path != installation_path:
        raise TorchRuntimeError(_(
            "A different external Torch installation was already selected. Restart the application after changing Torch installation settings."
        ))

    if str(installation_path) not in sys.path:
        sys.path.append(str(installation_path))
    _register_native_libraries(installation_path)
    _configured_path = installation_path
    logging.debug("Prepared external Torch runtime from %s", installation_path)
