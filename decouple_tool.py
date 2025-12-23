import os
import sys
import json
import time
import threading  # 【新增】用于线程同步等待
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
    progress_updated = Signal(int, str)  # 进度信号
    finished_success = Signal(str)       # 成功信号
    finished_error = Signal(str)         # 失败信号
    request_confirmation = Signal(str, str) # 【新增】请求确认信号 (标题, 内容)
    
    def __init__(self, dirs, use_cache_override=None):
        super().__init__()
        self.dirs = dirs 
        self.use_cache_override = use_cache_override 
        self._is_cancelled = False
        
        # 线程同步工具
        self._confirm_event = threading.Event()
        self._confirm_result = False

    def cancel(self):
        self._is_cancelled = True
        # 如果线程正阻塞在等待确认中，需要唤醒它以便它能检测到取消标志并退出
        self._confirm_result = False 
        self._confirm_event.set()

    def _wait_for_user_choice(self, title, message):
        """辅助函数：发送信号给UI并阻塞等待结果"""
        self._confirm_event.clear() # 重置信号灯
        self.request_confirmation.emit(title, message) # 通知UI弹窗
        self._confirm_event.wait() # 阻塞等待UI唤醒
        return self._confirm_result

    def run(self):
        try:
            dir_rgb, dir_input, dir_output = self.dirs
            black_level = 0
            
            # --- Step 1: 准备矩阵 ---
            self.progress_updated.emit(0, "步骤 1/4: 准备校正矩阵...")
            matrix_path = os.path.join(dir_rgb, "calibration_matrix.npy")
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

                files = [f for f in os.listdir(dir_rgb) if f.lower().endswith(('.tif', '.tiff'))]
                if len(files) != 3:
                    raise ValueError(f"RGB 文件夹必须包含且仅包含 3 张 TIFF 图片，当前找到 {len(files)} 张")
                
                vecs = []
                file_names = [] # 用于显示的文名列表
                
                for idx, f in enumerate(files):
                    if self._is_cancelled: return
                    self.progress_updated.emit(0, f"正在读取校正图片: {f} ...")
                    
                    path = os.path.join(dir_rgb, f)
                    vec = self.get_roi_average(path, black_level)
                    vecs.append(vec)
                    file_names.append(f)
                
                vecs = np.array(vecs).T 
                idx_r = np.argmax(vecs[0, :])
                idx_g = np.argmax(vecs[1, :])
                idx_b = np.argmax(vecs[2, :])
                
                # 【新增】构造确认信息并暂停等待用户
                msg_R = f"【红色文件 (R)】: {file_names[idx_r]}\n   识别均值: R={vecs[0, idx_r]:.0f}, G={vecs[1, idx_r]:.0f}, B={vecs[2, idx_r]:.0f}"
                msg_G = f"【绿色文件 (G)】: {file_names[idx_g]}\n   识别均值: R={vecs[0, idx_g]:.0f}, G={vecs[1, idx_g]:.0f}, B={vecs[2, idx_g]:.0f}"
                msg_B = f"【蓝色文件 (B)】: {file_names[idx_b]}\n   识别均值: R={vecs[0, idx_b]:.0f}, G={vecs[1, idx_b]:.0f}, B={vecs[2, idx_b]:.0f}"
                
                full_msg = f"自动识别结果如下，请确认是否正确：\n\n{msg_R}\n\n{msg_G}\n\n{msg_B}"
                
                # 阻塞等待主线程弹窗结果
                if not self._wait_for_user_choice("确认校正信息", full_msg):
                    # 用户选择了 No 或 Cancel
                    if not self._is_cancelled: # 如果不是被停止按钮取消的，就是用户在弹窗点否
                        self.finished_error.emit("用户取消处理")
                    return 

                # 用户确认后继续
                M_obs = np.column_stack((vecs[:, idx_r], vecs[:, idx_g], vecs[:, idx_b]))
                if np.linalg.cond(M_obs) > 1e15:
                    raise ValueError("观测矩阵奇异，无法计算")
                
                M_inv = np.linalg.inv(M_obs)
                row_sums = M_inv.sum(axis=1, keepdims=True)
                M_Final = M_inv / row_sums
                
                np.save(matrix_path, M_Final)

            # --- Step 2: 批量处理 ---
            self.progress_updated.emit(10, "步骤 2/4: 正在处理图片...")
            
            input_files = [f for f in os.listdir(dir_input) if f.lower().endswith(('.tif', '.tiff'))]
            total = len(input_files)
            if total == 0: raise ValueError("Input 文件夹为空")

            for i, fname in enumerate(input_files):
                if self._is_cancelled: return
                
                in_path = os.path.join(dir_input, fname)
                out_path = os.path.join(dir_output, fname)
                
                self.process_image(in_path, out_path, M_Final, black_level)
                
                prog = int(10 + (i + 1) / total * 80)
                self.progress_updated.emit(prog, f"正在处理: {fname}")

            # --- Step 3: Contact Sheet ---
            if self._is_cancelled: return
            self.progress_updated.emit(90, "步骤 3/4: 生成缩略图总览...")
            self.create_contact_sheet(dir_output)
            self.progress_updated.emit(100, "完成")

            self.finished_success.emit(dir_output)

        except Exception as e:
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

    def create_contact_sheet(self, output_dir):
        files = [f for f in os.listdir(output_dir) if f.lower().endswith(('.tif', '.tiff')) 
                 and "contactsheet" not in f.lower()]
        if not files: return
        
        imgs = []
        max_w, max_h = 0, 0
        
        for f in files:
            path = os.path.join(output_dir, f)
            img = tifffile.imread(path)
            # 缩小 5 倍 (原为 10 倍)
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
        
        save_path = os.path.join(output_dir, "contactsheet.tiff")
        tifffile.imwrite(save_path, contact_sheet, compression='zlib')


