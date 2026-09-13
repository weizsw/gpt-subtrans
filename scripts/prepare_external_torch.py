"""Prepare and validate an external Torch location for frozen Qwen builds."""

import argparse
import json
import platform
from pathlib import Path
import subprocess
import struct
import sys
import sysconfig


OFFICIAL_PYTORCH_SELECTOR = "https://pytorch.org/get-started/locally/"
METADATA_FILENAME = "frozen-python-compatibility.json"
PROBE_TIMEOUT_SECONDS = 15


def BuildCompatibilityMetadata() -> dict[str, object]:
    """Return compatibility data derived from the active Python interpreter."""
    version = sys.version_info
    python_version = f"{version.major}.{version.minor}.{version.micro}"
    python_abi = _GetPythonAbi()
    operating_system = platform.system() or sys.platform
    architecture = platform.machine() or "unknown"
    pointer_bits = struct.calcsize("P") * 8

    return {
        "schema_version": 1,
        "kind": "llm-subtrans-frozen-python-compatibility",
        "python_abi": python_abi,
        "python_version": f"{version.major}.{version.minor}",
        "os": operating_system,
        "architecture": architecture,
        "python": {
            "implementation": sys.implementation.name,
            "version": python_version,
            "abi": python_abi,
            "cache_tag": sys.implementation.cache_tag,
        },
        "platform": {
            "os": operating_system,
            "sys_platform": sys.platform,
            "architecture": architecture,
            "pointer_bits": pointer_bits,
        },
        "compatibility": {
            "python_implementation": sys.implementation.name,
            "python_abi": python_abi,
            "python_version": f"{version.major}.{version.minor}",
            "os": operating_system,
            "architecture": architecture,
            "pointer_bits": pointer_bits,
        },
        "torch": {
            "installation": "external",
            "package_policy": "user-installed-complete-runtime",
        },
        "pytorch_selector": OFFICIAL_PYTORCH_SELECTOR,
    }


def PrepareExternalTorch(
    output_directory : str|Path,
    frozen_metadata_path : str|Path,
) -> dict[str, object]:
    """Prepare an empty external location and print official install guidance.

    This function checks the frozen target before creating layout and metadata. It never
    copies, installs, downloads, or reconstructs Torch packages. The user must
    install a complete compatible Torch venv using the official selector.
    """
    output_root = Path(output_directory).expanduser().resolve()
    frozen_metadata = ReadCompatibilityMetadata(frozen_metadata_path)
    python_executable = _GetVenvPython(output_root)
    actual = (_ProbePythonCompatibility(python_executable) if python_executable is not None
              else BuildCompatibilityMetadata()["compatibility"])
    _CheckCompatibility(actual, frozen_metadata["compatibility"])
    output_root.mkdir(parents=True, exist_ok=True)
    _GetSitePackagesDirectory(output_root, create=True)

    metadata = frozen_metadata
    _WriteMetadata(output_root / METADATA_FILENAME, metadata)

    print(f"Prepared external Torch location: {output_root}")
    print("Install a complete compatible Torch environment using the command selected at:")
    print(OFFICIAL_PYTORCH_SELECTOR)
    print("Point Qwen Local at the complete venv root after installation.")
    return metadata


def ValidateExternalTorch(installation_directory : str|Path, frozen_metadata_path : str|Path) -> Path:
    """Check Torch directory presence and interpreter compatibility, without importing Torch."""
    frozen_metadata = ReadCompatibilityMetadata(frozen_metadata_path)
    installation_root = Path(installation_directory).expanduser().resolve()
    site_packages = _GetSitePackagesDirectory(installation_root, create=False)
    if not (site_packages / "torch").is_dir():
        raise RuntimeError(
            "The external location does not contain a 'torch' package directory. "
            f"Install the complete hardware-appropriate environment selected at {OFFICIAL_PYTORCH_SELECTOR}."
        )

    python_executable = _GetVenvPython(installation_root)
    if python_executable is None:
        raise RuntimeError(
            "The external Torch location must be a complete venv root containing its Python executable."
        )

    actual = _ProbePythonCompatibility(python_executable)
    _CheckCompatibility(actual, frozen_metadata["compatibility"])

    print(f"Torch directory present; interpreter matches frozen metadata: {installation_root}")
    print("Torch import, native dependencies, and accelerator availability have not been tested.")
    return installation_root


