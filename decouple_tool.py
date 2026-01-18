import os
import sys
import json
import time
import numpy as np
import tifffile
from pathlib import Path

# 使用 PySide6 替代 Tkinter
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QProgressBar, QFileDialog, 
    QMessageBox, QGroupBox, QGridLayout, QStyle
)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QSize, QSettings
from PySide6.QtGui import QIcon, QPixmap, QAction

# =========================================================================
# 后台工作线程 (Worker)
# =========================================================================
class ProcessingWorker(QThread):
    progress_updated = Signal(int, str)  # 进度信号 (百分比, 状态文本)
    finished_success = Signal(str)       # 成功信号 (输出目录)
    finished_error = Signal(str)         # 失败信号 (错误信息)
    request_confirmation = Signal(str, str) # 请求确认信号 (标题, 内容)
    
    def __init__(self, dir_rgb, input_files, dir_output, dir_contactsheet, use_cache_override=None):
        super().__init__()
        self.dir_rgb = dir_rgb
        self.input_files = input_files # 文件路径列表
        self.dir_output = dir_output
        self.dir_contactsheet = dir_contactsheet 
        self.use_cache_override = use_cache_override 
        self._is_cancelled = False
        
        # 线程同步工具
        import threading
        self._confirm_event = threading.Event()
        self._confirm_result = False

    def cancel(self):
        self._is_cancelled = True
        self._confirm_result = False
        self._confirm_event.set()

    def _wait_for_user_choice(self, title, message):
        """辅助函数：发送信号给UI并阻塞等待结果"""
        self._confirm_event.clear()
        self.request_confirmation.emit(title, message)
        self._confirm_event.wait()
        return self._confirm_result

    def run(self):
        try:
            black_level = 0
            
            # --- Step 1: 准备矩阵 ---
            self.progress_updated.emit(0, "步骤 1/4: 准备校正矩阵...")
            matrix_path = os.path.join(self.dir_rgb, "calibration_matrix.npy")
            M_Final = None

            # 1.1 检查缓存策略
            if self.use_cache_override is True and os.path.exists(matrix_path):
                try:
                    M_Final = np.load(matrix_path)
                except Exception as e:
                    print(f"加载缓存失败: {e}")
            
            # 1.2 重新计算
            if M_Final is None:
                if self._is_cancelled: return

                files = [f for f in os.listdir(self.dir_rgb) if f.lower().endswith(('.tif', '.tiff'))]
                if len(files) != 3:
                    raise ValueError(f"RGB 文件夹必须包含且仅包含 3 张 TIFF 图片，当前找到 {len(files)} 张")
                
                vecs = []
                file_names = []
                
                for idx, f in enumerate(files):
                    if self._is_cancelled: return
                    self.progress_updated.emit(0, f"正在读取校正图片: {f} ...")
                    
                    path = os.path.join(self.dir_rgb, f)
                    vec = self.get_roi_average(path, black_level)
                    vecs.append(vec)
                    file_names.append(f)
                
                vecs = np.array(vecs).T 
                idx_r = np.argmax(vecs[0, :])
                idx_g = np.argmax(vecs[1, :])
                idx_b = np.argmax(vecs[2, :])
                
                # 构造确认信息
                msg_R = f"【红色 (R)】: {file_names[idx_r]}\n   均值: R={vecs[0, idx_r]:.0f}, G={vecs[1, idx_r]:.0f}, B={vecs[2, idx_r]:.0f}"
                msg_G = f"【绿色 (G)】: {file_names[idx_g]}\n   均值: R={vecs[0, idx_g]:.0f}, G={vecs[1, idx_g]:.0f}, B={vecs[2, idx_g]:.0f}"
                msg_B = f"【蓝色 (B)】: {file_names[idx_b]}\n   均值: R={vecs[0, idx_b]:.0f}, G={vecs[1, idx_b]:.0f}, B={vecs[2, idx_b]:.0f}"
                full_msg = f"自动识别结果如下，请确认：\n\n{msg_R}\n\n{msg_G}\n\n{msg_B}"
                
                # 阻塞并请求确认
                if not self._wait_for_user_choice("确认校正信息", full_msg):
                    if not self._is_cancelled:
                        self.finished_error.emit("用户取消处理")
                    return

                M_obs = np.column_stack((vecs[:, idx_r], vecs[:, idx_g], vecs[:, idx_b]))
                if np.linalg.cond(M_obs) > 1e15:
                    raise ValueError("观测矩阵奇异，无法计算")
                
                M_inv = np.linalg.inv(M_obs)
                row_sums = M_inv.sum(axis=1, keepdims=True)
                M_Final = M_inv / row_sums
                
                np.save(matrix_path, M_Final)

            # --- Step 2: 批量处理 ---
            self.progress_updated.emit(10, "步骤 2/4: 正在处理图片...")
            
            total = len(self.input_files)
            if total == 0: raise ValueError("未选择输入文件")

            generated_files = [] 

            for i, in_path in enumerate(self.input_files):
                if self._is_cancelled: return
                
                fname = os.path.basename(in_path)
                out_path = os.path.join(self.dir_output, fname)
                
                self.process_image(in_path, out_path, M_Final, black_level)
                generated_files.append(out_path)
                
                prog = int(10 + (i + 1) / total * 80)
                self.progress_updated.emit(prog, f"正在处理: {fname}")

            # --- Step 3: Contact Sheet ---
            if self._is_cancelled: return
            
            if len(generated_files) > 1:
                self.progress_updated.emit(90, "步骤 3/4: 生成缩略图总览...")
                self.create_contact_sheet(generated_files, self.dir_contactsheet)
            else:
                self.progress_updated.emit(90, "步骤 3/4: 单张图片，跳过缩略图...")

            self.progress_updated.emit(100, "完成")
            self.finished_success.emit(self.dir_output)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.finished_error.emit(str(e))

    def get_roi_average(self, path, black_lvl):
        img = tifffile.imread(path)
        arr = img.astype(np.float64)
        arr = arr - black_lvl
        h, w = arr.shape[:2]
        if h > 10 and w > 10:
            roi = arr[int(h*0.4):int(h*0.6), int(w*0.4):int(w*0.6)]
        else:
            roi = arr
        return np.mean(roi, axis=(0, 1))

    def process_image(self, in_path, out_path, M, black_lvl):
        arr = tifffile.imread(in_path).astype(np.float64)
        arr = arr - black_lvl
        h, w, c = arr.shape
        pixels = arr.reshape(-1, 3)
        pixels_corr = pixels @ M.T
        pixels_corr = np.clip(pixels_corr, 0, 65535)
        img_out_arr = pixels_corr.reshape(h, w, c).astype(np.uint16)
        tifffile.imwrite(out_path, img_out_arr, compression='zlib')

    def create_contact_sheet(self, image_paths, output_dir):
        if not image_paths: return
        imgs = []
        max_w, max_h = 0, 0
        
        for path in image_paths:
            if not os.path.exists(path): continue
            img = tifffile.imread(path)
            img_small = img[::5, ::5, :]
            imgs.append(img_small)
            h, w = img_small.shape[:2]
            max_w = max(max_w, w)
            max_h = max(max_h, h)
        
        if not imgs: return
        cols = 6
        rows = int(np.ceil(len(imgs) / cols))
        canvas_w = cols * max_w
        canvas_h = rows * max_h
        contact_sheet = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint16)
        
        for idx, img in enumerate(imgs):
            h, w = img.shape[:2]
            row = idx // cols
            col = idx % cols
            x = col * max_w
            y = row * max_h
            contact_sheet[y:y+h, x:x+w, :] = img
        
        if not os.path.exists(output_dir):
            try: os.makedirs(output_dir)
            except: return

        save_path = os.path.join(output_dir, "contactsheet.tiff")
        tifffile.imwrite(save_path, contact_sheet, compression='zlib')


