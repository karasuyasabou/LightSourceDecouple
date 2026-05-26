import os
import sys
import json
import time

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QProgressBar, QFileDialog, 
    QMessageBox, QGroupBox, QGridLayout, QComboBox
)
from PySide6.QtCore import Slot
from PySide6.QtGui import QIcon

from .icc import CUSTOM_ICC_OPTION, ICC_PROFILE_FILES
from .paths import get_app_base_path
from .raw_convert import RAW_MODE_AUTO, RAW_MODE_DNG, RAW_MODE_LIBRAW, image_file_filter
from .worker import ProcessingWorker

RAW_MODE_LABELS = {
    RAW_MODE_AUTO:   "自动（推荐）",
    RAW_MODE_DNG:    "Adobe DNG Converter（画质优先）",
    RAW_MODE_LIBRAW: "libraw（免安装）",
}


# =========================================================================
# 主窗口 (PySide6)
# =========================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("光源-CMOS去串扰工具")
        self.setFixedSize(800, 495)
        
        self.dir_rgb = ""
        self.input_files_str = "" 
        self.dir_output = ""
        self.dir_contactsheet = "" 
        self.worker = None
        self.is_running = False
        self.last_input_dir = "" # 【新增】用于记忆上次文件选择目录
        self.last_icc_dir = ""
        self.custom_icc_path = ""
        self.last_noncustom_icc_mode = "none"
        
        self._setup_icon()
        self.setup_ui()
        self.load_settings()

    def _setup_icon(self):
        base_path = get_app_base_path()
        icon_path = os.path.join(base_path, "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def get_standard_config_path(self):
        app_name = "DecoupleTool"
        if sys.platform == 'win32':
            base = os.environ.get('APPDATA') or os.path.expanduser('~\\AppData\\Roaming')
        elif sys.platform == 'darwin':
            base = os.path.expanduser('~/Library/Application Support')
        else:
            base = os.path.expanduser('~/.config')
        
        config_dir = os.path.join(base, app_name)
        if not os.path.exists(config_dir):
            try: os.makedirs(config_dir)
            except: return os.path.join(os.path.expanduser("~"), ".decouple_tool_config.json")
        return os.path.join(config_dir, "config.json")

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        group_box = QGroupBox("路径设置")
        grid_layout = QGridLayout(group_box)
        grid_layout.setSpacing(10)
        
        grid_layout.addWidget(QLabel("RGB 校正文件夹:"), 0, 0)
        self.edit_rgb = QLineEdit()
        grid_layout.addWidget(self.edit_rgb, 0, 1)
        btn_rgb = QPushButton("浏览...")
        btn_rgb.clicked.connect(self.browse_rgb)
        grid_layout.addWidget(btn_rgb, 0, 2)

        grid_layout.addWidget(QLabel("Input 待处理文件:"), 1, 0)
        self.edit_input = QLineEdit()
        self.edit_input.setPlaceholderText("可选择多个文件，路径以分号分隔")
        grid_layout.addWidget(self.edit_input, 1, 1)
        btn_input = QPushButton("浏览文件...")
        btn_input.clicked.connect(self.browse_input_files) 
        grid_layout.addWidget(btn_input, 1, 2)

        grid_layout.addWidget(QLabel("Output 输出文件夹:"), 2, 0)
        self.edit_output = QLineEdit()
        grid_layout.addWidget(self.edit_output, 2, 1)
        btn_output = QPushButton("浏览...")
        btn_output.clicked.connect(self.browse_output)
        grid_layout.addWidget(btn_output, 2, 2)

        grid_layout.addWidget(QLabel("缩略图 输出位置:"), 3, 0)
        self.edit_contactsheet = QLineEdit()
        grid_layout.addWidget(self.edit_contactsheet, 3, 1)
        btn_contactsheet = QPushButton("浏览...")
        btn_contactsheet.clicked.connect(self.browse_contactsheet)
        grid_layout.addWidget(btn_contactsheet, 3, 2)

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

    def update_button_style(self, is_running):
        if is_running:
            self.btn_action.setText("停止")
            self.btn_action.setStyleSheet("QPushButton { font-size: 14px; font-weight: bold; background-color: #E0E0E0; color: black; border: 1px solid #C0C0C0; border-radius: 6px; }")
        else:
            self.btn_action.setText("开始处理")
            self.btn_action.setStyleSheet("QPushButton { font-size: 14px; font-weight: bold; background-color: #007AFF; color: white; border: none; border-radius: 6px; }")

    def browse_rgb(self):
        path = QFileDialog.getExistingDirectory(self, "选择 RGB 校正文件夹", self.edit_rgb.text())
        if path: self.edit_rgb.setText(path)

    def browse_input_files(self):
        # 优先级：1. 当前输入框里有路径，取其目录 2. 上次保存的目录 (self.last_input_dir) 3. 工作目录
        start_dir = os.getcwd()
        current_text = self.edit_input.text()
        
        if current_text:
            first_file = current_text.split(';')[0].strip()
            if first_file and os.path.exists(os.path.dirname(first_file)):
                start_dir = os.path.dirname(first_file)
        elif self.last_input_dir and os.path.exists(self.last_input_dir):
            start_dir = self.last_input_dir

        files, _ = QFileDialog.getOpenFileNames(self, "选择待处理图片 (支持多选)", start_dir, image_file_filter())
        if files: 
            self.edit_input.setText("; ".join(files))
            # 更新上次目录记忆
            self.last_input_dir = os.path.dirname(files[0])

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
            "rgb": os.path.join(cwd, "RGB"),
            "output": os.path.join(cwd, "output"),
            "contactsheet": os.path.join(cwd, "output"),
            "input_dir": "",
            "icc_profile_mode": "none",
            "custom_icc_path": "",
            "raw_mode": RAW_MODE_AUTO,
        }
        cfg_path = self.get_standard_config_path()
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    defaults.update(data)
            except: pass
        self.edit_rgb.setText(defaults["rgb"])
        self.edit_output.setText(defaults["output"])
        self.edit_contactsheet.setText(defaults.get("contactsheet", defaults["output"]))
        self.last_input_dir = defaults.get("input_dir", "") # 加载上次目录
        icc_profile_mode = defaults.get("icc_profile_mode", "none")
        index = self.combo_icc.findText(icc_profile_mode)
        self.combo_icc.setCurrentIndex(index if index >= 0 else 0)
        self.custom_icc_path = defaults.get("custom_icc_path", "").strip()
        if self.custom_icc_path:
            self.last_icc_dir = os.path.dirname(self.custom_icc_path)
        if self.combo_icc.currentText() != CUSTOM_ICC_OPTION:
            self.last_noncustom_icc_mode = self.combo_icc.currentText()

        raw_mode = defaults.get("raw_mode", RAW_MODE_AUTO)
        raw_label = RAW_MODE_LABELS.get(raw_mode, RAW_MODE_LABELS[RAW_MODE_AUTO])
        idx = self.combo_raw_mode.findText(raw_label)
        self.combo_raw_mode.setCurrentIndex(idx if idx >= 0 else 0)

    def save_settings(self):
        # 尝试从当前输入推断 input_dir，如果没有输入，则保留 self.last_input_dir
        current_text = self.edit_input.text()
        input_dir_to_save = self.last_input_dir
        
        if current_text:
            first_file = current_text.split(';')[0].strip()
            if first_file:
                input_dir_to_save = os.path.dirname(first_file)
                self.last_input_dir = input_dir_to_save

        raw_label = self.combo_raw_mode.currentText()
        raw_mode = next((k for k, v in RAW_MODE_LABELS.items() if v == raw_label), RAW_MODE_AUTO)

        data = {
            "rgb": self.edit_rgb.text(),
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
        self.dir_rgb = self.edit_rgb.text()
        self.input_files_str = self.edit_input.text()
        self.dir_output = self.edit_output.text()
        self.dir_contactsheet = self.edit_contactsheet.text()
        icc_mode = self.combo_icc.currentText()
        custom_icc_path = self.custom_icc_path.strip()
        raw_label = self.combo_raw_mode.currentText()
        raw_mode = next((k for k, v in RAW_MODE_LABELS.items() if v == raw_label), RAW_MODE_AUTO)
        self.save_settings()

        if not all([self.dir_rgb, self.dir_output, self.dir_contactsheet]):
            QMessageBox.critical(self, "错误", "路径不能为空")
            return
        if not self.input_files_str:
            QMessageBox.critical(self, "错误", "请选择至少一个输入文件")
            return
        input_files = [f.strip() for f in self.input_files_str.split(';') if f.strip()]
        
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

        matrix_path = os.path.join(self.dir_rgb, "calibration_matrix.npy")
        use_cache = None
        if os.path.exists(matrix_path):
            mod_time = time.ctime(os.path.getmtime(matrix_path))
            reply = QMessageBox.question(self, "发现缓存", f"发现已存在的校正文件：\n修改时间: {mod_time}\n\n是否直接使用？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            use_cache = (reply == QMessageBox.StandardButton.Yes)

        self.worker = ProcessingWorker(
            self.dir_rgb,
            input_files,
            self.dir_output,
            self.dir_contactsheet,
            icc_mode=icc_mode,
            custom_icc_path=custom_icc_path,
            use_cache_override=use_cache,
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
        self.edit_rgb.setEnabled(not running)
        self.edit_input.setEnabled(not running)
        self.edit_output.setEnabled(not running)
        self.edit_contactsheet.setEnabled(not running)
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
    def on_worker_success(self, output_dir):
        self.set_ui_running(False)
        QMessageBox.information(self, "完成", "处理完毕")

    @Slot(str)
    def on_worker_error(self, err_msg):
        self.set_ui_running(False)
        if "用户取消" not in err_msg: QMessageBox.critical(self, "错误", f"发生错误:\n{err_msg}")
        else: self.status_label.setText("已取消")

    @Slot()
    def on_worker_finished_cleanup(self):
        if self.is_running:
            self.set_ui_running(False)
            self.status_label.setText("操作已取消")
