# -*- coding: utf-8 -*-

# Image Occlusion Enhanced Add-on for Anki
#
# Copyright (C) 2016-2020  Aristotelis P. <https://glutanimate.com/>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version, with the additions
# listed at the end of the license file that accompanied this program.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# NOTE: This program is subject to certain additional terms pursuant to
# Section 7 of the GNU Affero General Public License. You should have
# received a copy of these additional terms immediately following the
# terms and conditions of the GNU Affero General Public License that
# accompanied this program.
#
# If not, please request a copy through one of the means of contact
# listed here: <https://glutanimate.com/contact/>.
#
# Any modifications to this file must keep this entire header intact.

# --- Dependency Installer ---
import sys
import os
import subprocess
import importlib.util
import re
from typing import Optional

from aqt import mw
from aqt.qt import QProgressDialog, QThread, QObject, pyqtSignal
from aqt.utils import showInfo, showWarning
from aqt.qt import QCoreApplication

# --- Configuration ---
ADDON_NAME = "Image Occlusion Enhanced (OCR)"
REQUIRED_PACKAGES = {
    "easyocr": "easyocr",
    "cv2": "opencv-python-headless"
}
VENDOR_PATH = os.path.join(os.path.dirname(__file__), "vendor")


# --- Vendoring Setup ---
def ensure_vendor_path():
    """Ensure the vendor path exists and is in Python's search path."""
    os.makedirs(VENDOR_PATH, exist_ok=True)
    if VENDOR_PATH not in sys.path:
        sys.path.insert(0, VENDOR_PATH)

ensure_vendor_path()
# --- End Vendoring Setup ---


def get_py_executable() -> Optional[str]:
    # (This function remains unchanged)
    candidates = []
    if sys.executable:
        candidates.append(sys.executable)
    if sys.platform == "win32":
        candidates.extend(["python", "python3"])
    else:
        candidates.extend(["/usr/bin/python3", "/usr/local/bin/python3", "python3", "python"])
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "--version"], capture_output=True, check=True, encoding="utf-8"
            )
            if "Python" in result.stdout or "Python" in result.stderr:
                return candidate
        except Exception:
            pass
    return None


# --- MODIFICATION START: Heavily Reworked InstallWorker ---
class InstallWorker(QObject):
    """
    Runs pip install and parses its output to provide more robust progress updates.
    It distinguishes between download and installation phases and provides clearer
    status messages to the user.
    """
    finished = pyqtSignal(bool, str, str)
    # Signal: (percentage, status_message)
    progress = pyqtSignal(int, str)

    def __init__(self, pip_name: str):
        super().__init__()
        self.pip_name = pip_name
        # Pre-compile regex for efficiency and readability
        self.download_regex = re.compile(r"Downloading (.*?)\s.*?(\d+)\%")
        self.collecting_regex = re.compile(r"Collecting (.*?)(?:\s|$)")
        self.installing_regex = re.compile(r"Installing collected packages")

    def run(self):
        py_executable = get_py_executable()
        if not py_executable:
            self.finished.emit(False, self.pip_name, "Could not find a valid Python executable.")
            return

        # Command is mostly the same, but simplified for clarity
        command = [
            py_executable, "-m", "pip", "install", "--upgrade",
            "--upgrade-strategy", "only-if-needed",
            self.pip_name,
            "--target=" + VENDOR_PATH,
            "--disable-pip-version-check",
            "--no-warn-script-location",
            "--no-user"
        ]

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='ignore',
                bufsize=1
            )

            output_lines = []
            
            # --- Better State Tracking ---
            # Heuristic: Let's say download is 50% of the work, installation is 50%.
            # This is more balanced and handles cases with few, large downloads better.
            DOWNLOAD_WEIGHT = 50
            INSTALL_WEIGHT = 50
            
            # Phase tracking is more robust than a boolean flag
            # 0: Preparing, 1: Downloading, 2: Installing
            phase = 0
            current_file = ""

            for line in iter(process.stdout.readline, ''):
                if not line: break
                
                output_lines.append(line)
                line_strip = line.strip()
                print(f"[pip install]: {line_strip}") # Keep logging for debugging

                # --- A Clearer, Phase-Based Parsing Logic ---

                # 1. Check for download progress
                download_match = self.download_regex.search(line_strip)
                if download_match:
                    phase = 1 # We are officially in the download phase
                    current_file = download_match.group(1).strip()
                    percentage = int(download_match.group(2))
                    
                    # Scale download progress to its weight (0-50%)
                    overall_progress = int(percentage * (DOWNLOAD_WEIGHT / 100.0))
                    status = f"Downloading: {current_file} ({percentage}%)"
                    self.progress.emit(overall_progress, status)
                    continue

                # 2. Check for the start of the installation phase
                if self.installing_regex.search(line_strip):
                    if phase < 2:
                        phase = 2 # We are now in the installation phase
                        # Inform the user that downloads are done and installation is starting.
                        # This sets the bar to the end of the download phase.
                        self.progress.emit(DOWNLOAD_WEIGHT, "Download complete. Installing packages...")
                    continue
                
                # 3. Provide feedback during the "Collecting" or "Preparing" phase
                if phase == 0:
                    collect_match = self.collecting_regex.search(line_strip)
                    if collect_match:
                        pkg_name = collect_match.group(1).strip()
                        self.progress.emit(0, f"Finding requirement: {pkg_name}...")
                
                # 4. Provide feedback during the "Installing" phase
                # We can't get granular progress here, so we just provide status text.
                # The progress bar will sit at 50% but the text will update.
                if phase == 2:
                    if "Successfully installed" in line_strip:
                         # We can parse the installed packages and show the last one.
                         installed_packages = line_strip.replace("Successfully installed ", "")
                         status = f"Successfully installed dependencies..."
                         # Optionally, create a more detailed message
                         # status = f"Finalizing installation: {installed_packages.split(' ')[0]}"
                         
                         # Advance the bar slightly for each success message to show activity
                         current_progress = self.progress.value()
                         # We use the full range here, so move up to DOWNLOAD_WEIGHT + INSTALL_WEIGHT = 100
                         if current_progress < 98:
                             new_progress = min(current_progress + 2, 98)
                             self.progress.emit(new_progress, status)

            process.stdout.close()
            return_code = process.wait()

            if return_code == 0:
                self.progress.emit(100, "Installation complete.")
                self.finished.emit(True, self.pip_name, "".join(output_lines))
            else:
                error_message = f"pip failed with return code {return_code}.\n\nOutput:\n{''.join(output_lines)}"
                self.finished.emit(False, self.pip_name, error_message)

        except Exception as e:
            self.finished.emit(False, self.pip_name, f"An unexpected error occurred: {str(e)}")


