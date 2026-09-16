"""Run optional SDK and real media integration checks separately from unit tests."""
import importlib
import logging
import sys
import unittest
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
# Prefer this checkout over an editable installation in another worktree.
sys.path.insert(0, str(_root))

from tests.GuiTestSupport import ConfigureOffscreenPlatform


class GuiDependenciesUnavailable(unittest.TestCase):
    """Report optional GUI integration coverage that could not run."""

    def runTest(self) -> None:
        self.skipTest('PySide6 GUI dependencies are unavailable')


def Main() -> int:
    """Discover integration tests and return failure status to release scripts."""
    root = _root
    results = root / 'test_results'
    results.mkdir(exist_ok=True)
    logging.basicConfig(filename=results / 'integration_tests.log', filemode='w',
                        encoding='utf-8', level=logging.INFO)

    # Run the non-GUI suite to completion before PySide6 is ever imported. PySide6's
    # shiboken signature loader installs a global import hook that inspects every
    # subsequent import in the process; when transformers/sklearn/pandas are imported
    # afterwards (e.g. by the Qwen tests), that hook corrupts six's synthetic module
    # machinery and produces misleading "cannot import name" failures.
    suite = unittest.TestLoader().discover(
        str(root / 'tests' / 'IntegrationTests'), pattern='test_*.py', top_level_dir=str(root))
    if suite.countTestCases() == 0:
        print('No integration tests discovered.', file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=1).run(suite)

    ConfigureOffscreenPlatform()
    gui_suite = unittest.TestSuite()
    try:
        importlib.import_module('PySide6.QtGui')
        importlib.import_module('PySide6.QtWidgets')
    except (ImportError, OSError) as error:
        logging.info('Skipping GUI integration tests: %s', error)
        gui_suite.addTest(GuiDependenciesUnavailable())
    else:
        gui_suite.addTests(unittest.TestLoader().discover(
            str(root / 'tests' / 'GuiIntegrationTests'), pattern='test_*.py', top_level_dir=str(root)))
    gui_result = unittest.TextTestRunner(verbosity=1).run(gui_suite)

    return 0 if result.wasSuccessful() and gui_result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(Main())
