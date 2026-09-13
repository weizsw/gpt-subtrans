"""Dialog for setting up a Torch installation for local transcription."""

from __future__ import annotations

import importlib.util
import logging
import os
import platform
import regex
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from PySubtrans.Helpers.Localization import _
from PySubtrans.Helpers.Resources import GetConfigDir
from PySubtrans.Transcription.TorchValidation import (
    build_current_compatibility,
    check_compatibility,
    find_compatibility_metadata,
    has_torch_package,
    read_compatibility_metadata,
)


# Minimum NVIDIA driver versions for each CUDA toolkit (Windows).
# Source: https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/
_CUDA_MIN_DRIVER_WINDOWS : list[tuple[str, str, str]] = [
    ('13.0', '580.65', 'https://download.pytorch.org/whl/cu130'),
    ('12.6', '560.70', 'https://download.pytorch.org/whl/cu126'),
    ('12.4', '551.61', 'https://download.pytorch.org/whl/cu124'),
    ('12.1', '527.41', 'https://download.pytorch.org/whl/cu121'),
    ('11.8', '520.06', 'https://download.pytorch.org/whl/cu118'),
]

_CUDA_MIN_DRIVER_LINUX : list[tuple[str, str, str]] = [
    ('13.0', '580.65.06', 'https://download.pytorch.org/whl/cu130'),
    ('12.6', '560.28.03', 'https://download.pytorch.org/whl/cu126'),
    ('12.4', '550.54.14', 'https://download.pytorch.org/whl/cu124'),
    ('12.1', '525.60.13', 'https://download.pytorch.org/whl/cu121'),
    ('11.8', '520.61.05', 'https://download.pytorch.org/whl/cu118'),
]

_ROCM_INDEX_URL = 'https://download.pytorch.org/whl/rocm6.2.4'
_CPU_INDEX_URL = 'https://download.pytorch.org/whl/cpu'
_NVIDIA_CUDA_VERSION_PATTERN = regex.compile(
    r'CUDA\s+(?:UMD\s+)?Version:\s*([0-9]+(?:\.[0-9]+)?)',
    regex.IGNORECASE,
)


class _HardwareDetection:
    """Result of automatic hardware detection."""

    def __init__(
        self,
        description : str,
        index_url : str,
        is_gpu : bool,
        guidance : str = '',
        hardware_detected : bool = False,
    ):
        self.description = description
        self.index_url = index_url
        self.is_gpu = is_gpu
        self.guidance = guidance
        self.hardware_detected = hardware_detected


def _parse_driver_version(version_str : str) -> tuple[int, ...]:
    """Parse a dotted driver version string into a comparable tuple."""
    parts : list[int] = []
    for segment in version_str.strip().split('.'):
        try:
            parts.append(int(segment))
        except ValueError:
            break
    return tuple(parts)