def ReadCompatibilityMetadata(metadata_path : str|Path) -> dict[str, object]:
    """Read the frozen build's required schema, rejecting incomplete targets."""
    metadata = json.loads(Path(metadata_path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1 or metadata.get("kind") != "llm-subtrans-frozen-python-compatibility":
        raise ValueError("Unsupported frozen Python compatibility metadata.")

    compatibility = metadata.get("compatibility")
    fields = ("python_implementation", "python_abi", "python_version", "os", "architecture")
    if not isinstance(compatibility, dict) or any(
        not isinstance(compatibility.get(key), str) or not compatibility[key] for key in fields
    ) or type(compatibility.get("pointer_bits")) is not int or compatibility["pointer_bits"] not in (32, 64):
        raise ValueError("Frozen Python metadata has missing or invalid compatibility fields.")
    return metadata


def _CheckCompatibility(actual : object, expected : object) -> None:
    """Compare interpreter facts to the frozen target, accepting architecture aliases."""
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        raise ValueError("Invalid interpreter compatibility facts.")

    aliases = {"amd64": "x8664", "aarch64": "arm64", "i386": "x86", "i686": "x86"}
    mismatches : list[str] = []
    for key in ("python_implementation", "python_abi", "python_version", "os", "architecture", "pointer_bits"):
        observed = actual.get(key)
        required = expected.get(key)
        if key == "architecture":
            observed = str(observed).casefold().replace("-", "").replace("_", "")
            required = str(required).casefold().replace("-", "").replace("_", "")
            observed = aliases.get(observed, observed)
            required = aliases.get(required, required)
        if observed != required:
            mismatches.append(f"{key}: external={observed!r}, frozen={required!r}")
    if mismatches:
        raise RuntimeError("Interpreter does not match frozen application: " + "; ".join(mismatches))


def WriteCompatibilityMetadata(metadata_path : str|Path) -> dict[str, object]:
    """Write compatibility metadata without creating or copying a runtime."""
    metadata = BuildCompatibilityMetadata()
    resolved_path = Path(metadata_path).expanduser().resolve()
    _WriteMetadata(resolved_path, metadata)
    print(f"Frozen Python compatibility metadata: {resolved_path}")
    return metadata


def main(arguments : list[str]|None = None) -> int:
    """Run metadata-only, preparation, or validation mode."""
    parser = argparse.ArgumentParser(
        description=(
            "Prepare or validate an external Torch location without copying or downloading packages. "
            "Use the official PyTorch selector for installation."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--metadata-only",
        action="store_true",
        help="Write compatibility metadata only; copy no Torch files.",
    )
    mode.add_argument(
        "--prepare-external-dir",
        metavar="PATH",
        help="Create an empty external venv/site-packages location and print install guidance.",
    )
    mode.add_argument(
        "--validate-external-dir",
        metavar="PATH",
        help="Check Torch directory presence and interpreter compatibility in a user-installed venv.",
    )
    parser.add_argument(
        "--metadata-path",
        help="Output JSON path for --metadata-only.",
    )
    parser.add_argument("--frozen-metadata", help="Required frozen application's JSON for preparation and validation.")
    parsed = parser.parse_args(arguments)

    try:
        if parsed.metadata_only:
            if parsed.frozen_metadata:
                parser.error("--frozen-metadata is not used with --metadata-only")
            if not parsed.metadata_path:
                parser.error("--metadata-only requires --metadata-path")
            WriteCompatibilityMetadata(parsed.metadata_path)
        else:
            if parsed.metadata_path:
                parser.error("--metadata-path is only used with --metadata-only")
            if not parsed.frozen_metadata:
                parser.error("External setup requires --frozen-metadata from the frozen application")
            if parsed.prepare_external_dir:
                PrepareExternalTorch(parsed.prepare_external_dir, parsed.frozen_metadata)
            else:
                ValidateExternalTorch(parsed.validate_external_dir, parsed.frozen_metadata)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Unable to prepare or validate external Torch: {error}", file=sys.stderr)
        return 1
    return 0


def _GetPythonAbi() -> str:
    """Return the interpreter cache tag used by compatible extension wheels."""
    cache_tag = sys.implementation.cache_tag
    if cache_tag:
        return cache_tag

    soabi = sysconfig.get_config_var("SOABI")
    if soabi:
        return str(soabi)
    return f"{sys.implementation.name}{sys.version_info.major}{sys.version_info.minor}"


def _GetSitePackagesDirectory(root : Path, create : bool) -> Path:
    """Resolve a runtime layout supported by TorchRuntime."""
    candidates = [root, root / "site-packages", root / "Lib" / "site-packages"]
    candidates.extend(sorted(root.glob("lib/python*/site-packages")))
    for candidate in candidates:
        if (candidate / "torch").is_dir():
            return candidate

    if not create:
        raise RuntimeError("The external Torch location has no supported site-packages directory.")

    posix_paths = sorted(root.glob("lib/python*/site-packages"))
    if posix_paths:
        destination = posix_paths[0]
    elif (root / "Lib").is_dir():
        destination = root / "Lib" / "site-packages"
    else:
        destination = root / "site-packages"
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _GetVenvPython(root : Path) -> Path|None:
    """Find a Windows or POSIX venv interpreter."""
    candidates = (root / "Scripts" / "python.exe", root / "bin" / "python")
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _ProbePythonCompatibility(python_executable : Path) -> dict[str, object]:
    """Read ABI and platform facts from the external venv's own interpreter."""
    probe = (
        "import json,platform,struct,sys,sysconfig;"
        "v=sys.version_info;"
        "abi=sys.implementation.cache_tag or sysconfig.get_config_var('SOABI') or "
        "f'{sys.implementation.name}{v.major}{v.minor}';"
        "print(json.dumps({'python_implementation': sys.implementation.name,"
        "'python_abi': abi,"
        "'python_version': f'{v.major}.{v.minor}',"
        "'os': platform.system() or sys.platform,"
        "'architecture': platform.machine() or 'unknown',"
        "'pointer_bits': struct.calcsize('P') * 8}))"
    )
    result = subprocess.run(
        [str(python_executable), "-I", "-S", "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise RuntimeError("The external venv returned invalid compatibility data.")
    return parsed


def _WriteMetadata(metadata_path : Path, metadata : dict[str, object]) -> None:
    """Write deterministic UTF-8 compatibility metadata."""
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
