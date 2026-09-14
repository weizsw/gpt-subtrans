import os
import sys
from unittest.mock import patch

if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from GuiSubtrans.Widgets.TorchSetupDialog import (
    TorchSetupDialog,
    _detect_hardware,
    _select_cuda_build,
    _HardwareDetection,
)
from PySubtrans.Helpers.TestCases import LoggedTestCase


class TestTorchSetupSelection(LoggedTestCase):
    """Verify Torch selection follows the driver's reported CUDA capability."""

    application : QApplication

    _CUDA_BUILDS : list[tuple[str, str, str]] = [
        ('13.0', '580.65', 'https://download.pytorch.org/whl/cu130'),
        ('12.6', '560.70', 'https://download.pytorch.org/whl/cu126'),
    ]

    def test_reported_cuda_capability_selects_available_build(self) -> None:
        """A newer driver report selects the newest available PyTorch build."""
        selected = _select_cuda_build('616.56', '13.4', self._CUDA_BUILDS)

        self.assertLoggedEqual(
            'cu130 selected for CUDA 13.4 driver capability',
            ('13.0', 'https://download.pytorch.org/whl/cu130'),
            selected,
        )

    def test_reported_cuda_capability_limits_build_selection(self) -> None:
        """A driver capable of only CUDA 12.6 does not select a newer wheel."""
        selected = _select_cuda_build('616.56', '12.6', self._CUDA_BUILDS)

        self.assertLoggedEqual(
            'cu126 selected for CUDA 12.6 driver capability',
            ('12.6', 'https://download.pytorch.org/whl/cu126'),
            selected,
        )

    def test_hardware_detection_reports_driver_cuda_capability(self) -> None:
        """The hardware description tells the user what the driver reported."""
        with patch('GuiSubtrans.Widgets.TorchSetupDialog._detect_nvidia_driver', return_value='616.56'), \
                patch('GuiSubtrans.Widgets.TorchSetupDialog._detect_nvidia_cuda_version', return_value='13.4'):
            hardware = _detect_hardware()

        self.assertLoggedIsNotNone('hardware detection result', hardware)
        if hardware is not None:
            self.assertLoggedEqual('cu130 wheel index', 'https://download.pytorch.org/whl/cu130', hardware.index_url)
            self.assertLoggedIn('reported CUDA version', 'driver reports CUDA 13.4', hardware.description)

    @classmethod
    def setUpClass(cls) -> None:
        """Create the shared Qt application for dialog interaction tests."""
        super().setUpClass()
        existing = QApplication.instance()
        cls.application = existing if isinstance(existing, QApplication) else QApplication([])

    def test_cpu_fallback_requires_explicit_confirmation(self) -> None:
        """A detected GPU without a driver cannot silently install CPU Torch."""
        hardware = _HardwareDetection(
            'NVIDIA GPU detected, but its driver is unavailable',
            'https://download.pytorch.org/whl/cpu',
            False,
            'Install the latest NVIDIA driver, restart, and try again.',
            hardware_detected=True,
        )
        with patch('GuiSubtrans.Widgets.TorchSetupDialog._detect_hardware', return_value=hardware), \
                patch('GuiSubtrans.Widgets.TorchSetupDialog._find_existing_torch', return_value=None):
            dialog = TorchSetupDialog()

        try:
            dialog._show_page(1)
            self.assertLoggedFalse('CPU install requires confirmation', dialog._next_button.isEnabled())
            self.assertLoggedEqual('CPU fallback action', 'Install CPU-only Torch', dialog._next_button.text())
            if dialog._cpu_fallback_checkbox is not None:
                self.assertLoggedFalse('CPU fallback is initially unchecked', dialog._cpu_fallback_checkbox.isChecked())
                dialog._cpu_fallback_checkbox.setChecked(True)
                self.assertLoggedTrue('CPU install enabled after confirmation', dialog._next_button.isEnabled())
        finally:
            dialog.deleteLater()
            self.application.processEvents()