def _detect_nvidia_driver() -> str|None:
    """Query the NVIDIA driver version via nvidia-smi, or return None."""
    nvidia_smi = shutil.which('nvidia-smi')
    if not nvidia_smi:
        return None

    try:
        result = subprocess.run(
            [nvidia_smi, '--query-gpu=driver_version', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Multi-GPU: take the first line
            return result.stdout.strip().splitlines()[0].strip()
    except (OSError, subprocess.TimeoutExpired):
        pass

    return None


def _detect_nvidia_cuda_version() -> str|None:
    """Read the maximum CUDA API version reported by the NVIDIA driver."""
    nvidia_smi = shutil.which('nvidia-smi')
    if not nvidia_smi:
        return None

    try:
        result = subprocess.run(
            [nvidia_smi], capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            match = _NVIDIA_CUDA_VERSION_PATTERN.search(result.stdout)
            if match:
                return match.group(1)
    except (OSError, subprocess.TimeoutExpired):
        pass

    return None


def _select_cuda_build(
    driver_version : str,
    reported_cuda_version : str|None,
    table : list[tuple[str, str, str]],
) -> tuple[str, str]|None:
    """Select the newest available PyTorch build supported by the driver."""
    driver_tuple = _parse_driver_version(driver_version)
    reported_cuda_tuple = _parse_driver_version(reported_cuda_version) if reported_cuda_version else None

    for cuda_version, min_driver, index_url in table:
        if driver_tuple < _parse_driver_version(min_driver):
            continue
        if reported_cuda_tuple is not None and _parse_driver_version(cuda_version) > reported_cuda_tuple:
            continue
        return cuda_version, index_url

    return None


def _detect_gpu_hardware() -> str|None:
    """Scan for GPU hardware by vendor name, without relying on driver toolkits.

    Returns 'nvidia', 'amd', 'intel', or None.
    """
    gpu_names : list[str] = []

    if sys.platform == 'win32':
        try:
            result = subprocess.run(
                ['wmic', 'path', 'win32_VideoController', 'get', 'Name'],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                gpu_names = result.stdout.strip().splitlines()
        except (OSError, subprocess.TimeoutExpired):
            pass

    elif sys.platform == 'linux':
        lspci = shutil.which('lspci')
        if lspci:
            try:
                result = subprocess.run(
                    [lspci], capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    gpu_names = [
                        line for line in result.stdout.splitlines()
                        if any(tag in line.lower() for tag in ('vga', '3d', 'display'))
                    ]
            except (OSError, subprocess.TimeoutExpired):
                pass

    combined = ' '.join(gpu_names).lower()
    if 'nvidia' in combined or 'geforce' in combined or 'quadro' in combined or 'tesla' in combined:
        return 'nvidia'
    if 'amd' in combined or 'radeon' in combined:
        return 'amd'
    if 'intel' in combined and ('arc' in combined or 'iris' in combined or 'xe' in combined):
        return 'intel'

    return None


def _detect_hardware() -> _HardwareDetection|None:
    """Detect the best available hardware for Torch and return install parameters."""

    # macOS Apple Silicon: MPS is in the default PyPI torch wheel
    if sys.platform == 'darwin' and platform.machine() == 'arm64':
        return _HardwareDetection(
            description=_("Apple Silicon detected -- Torch will use Metal Performance Shaders (MPS)"),
            index_url='',  # default PyPI wheel includes MPS
            is_gpu=True,
        )

    # Try NVIDIA via driver query
    driver_version = _detect_nvidia_driver()
    if driver_version:
        table = _CUDA_MIN_DRIVER_LINUX if sys.platform == 'linux' else _CUDA_MIN_DRIVER_WINDOWS
        reported_cuda_version = _detect_nvidia_cuda_version()
        selected_build = _select_cuda_build(driver_version, reported_cuda_version, table)

        if selected_build:
            cuda_version, index_url = selected_build
            reported_text = (
                _('; driver reports CUDA {cuda}').format(cuda=reported_cuda_version)
                if reported_cuda_version else ''
            )
            return _HardwareDetection(
                description=_("NVIDIA GPU detected (driver {driver}{reported}) -- will install Torch with CUDA {cuda}").format(
                    driver=driver_version, reported=reported_text, cuda=cuda_version),
                index_url=index_url,
                is_gpu=True,
            )

        # Driver found but too old for any known CUDA
        return _HardwareDetection(
            description=_("NVIDIA GPU detected but driver {driver} is too old for CUDA. "
                          "Update your driver from nvidia.com, then try again.").format(driver=driver_version),
            index_url=_CPU_INDEX_URL,
            is_gpu=False,
            hardware_detected=True,
        )

    # Try AMD ROCm (Linux only — PyTorch does not ship ROCm wheels for Windows)
    if sys.platform == 'linux' and shutil.which('rocminfo'):
        return _HardwareDetection(
            description=_("AMD GPU detected (ROCm) -- will install Torch with ROCm support"),
            index_url=_ROCM_INDEX_URL,
            is_gpu=True,
        )

    # No NVIDIA driver detected — scan for GPU hardware and give specific guidance
    gpu_vendor = _detect_gpu_hardware()

    if gpu_vendor == 'nvidia':
        return _HardwareDetection(
            description=_("NVIDIA GPU detected, but its driver is unavailable"),
            index_url=_CPU_INDEX_URL,
            is_gpu=False,
            guidance=_("Install the latest NVIDIA driver for this GPU from nvidia.com, "
                       "restart the computer, and choose Set up Torch again."),
            hardware_detected=True,
        )

    if gpu_vendor == 'amd':
        if sys.platform == 'linux':
            return _HardwareDetection(
                description=_("AMD GPU found but ROCm is not installed"),
                index_url=_CPU_INDEX_URL,
                is_gpu=False,
                guidance=_("Install ROCm from AMD's documentation, restart, and try again."),
                hardware_detected=True,
            )
        return _HardwareDetection(
            description=_("AMD GPU found"),
            index_url=_CPU_INDEX_URL,
            is_gpu=False,
            guidance=_("PyTorch GPU support for AMD requires Linux with ROCm."),
            hardware_detected=True,
        )

    if gpu_vendor == 'intel':
        return _HardwareDetection(
            description=_("Intel GPU found"),
            index_url=_CPU_INDEX_URL,
            is_gpu=False,
            guidance=_("PyTorch XPU support requires the Intel oneAPI toolkit (intel.com/oneapi)."),
            hardware_detected=True,
        )

    # No GPU detected at all — return None-like result; dialog will ask the user
    return None


# Answers for the "what GPU do you have?" fallback question
_GPU_VENDOR_OPTIONS : dict[str, _HardwareDetection] = {
    _("NVIDIA"): _HardwareDetection(
        description=_("NVIDIA selected -- install the NVIDIA driver before setting up Torch"),
        index_url=_CPU_INDEX_URL,
        is_gpu=False,
        guidance=_("Install the latest NVIDIA driver for this GPU from nvidia.com, "
                   "restart the computer, and choose Set up Torch again."),
        hardware_detected=True,
    ),
    _("AMD"): _HardwareDetection(
        description=_("AMD selected"),
        index_url=_ROCM_INDEX_URL if sys.platform == 'linux' else _CPU_INDEX_URL,
        is_gpu=sys.platform == 'linux',
        guidance='' if sys.platform == 'linux'
                 else _("PyTorch GPU support for AMD requires Linux with ROCm."),
        hardware_detected=sys.platform != 'linux',
    ),
    _("Intel"): _HardwareDetection(
        description=_("Intel selected"),
        index_url=_CPU_INDEX_URL,
        is_gpu=False,
        guidance=_("PyTorch XPU support requires the Intel oneAPI toolkit (intel.com/oneapi)."),
        hardware_detected=True,
    ),
    _("No GPU / CPU only"): _HardwareDetection(
        description=_("CPU selected -- transcription will work but will be significantly slower than with a GPU"),
        index_url=_CPU_INDEX_URL,
        is_gpu=False,
    ),
}


def _find_existing_torch() -> str|None:
    """Try to locate an existing torch installation.

    Returns the directory path if found, or None.
    """
    spec = importlib.util.find_spec('torch')
    if spec and spec.origin:
        torch_path = Path(spec.origin).parent
        # Walk up to find the site-packages directory
        for parent in [torch_path.parent, torch_path.parent.parent]:
            if parent.name == 'site-packages':
                return str(parent.parent)
        return str(torch_path.parent)

    return None


class TorchSetupDialog(QDialog):
    """Step-by-step dialog that helps users choose and install a Torch environment."""

    def __init__(self, current_path : str = '', parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Set up Torch for Local Transcription"))
        self.setMinimumWidth(680)
        self.setMinimumHeight(600)

        self.chosen_path : str = ''
        self._process : QProcess|None = None
        self._hardware : _HardwareDetection|None = _detect_hardware()
        self._existing_path : str|None = None
        self._cpu_fallback_checkbox : QCheckBox|None = None
        self._current_page : int = 0
        self._installation_complete : bool = False
        self._installation_failed : bool = False

        self._build_ui(current_path)
        self._scan_for_existing_torch()

    def _build_ui(self, current_path : str) -> None:
        """Construct the four-page setup flow."""
        layout = QVBoxLayout(self)
        self._page_stack = QStackedWidget(self)
        layout.addWidget(self._page_stack, 1)

        self._build_choice_page()
        self._build_install_page(current_path)
        self._build_progress_page()
        self._build_completion_page()

        self._button_box = QDialogButtonBox(self)
        self._back_button = QPushButton(_("Back"), self)
        self._next_button = QPushButton(_("Continue"), self)
        self._finish_button = QPushButton(_("Save Torch selection"), self)
        self._button_box.addButton(self._back_button, QDialogButtonBox.ButtonRole.ActionRole)
        self._button_box.addButton(self._next_button, QDialogButtonBox.ButtonRole.ActionRole)
        self._button_box.addButton(self._finish_button, QDialogButtonBox.ButtonRole.ActionRole)
        self._cancel_button = self._button_box.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._back_button.clicked.connect(self._on_back)
        self._next_button.clicked.connect(self._on_next)
        self._finish_button.clicked.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

        self._show_page(0)

    def _build_choice_page(self) -> None:
        """Build the first page where the user chooses one setup route."""
        page = QWidget(self)
        page_layout = QVBoxLayout(page)

        title = QLabel(_("Step 1 of 3 - Choose how to provide Torch"))
        title.setStyleSheet("font-weight: bold;")
        page_layout.addWidget(title)
        page_layout.addWidget(QLabel(_("Choose one option, then click Continue. You will not need to complete both.")))

        self._existing_group = QGroupBox(_("Use an existing installation"), page)
        existing_layout = QVBoxLayout(self._existing_group)
        self._existing_radio = QRadioButton(_("Use the detected Torch environment"), self._existing_group)
        self._existing_radio.setEnabled(False)
        existing_layout.addWidget(self._existing_radio)
        self._existing_label = QLabel(_("Searching for an existing Torch installation..."), self._existing_group)
        self._existing_label.setWordWrap(True)
        existing_layout.addWidget(self._existing_label)
        page_layout.addWidget(self._existing_group)

        install_group = QGroupBox(_("Install automatically"), page)
        install_layout = QVBoxLayout(install_group)
        self._automatic_radio = QRadioButton(_("Create a new environment and install Torch"), install_group)
        self._automatic_radio.setChecked(True)
        install_layout.addWidget(self._automatic_radio)
        install_layout.addWidget(QLabel(_("The next page will ask where to create it and show the detected hardware.")))
        page_layout.addWidget(install_group)
        page_layout.addStretch(1)

        self._existing_radio.toggled.connect(self._on_choice_changed)
        self._automatic_radio.toggled.connect(self._on_choice_changed)
        self._page_stack.addWidget(page)

    def _build_install_page(self, current_path : str) -> None:
        """Build the page for the automatic installation choices."""
        page = QWidget(self)
        page_layout = QVBoxLayout(page)

        title = QLabel(_("Step 2 of 3 - Choose the installation options"))
        title.setStyleSheet("font-weight: bold;")
        page_layout.addWidget(title)
        page_layout.addWidget(QLabel(_("Choose a folder for the new environment. Hardware detection selects the Torch build automatically.")))

        directory_group = QGroupBox(_("Installation folder"), page)
        directory_layout = QVBoxLayout(directory_group)
        dir_row = QHBoxLayout()
        default_path = current_path or os.path.join(GetConfigDir(), "torch-env")
        self._dir_field = QLineEdit(default_path, directory_group)
        self._dir_field.setCursorPosition(0)
        self._dir_field.setToolTip(_("A new Python virtual environment will be created here."))
        dir_row.addWidget(self._dir_field)
        browse_button = QPushButton(_("Browse..."), directory_group)
        browse_button.clicked.connect(self._on_browse)
        dir_row.addWidget(browse_button)
        directory_layout.addLayout(dir_row)
        directory_layout.addWidget(QLabel(_("Use a new or empty folder; existing files will not be overwritten.")))
        self._install_error_label = QLabel('', directory_group)
        self._install_error_label.setWordWrap(True)
        directory_layout.addWidget(self._install_error_label)
        page_layout.addWidget(directory_group)

        hardware_group = QGroupBox(_("Detected hardware"), page)
        hardware_layout = QVBoxLayout(hardware_group)
        if self._hardware:
            self._hardware_label = QLabel(self._hardware.description, hardware_group)
            self._hardware_label.setWordWrap(True)
            hardware_layout.addWidget(self._hardware_label)

            if self._hardware.guidance:
                guidance_label = QLabel(self._hardware.guidance, hardware_group)
                guidance_label.setWordWrap(True)
                hardware_layout.addWidget(guidance_label)

            if not self._hardware.is_gpu:
                cpu_warning = QLabel(
                    _("GPU acceleration is strongly recommended. "
                      "See <a href=\"https://pytorch.org/get-started/locally/\">pytorch.org</a> "
                      "for hardware-specific instructions."),
                    hardware_group)
                cpu_warning.setWordWrap(True)
                cpu_warning.setOpenExternalLinks(True)
                hardware_layout.addWidget(cpu_warning)
        else:
            hardware_layout.addWidget(QLabel(_("GPU detection was inconclusive. Choose the closest option:"), hardware_group))
            self._gpu_combo = QComboBox(hardware_group)
            for vendor_label in _GPU_VENDOR_OPTIONS:
                self._gpu_combo.addItem(vendor_label)
            self._gpu_combo.setCurrentIndex(self._gpu_combo.count() - 1)
            self._gpu_combo.currentTextChanged.connect(self._on_gpu_vendor_changed)
            hardware_layout.addWidget(self._gpu_combo)
            self._vendor_guidance_label = QLabel('', hardware_group)
            self._vendor_guidance_label.setWordWrap(True)
            self._vendor_guidance_label.setOpenExternalLinks(True)
            hardware_layout.addWidget(self._vendor_guidance_label)
        page_layout.addWidget(hardware_group)

        self._cpu_fallback_checkbox = QCheckBox(
            _("Install CPU-only Torch anyway (transcription will be slower)"), page)
        self._cpu_fallback_checkbox.setVisible(False)
        self._cpu_fallback_checkbox.toggled.connect(self._on_cpu_fallback_changed)
        page_layout.addWidget(self._cpu_fallback_checkbox)

        if self._hardware is None:
            self._on_gpu_vendor_changed(self._gpu_combo.currentText())
        else:
            self._update_cpu_fallback_choice()

        page_layout.addStretch(1)
        self._page_stack.addWidget(page)

    def _build_progress_page(self) -> None:
        """Build the page that reports the two installation subprocesses."""
        page = QWidget(self)
        page_layout = QVBoxLayout(page)
        title = QLabel(_("Step 3 of 3 - Installing Torch"))
        title.setStyleSheet("font-weight: bold;")
        page_layout.addWidget(title)
        self._step_status_label = QLabel(_("Preparing installation..."), page)
        self._step_status_label.setWordWrap(True)
        page_layout.addWidget(self._step_status_label)
        self._log_output = QTextEdit(page)
        self._log_output.setReadOnly(True)
        # Keep Windows paths intact in diagnostic output; wrapping at the drive
        # letter colon makes paths look malformed and harder to copy.
        self._log_output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._log_output.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        page_layout.addWidget(self._log_output)
        self._page_stack.addWidget(page)

    def _build_completion_page(self) -> None:
        """Build the final page that confirms the selected environment."""
        page = QWidget(self)
        page_layout = QVBoxLayout(page)
        title = QLabel(_("Torch setup complete"))
        title.setStyleSheet("font-weight: bold;")
        page_layout.addWidget(title)
        self._completion_summary = QLabel(_("The Torch environment is ready to use."), page)
        self._completion_summary.setWordWrap(True)
        page_layout.addWidget(self._completion_summary)
        self._completion_path_label = QLabel('', page)
        self._completion_path_label.setWordWrap(True)
        page_layout.addWidget(self._completion_path_label)
        page_layout.addWidget(QLabel(_("Click Save Torch selection, then restart the application before starting a local transcription."), page))
        page_layout.addStretch(1)
        self._page_stack.addWidget(page)

    def _show_page(self, page_index : int) -> None:
        """Show one setup page and configure only its relevant navigation buttons."""
        self._current_page = page_index
        self._page_stack.setCurrentIndex(page_index)

        self._back_button.setVisible(page_index == 1 or (page_index == 2 and self._installation_failed))
        self._back_button.setEnabled(page_index == 1 or (page_index == 2 and self._installation_failed))
        self._next_button.setVisible(page_index in (0, 1) or (page_index == 2 and self._installation_complete))
        self._next_button.setEnabled(page_index in (0, 1) or (page_index == 2 and self._installation_complete))
        self._finish_button.setVisible(page_index == 3)
        self._cancel_button.setVisible(page_index != 3)

        if page_index == 0:
            self._on_choice_changed()
        elif page_index == 1:
            self._update_cpu_fallback_choice()
            self._next_button.setText(
                _("Install CPU-only Torch") if self._requires_cpu_confirmation() else _("Install Torch"))
            self._next_button.setEnabled(self._can_start_install())
        elif page_index == 2:
            self._next_button.setText(_("Continue"))

    def _on_choice_changed(self) -> None:
        """Update the first-page action to match the selected setup route."""
        if self._current_page != 0:
            return

        if self._existing_radio.isChecked():
            self._next_button.setText(_("Use existing installation"))
            self._next_button.setEnabled(bool(self._existing_path))
        else:
            self._next_button.setText(_("Choose installation options"))
            self._next_button.setEnabled(True)

    def _on_next(self) -> None:
        """Advance the guided flow or start the selected installation."""
        if self._current_page == 0:
            if self._existing_radio.isChecked():
                if self._existing_path:
                    self._validate_and_accept(self._existing_path)
                    self._show_page(3)
            else:
                self._show_page(1)
            return

        if self._current_page == 1:
            if self._on_install():
                self._show_page(2)
            return

        if self._current_page == 2 and self._installation_complete:
            self._show_page(3)

    def _on_back(self) -> None:
        """Return to the previous setup choice when no installation is running."""
        if self._current_page == 1:
            self._show_page(0)
        elif self._current_page == 2 and self._installation_failed:
            self._show_page(1)

    def _requires_cpu_confirmation(self) -> bool:
        """Whether a detected GPU lacks a usable Torch acceleration backend."""
        return bool(self._hardware and self._hardware.hardware_detected and not self._hardware.is_gpu)

    def _can_start_install(self) -> bool:
        """Whether the current hardware choice permits automatic installation."""
        return not self._requires_cpu_confirmation() or bool(
            self._cpu_fallback_checkbox and self._cpu_fallback_checkbox.isChecked())

    def _update_cpu_fallback_choice(self) -> None:
        """Show the explicit CPU fallback choice when GPU setup is unavailable."""
        if self._cpu_fallback_checkbox is None:
            return

        requires_confirmation = self._requires_cpu_confirmation()
        self._cpu_fallback_checkbox.setVisible(requires_confirmation)
        if not requires_confirmation:
            self._cpu_fallback_checkbox.setChecked(False)

        if hasattr(self, '_next_button') and self._current_page == 1:
            self._next_button.setText(
                _("Install CPU-only Torch") if requires_confirmation else _("Install Torch"))
            self._next_button.setEnabled(self._can_start_install())

    def _on_cpu_fallback_changed(self, checked : bool) -> None:
        """Update installation navigation after an explicit CPU fallback choice."""
        del checked
        self._update_cpu_fallback_choice()

    def _on_gpu_vendor_changed(self, vendor_text : str) -> None:
        """Update hardware selection when the user picks a GPU vendor."""
        selection = _GPU_VENDOR_OPTIONS.get(vendor_text)
        if selection:
            self._hardware = selection
            self._vendor_guidance_label.setText(selection.guidance)
        else:
            self._hardware = None
        self._update_cpu_fallback_choice()

    def _scan_for_existing_torch(self) -> None:
        """Look for a torch installation already on the system."""
        existing = _find_existing_torch()
        if existing:
            self._existing_label.setText(
                _("Found an existing Torch installation at: {path}").format(path=existing)
            )
            self._existing_path = existing
            self._existing_radio.setEnabled(True)
        else:
            self._existing_label.setText(_("No existing Torch installation found on this system."))
            self._existing_path = None
            self._existing_radio.setEnabled(False)

    def _on_use_existing(self) -> None:
        """Select and continue with the detected existing installation."""
        if self._existing_path:
            self._existing_radio.setChecked(True)
            self._on_next()

    def _on_browse(self) -> None:
        """Open a directory picker."""
        directory = QFileDialog.getExistingDirectory(
            self, _("Select Torch Installation Directory"), self._dir_field.text()
        )
        if directory:
            self._dir_field.setText(directory)

    def _on_install(self) -> bool:
        """Start the automatic installation and return whether it was started."""
        if not self._can_start_install():
            self._install_error_label.setText(
                _("Install or update the GPU driver, then run setup again, or select Install CPU-only Torch anyway."))
            return False

        target_dir = self._dir_field.text().strip()
        if not target_dir:
            self._install_error_label.setText(_("Choose an installation folder before continuing."))
            return False

        self._install_error_label.clear()
        self._installation_complete = False
        self._installation_failed = False
        self._step_status_label.setText(_("Step 1 of 2: Creating the private Python environment..."))
        self._log_output.clear()

        # Find a Python interpreter to create the venv with
        python = self._find_python()
        if not python:
            self._install_error_label.setText(_("Python 3.10 or newer is required. Install Python, then try again."))
            return False

        self._target_dir = target_dir
        self._log(_("Creating virtual environment at {path}...").format(path=target_dir))
        self._run_step_create_venv(python, target_dir)
        return True

    def _find_python(self) -> str|None:
        """Locate a usable Python interpreter."""
        # Prefer the running interpreter if it's not frozen
        if not getattr(sys, 'frozen', False):
            return sys.executable

        # Fall back to PATH
        for name in ('python3', 'python'):
            found = shutil.which(name)
            if found:
                return found

        return None

    def _run_step_create_venv(self, python : str, target_dir : str) -> None:
        """Step 1: Create a venv at the target directory."""
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_process_output)
        self._process.finished.connect(self._on_venv_created)
        self._process.start(python, ['-m', 'venv', target_dir])

    def _on_venv_created(self, exit_code : int, exit_status : QProcess.ExitStatus) -> None:
        """After venv creation, install torch."""
        if exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
            self._log(_("Error: Virtual environment creation failed (exit code {code}).").format(code=exit_code))
            self._installation_failed = True
            self._step_status_label.setText(_("Step 1 could not be completed. Check the log and try again."))
            self._show_page(2)
            return

        self._log(_("Virtual environment created successfully."))
        self._step_status_label.setText(_("Step 2 of 2: Installing the Torch build for your hardware..."))
        self._log(_("Installing Torch (this may take several minutes)..."))
        self._run_step_install_torch()

    def _run_step_install_torch(self) -> None:
        """Step 2: Install torch into the venv via pip."""
        target_dir = self._target_dir

        # Determine pip path
        if sys.platform == 'win32':
            pip = os.path.join(target_dir, 'Scripts', 'pip')
        else:
            pip = os.path.join(target_dir, 'bin', 'pip')

        args = ['install', 'torch']

        # Add index URL from hardware detection
        if self._hardware and self._hardware.index_url:
            args.extend(['--index-url', self._hardware.index_url])

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_process_output)
        self._process.finished.connect(self._on_torch_installed)
        self._process.start(pip, args)

    def _on_torch_installed(self, exit_code : int, exit_status : QProcess.ExitStatus) -> None:
        """After torch install, validate the result."""
        if exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
            self._log(_("Error: Torch installation failed (exit code {code}). Check the output above for details.").format(code=exit_code))
            self._installation_failed = True
            self._step_status_label.setText(_("Step 2 could not be completed. Check the log and try again."))
            self._show_page(2)
            return

        self._log(_("Torch installed successfully."))
        self._step_status_label.setText(_("Installation finished. Verifying the Torch environment..."))
        self._validate_and_accept(self._target_dir)

    def _validate_and_accept(self, directory : str) -> None:
        """Validate that the directory contains a usable torch installation.

        Checks for the torch package, then validates ABI compatibility against
        the frozen build's metadata (when present).  Incompatibilities are
        surfaced here rather than deferring to a crash after restart.
        """
        root = Path(directory).expanduser()

        if has_torch_package(root):
            self._log(_("Validated Torch installation at {path}.").format(path=directory))
        else:
            self._log(_("Warning: could not detect a torch package in {path}.").format(path=directory))
            self._log(_("The directory may still be valid. You can accept it or try installing again."))

        # Check ABI compatibility when frozen metadata is available
        metadata_path = find_compatibility_metadata()
        if metadata_path is not None:
            try:
                metadata = read_compatibility_metadata(metadata_path)
                compatibility = metadata["compatibility"]
                if isinstance(compatibility, dict):
                    actual = build_current_compatibility()
                    check_compatibility(actual, compatibility)
                self._log(_("ABI compatibility check passed."))
            except (ValueError, RuntimeError) as error:
                self._log(_("Warning: {error}").format(error=error))
                self._log(_("The installation may not work with this application."))

        self.chosen_path = directory
        self._installation_complete = True
        self._installation_failed = False
        self._completion_path_label.setText(_("Selected environment: {path}").format(path=directory))
        self._completion_summary.setText(_("Torch is installed and the environment has been selected."))
        self._step_status_label.setText(_("Installation complete. Click Continue to review the selected environment."))
        self._show_page(2)

    def _on_accept(self) -> None:
        """Show a restart reminder and accept the selected environment."""
        if not self.chosen_path:
            return

        QMessageBox.information(
            self,
            _("Restart Required"),
            _("The application needs to be restarted for the new Torch installation to take effect."),
        )
        self.accept()

    def reject(self) -> None:
        """Stop an active installer before closing the dialog."""
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1000)
        super().reject()

    def _on_process_output(self) -> None:
        """Append process stdout/stderr to the log widget."""
        if self._process:
            text = bytes(self._process.readAllStandardOutput().data()).decode('utf-8', errors='replace')
            self._log_output.append(text.rstrip())

    def _log(self, message : str) -> None:
        """Append a message to the log widget."""
        self._log_output.append(message)
        logging.info(message)
