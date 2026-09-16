import logging

try:
    from PyInstaller.utils.hooks import collect_submodules  # type: ignore

    hiddenimports = collect_submodules('scripts')
    hiddenimports += collect_submodules('PySubtrans.Providers')
    hiddenimports += collect_submodules('PySubtrans.Formats')

    # qwen_asr is imported dynamically via importlib.import_module so
    # PyInstaller's static analyser never follows the chain.  Seeding
    # the package here lets hook-qwen_asr.py fire and collect its data.
    hiddenimports += ['qwen_asr']

    # Torch is excluded from static analysis entirely (see below), so PyInstaller
    # has no way to see that several of its own internals (e.g. torch._inductor,
    # torch._dispatch.python, torch._guards) import unittest.mock at runtime for
    # ordinary compile/dispatch bookkeeping, not just for tests. The external
    # Torch venv only contributes its site-packages to sys.path, not its stdlib,
    # so the frozen app's own bundle must carry unittest.mock itself or Qwen Local
    # transcription fails with "No module named 'unittest.mock'" once torch code
    # actually touches it (importing unittest.mock also runs unittest/__init__.py,
    # so the whole package is pulled in regardless of naming just the submodule).
    hiddenimports += ['unittest.mock']

    # Torch is installed separately for frozen Qwen Local deployments.  Keep
    # both Python packages and their native payloads out of the application;
    # the runtime loader adds a compatible external installation explicitly.
    excludedimports = ['torch', 'torchgen']

except ImportError:
    logging.info("PyInstaller not found, skipping hook")
