import logging

try:
    from PyInstaller.utils.hooks import collect_submodules  # type: ignore

    hiddenimports = collect_submodules('scripts')
    hiddenimports += collect_submodules('PySubtrans.Providers')
    hiddenimports += collect_submodules('PySubtrans.Formats')

    # Torch is installed separately for frozen Qwen Local deployments.  Keep
    # both Python packages and their native payloads out of the application;
    # the runtime loader adds a compatible external installation explicitly.
    excludedimports = ['torch', 'torchgen']

except ImportError:
    logging.info("PyInstaller not found, skipping hook")
