"""PyInstaller runtime compatibility shim for the nagisa package.

nagisa predates mandatory relative imports.  Its ``train.py`` module uses
bare absolute names (``import prepro``, ``import model``, ``import tagger``,
``import mecab_system_eval``) that relied on the package directory being on
sys.path at runtime.  In a frozen build the PYZ archive stores these modules
as ``nagisa.prepro`` etc., not as top-level names, so the bare imports would
raise ``ModuleNotFoundError``.

``nagisa.train`` is the training subsystem and is never called during
inference; ``nagisa/__init__.py`` imports only ``fit`` from it, which is
unused.  Stubbing the module in ``sys.modules`` before the package loads
prevents the bare-import chain from running at all.

This hook runs at frozen application startup, before any user imports.
"""

import sys
import types

# Stub nagisa.train so that nagisa/__init__.py's `from nagisa.train import fit`
# succeeds without executing train.py's Python-2-style bare absolute imports.
# Training is never used during inference; the stub exposes a no-op fit.
if "nagisa.train" not in sys.modules:
    _train_stub = types.ModuleType("nagisa.train")
    _train_stub.fit = None  # type: ignore[assignment]
    sys.modules["nagisa.train"] = _train_stub
