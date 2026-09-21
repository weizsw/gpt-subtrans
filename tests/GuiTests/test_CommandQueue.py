"""Verify how a command that fails with an exception reports itself to the queue."""
import logging

from tests.GuiTestSupport import ConfigureOffscreenPlatform

ConfigureOffscreenPlatform()

from PySide6.QtWidgets import QApplication

from GuiSubtrans.Command import Command, CommandError
from PySubtrans.Helpers.TestCases import LoggedTestCase


class FailingCommand(Command):
    """Command that raises a supplied exception from execute()."""

    def __init__(self, error : Exception) -> None:
        super().__init__()
        self.error : Exception = error

    def execute(self) -> bool:
        raise self.error


class TestFatalCommandErrors(LoggedTestCase):
    """A fatal error must leave the command failed and terminal so the chain stops."""

    application : QApplication

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        existing = QApplication.instance()
        cls.application = existing if isinstance(existing, QApplication) else QApplication([])

    def _run_command(self, error : Exception) -> FailingCommand:
        """Run a command that fails with the given error, muting the expected error log."""
        command = FailingCommand(error)
        with self.assertLogs(level=logging.ERROR):
            command.run()

        return command

    def test_command_error_is_terminal(self) -> None:
        command = self._run_command(CommandError("test failure", command=Command()))

        self.assertLoggedEqual("command succeeded", False, command.succeeded)
        self.assertLoggedTrue("command terminal", command.terminal)

    def test_unexpected_error_is_not_terminal(self) -> None:
        command = self._run_command(RuntimeError("something unexpected"))

        self.assertLoggedEqual("command succeeded", False, command.succeeded)
        self.assertLoggedFalse("command terminal", command.terminal)


if __name__ == '__main__':
    import unittest
    unittest.main()