# =========================================================================
# 主窗口 (PySide6)
# =========================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("光源-CMOS去串扰工具")
        self.setFixedSize(800, 350)
        
        self.dir_rgb = ""
        self.dir_input = ""
        self.dir_output = ""
        self.worker = None
        self.is_running = False
        
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
            try:
                os.makedirs(config_dir)
            except:
                return os.path.join(os.path.expanduser("~"), ".decouple_tool_config.json")
        return os.path.join(config_dir, "config.json")

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # 1. 路径设置组
        group_box = QGroupBox("路径设置")
        grid_layout = QGridLayout(group_box)
        grid_layout.setSpacing(10)
        
        grid_layout.addWidget(QLabel("RGB 校正文件夹:"), 0, 0)
        self.edit_rgb = QLineEdit()
        grid_layout.addWidget(self.edit_rgb, 0, 1)
        btn_rgb = QPushButton("浏览...")
        btn_rgb.clicked.connect(self.browse_rgb)
        grid_layout.addWidget(btn_rgb, 0, 2)

        grid_layout.addWidget(QLabel("Input 待处理文件夹:"), 1, 0)
        self.edit_input = QLineEdit()
        grid_layout.addWidget(self.edit_input, 1, 1)
        btn_input = QPushButton("浏览...")
        btn_input.clicked.connect(self.browse_input)
        grid_layout.addWidget(btn_input, 1, 2)

        grid_layout.addWidget(QLabel("Output 输出文件夹:"), 2, 0)
        self.edit_output = QLineEdit()
        grid_layout.addWidget(self.edit_output, 2, 1)
        btn_output = QPushButton("浏览...")
        btn_output.clicked.connect(self.browse_output)
        grid_layout.addWidget(btn_output, 2, 2)
        
        main_layout.addWidget(group_box)

        # 2. 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        main_layout.addWidget(self.progress_bar)

        # 3. 状态标签
        self.status_label = QLabel("就绪")
        main_layout.addWidget(self.status_label)

        # 4. 按钮区域
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
            self.btn_action.setStyleSheet("""
                QPushButton {
                    font-size: 14px; font-weight: bold;
                    background-color: #E0E0E0; color: black;
                    border: 1px solid #C0C0C0; border-radius: 6px;
                }
                QPushButton:hover { background-color: #D0D0D0; }
            """)
        else:
            self.btn_action.setText("开始处理")
            self.btn_action.setStyleSheet("""
                QPushButton {
                    font-size: 14px; font-weight: bold;
                    background-color: #007AFF; color: white;
                    border: none; border-radius: 6px;
                }
                QPushButton:hover { background-color: #0069D9; }
                QPushButton:pressed { background-color: #0051A8; }
                QPushButton:disabled { background-color: #CCCCCC; }
            """)

    def browse_rgb(self):
        path = QFileDialog.getExistingDirectory(self, "选择 RGB 校正文件夹", self.edit_rgb.text())
        if path: self.edit_rgb.setText(path)

    def browse_input(self):
        path = QFileDialog.getExistingDirectory(self, "选择 Input 文件夹", self.edit_input.text())
        if path: self.edit_input.setText(path)

    def browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择 Output 文件夹", self.edit_output.text())
        if path: self.edit_output.setText(path)

    def load_settings(self):
        defaults = {
            "rgb": os.path.join(os.getcwd(), "RGB"),
            "input": os.path.join(os.getcwd(), "input"),
            "output": os.path.join(os.getcwd(), "output")
        }
        cfg_path = self.get_standard_config_path()
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    defaults.update(data)
            except: pass
        self.edit_rgb.setText(defaults["rgb"])
        self.edit_input.setText(defaults["input"])
        self.edit_output.setText(defaults["output"])

    def save_settings(self):
        data = {
            "rgb": self.edit_rgb.text(),
            "input": self.edit_input.text(),
            "output": self.edit_output.text()
        }
        cfg_path = self.get_standard_config_path()
        try:
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"保存配置失败: {e}")

    # --- 核心控制 ---
    def toggle_process(self):
        if not self.is_running:
            self.start_process()
        else:
            self.stop_process()

    def start_process(self):
        self.dir_rgb = self.edit_rgb.text()
        self.dir_input = self.edit_input.text()
        self.dir_output = self.edit_output.text()
        self.save_settings()

        if not all([self.dir_rgb, self.dir_input, self.dir_output]):
            QMessageBox.critical(self, "错误", "路径不能为空")
            return
        
        if not os.path.exists(self.dir_output):
            try:
                os.makedirs(self.dir_output)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法创建输出目录:\n{e}")
                return

        # 主线程检查缓存
        matrix_path = os.path.join(self.dir_rgb, "calibration_matrix.npy")
        use_cache = None
        if os.path.exists(matrix_path):
            mod_time = time.ctime(os.path.getmtime(matrix_path))
            reply = QMessageBox.question(
                self, "发现缓存", 
                f"发现已存在的校正文件：\n修改时间: {mod_time}\n\n是否直接使用？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            use_cache = (reply == QMessageBox.StandardButton.Yes)

        self.worker = ProcessingWorker([self.dir_rgb, self.dir_input, self.dir_output], use_cache_override=use_cache)
        self.worker.progress_updated.connect(self.on_worker_progress)
        self.worker.finished_success.connect(self.on_worker_success)
        self.worker.finished_error.connect(self.on_worker_error)
        # 【新增】连接确认请求信号
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
        
        if running:
            self.progress_bar.setValue(0)
        else:
            self.btn_action.setEnabled(True)
            self.status_label.setText("就绪")

    @Slot(int, str)
    def on_worker_progress(self, val, msg):
        self.progress_bar.setValue(val)
        self.status_label.setText(msg)

    @Slot(str, str)
    def on_worker_request_confirmation(self, title, msg):
        """处理来自 Worker 的弹窗请求"""
        reply = QMessageBox.question(
            self, title, msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if self.worker:
            self.worker._confirm_result = (reply == QMessageBox.StandardButton.Yes)
            self.worker._confirm_event.set() # 唤醒 Worker

    @Slot(str)
    def on_worker_success(self, output_dir):
        self.set_ui_running(False)
        QMessageBox.information(self, "完成", "处理完毕")
        if sys.platform == 'win32':
            os.startfile(output_dir)
        elif sys.platform == 'darwin':
            os.system(f'open "{output_dir}"')
        else:
            os.system(f'xdg-open "{output_dir}"')

    @Slot(str)
    def on_worker_error(self, err_msg):
        self.set_ui_running(False)
        if "用户取消" not in err_msg:
             QMessageBox.critical(self, "错误", f"发生错误:\n{err_msg}")
        else:
            self.status_label.setText("已取消")

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
