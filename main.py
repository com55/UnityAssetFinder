import UnityPy
from pathlib import Path
from collections import deque
import multiprocessing
import sys
import os
import shutil
import subprocess
import threading
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QCheckBox, QLabel, QFileDialog, QProgressBar,
    QListWidget, QListWidgetItem, QToolButton, QGroupBox, QMessageBox, QComboBox
)
from PySide6.QtCore import QLocale, QThread, QObject, Signal, QSettings, Qt, QSize, QEvent, QUrl
from PySide6.QtGui import QIcon, QDesktopServices, QGuiApplication

class ResultItemWidget(QWidget):
    widgetClicked = Signal()
    copyFileRequested = Signal(str)

    def __init__(self, file_path, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self.path_edit = QLineEdit(self.file_path, self)
        self.path_edit.setReadOnly(True)
        self.path_edit.setCursorPosition(0)
        self.path_edit.setStyleSheet("QLineEdit { border: none; background: transparent; }")
        self.path_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.path_edit.installEventFilter(self)
        layout.addWidget(self.path_edit, 1) # Give label more space

        # Buttons
        copy_path_btn = QToolButton(self)
        copy_path_btn.setIcon(QIcon.fromTheme(QIcon.ThemeIcon.EditCopy))
        copy_path_btn.setToolTip("Copy Path")
        copy_path_btn.clicked.connect(self._copy_path)
        layout.addWidget(copy_path_btn)
        
        open_explorer_btn = QToolButton(self)
        open_explorer_btn.setIcon(QIcon.fromTheme(QIcon.ThemeIcon.FolderOpen))
        open_explorer_btn.setToolTip("Open file in explorer")
        open_explorer_btn.clicked.connect(self._open_in_explorer)
        layout.addWidget(open_explorer_btn)

        copy_file_btn = QToolButton(self)
        copy_file_btn.setIcon(QIcon.fromTheme(QIcon.ThemeIcon.DocumentSave))
        copy_file_btn.setToolTip("Copy file to...")
        copy_file_btn.clicked.connect(self._copy_file_to)
        layout.addWidget(copy_file_btn)

    def eventFilter(self, watched, event):
        if watched == self.path_edit:
            if event.type() == QEvent.Type.MouseButtonPress:
                self.widgetClicked.emit()
            elif event.type() == QEvent.Type.MouseButtonDblClick:
                self._open_file_with_default_app()
                return True
        return super().eventFilter(watched, event)

    def _open_file_with_default_app(self):
        self.widgetClicked.emit()
        file_path = os.path.normpath(self.file_path)
        try:
            if sys.platform == "win32":
                os.startfile(file_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", file_path], check=False)
            else:
                subprocess.run(["xdg-open", file_path], check=False)
        except Exception:
            QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))

    def _open_in_explorer(self):
        self.widgetClicked.emit()
        file_path = os.path.normpath(self.file_path)
        if sys.platform == "win32":
            subprocess.run(['explorer', '/select,', file_path])
        elif sys.platform == "darwin":  # macOS
            subprocess.run(['open', '-R', file_path])
        else:  # linux variants
            folder = os.path.dirname(file_path)
            QDesktopServices.openUrl(f"file:///{folder}")


    def _copy_path(self):
        self.widgetClicked.emit()
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.file_path)

    def _copy_file_to(self):
        self.widgetClicked.emit()
        self.copyFileRequested.emit(self.file_path)

def process_file(
    file,
    keywords,
    find_in_container=True,
    find_in_name=True,
    find_in_path_id=True
):
    try:
        env = UnityPy.load(str(file))
        for obj in env.objects:
            if find_in_container:
                if obj.container and keywords in obj.container:
                    return str(file)
            if find_in_name:
                peek_name = obj.peek_name()
                if peek_name and keywords in obj.peek_name():
                    return str(file)
            if find_in_path_id:
                if keywords in str(obj.path_id):
                    return str(file)
    except Exception as e:
        # Optionally log the error e
        pass
    return None