# =========================================================================
# 主窗口 (PySide6)
# =========================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("光源-CMOS去串扰工具")
        self.setFixedSize(800, 420)
        
        self.dir_rgb = ""
        self.input_files_str = "" 
        self.dir_output = ""
        self.dir_contactsheet = "" 
        self.worker = None
        self.is_running = False
        self.last_input_dir = "" # 【新增】用于记忆上次文件选择目录
        
        self._setup_icon()
        self.setup_ui()
        self.load_settings()

    def _setup_icon(self):
        if hasattr(sys, '_MEIPASS'):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.abspath(".")
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

        files, _ = QFileDialog.getOpenFileNames(self, "选择待处理图片 (支持多选)", start_dir, "Images (*.tif *.tiff)")
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

    def load_settings(self):
        cwd = os.getcwd()
        defaults = {
            "rgb": os.path.join(cwd, "RGB"), 
            "output": os.path.join(cwd, "output"), 
            "contactsheet": os.path.join(cwd, "output"),
            "input_dir": "" # 默认空
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

    def save_settings(self):
        # 尝试从当前输入推断 input_dir，如果没有输入，则保留 self.last_input_dir
        current_text = self.edit_input.text()
        input_dir_to_save = self.last_input_dir
        
        if current_text:
            first_file = current_text.split(';')[0].strip()
            if first_file:
                input_dir_to_save = os.path.dirname(first_file)
                self.last_input_dir = input_dir_to_save

        data = {
            "rgb": self.edit_rgb.text(), 
            "input_dir": input_dir_to_save, 
            "output": self.edit_output.text(), 
            "contactsheet": self.edit_contactsheet.text()
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

        matrix_path = os.path.join(self.dir_rgb, "calibration_matrix.npy")
        use_cache = None
        if os.path.exists(matrix_path):
            mod_time = time.ctime(os.path.getmtime(matrix_path))
            reply = QMessageBox.question(self, "发现缓存", f"发现已存在的校正文件：\n修改时间: {mod_time}\n\n是否直接使用？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            use_cache = (reply == QMessageBox.StandardButton.Yes)

        self.worker = ProcessingWorker(self.dir_rgb, input_files, self.dir_output, self.dir_contactsheet, use_cache_override=use_cache)
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
        if sys.platform == 'win32': os.startfile(output_dir)
        elif sys.platform == 'darwin': os.system(f'open "{output_dir}"')
        else: os.system(f'xdg-open "{output_dir}"')

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

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
