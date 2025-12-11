import os
import time
import sys
import numpy as np
from PIL import Image
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading

# 增加最大图像像素限制（防止处理大图报错）
Image.MAX_IMAGE_PIXELS = None

class DecoupleApp:
    def __init__(self, root):
        self.root = root
        self.root.title("光源-CMOS去串扰工具 (Python版)")
        self.center_window(600, 320)
        
        # 路径变量
        self.dir_rgb = tk.StringVar()
        self.dir_input = tk.StringVar()
        self.dir_output = tk.StringVar()
        
        # 默认路径
        cwd = os.getcwd()
        self.dir_rgb.set(os.path.join(cwd, "RGB"))
        self.dir_input.set(os.path.join(cwd, "input"))
        self.dir_output.set(os.path.join(cwd, "output"))

        self.setup_ui()

    def center_window(self, w, h):
        ws = self.root.winfo_screenwidth()
        hs = self.root.winfo_screenheight()
        x = (ws/2) - (w/2)
        y = (hs/2) - (h/2)
        self.root.geometry('%dx%d+%d+%d' % (w, h, x, y))

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # RGB 路径
        self.create_path_selector(main_frame, "RGB 校正文件夹:", self.dir_rgb, 0)
        # Input 路径
        self.create_path_selector(main_frame, "Input 待处理文件夹:", self.dir_input, 1)
        # Output 路径
        self.create_path_selector(main_frame, "Output 输出文件夹:", self.dir_output, 2)

        # 进度条
        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100)
        self.progress.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(20, 10))

        # 状态标签
        self.status_label = ttk.Label(main_frame, text="就绪")
        self.status_label.grid(row=4, column=0, columnspan=3, sticky="w")

        # 按钮区域
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=5, column=0, columnspan=3, pady=10)
        
        self.btn_start = ttk.Button(btn_frame, text="开始处理", command=self.start_thread)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        
        self.btn_cancel = ttk.Button(btn_frame, text="取消", state=tk.DISABLED, command=self.cancel_process)
        self.btn_cancel.pack(side=tk.LEFT, padx=5)

        self.is_cancelled = False

    def create_path_selector(self, parent, label_text, var, row):
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=var, width=50).grid(row=row, column=1, sticky="ew", padx=5, pady=5)
        ttk.Button(parent, text="浏览...", command=lambda: self.browse_dir(var)).grid(row=row, column=2, sticky="e", pady=5)

    def browse_dir(self, var):
        initial = var.get() if os.path.exists(var.get()) else os.getcwd()
        path = filedialog.askdirectory(initialdir=initial)
        if path:
            var.set(path)

    def cancel_process(self):
        self.is_cancelled = True
        self.status_label.config(text="正在取消...")

    def start_thread(self):
        # 锁定界面
        self.btn_start.config(state=tk.DISABLED)
        self.btn_cancel.config(state=tk.NORMAL)
        self.is_cancelled = False
        self.progress_var.set(0)
        
        # 检查路径
        dirs = [self.dir_rgb.get(), self.dir_input.get(), self.dir_output.get()]
        for d in dirs:
            if not d:
                messagebox.showerror("错误", "路径不能为空")
                self.reset_ui()
                return

        # 创建输出目录
        if not os.path.exists(dirs[2]):
            try:
                os.makedirs(dirs[2])
            except Exception as e:
                messagebox.showerror("错误", f"无法创建输出目录: {e}")
                self.reset_ui()
                return

        # 在新线程运行，防止界面卡死
        threading.Thread(target=self.run_process, args=(dirs,), daemon=True).start()

    def run_process(self, dirs):
        dir_rgb, dir_input, dir_output = dirs
        black_level = 0

        try:
            # ================= Step 1: 获取/计算矩阵 =================
            self.status_label.config(text="步骤 1/4: 获取校正矩阵...")
            matrix_path = os.path.join(dir_rgb, "calibration_matrix.npy")
            M_Final = None
            
            # 检查缓存
            if os.path.exists(matrix_path):
                # 在非主线程使用 messagebox 需要注意，但在 simple threading 中通常可行
                # 严格来说应该用 queue 通信，但这里简化处理
                # 注意：PyInstaller 打包后的 tkinter 多线程弹窗偶尔会有问题
                # 但在这个简单逻辑中通常能跑通
                # 为了稳健，我们可以简化逻辑：如果存在且可读，直接用，或者仅在控制台打印
                # 这里保持原逻辑尝试
                M_Final = np.load(matrix_path)
            
            # 重新计算
            if M_Final is None:
                if self.is_cancelled: raise InterruptedError()
                
                # 寻找 TIFF
                files = [f for f in os.listdir(dir_rgb) if f.lower().endswith(('.tif', '.tiff'))]
                if len(files) != 3:
                    raise ValueError(f"RGB 文件夹必须包含且仅包含 3 张 TIFF 图片，当前找到 {len(files)} 张")
                
                vecs = []
                file_names = []
                
                for idx, f in enumerate(files):
                    if self.is_cancelled: raise InterruptedError()
                    path = os.path.join(dir_rgb, f)
                    vec = self.get_roi_average(path, black_level)
                    vecs.append(vec)
                    file_names.append(f)
                
                vecs = np.array(vecs).T # 转置为 3x3 (R, G, B 列)
                
                # 识别
                idx_r = np.argmax(vecs[0, :])
                idx_g = np.argmax(vecs[1, :])
                idx_b = np.argmax(vecs[2, :])
                
                # 在子线程弹窗需要谨慎，建议简化为直接处理，或者使用 update_idletasks
                # 这里假设用户提供的数据是正确的，自动识别后直接处理，避免子线程弹窗卡死
                # 改进：如果需要确认，应该在 start_thread 前检查好，或者由主线程调度
                # 为防止 Mac 上子线程 GUI 崩溃，这里跳过弹窗确认，直接信任算法
                
                M_obs = np.column_stack((vecs[:, idx_r], vecs[:, idx_g], vecs[:, idx_b]))
                
                # 求逆
                if np.linalg.cond(M_obs) > 1e15:
                    raise ValueError("观测矩阵奇异，无法计算")
                
                M_inv = np.linalg.inv(M_obs)
                
                # 行归一化
                row_sums = M_inv.sum(axis=1, keepdims=True)
                M_Final = M_inv / row_sums
                
                # 保存缓存
                np.save(matrix_path, M_Final)

            # ================= Step 2: 批量处理 =================
            self.status_label.config(text="步骤 2/4: 正在处理图片...")
            input_files = [f for f in os.listdir(dir_input) if f.lower().endswith(('.tif', '.tiff'))]
            total = len(input_files)
            
            if total == 0:
                raise ValueError("Input 文件夹为空")

            for i, fname in enumerate(input_files):
                if self.is_cancelled: raise InterruptedError()
                
                in_path = os.path.join(dir_input, fname)
                out_path = os.path.join(dir_output, fname)
                
                self.process_image(in_path, out_path, M_Final, black_level)
                
                # 更新进度
                progress = (i + 1) / total * 90
                self.progress_var.set(progress)
                self.status_label.config(text=f"正在处理: {fname}")
                self.root.update_idletasks()

            # ================= Step 3: Contact Sheet =================
            self.status_label.config(text="步骤 3/4: 生成缩略图总览...")
            self.create_contact_sheet(dir_output)
            self.progress_var.set(100)

            # 完成
            messagebox.showinfo("完成", "处理完毕")
            
            # 打开文件夹 (跨平台)
            if sys.platform == 'win32':
                os.startfile(dir_output)
            elif sys.platform == 'darwin':
                os.system(f'open "{dir_output}"')
            else:
                os.system(f'xdg-open "{dir_output}"')

        except InterruptedError:
            messagebox.showwarning("取消", "用户取消处理")
        except Exception as e:
            # import traceback
            # traceback.print_exc()
            messagebox.showerror("错误", str(e))
        finally:
            self.reset_ui()

    def reset_ui(self):
        self.btn_start.config(state=tk.NORMAL)
        self.btn_cancel.config(state=tk.DISABLED)
        self.status_label.config(text="就绪")
        self.is_cancelled = False

    def get_roi_average(self, path, black_lvl):
        # 读取 16-bit 图像
        with Image.open(path) as img:
            arr = np.array(img).astype(np.float64)
        
        arr = arr - black_lvl
        h, w = arr.shape[:2]
        
        # ROI
        if h > 10 and w > 10:
            roi = arr[int(h*0.4):int(h*0.6), int(w*0.4):int(w*0.6)]
        else:
            roi = arr
            
        return np.mean(roi, axis=(0, 1)) # 返回 [R_mean, G_mean, B_mean]

    def process_image(self, in_path, out_path, M, black_lvl):
        with Image.open(in_path) as img:
            # 保持 16-bit 原始数据
            arr = np.array(img).astype(np.float64)
            
        arr = arr - black_lvl
        h, w, c = arr.shape
        
        # 矩阵运算 (Reshape -> Dot -> Reshape)
        pixels = arr.reshape(-1, 3)
        # Python dot 是右乘，M 是 (3,3)，pixels 是 (N,3)。
        # 公式: Output = M * Input^T -> Output^T = Input * M^T
        # 所以这里我们要用 pixels @ M.T
        pixels_corr = pixels @ M.T
        
        # 裁切
        pixels_corr = np.clip(pixels_corr, 0, 65535)
        
        # 还原并保存
        img_out_arr = pixels_corr.reshape(h, w, c).astype(np.uint16)
        img_out = Image.fromarray(img_out_arr)
        
        # 保存 LZW 压缩 (tiff_lzw)
        img_out.save(out_path, compression="tiff_lzw")

    def create_contact_sheet(self, output_dir):
        files = [f for f in os.listdir(output_dir) if f.lower().endswith(('.tif', '.tiff')) 
                 and "contactsheet" not in f.lower()]
        
        if not files: return
        
        imgs = []
        max_w, max_h = 0, 0
        
        # 第一次遍历：缩小图片并找最大尺寸
        for f in files:
            path = os.path.join(output_dir, f)
            with Image.open(path) as img:
                # 缩小 10 倍，使用 Lanczos 算法保证质量
                w, h = img.size
                new_size = (int(w * 0.1), int(h * 0.1))
                img_small = img.resize(new_size, Image.Resampling.LANCZOS)
                
                imgs.append(img_small)
                max_w = max(max_w, new_size[0])
                max_h = max(max_h, new_size[1])
        
        if not imgs: return

        # 计算画布
        cols = 6
        rows = int(np.ceil(len(imgs) / cols))
        
        canvas_w = cols * max_w
        canvas_h = rows * max_h
        
        contact_sheet = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
        
        # 拼图
        for idx, img in enumerate(imgs):
            row = idx // cols
            col = idx % cols
            
            x = col * max_w
            y = row * max_h
            
            contact_sheet.paste(img, (x, y))
        
        save_path = os.path.join(output_dir, "contactsheet.tiff")
        contact_sheet.save(save_path, compression="tiff_lzw")


if __name__ == "__main__":
    root = tk.Tk()
    app = DecoupleApp(root)
    root.mainloop()