class Worker(QObject):
    finished = Signal()
    update = Signal(str)

    def __init__(self, path, keywords, find_in_container, find_in_name, find_in_path_id, extension, cpu_cores):
        super().__init__()
        self.path = path
        self.keywords = keywords
        self.find_in_container = find_in_container
        self.find_in_name = find_in_name
        self.find_in_path_id = find_in_path_id
        self.extension = extension
        self._cpu_lock = threading.Lock()
        self.cpu_cores = max(1, int(cpu_cores))
        self.pool = None
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_event = threading.Event()

    def _get_cpu_cores(self):
        with self._cpu_lock:
            return self.cpu_cores

    def set_cpu_cores(self, cpu_cores):
        with self._cpu_lock:
            self.cpu_cores = max(1, int(cpu_cores))

    def run(self):
        self.pool = None
        try:
            if not (self.path and self.keywords):
                return
            if self._stop_event.is_set():
                return

            files = [str(file) for file in Path(self.path).rglob(f"*.{self.extension}")]
            total_files = len(files)
            if total_files == 0:
                self.update.emit("PROGRESS_MAX:0")
                return

            self.update.emit(f"PROGRESS_MAX:{total_files}")

            pending_files = deque(files)
            in_flight = {}
            completed_count = 0

            while not self._stop_event.is_set():
                if not self._pause_event.is_set():
                    # Hard pause: stop all active worker processes and requeue unfinished work.
                    if in_flight:
                        for file_path in reversed(list(in_flight.keys())):
                            pending_files.appendleft(file_path)
                        in_flight.clear()

                    if self.pool:
                        self.pool.terminate()
                        self.pool.join()
                        self.pool = None

                    while not self._stop_event.is_set() and not self._pause_event.wait(timeout=0.1):
                        pass
                    continue

                if not pending_files and not in_flight:
                    break

                if self.pool is None:
                    self.pool = multiprocessing.Pool(processes=self._get_cpu_cores())

                max_in_flight = self._get_cpu_cores()
                while len(in_flight) < max_in_flight and pending_files and self._pause_event.is_set() and not self._stop_event.is_set():
                    file_path = pending_files.popleft()
                    in_flight[file_path] = self.pool.apply_async(
                        process_file,
                        args=(file_path, self.keywords, self.find_in_container, self.find_in_name, self.find_in_path_id)
                    )

                has_completed = False
                for file_path, res in list(in_flight.items()):
                    if not res.ready():
                        continue

                    has_completed = True
                    try:
                        matched_path = res.get()
                        if matched_path:
                            self.update.emit(f"FOUND:{matched_path}")
                    except Exception as e:
                        print(f"Error processing file: {e}")

                    completed_count += 1
                    self.update.emit(f"PROGRESS_VALUE:{completed_count}")
                    del in_flight[file_path]

                if not has_completed:
                    self._stop_event.wait(timeout=0.05)
        finally:
            if self.pool:
                if self._stop_event.is_set():
                    self.pool.terminate()
                else:
                    self.pool.close()
                self.pool.join()
            self.finished.emit()

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()


class SearchOptionsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_running = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)

        # Path selection
        path_layout = QHBoxLayout()
        self.path_input = QLineEdit(self)
        self.path_input.setPlaceholderText("Enter path to search")
        self.path_input.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.browse_button = QPushButton("Browse...", self)
        path_layout.addWidget(QLabel("Path:"))
        path_layout.addWidget(self.path_input)
        path_layout.addWidget(self.browse_button)
        layout.addLayout(path_layout)

        # Keywords
        keywords_layout = QHBoxLayout()
        self.keywords_input = QLineEdit(self)
        self.keywords_input.setPlaceholderText("Enter keywords")
        self.keywords_input.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        keywords_layout.addWidget(QLabel("Keywords:"))
        keywords_layout.addWidget(self.keywords_input)
        layout.addLayout(keywords_layout)

        # Search options
        options_layout = QHBoxLayout()
        options_layout.addWidget(QLabel("Find in"))

        self.find_in_name_cb = QCheckBox("Name", self)
        self.find_in_name_cb.setChecked(True)

        self.find_in_container_cb = QCheckBox("Container", self)
        self.find_in_container_cb.setChecked(True)

        self.find_in_path_id_cb = QCheckBox("Path ID", self)
        self.find_in_path_id_cb.setChecked(True)

        options_layout.addWidget(self.find_in_name_cb)
        options_layout.addWidget(self.find_in_container_cb)
        options_layout.addWidget(self.find_in_path_id_cb)
        options_layout.addStretch(1)
        layout.addLayout(options_layout)

        # Extra options
        extra_options_layout = QHBoxLayout()
        layout.addLayout(extra_options_layout)

        extra_options_layout.addWidget(QLabel("File Extension:"))
        self.extension_combo = QComboBox(self)
        self.extension_combo.setEditable(True)
        self.extension_combo.addItems(["bundle", "unity3d"])
        self.extension_combo.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        extra_options_layout.addWidget(self.extension_combo, 1)

        extra_options_layout.addWidget(QLabel("CPU Used:"))
        self.cpu_combo = QComboBox(self)
        cpu_count = os.cpu_count() or 1
        for core_count in range(1, cpu_count + 1):
            label = f"{core_count} Core" if core_count == 1 else f"{core_count} Cores"
            self.cpu_combo.addItem(label, core_count)
        self.cpu_combo.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        extra_options_layout.addWidget(self.cpu_combo, 1)

        extra_options_layout.addStretch(2)

        # Search controls
        controls_layout = QHBoxLayout()
        self.main_action_button = QPushButton("Start", self)
        self.main_action_button.setFixedHeight(30)
        controls_layout.addWidget(self.main_action_button)

        self.stop_button = QPushButton("Stop", self)
        self.stop_button.setFixedHeight(30)
        self.stop_button.setVisible(False)
        controls_layout.addWidget(self.stop_button)

        layout.addLayout(controls_layout)

    def set_search_running(self, is_running):
        self._search_running = is_running
        self.path_input.setEnabled(not is_running)
        self.browse_button.setEnabled(not is_running)
        self.keywords_input.setEnabled(not is_running)
        self.find_in_container_cb.setEnabled(not is_running)
        self.find_in_name_cb.setEnabled(not is_running)
        self.find_in_path_id_cb.setEnabled(not is_running)
        self.extension_combo.setEnabled(not is_running)
        self.cpu_combo.setEnabled(not is_running)
        self.main_action_button.setEnabled(True)
        self.main_action_button.setText("Pause" if is_running else "Start")
        self.stop_button.setVisible(is_running)
        self.stop_button.setEnabled(is_running)

    def set_paused(self, is_paused):
        if self._search_running:
            self.main_action_button.setText("Resume" if is_paused else "Pause")
            self.cpu_combo.setEnabled(is_paused)
        else:
            self.main_action_button.setText("Start")

class FindAssetFileApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Unity Asset Finder")
        self.settings = QSettings("com55", "UnityAssetFinderApp")
        self.last_copy_dir = ""
        self.worker = None
        self.worker_thread = None
        self.is_paused = False
        self.search_stopped = False
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Top search options widget
        self.search_options_widget = SearchOptionsWidget(self)
        self.search_options_widget.main_action_button.clicked.connect(self.handle_main_action)
        self.search_options_widget.stop_button.clicked.connect(self.stop_search)
        self.search_options_widget.browse_button.clicked.connect(self.browse_folder)
        self.search_options_widget.extension_combo.currentTextChanged.connect(
            lambda text: self.settings.setValue("last_extension", text)
        )
        self.search_options_widget.cpu_combo.currentIndexChanged.connect(self.on_cpu_cores_changed)
        layout.addWidget(self.search_options_widget)

        # Progress bar
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setValue(0)
        self.progress_bar.setLocale(QLocale.Language.English)
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.set_progress_state("ready")
        layout.addWidget(self.progress_bar)

        # Results
        self.results_list = QListWidget(self)
        layout.addWidget(self.results_list)

    def set_progress_state(self, state):
        if state == "ready":
            chunk_color = "#64748b"
            text = "Ready"
        elif state == "running":
            chunk_color = "#0ea5e9"
            text = "Running %v/%m"
        elif state == "paused":
            chunk_color = "#f59e0b"
            text = "Paused %v/%m"
        elif state == "stopping":
            chunk_color = "#f97316"
            text = "Stopping..."
        elif state == "stopped":
            chunk_color = "#ef4444"
            text = "Stopped"
        elif state == "done":
            chunk_color = "#22c55e"
            text = "Done"
        elif state == "input_error":
            chunk_color = "#ef4444"
            text = "Please enter a path and keywords"
        else:
            chunk_color = "#64748b"
            text = state

        self.progress_bar.setFormat(text)
        self.progress_bar.setStyleSheet(
            f"QProgressBar {{ text-align: center; }} QProgressBar::chunk {{ background-color: {chunk_color}; }}"
        )

    def browse_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Select Folder", self.search_options_widget.path_input.text())
        if folder_path:
            self.search_options_widget.path_input.setText(folder_path)
            self.settings.setValue("last_path", folder_path)

    def load_settings(self):
        last_path = self.settings.value("last_path", "")
        if last_path:
            self.search_options_widget.path_input.setText(str(last_path))
        
        last_extension = self.settings.value("last_extension", "unity3d")
        self.search_options_widget.extension_combo.setCurrentText(str(last_extension))

        cpu_count = os.cpu_count() or 1
        last_cpu_cores = self.settings.value("last_cpu_cores", None)

        if last_cpu_cores is None:
            # Backward compatibility with the older percent-based setting.
            last_cpu_index = self.settings.value("last_cpu_index", 3, type=int)
            cpu_percentages = [0.25, 0.50, 0.75, 1.0]
            mapped_index = max(0, min(int(str(last_cpu_index)), len(cpu_percentages) - 1))
            last_cpu_cores = max(1, int(cpu_count * cpu_percentages[mapped_index]))

        core_value = max(1, min(int(str(last_cpu_cores)), cpu_count))
        core_index = self.search_options_widget.cpu_combo.findData(core_value)
        if core_index >= 0:
            self.search_options_widget.cpu_combo.setCurrentIndex(core_index)
        elif self.search_options_widget.cpu_combo.count() > 0:
            self.search_options_widget.cpu_combo.setCurrentIndex(self.search_options_widget.cpu_combo.count() - 1)

        self.last_copy_dir = self.settings.value("last_copy_dir", "")

    def on_cpu_cores_changed(self):
        core_count = self.search_options_widget.cpu_combo.currentData() or 1
        self.settings.setValue("last_cpu_cores", core_count)

        # While paused, apply new cores for the next pool created on resume.
        if self.worker and self.is_paused:
            self.worker.set_cpu_cores(core_count)

    def handle_main_action(self):
        if self.worker:
            self.toggle_pause()
        else:
            self.start_search()

    def start_search(self):
        path = self.search_options_widget.path_input.text()
        keywords = self.search_options_widget.keywords_input.text()

        if not path or not keywords:
            self.set_progress_state("input_error")
            return

        self.search_options_widget.set_search_running(True)
        self.search_options_widget.set_paused(False)
        self.results_list.clear()
        self.progress_bar.setValue(0)
        self.set_progress_state("running")
        self.is_paused = False
        self.search_stopped = False
        
        extension = self.search_options_widget.extension_combo.currentText()
        cpu_cores = self.search_options_widget.cpu_combo.currentData() or 1
        find_in_container = self.search_options_widget.find_in_container_cb.isChecked()
        find_in_name = self.search_options_widget.find_in_name_cb.isChecked()
        find_in_path_id = self.search_options_widget.find_in_path_id_cb.isChecked()

        self.worker_thread = QThread()
        self.worker = Worker(path, keywords, find_in_container, find_in_name, find_in_path_id, extension, cpu_cores)
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker.update.connect(self.update_output)
        
        self.worker_thread.finished.connect(self.on_search_finished)

        self.worker_thread.start()

    def toggle_pause(self):
        if not self.worker:
            return

        self.is_paused = not self.is_paused
        if self.is_paused:
            self.worker.pause()
            self.set_progress_state("paused")
        else:
            self.worker.resume()
            self.set_progress_state("running")
        self.search_options_widget.set_paused(self.is_paused)

    def stop_search(self):
        if not self.worker:
            return

        self.search_stopped = True
        self.worker.stop()
        self.search_options_widget.main_action_button.setEnabled(False)
        self.search_options_widget.stop_button.setEnabled(False)
        self.set_progress_state("stopping")

    def on_search_finished(self):
        self.search_options_widget.set_search_running(False)
        self.search_options_widget.set_paused(False)
        self.search_options_widget.main_action_button.setEnabled(True)
        if self.search_stopped:
            self.set_progress_state("stopped")
        else:
            self.set_progress_state("done")
        self.is_paused = False
        self.worker = None
        self.worker_thread = None

    def update_output(self, message):
        if message.startswith("PROGRESS_MAX:"):
            max_val = int(message.split(":", 1)[1])
            self.progress_bar.setMaximum(max_val)
        elif message.startswith("PROGRESS_VALUE:"):
            self.progress_bar.setValue(int(message.split(":", 1)[1]))
        elif message.startswith("FOUND:"):
            file_path = message.split(":", 1)[1]
            self.add_result_item(file_path)
        else:
            # For other info messages, could add a status bar later
            print(message) # Or append to a log widget

    def add_result_item(self, file_path):
        item = QListWidgetItem(self.results_list)
        widget = ResultItemWidget(file_path)

        widget.widgetClicked.connect(lambda: self.results_list.setCurrentItem(item))
        widget.copyFileRequested.connect(self.handle_copy_file_request)

        item.setSizeHint(widget.sizeHint())
        self.results_list.addItem(item)
        self.results_list.setItemWidget(item, widget)

    def handle_copy_file_request(self, source_path):
        file_name = os.path.basename(source_path)
        start_path = os.path.join(str(self.last_copy_dir), file_name)

        destination_path, _ = QFileDialog.getSaveFileName(self, "Save File As", start_path)
        
        if destination_path:
            new_dir = os.path.dirname(destination_path)
            self.last_copy_dir = new_dir
            self.settings.setValue("last_copy_dir", new_dir)
            
            try:
                shutil.copy(source_path, destination_path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not copy file: {e}")

    def closeEvent(self, event):
        if hasattr(self, 'worker') and self.worker:
            self.worker.stop()
            if self.worker_thread:
                self.worker_thread.quit()
                self.worker_thread.wait()
        event.accept()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
        app.setStyle('fusion')
    
    window = FindAssetFileApp()
    window.resize(600, 600)
    window.show()
    sys.exit(app.exec())