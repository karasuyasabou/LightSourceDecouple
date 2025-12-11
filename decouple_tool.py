import os
import sys
import json
import time
import numpy as np
import tifffile
from pathlib import Path

# 引入 PySide6 (Qt) 替代 Tkinter
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QProgressBar, QFileDialog, 
    QMessageBox, QGroupBox, QStyle
)
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QIcon, QPixmap

# =========================================================================
# 后台工作线程 (Worker)
# 负责繁重的图像处理任务，避免阻塞 UI
# =========================================================================
class ProcessingWorker(QThread):
    progress_updated = Signal(int, str)  # 进度信号 (百分比, 状态文本)
    finished_success = Signal(str)       # 成功信号 (输出目录)
    finished_error = Signal(str)         # 失败信号 (错误信息)
    request_confirm = Signal(str, str, dict) # 请求确认信号 (标题, 内容, 数据)

    def __init__(self, dirs, use_cache_decision=None):
        super().__init__()
        self.dirs = dirs # [rgb_dir, input_dir, output_dir]
        self.user_response = None # 用于存储用户对弹窗的反馈
        self.use_cache_decision = use_cache_decision # True/False/None
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            dir_rgb, dir_input, dir_output = self.dirs
            black_level = 0
            
            # --- Step 1: 准备矩阵 ---
            self.progress_updated.emit(0, "步骤 1/4: 准备校正矩阵...")
            matrix_path = os.path.join(dir_rgb, "calibration_matrix.npy")
            M_Final = None

            # 1.1 检查缓存
            if os.path.exists(matrix_path) and self.use_cache_decision is None:
                # 需要主线程询问用户
                mod_time = time.ctime(os.path.getmtime(matrix_path))
                # 发送信号等待主线程处理
                pass 
            
            # 如果主线程决定使用缓存
            if self.use_cache_decision is True and os.path.exists(matrix_path):
                M_Final = np.load(matrix_path)
            
            # 1.2 重新计算
            if M_Final is None:
                if self._is_cancelled: return

                files = [f for f in os.listdir(dir_rgb) if f.lower().endswith(('.tif', '.tiff'))]
                if len(files) != 3:
                    raise ValueError(f"RGB 文件夹必须包含且仅包含 3 张 TIFF 图片，当前找到 {len(files)} 张")
                
                vecs = []
                file_names = []
                
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
                
                prog = int(10 + (i + 1) / total * 80) # 10% -> 90%
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
            # 缩小 10 倍
            img_small = img[::10, ::10, :]
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
        self.setFixedSize(800, 350) # 固定大小
        
        # 确定配置文件路径 (使用标准系统路径)
        self.config_path = self.get_standard_config_path()
        
        # 设置图标
        if hasattr(sys, '_MEIPASS'):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.abspath(".")
        icon_path = os.path.join(base_path, "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # 状态变量
        self.dir_rgb = ""
        self.dir_input = ""
        self.dir_output = ""
        self.worker = None
        self.is_running = False

        self.setup_ui()
        self.load_settings()

    def get_standard_config_path(self):
        """获取跨平台的标准化配置文件路径"""
        app_name = "DecoupleTool"
        
        if sys.platform == 'win32':
            # Windows: %APPDATA%/DecoupleTool
            base_dir = os.environ.get('APPDATA') or os.path.expanduser('~\\AppData\\Roaming')
        elif sys.platform == 'darwin':
            # macOS: ~/Library/Application Support/DecoupleTool
            base_dir = os.path.expanduser('~/Library/Application Support')
        else:
            # Linux: ~/.config/DecoupleTool
            base_dir = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
        
        # 拼接应用文件夹
        config_dir = os.path.join(base_dir, app_name)
        
        # 如果文件夹不存在，自动创建
        if not os.path.exists(config_dir):
            try:
                os.makedirs(config_dir)
            except Exception as e:
                print(f"无法创建配置文件目录: {e}, 回退到临时目录")
                return os.path.join(os.path.expanduser("~"), ".decouple_tool_config.json")
        
        return os.path.join(config_dir, "config.json")

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # 1. 路径选择区域
        group_box = QGroupBox("路径设置")
        group_layout = QGridLayout_Compat(group_box) # 使用自定义辅助函数简化布局
        
        self.edit_rgb = self.create_path_row(group_layout, 0, "RGB 校正文件夹:", self.browse_rgb)
        self.edit_input = self.create_path_row(group_layout, 1, "Input 待处理文件夹:", self.browse_input)
        self.edit_output = self.create_path_row(group_layout, 2, "Output 输出文件夹:", self.browse_output)
        
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
        
        # 开始/停止按钮 (参考 PySide6 风格)
        self.btn_action = QPushButton("开始处理")
        self.btn_action.setMinimumWidth(120)
        self.btn_action.setMinimumHeight(40)
        # 设置一点样式
        self.btn_action.setStyleSheet("""
            QPushButton {
                font-size: 14px;
                font-weight: bold;
                background-color: #007AFF; 
                color: white;
                border-radius: 6px;
                padding: 5px;
            }
            QPushButton:pressed {
                background-color: #005ecb;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.btn_action.clicked.connect(self.toggle_process)
        btn_layout.addWidget(self.btn_action)

        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

    def create_path_row(self, layout, row, label_text, callback):
        label = QLabel(label_text)
        edit = QLineEdit()
        btn = QPushButton("浏览...")
        btn.clicked.connect(callback)
        
        layout.addWidget(label, row, 0)
        layout.addWidget(edit, row, 1)
        layout.addWidget(btn, row, 2)
        return edit

    # --- 浏览回调 ---
    def browse_rgb(self): self._browse(self.edit_rgb)
    def browse_input(self): self._browse(self.edit_input)
    def browse_output(self): self._browse(self.edit_output)

    def _browse(self, edit_widget):
        current = edit_widget.text()
        start_dir = current if os.path.exists(current) else os.getcwd()
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", start_dir)
        if path:
            edit_widget.setText(path)

    # --- 逻辑控制 ---
    def toggle_process(self):
        if not self.is_running:
            self.start_process()
        else:
            self.stop_process()

    def start_process(self):
        # 保存设置
        self.dir_rgb = self.edit_rgb.text()
        self.dir_input = self.edit_input.text()
        self.dir_output = self.edit_output.text()
        self.save_settings()

        # 验证路径
        if not all([self.dir_rgb, self.dir_input, self.dir_output]):
            QMessageBox.critical(self, "错误", "路径不能为空")
            return
        
        if not os.path.exists(self.dir_output):
            try:
                os.makedirs(self.dir_output)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法创建输出目录: {e}")
                return

        # 检查缓存 (主线程逻辑)
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

        # 启动 Worker
        self.worker = ProcessingWorker(
            [self.dir_rgb, self.dir_input, self.dir_output], 
            use_cache_decision=use_cache
        )
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.finished_success.connect(self.on_success)
        self.worker.finished_error.connect(self.on_error)
        
        self.worker.start()
        self.set_running_state(True)

    def stop_process(self):
        if self.worker and self.worker.isRunning():
            self.status_label.setText("正在停止...")
            self.worker.cancel()
            # 按钮禁用，等待线程自然结束
            self.btn_action.setEnabled(False)

    def set_running_state(self, running):
        self.is_running = running
        if running:
            self.btn_action.setText("停止")
            self.btn_action.setStyleSheet("""
                QPushButton {
                    font-size: 14px; font-weight: bold;
                    background-color: #e0e0e0; color: black;
                    border: 1px solid #c0c0c0; border-radius: 6px; padding: 5px;
                }
                QPushButton:hover { background-color: #d0d0d0; }
            """)
            self.edit_rgb.setEnabled(False)
            self.edit_input.setEnabled(False)
            self.edit_output.setEnabled(False)
        else:
            self.btn_action.setText("开始处理")
            self.btn_action.setEnabled(True)
            self.btn_action.setStyleSheet("""
                QPushButton {
                    font-size: 14px; font-weight: bold;
                    background-color: #007AFF; color: white;
                    border-radius: 6px; padding: 5px;
                }
                QPushButton:pressed { background-color: #005ecb; }
            """)
            self.edit_rgb.setEnabled(True)
            self.edit_input.setEnabled(True)
            self.edit_output.setEnabled(True)
            self.status_label.setText("就绪")
            self.progress_bar.setValue(0)

    def update_progress(self, val, msg):
        self.progress_bar.setValue(val)
        self.status_label.setText(msg)

    def on_success(self, output_dir):
        self.set_running_state(False)
        QMessageBox.information(self, "完成", "处理完毕")
        
        # 打开文件夹
        if sys.platform == 'win32':
            os.startfile(output_dir)
        elif sys.platform == 'darwin':
            os.system(f'open "{output_dir}"')
        else:
            os.system(f'xdg-open "{output_dir}"')

    def on_error(self, err_msg):
        self.set_running_state(False)
        if "用户取消" not in err_msg: # 如果是用户手动取消不弹报错
            QMessageBox.critical(self, "错误", f"发生错误: {err_msg}")
        else:
            self.status_label.setText("已取消")

    # --- 配置读写 ---
    def load_settings(self):
        # 默认路径
        cwd = os.getcwd()
        defaults = {
            "rgb": os.path.join(cwd, "RGB"),
            "input": os.path.join(cwd, "input"),
            "output": os.path.join(cwd, "output")
        }
        
        # 尝试读取配置文件
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                    defaults.update(data)
            except Exception: pass
        
        self.edit_rgb.setText(defaults["rgb"])
        self.edit_input.setText(defaults["input"])
        self.edit_output.setText(defaults["output"])

    def save_settings(self):
        data = {
            "rgb": self.edit_rgb.text(),
            "input": self.edit_input.text(),
            "output": self.edit_output.text()
        }
        try:
            with open(self.config_path, "w", encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception: pass

# 辅助类：因为PySide6的QGridLayout需要addWidget(widget, row, col)
class QGridLayout_Compat:
    def __init__(self, parent_groupbox):
        self.layout = QGridLayout()
        parent_groupbox.setLayout(self.layout)
    def addWidget(self, w, r, c):
        self.layout.addWidget(w, r, c)

from PySide6.QtWidgets import QGridLayout

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
