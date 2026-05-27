import os
import json

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QProgressBar, QFileDialog, 
    QMessageBox, QGroupBox, QGridLayout, QComboBox, QFrame,
    QScrollArea, QToolButton, QSizePolicy, QCheckBox
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QIcon

from .calibration import (
    format_cache_created_date,
    get_calibration_matrix_path,
    get_standard_config_path,
    validate_input_image_files,
    validate_rgb_calibration_files,
)
from .icc import CUSTOM_ICC_OPTION, ICC_PROFILE_FILES
from .paths import get_app_base_path
from .raw_convert import RAW_MODE_AUTO, RAW_MODE_DNG, RAW_MODE_LIBRAW, image_file_filter
from .worker import ProcessingWorker

RAW_MODE_LABELS = {
    RAW_MODE_AUTO:   "自动",
    RAW_MODE_DNG:    "Adobe DNG Converter（推荐）",
    RAW_MODE_LIBRAW: "libraw（免安装）",
}

DEFAULT_RAW_MODE = RAW_MODE_AUTO


def raw_mode_from_label(label):
    return next((k for k, v in RAW_MODE_LABELS.items() if v == label), DEFAULT_RAW_MODE)


class FileCard(QFrame):
    remove_requested = Signal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.setObjectName("fileCard")
        self.setFixedSize(104, 56)

        self.body = QFrame(self)
        self.body.setObjectName("fileCardBody")
        self.body.setGeometry(0, 4, 100, 52)

        self.name_line1 = QLabel(self.body)
        self.name_line1.setObjectName("fileName")
        self.name_line1.setToolTip(path)
        self.name_line1.setWordWrap(False)
        self.name_line1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_line1.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        self.name_line2 = QLabel(self.body)
        self.name_line2.setObjectName("fileName")
        self.name_line2.setToolTip(path)
        self.name_line2.setWordWrap(False)
        self.name_line2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_line2.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        remove_button = QToolButton(self)
        remove_button.setObjectName("removeButton")
        remove_button.setText("×")
        remove_button.setFixedSize(11, 11)
        remove_button.clicked.connect(lambda: self.remove_requested.emit(self.path))

        self._layout_content()

    def _display_lines(self, width):
        stem = os.path.splitext(os.path.basename(self.path))[0]
        if not stem:
            stem = os.path.basename(self.path)

        metrics = self.fontMetrics()
        if metrics.horizontalAdvance(stem) <= width:
            return metrics.elidedText(stem, Qt.TextElideMode.ElideRight, width), ""

        return self._split_elided(stem, width)

    def _split_elided(self, stem, width):
        separators = ["_", "-", " "]
        parts = None
        separator = ""
        for sep in separators:
            if sep in stem:
                parts = stem.split(sep)
                separator = sep
                break

        if parts:
            midpoint = max(1, len(parts) // 2)
            first = separator.join(parts[:midpoint])
            second = separator.join(parts[midpoint:])
        else:
            midpoint = len(stem) // 2
            first = stem[:midpoint]
            second = stem[midpoint:]

        metrics = self.fontMetrics()
        return (
            metrics.elidedText(first, Qt.TextElideMode.ElideRight, width),
            metrics.elidedText(second, Qt.TextElideMode.ElideRight, width),
        )

    def _layout_content(self):
        self.body.setGeometry(0, 4, 100, 52)

        button = self.findChild(QToolButton, "removeButton")
        if button:
            button.move(self.width() - button.width() - 2, 0)
            button.raise_()

        text_x = 3
        text_width = self.body.width() - text_x * 2
        line1, line2 = self._display_lines(text_width)

        self.name_line1.setText(line1)
        self.name_line2.setText(line2)

        if line2:
            self.name_line1.setGeometry(text_x, 13, text_width, 14)
            self.name_line2.setGeometry(text_x, 27, text_width, 14)
            self.name_line2.show()
        else:
            self.name_line1.setGeometry(text_x, 18, text_width, 16)
            self.name_line2.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_content()


class FileDropArea(QFrame):
    files_added = Signal(list)

    def __init__(self, placeholder, visible_rows=3):
        super().__init__()
        self._files = []
        self._placeholder = placeholder
        self._cards_per_row = 6
        self.setAcceptDrops(True)
        self.setObjectName("fileDropArea")
        self.setFixedHeight(self._height_for_rows(visible_rows))

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("fileScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.viewport().setStyleSheet("background: transparent;")

        self.content = QWidget()
        self.content.setObjectName("fileDropContent")
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll_area.setWidget(self.content)

        self.placeholder_label = QLabel(placeholder)
        self.placeholder_label.setObjectName("dropPlaceholder")
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setWordWrap(True)

        outer_layout.addWidget(self.scroll_area)
        outer_layout.addWidget(self.placeholder_label)
        self._refresh()

    def _height_for_rows(self, rows):
        card_height = 56
        vertical_gap = 8
        vertical_padding = 20
        return vertical_padding + rows * card_height + max(0, rows - 1) * vertical_gap

    def files(self):
        return list(self._files)

    def set_files(self, files):
        self._files = self._dedupe(files)
        self._refresh()

    def add_files(self, files):
        merged = self._files + [path for path in files if path]
        self._files = self._dedupe(merged)
        self._refresh()

    def set_placeholder(self, text):
        self._placeholder = text
        self.placeholder_label.setText(text)
        self._refresh()

    def _dedupe(self, files):
        seen = set()
        deduped = []
        for path in files:
            normalized = os.path.abspath(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(path)
        return deduped

    def _remove_file(self, path):
        self._files = [item for item in self._files if os.path.abspath(item) != os.path.abspath(path)]
        self._refresh()

    def _refresh(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        has_files = bool(self._files)
        self.scroll_area.setVisible(has_files)
        self.placeholder_label.setVisible(not has_files)
        self.placeholder_label.setText(self._placeholder)

        for index, path in enumerate(self._files):
            card = FileCard(path)
            card.remove_requested.connect(self._remove_file)
            row = index // self._cards_per_row
            col = index % self._cards_per_row
            self.grid.addWidget(card, row, col)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile() and os.path.isfile(url.toLocalFile())
        ]
        if paths:
            self.files_added.emit(paths)
            event.acceptProposedAction()


# =========================================================================
# 主窗口 (PySide6)
# =========================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("光源-CMOS去串扰工具")
        self.setFixedSize(980, 560)
        
        self.rgb_files = []
        self.input_files = []
        self.dir_output = ""
        self.dir_contactsheet = "" 
        self.worker = None
        self.is_running = False
        self.last_input_dir = "" # 【新增】用于记忆上次文件选择目录
        self.last_rgb_dir = ""
        self.last_icc_dir = ""
        self.custom_icc_path = ""
        self.last_noncustom_icc_mode = "none"
        self.calibration_dialog = None
        
        self._setup_icon()
        self.setup_ui()
        self.load_settings()

    def _setup_icon(self):
        base_path = get_app_base_path()
        icon_path = os.path.join(base_path, "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def get_standard_config_path(self):
        return get_standard_config_path()

    def setup_ui(self):
        central_widget = QWidget()
        central_widget.setObjectName("mainWindow")
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(24, 14, 24, 14)
        main_layout.setSpacing(10)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        group_box = QGroupBox()
        group_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid_layout = QGridLayout(group_box)
        grid_layout.setContentsMargins(14, 18, 14, 14)
        grid_layout.setHorizontalSpacing(12)
        grid_layout.setVerticalSpacing(10)
        grid_layout.setColumnStretch(1, 1)
        grid_layout.setColumnMinimumWidth(2, 96)
        
        grid_layout.addWidget(QLabel("Input 待处理文件:"), 0, 0)
        self.input_drop = FileDropArea("拖拽或浏览添加待处理图片", visible_rows=3)
        self.input_drop.files_added.connect(self.add_input_files)
        grid_layout.addWidget(self.input_drop, 0, 1)
        self.btn_input = QPushButton("浏览文件...")
        self.btn_input.setFixedWidth(96)
        self.btn_input.clicked.connect(self.browse_input_files) 
        grid_layout.addWidget(self.btn_input, 0, 2)

        grid_layout.addWidget(QLabel("解耦矩阵:"), 1, 0)
        matrix_layout = QHBoxLayout()
        matrix_layout.setContentsMargins(0, 0, 0, 0)
        matrix_layout.setSpacing(10)
        self.chk_use_existing_matrix = QCheckBox()
        self.chk_use_existing_matrix.toggled.connect(self.on_use_existing_matrix_toggled)
        self.btn_rgb = QPushButton("选择 RGB 文件")
        self.btn_rgb.setObjectName("rgbButton")
        self.btn_rgb.setFixedSize(104, 28)
        self.btn_rgb.clicked.connect(self.browse_rgb)
        matrix_layout.addWidget(self.chk_use_existing_matrix)
        matrix_layout.addWidget(self.btn_rgb)
        matrix_layout.addStretch()
        grid_layout.addLayout(matrix_layout, 1, 1, 1, 2)
        grid_layout.setRowMinimumHeight(1, 32)

        grid_layout.addWidget(QLabel("Output 输出文件夹:"), 2, 0)
        self.edit_output = QLineEdit()
        grid_layout.addWidget(self.edit_output, 2, 1)
        self.btn_output = QPushButton("浏览...")
        self.btn_output.setFixedWidth(96)
        self.btn_output.clicked.connect(self.browse_output)
        grid_layout.addWidget(self.btn_output, 2, 2)

        grid_layout.addWidget(QLabel("缩略图 输出位置:"), 3, 0)
        self.edit_contactsheet = QLineEdit()
        grid_layout.addWidget(self.edit_contactsheet, 3, 1)
        self.btn_contactsheet = QPushButton("浏览...")
        self.btn_contactsheet.setFixedWidth(96)
        self.btn_contactsheet.clicked.connect(self.browse_contactsheet)
        grid_layout.addWidget(self.btn_contactsheet, 3, 2)

        grid_layout.addWidget(QLabel("输出 ICC:"), 4, 0)
        self.combo_icc = QComboBox()
        self.combo_icc.addItems([
            "none",
            "ACESCG Linear",
            "Kodak2383_Linear",
            "KodakEnduraPremier_Linear",
            CUSTOM_ICC_OPTION,
        ])
        self.combo_icc.activated.connect(self.on_icc_mode_activated)
        grid_layout.addWidget(self.combo_icc, 4, 1, 1, 2)

        grid_layout.addWidget(QLabel("RAW 转换模式:"), 5, 0)
        self.combo_raw_mode = QComboBox()
        for label in RAW_MODE_LABELS.values():
            self.combo_raw_mode.addItem(label)
        grid_layout.addWidget(self.combo_raw_mode, 5, 1, 1, 2)

        main_layout.addWidget(group_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(10)
        main_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪")
        main_layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_action = QPushButton("开始处理")
        self.btn_action.setFixedSize(140, 40)
        self.update_button_style(is_running=False)
        self.btn_action.clicked.connect(self.toggle_process)
        btn_layout.addWidget(self.btn_action)
        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)
        self.setStyleSheet("""
            QWidget#mainWindow {
                background: #1F1F1F;
            }
            QGroupBox {
                background: #2A2A2A;
                border: 1px solid #3D3D3D;
                border-radius: 12px;
                color: #E8E8E8;
                font-weight: 600;
                margin-top: 0px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 6px;
                background: #1F1F1F;
            }
            QLabel {
                color: #E8E8E8;
                font-size: 13px;
            }
            QLineEdit, QComboBox {
                background: #383838;
                border: none;
                border-radius: 8px;
                color: #F2F2F2;
                min-height: 28px;
                padding: 0 10px;
            }
            QComboBox {
                padding-right: 30px;
            }
            QComboBox::drop-down {
                background: transparent;
                border: none;
                width: 28px;
            }
            QComboBox::down-arrow {
                image: none;
                border: none;
            }
            QLineEdit:disabled, QComboBox:disabled {
                background: #333333;
                color: #8A8A8A;
            }
            QPushButton {
                background: #3C3C3C;
                border: none;
                border-radius: 8px;
                color: #F2F2F2;
                min-height: 30px;
                padding: 0 12px;
            }
            QPushButton:hover {
                background: #464646;
            }
            QPushButton:pressed {
                background: #505050;
            }
            QPushButton:disabled {
                background: #333333;
                color: #777777;
            }
            QPushButton#rgbButton {
                min-height: 24px;
                max-height: 24px;
                border-radius: 7px;
                padding: 0 10px;
                font-size: 12px;
            }
            QCheckBox {
                color: #E8E8E8;
                spacing: 6px;
            }
            QProgressBar {
                background: #333333;
                border: none;
                border-radius: 5px;
                color: transparent;
            }
            QProgressBar::chunk {
                background: #0A84FF;
                border-radius: 5px;
            }
            QFrame#fileDropArea {
                background: #333333;
                border: none;
                border-radius: 10px;
            }
            QScrollArea#fileScrollArea {
                background: transparent;
                border: none;
            }
            QWidget#fileDropContent {
                background: transparent;
            }
            QFrame#fileCard {
                background: transparent;
                border: none;
            }
            QFrame#fileCardBody {
                background: #2C2C2C;
                border: 1px solid #4A4A4A;
                border-radius: 8px;
            }
            QLabel#fileName {
                color: #F0F0F0;
                font-size: 11px;
                font-weight: 600;
                padding: 0px;
            }
            QLabel#dropPlaceholder {
                color: #9A9A9A;
                font-size: 13px;
            }
            QToolButton#removeButton {
                background: #3A3A3A;
                color: #E8E8E8;
                border: 1px solid #565656;
                border-radius: 5px;
                font-size: 8px;
                font-weight: bold;
                padding: 0px;
            }
            QToolButton#removeButton:hover {
                background: #484848;
            }
        """)

    def update_button_style(self, is_running):
        if is_running:
            self.btn_action.setText("停止")
            self.btn_action.setStyleSheet("QPushButton { font-size: 14px; font-weight: bold; background-color: #E0E0E0; color: black; border: 1px solid #C0C0C0; border-radius: 6px; }")
        else:
            self.btn_action.setText("开始处理")
            self.btn_action.setStyleSheet("QPushButton { font-size: 14px; font-weight: bold; background-color: #007AFF; color: white; border: none; border-radius: 6px; }")

    def browse_rgb(self):
        start_dir = self.last_rgb_dir if self.last_rgb_dir and os.path.exists(self.last_rgb_dir) else os.getcwd()
        files, _ = QFileDialog.getOpenFileNames(self, "选择 3 个 RGB 校正文件", start_dir, image_file_filter())
        if files:
            self.set_rgb_files(files)

    def browse_input_files(self):
        start_dir = os.getcwd()
        current_files = self.input_drop.files()
        if current_files and os.path.exists(os.path.dirname(current_files[0])):
            start_dir = os.path.dirname(current_files[0])
        elif self.last_input_dir and os.path.exists(self.last_input_dir):
            start_dir = self.last_input_dir

        files, _ = QFileDialog.getOpenFileNames(self, "选择待处理图片 (支持多选)", start_dir, image_file_filter())
        if files:
            self.add_input_files(files)

    def add_input_files(self, files):
        try:
            validate_input_image_files(files)
        except ValueError as e:
            QMessageBox.critical(self, "错误", str(e))
            return
        self.input_drop.add_files(files)
        self.input_files = self.input_drop.files()
        if files:
            self.last_input_dir = os.path.dirname(files[0])

    def set_rgb_files(self, files):
        try:
            files = validate_rgb_calibration_files(files)
        except ValueError as e:
            QMessageBox.critical(self, "错误", str(e))
            return
        self.rgb_files = files
        if files:
            self.last_rgb_dir = os.path.dirname(files[0])
        self.save_settings()
        self.start_calibration(files)

    def refresh_matrix_option(self):
        matrix_path = get_calibration_matrix_path()
        created_date = format_cache_created_date(matrix_path)
        self.chk_use_existing_matrix.blockSignals(True)
        if created_date:
            self.chk_use_existing_matrix.setText(f"沿用现有计算结果({created_date})")
            self.chk_use_existing_matrix.setEnabled(True)
            if not self.chk_use_existing_matrix.isChecked():
                self.chk_use_existing_matrix.setChecked(True)
        else:
            self.chk_use_existing_matrix.setText("沿用现有计算结果(未检测到)")
            self.chk_use_existing_matrix.setChecked(False)
            self.chk_use_existing_matrix.setEnabled(False)
        self.chk_use_existing_matrix.blockSignals(False)
        self.on_use_existing_matrix_toggled(self.chk_use_existing_matrix.isChecked())

    def on_use_existing_matrix_toggled(self, checked):
        self.btn_rgb.setVisible(not checked)

    def start_calibration(self, files):
        matrix_path = get_calibration_matrix_path()
        raw_label = self.combo_raw_mode.currentText()
        raw_mode = raw_mode_from_label(raw_label)
        self.worker = ProcessingWorker(
            files,
            [],
            "",
            "",
            use_cache_override=False,
            matrix_path=matrix_path,
            calibration_only=True,
            confirm_calibration=False,
            raw_mode=raw_mode,
        )
        self.worker.progress_updated.connect(self.on_worker_progress)
        self.worker.finished_success.connect(self.on_calibration_success)
        self.worker.finished_error.connect(self.on_worker_error)
        self.worker.request_confirmation.connect(self.on_worker_request_confirmation)
        self.worker.finished.connect(self.on_worker_finished_cleanup)
        self.set_ui_running(True)
        self.show_calibration_dialog()
        self.worker.start()

    def show_calibration_dialog(self):
        self.hide_calibration_dialog()
        self.calibration_dialog = QMessageBox(self)
        self.calibration_dialog.setWindowTitle("请稍候")
        self.calibration_dialog.setText("求解RGB矫正矩阵中……")
        self.calibration_dialog.setStandardButtons(QMessageBox.StandardButton.NoButton)
        self.calibration_dialog.setIcon(QMessageBox.Icon.Information)
        self.calibration_dialog.setModal(True)
        self.calibration_dialog.show()

    def hide_calibration_dialog(self):
        if self.calibration_dialog:
            self.calibration_dialog.close()
            self.calibration_dialog.deleteLater()
            self.calibration_dialog = None

    def browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择 Output 文件夹", self.edit_output.text())
        if path: self.edit_output.setText(path)

    def browse_contactsheet(self):
        path = QFileDialog.getExistingDirectory(self, "选择缩略图输出位置", self.edit_contactsheet.text())
        if path: self.edit_contactsheet.setText(path)

    def browse_custom_icc(self):
        start_dir = get_app_base_path()
        current_path = self.custom_icc_path.strip()
        if current_path and os.path.exists(os.path.dirname(current_path)):
            start_dir = os.path.dirname(current_path)
        elif self.last_icc_dir and os.path.exists(self.last_icc_dir):
            start_dir = self.last_icc_dir

        path, _ = QFileDialog.getOpenFileName(self, "选择 ICC 文件", start_dir, "ICC Profiles (*.icc *.icm)")
        if path:
            self.last_icc_dir = os.path.dirname(path)
        return path

    def on_icc_mode_activated(self, index):
        mode = self.combo_icc.itemText(index)
        if mode == CUSTOM_ICC_OPTION:
            selected_path = self.browse_custom_icc()
            if selected_path:
                self.custom_icc_path = selected_path
            elif not self.custom_icc_path:
                self.combo_icc.blockSignals(True)
                self.combo_icc.setCurrentText(self.last_noncustom_icc_mode)
                self.combo_icc.blockSignals(False)
                return
        else:
            self.last_noncustom_icc_mode = mode

    def load_settings(self):
        cwd = os.getcwd()
        defaults = {
            "output": os.path.join(cwd, "output"),
            "contactsheet": os.path.join(cwd, "output"),
            "input_dir": "",
            "rgb_files": [],
            "last_rgb_dir": os.path.join(cwd, "RGB"),
            "icc_profile_mode": "none",
            "custom_icc_path": "",
            "raw_mode": DEFAULT_RAW_MODE,
        }
        cfg_path = self.get_standard_config_path()
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    defaults.update(data)
            except: pass
        self.last_rgb_dir = defaults.get("last_rgb_dir") or defaults.get("rgb", "") or os.getcwd()
        self.edit_output.setText(defaults["output"])
        self.edit_contactsheet.setText(defaults.get("contactsheet", defaults["output"]))
        self.last_input_dir = defaults.get("input_dir", "") # 加载上次目录
        self.refresh_matrix_option()
        icc_profile_mode = defaults.get("icc_profile_mode", "none")
        index = self.combo_icc.findText(icc_profile_mode)
        self.combo_icc.setCurrentIndex(index if index >= 0 else 0)
        self.custom_icc_path = defaults.get("custom_icc_path", "").strip()
        if self.custom_icc_path:
            self.last_icc_dir = os.path.dirname(self.custom_icc_path)
        if self.combo_icc.currentText() != CUSTOM_ICC_OPTION:
            self.last_noncustom_icc_mode = self.combo_icc.currentText()

        raw_mode = defaults.get("raw_mode", DEFAULT_RAW_MODE)
        raw_label = RAW_MODE_LABELS.get(raw_mode, RAW_MODE_LABELS[DEFAULT_RAW_MODE])
        idx = self.combo_raw_mode.findText(raw_label)
        self.combo_raw_mode.setCurrentIndex(idx if idx >= 0 else 0)

    def save_settings(self):
        input_files = self.input_drop.files()
        input_dir_to_save = self.last_input_dir
        if input_files:
            input_dir_to_save = os.path.dirname(input_files[0])
            self.last_input_dir = input_dir_to_save

        raw_label = self.combo_raw_mode.currentText()
        raw_mode = raw_mode_from_label(raw_label)

        data = {
            "rgb_files": self.rgb_files,
            "last_rgb_dir": self.last_rgb_dir,
            "input_dir": input_dir_to_save,
            "output": self.edit_output.text(),
            "contactsheet": self.edit_contactsheet.text(),
            "icc_profile_mode": self.combo_icc.currentText(),
            "custom_icc_path": self.custom_icc_path,
            "raw_mode": raw_mode,
        }
        try:
            with open(self.get_standard_config_path(), 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"保存配置失败: {e}")

    def toggle_process(self):
        if not self.is_running: self.start_process()
        else: self.stop_process()

    def start_process(self):
        self.input_files = self.input_drop.files()
        self.dir_output = self.edit_output.text()
        self.dir_contactsheet = self.edit_contactsheet.text()
        icc_mode = self.combo_icc.currentText()
        custom_icc_path = self.custom_icc_path.strip()
        raw_label = self.combo_raw_mode.currentText()
        raw_mode = raw_mode_from_label(raw_label)
        self.save_settings()

        if not all([self.dir_output, self.dir_contactsheet]):
            QMessageBox.critical(self, "错误", "路径不能为空")
            return
        if not self.input_files:
            QMessageBox.critical(self, "错误", "请选择至少一个输入文件")
            return
        
        for d in [self.dir_output, self.dir_contactsheet]:
            if not os.path.exists(d):
                try: os.makedirs(d)
                except Exception as e:
                    QMessageBox.critical(self, "错误", f"无法创建目录:\n{e}")
                    return

        if icc_mode in ICC_PROFILE_FILES:
            icc_path = os.path.join(get_app_base_path(), "icc", ICC_PROFILE_FILES[icc_mode])
            if not os.path.exists(icc_path):
                QMessageBox.critical(self, "错误", f"找不到 ICC 文件:\n{icc_path}")
                return
        elif icc_mode == CUSTOM_ICC_OPTION:
            if not custom_icc_path:
                QMessageBox.critical(self, "错误", "请选择自定义 ICC 文件")
                return
            if not os.path.exists(custom_icc_path):
                QMessageBox.critical(self, "错误", f"找不到 ICC 文件:\n{custom_icc_path}")
                return

        matrix_path = get_calibration_matrix_path()
        if not self.chk_use_existing_matrix.isChecked() or not os.path.exists(matrix_path):
            QMessageBox.critical(self, "错误", "请先选择 RGB 文件并完成解耦矩阵计算")
            return

        self.worker = ProcessingWorker(
            [],
            self.input_files,
            self.dir_output,
            self.dir_contactsheet,
            icc_mode=icc_mode,
            custom_icc_path=custom_icc_path,
            use_cache_override=True,
            matrix_path=matrix_path,
            raw_mode=raw_mode,
        )
        self.worker.progress_updated.connect(self.on_worker_progress)
        self.worker.finished_success.connect(self.on_worker_success)
        self.worker.finished_error.connect(self.on_worker_error)
        self.worker.request_confirmation.connect(self.on_worker_request_confirmation) 
        self.worker.finished.connect(self.on_worker_finished_cleanup)
        self.set_ui_running(True)
        self.worker.start()

    def stop_process(self):
        if self.worker and self.worker.isRunning():
            self.status_label.setText("正在停止...")
            self.btn_action.setEnabled(False) 
            self.worker.cancel()

    def set_ui_running(self, running):
        self.is_running = running
        self.update_button_style(running)
        self.input_drop.setEnabled(not running)
        self.chk_use_existing_matrix.setEnabled(
            (not running) and os.path.exists(get_calibration_matrix_path())
        )
        self.btn_rgb.setEnabled(not running)
        self.btn_input.setEnabled(not running)
        self.edit_output.setEnabled(not running)
        self.edit_contactsheet.setEnabled(not running)
        self.btn_output.setEnabled(not running)
        self.btn_contactsheet.setEnabled(not running)
        self.combo_icc.setEnabled(not running)
        self.combo_raw_mode.setEnabled(not running)
        if running: self.progress_bar.setValue(0)
        else:
            self.btn_action.setEnabled(True)
            self.status_label.setText("就绪")

    @Slot(int, str)
    def on_worker_progress(self, val, msg):
        self.progress_bar.setValue(val)
        self.status_label.setText(msg)

    @Slot(str, str)
    def on_worker_request_confirmation(self, title, msg):
        reply = QMessageBox.question(self, title, msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if self.worker:
            self.worker._confirm_result = (reply == QMessageBox.StandardButton.Yes)
            self.worker._confirm_event.set()

    @Slot(str)
    def on_calibration_success(self, matrix_path):
        self.hide_calibration_dialog()
        self.set_ui_running(False)
        self.refresh_matrix_option()
        self.chk_use_existing_matrix.setChecked(True)
        QMessageBox.information(self, "计算完成", "计算完成")

    @Slot(str)
    def on_worker_success(self, output_dir):
        self.hide_calibration_dialog()
        self.set_ui_running(False)
        QMessageBox.information(self, "完成", "处理完毕")

    @Slot(str)
    def on_worker_error(self, err_msg):
        self.hide_calibration_dialog()
        self.set_ui_running(False)
        if "用户取消" not in err_msg: QMessageBox.critical(self, "错误", f"发生错误:\n{err_msg}")
        else: self.status_label.setText("已取消")

    @Slot()
    def on_worker_finished_cleanup(self):
        self.hide_calibration_dialog()
        if self.is_running:
            self.set_ui_running(False)
            self.status_label.setText("操作已取消")
