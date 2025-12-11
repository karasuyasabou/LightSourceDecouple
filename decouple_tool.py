import os
import time
import sys
import numpy as np
import tifffile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading

class DecoupleApp:
    def __init__(self, root):
        self.root = root
        self.root.title("光源-CMOS去串扰工具 (稳定版)")
        self.center_window(800, 350)
        self.root.resizable(False, False)
        
        # 加载图标
        icon_path = os.path.join(os.getcwd(), "icon.png")
        if os.path.exists(icon_path):
            try:
                icon_img = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(False, icon_img)
            except Exception:
                pass
        
        # 路径变量
        cwd = os.getcwd()
        self.dir_rgb = tk.StringVar(value=os.path.join(cwd, "RGB"))
        self.dir_input = tk.StringVar(value=os.path.join(cwd, "input"))
        self.dir_output = tk.StringVar(value=os.path.join(cwd, "output"))

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

        self.create_path_selector(main_frame, "RGB 校正文件夹:", self.dir_rgb, 0)
        self.create_path_selector(main_frame, "Input 待处理文件夹:", self.dir_input, 1)
        self.create_path_selector(main_frame, "Output 输出文件夹:", self.dir_output, 2)

        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100)
        self.progress.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(30, 10))

        self.status_label = ttk.Label(main_frame, text="就绪")
        self.status_label.grid(row=4, column=0, columnspan=3, sticky="w")

        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=5, column=0, columnspan=3, pady=20)
        
        style = ttk.Style()
        style.configure("Big.TButton", font=("Helvetica", 10, "bold"))
        
        self.btn_start = ttk.Button(btn_frame, text="开始处理", style="Big.TButton", command=self.start_process_logic)
        self.btn_start.pack(side=tk.LEFT, padx=10, ipadx=10, ipady=5)
        
        self.btn_cancel = ttk.Button(btn_frame, text="取消", command=self.cancel_process, state=tk.DISABLED)
        self.btn_cancel.pack(side=tk.LEFT, padx=10, ipadx=5, ipady=5)

        self.is_cancelled = False

    def create_path_selector(self, parent, label_text, var, row):
        ttk.Label(parent, text=label_text, width=18).grid(row=row, column=0, sticky="w", pady=8)
        ttk.Entry(parent, textvariable=var, width=70).grid(row=row, column=1, sticky="w", padx=5, pady=8)
        ttk.Button(parent, text="浏览...", command=lambda: self.browse_dir(var)).grid(row=row, column=2, sticky="e", pady=8)

    def browse_dir(self, var):
        initial = var.get() if os.path.exists(var.get()) else os.getcwd()
        path = filedialog.askdirectory(initialdir=initial)
        if path: var.set(path)

    def cancel_process(self):
        self.is_cancelled = True
        self.status_label.config(text="正在取消...")

    # ================= 核心逻辑重构 =================
    
    def start_process_logic(self):
        """主入口：先在主线程准备矩阵，再去子线程跑批量"""
        self.btn_start.config(state=tk.DISABLED)
        self.btn_cancel.config(state=tk.NORMAL)
        self.is_cancelled = False
        self.progress_var.set(0)
        
        # 1. 检查路径
        dirs = [self.dir_rgb.get(), self.dir_input.get(), self.dir_output.get()]
        for d in dirs:
            if not d:
                messagebox.showerror("错误", "路径不能为空")
                self.reset_ui()
                return

        if not os.path.exists(dirs[2]):
            try:
                os.makedirs(dirs[2])
            except Exception as e:
                self.safe_showerror("错误", f"无法创建输出目录: {e}")
                self.reset_ui()
                return

        # 2. 【主线程执行】获取/计算矩阵
        # 移到主线程是为了保证弹窗 (messagebox) 绝对稳定，不会出现空白框
        try:
            self.root.config(cursor="watch") # 鼠标变沙漏
            self.status_label.config(text="步骤 1/4: 准备校正矩阵 (界面可能会短暂无响应)...")
            self.root.update() # 强制刷新界面
            
            M_Final = self.prepare_matrix(dirs[0])
            
            if M_Final is None: 
                # 这里通常是因为用户点了取消或者逻辑中断，UI已经在prepare_matrix里重置了吗？
                # 如果是 raise Error 出来的，会被下面的 except 捕获
                # 如果是 None 但没报错，说明是逻辑上的取消
                self.reset_ui()
                return

            # 3. 【子线程执行】矩阵准备好后，开启线程跑批量
            self.root.config(cursor="") # 恢复鼠标
            threading.Thread(target=self.run_batch_thread, args=(dirs[1], dirs[2], M_Final), daemon=True).start()
            
        except InterruptedError:
            self.safe_showwarning("取消", "用户取消处理")
            self.reset_ui()
        except Exception as e:
            self.safe_showerror("错误", str(e))
            self.reset_ui()
        finally:
            self.root.config(cursor="")

    def prepare_matrix(self, dir_rgb):
        """计算矩阵 (运行在主线程，允许直接弹窗)"""
        black_level = 0
        matrix_path = os.path.join(dir_rgb, "calibration_matrix.npy")
        M_Final = None
        
        # A. 检查缓存
        if os.path.exists(matrix_path):
            mod_time = time.ctime(os.path.getmtime(matrix_path))
            use_cache = messagebox.askyesno(
                "发现缓存", 
                f"发现已存在的校正文件：\n修改时间: {mod_time}\n\n是否直接使用？"
            )
            if use_cache:
                return np.load(matrix_path)
            if self.is_cancelled: raise InterruptedError()

        # B. 重新计算
        files = [f for f in os.listdir(dir_rgb) if f.lower().endswith(('.tif', '.tiff'))]
        if len(files) != 3:
            raise ValueError(f"RGB 文件夹必须包含且仅包含 3 张 TIFF 图片，当前找到 {len(files)} 张")
        
        vecs = []
        file_names = []
        
        for idx, f in enumerate(files):
            if self.is_cancelled: raise InterruptedError()
            
            # 读取大文件时刷新一下界面，防止完全假死
            self.status_label.config(text=f"正在读取校正图片: {f} ...")
            self.root.update() 
            
            path = os.path.join(dir_rgb, f)
            vec = self.get_roi_average(path, black_level)
            vecs.append(vec)
            file_names.append(f)
        
        vecs = np.array(vecs).T 
        
        idx_r = np.argmax(vecs[0, :])
        idx_g = np.argmax(vecs[1, :])
        idx_b = np.argmax(vecs[2, :])
        
        msg = (f"R: {file_names[idx_r]}\n"
               f"G: {file_names[idx_g]}\n"
               f"B: {file_names[idx_b]}\n\n"
               "识别结果是否正确？")
        
        if not messagebox.askyesno("确认", msg):
            raise InterruptedError()
        
        M_obs = np.column_stack((vecs[:, idx_r], vecs[:, idx_g], vecs[:, idx_b]))
        
        if np.linalg.cond(M_obs) > 1e15:
            raise ValueError("观测矩阵奇异，无法计算")
        
        M_inv = np.linalg.inv(M_obs)
        row_sums = M_inv.sum(axis=1, keepdims=True)
        M_Final = M_inv / row_sums
        
        # 保存
        np.save(matrix_path, M_Final)
        return M_Final

    def run_batch_thread(self, dir_input, dir_output, M_Final):
        """批量处理 (运行在子线程，禁止直接弹窗)"""
        try:
            black_level = 0
            
            # 线程安全的 UI 更新函数
            def update_status(text, val):
                self.status_label.config(text=text)
                self.progress_var.set(val)
            
            # --- 步骤 2: 批量处理 ---
            self.root.after(0, update_status, "步骤 2/4: 正在处理图片...", 0)
            
            input_files = [f for f in os.listdir(dir_input) if f.lower().endswith(('.tif', '.tiff'))]
            total = len(input_files)
            if total == 0: raise ValueError("Input 文件夹为空")

            for i, fname in enumerate(input_files):
                if self.is_cancelled: raise InterruptedError()
                
                in_path = os.path.join(dir_input, fname)
                out_path = os.path.join(dir_output, fname)
                
                self.process_image(in_path, out_path, M_Final, black_level)
                
                # 线程安全更新进度
                prog = (i + 1) / total * 90
                self.root.after(0, update_status, f"正在处理: {fname}", prog)

            # --- 步骤 3: Contact Sheet ---
            self.root.after(0, update_status, "步骤 3/4: 生成缩略图总览...", 90)
            self.create_contact_sheet(dir_output)
            self.root.after(0, update_status, "完成", 100)

            # --- 完成 ---
            self.root.after(0, self.on_success, dir_output)

        except InterruptedError:
            self.root.after(0, lambda: self.safe_showwarning("取消", "用户取消处理"))
            self.root.after(0, self.reset_ui)
        except Exception as e:
            # 捕获所有错误并安全传回主线程显示
            err_msg = str(e) if str(e) else f"未知错误: {type(e).__name__}"
            self.root.after(0, lambda: self.safe_showerror("错误", err_msg))
            self.root.after(0, self.reset_ui)

    def on_success(self, dir_output):
        self.reset_ui()
        messagebox.showinfo("完成", "处理完毕")
        
        # 打开文件夹
        if sys.platform == 'win32':
            os.startfile(dir_output)
        elif sys.platform == 'darwin':
            os.system(f'open "{dir_output}"')
        else:
            os.system(f'xdg-open "{dir_output}"')

    def reset_ui(self):
        self.btn_start.config(state=tk.NORMAL)
        self.btn_cancel.config(state=tk.DISABLED)
        self.status_label.config(text="就绪")
        self.root.config(cursor="")
        self.is_cancelled = False

    # 封装安全弹窗，防止空消息
    def safe_showerror(self, title, msg):
        if not msg: msg = "发生未知错误"
        messagebox.showerror(title, msg)
        
    def safe_showwarning(self, title, msg):
        if not msg: msg = "警告"
        messagebox.showwarning(title, msg)

    # --- 图像处理核心算法 (不变) ---
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

if __name__ == "__main__":
    root = tk.Tk()
    app = DecoupleApp(root)
    root.mainloop()