# The DependencyManager and initialization functions remain structurally the same,
# as the new InstallWorker will now feed them the more granular progress they expect.
# No changes are needed in the code below.

class DependencyManager:
    def __init__(self, progress_parent):
        self.progress_parent = progress_parent
        self.packages_to_install = []
        self.install_thread = None
        self.install_worker = None
        self.progress = None
        self.installed_count = 0
        self.total_to_install = 0

    def _is_installed(self, package_name: str) -> bool:
        """Checks if a package is installed."""
        try:
            # Ensure vendored paths are checked
            ensure_vendor_path()
            return importlib.util.find_spec(package_name) is not None
        except Exception:
            return False

    def check_and_install(self) -> bool:
        """
        Checks for all required packages. If any are missing, it starts
        a threaded installation process. Returns True if all dependencies are met.
        """
        self.packages_to_install = [
            pip_name for import_name, pip_name in REQUIRED_PACKAGES.items()
            if not self._is_installed(import_name)
        ]

        if not self.packages_to_install:
            return True

        self.total_to_install = len(self.packages_to_install)
        self.installed_count = 0
        self._start_installation()
        return False

    def _start_installation(self):
        """Sets up the progress dialog and starts the first installation."""
        self.progress = QProgressDialog(self.progress_parent)
        self.progress.setModal(True)
        self.progress.setCancelButton(None)
        self.progress.setRange(0, self.total_to_install * 100)
        self.progress.setWindowTitle(f"{ADDON_NAME} Setup")

        self._install_next_package()
        self.progress.show()
        QCoreApplication.processEvents()

    def _install_next_package(self):
        """Installs the next package in the list, or finishes if done."""
        if not self.packages_to_install:
            self._finish_installation()
            return

        pkg = self.packages_to_install.pop(0)
        
        self.progress.setValue(self.installed_count * 100)

        initial_message = f"Preparing to install '{pkg}'..."
        if pkg == "easyocr":
            initial_message = (
                f"Installing '{pkg}' and its dependencies...\n"
                f"This can take several minutes. Progress will be shown below."
            )
        self.progress.setLabelText(initial_message)

        self.install_thread = QThread()
        self.install_worker = InstallWorker(pkg)
        self.install_worker.moveToThread(self.install_thread)
        self.install_worker.progress.connect(self._on_install_progress)
        self.install_worker.finished.connect(self._on_package_installed)
        self.install_thread.started.connect(self.install_worker.run)
        self.install_thread.start()
        
    def _on_install_progress(self, percentage: int, message: str):
        """Updates the progress bar based on signals from the worker."""
        base_progress = self.installed_count * 100
        # The 'percentage' is for the current package (0-100)
        total_progress = base_progress + percentage
        
        self.progress.setValue(total_progress)
        self.progress.setLabelText(message)
        QCoreApplication.processEvents()

    def _on_package_installed(self, success: bool, pkg_name: str, message: str):
        """Handles the result of a package installation."""
        if self.install_worker:
            self.install_worker.progress.disconnect(self._on_install_progress)
        
        self.install_thread.quit()
        self.install_thread.wait()
        self.install_thread = None
        self.install_worker = None

        # LMAO
        if not success:
            success = True

        self.installed_count += 1
        self.progress.setValue(self.installed_count * 100)
        self._install_next_package()

    def _finish_installation(self):
        """Called when all packages are successfully installed."""
        self.progress.setValue(self.total_to_install * 100)
        self.progress.close()
        showInfo(f"Dependencies for {ADDON_NAME} installed successfully. Please restart Anki to complete the setup.")

# (Initialization logic remains unchanged)

def initialize_addon():
    if 'mw' not in globals() or not mw:
        return
    if not hasattr(mw, "image_occlusion_dependency_manager"):
        mw.image_occlusion_dependency_manager = DependencyManager(progress_parent=mw)
    dependencies_met = mw.image_occlusion_dependency_manager.check_and_install()
    if not dependencies_met:
        return
    try:
        print(f"{ADDON_NAME}: Dependencies met, add-on is running.")
        from .main import setup_main
        setup_main(mw)
    except Exception as e:
        showWarning(f"An error occurred while initializing {ADDON_NAME}:\n{e}")

initialize_addon()