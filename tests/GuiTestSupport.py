"""Shared bootstrap for tests that create a real Qt application.

Kept separate from tests.Helpers, which is also imported by non-GUI test
modules and must not carry a hard PySide6 dependency.
"""
import os
import sys


def ConfigureOffscreenPlatform() -> None:
    """Set the Qt platform and logging environment before PySide6 is imported.

    Must run before the first PySide6 import: QT_QPA_PLATFORM only takes effect
    at QApplication construction and QT_LOGGING_RULES at Qt's logging setup,
    both of which happen as a side effect of importing PySide6.
    """
    if sys.platform != 'win32':
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QT_LOGGING_RULES', 'qt.qpa.fonts=false')


def InstallOffscreenSizeHintFilter() -> None:
    """Silence the offscreen platform plugin's harmless propagateSizeHints() notice.

    Showing a real widget under QT_QPA_PLATFORM=offscreen always logs this fixed
    message; it is not a categorized log message, so QT_LOGGING_RULES cannot
    filter it. Only this exact known-benign message is dropped here, so other
    genuine Qt warnings/errors raised during a test still surface normally.
    """
    from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler

    def _filter(msg_type : QtMsgType, context : QMessageLogContext, message : str) -> None:
        if 'propagateSizeHints' in message:
            return
        print(message, file=sys.stderr)

    qInstallMessageHandler(_filter)
