"""PyInstaller collection hook for the qwen-asr package.

qwen-asr is a runtime dependency for Qwen Local transcription.  The frozen
application imports it dynamically via importlib.import_module so PyInstaller's
static analyser never follows the import chain to it; this hook ensures the
package and any bundled data files are collected.

Torch (a compile-time dependency of qwen_asr) is excluded from the bundle
and supplied as an external user-installed runtime via TorchRuntime.py.
"""

try:
    from PyInstaller.utils.hooks import collect_all  # type: ignore

    tmp_ret = collect_all('qwen_asr')
    datas, binaries, hiddenimports = tmp_ret[0], tmp_ret[1], tmp_ret[2]

except ImportError:
    import logging
    logging.info("PyInstaller not found, skipping hook-qwen_asr")
