import os
import time
import sys
import json  # 【新增】用于读写配置文件
import numpy as np
import tifffile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading

class DecoupleApp:
    CONFIG_FILE = "config.json"

    def __init__(self, root):
        self.root = root
        self.root.title("光源-CMOS去串扰工具 (稳定版)")
        self.center_window(800, 350)
        self.root.resizable(False, False)
        
        # --- 图标加载逻辑 ---
        if hasattr(sys, '_MEIPASS'):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.abspath(".")
            
        icon_path = os.path.join(base_path, "icon.png")
        if os.path.exists(icon_path):
            try:
                icon_img = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(False, icon_img)
            except Exception:
                pass
        
        # --- 初始化路径变量 ---
        self.dir_rgb = tk.StringVar()
        self.dir_input = tk.StringVar()
        self.dir_output = tk.StringVar()
        
        # 【修改点 1】加载上次的配置，如果没有则使用默认
        self.load_settings()

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

        main_frame.columnconfigure(1, weight=1)

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
        
        # 【修改点 2】增大主按钮的点击区域 (ipadx/ipady) 并添加手型光标
        self.btn_start = ttk.Button(
            btn_frame, 
            text="开始处理", 
            style="Big.TButton", 
            command=self.start_process_logic,
            cursor="hand2" # 鼠标悬停变手型
        )
        # ipadx/ipady 是内部填充，直接撑大按钮的可点击面积
        self.btn_start.pack(side=tk.LEFT, padx=10, ipadx=20, ipady=10)
        
        self.btn_cancel = ttk.Button(
            btn_frame, 
            text="取消", 
            command=self.cancel_process, 
            state=tk.DISABLED,
            cursor="hand2"
        )
        self.btn_cancel.pack(side=tk.LEFT, padx=10, ipadx=10, ipady=10)

        self.is_cancelled = False

    def create_path_selector(self, parent, label_text, var, row):
        ttk.Label(parent, text=label_text, width=18).grid(row=row, column=0, sticky="w", pady=8)
        
        ttk.Entry(parent, textvariable=var, width=45).grid(row=row, column=1, sticky="ew", padx=5, pady=8)
        
        # 【修改点 3】增大“浏览”按钮的点击区域
        btn = ttk.Button(
            parent, 
            text="浏览...", 
            command=lambda: self.browse_dir(var),
            cursor="hand2" # 鼠标悬停变手型
        )
        # ipady=5 让按钮变高，更容易点中
        btn.grid(row=row, column=2, sticky="e", pady=8, ipady=5)

    def browse_dir(self, var):
        initial = var.get() if os.path.exists(var.get()) else os.getcwd()
        path = filedialog.askdirectory(initialdir=initial)
        if path: var.set(path)

    def cancel_process(self):
        self.is_cancelled = True
        self.status_label.config(text="正在取消...")

    # ================= 配置读写逻辑 (新增) =================
    
    def load_settings(self):
        """加载配置文件，如果不存在则使用默认值"""
        cwd = os.getcwd()
        defaults = {
            "rgb": os.path.join(cwd, "RGB"),
            "input": os.path.join(cwd, "input"),
            "output": os.path.join(cwd, "output")
        }
        
        # 尝试读取 config.json
        config_path = os.path.join(cwd, self.CONFIG_FILE)
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding='utf-8') as f:
                    data = json.load(f)
                    # 更新默认值
                    defaults.update(data)
            except Exception:
                pass # 如果读取失败，就用默认值
        
        self.dir_rgb.set(defaults["rgb"])
        self.dir_input.set(defaults["input"])
        self.dir_output.set(defaults["output"])

    def save_settings(self):
        """保存当前路径到配置文件"""
        data = {
            "rgb": self.dir_rgb.get(),
            "input": self.dir_input.get(),
            "output": self.dir_output.get()
        }
        try:
            with open(self.CONFIG_FILE, "w", encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"保存配置失败: {e}")

    # ================= 核心逻辑 =================
    
    def start_process_logic(self):
        # 【修改点 4】开始处理时，自动保存当前路径配置
        self.save_settings()

        self.btn_start.config(state=tk.DISABLED)
        self.btn_cancel.config(state=tk.NORMAL)
        self.is_cancelled = False
        self.progress_var.set(0)
        
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

        try:
            self.root.config(cursor="watch")
            self.status_label.config(text="步骤 1/4: 准备校正矩阵 (界面可能会短暂无响应)...")
            self.root.update()
            
            M_Final = self.prepare_matrix(dirs[0])
            
            if M_Final is None: 
                self.reset_ui()
                return

            self.root.config(cursor="")
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
        black_level = 0
        matrix_path = os.path.join(dir_rgb, "calibration_matrix.npy")
        M_Final = None
        
        if os.path.exists(matrix_path):
            mod_time = time.ctime(os.path.getmtime(matrix_path))
            use_cache = messagebox.askyesno(
                "发现缓存", 
                f"发现已存在的校正文件：\n修改时间: {mod_time}\n\n是否直接使用？"
            )
            if use_cache:
                return np.load(matrix_path)
            if self.is_cancelled: raise InterruptedError()

        files = [f for f in os.listdir(dir_rgb) if f.lower().endswith(('.tif', '.tiff'))]
        if len(files) != 3:
            raise ValueError(f"RGB 文件夹必须包含且仅包含 3 张 TIFF 图片，当前找到 {len(files)} 张")
        
        vecs = []
        file_names = []
        
        for idx, f in enumerate(files):
            if self.is_cancelled: raise InterruptedError()
            
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
        
        np.save(matrix_path, M_Final)
        return M_Final

    def run_batch_thread(self, dir_input, dir_output, M_Final):
        try:
            black_level = 0
            
            def update_status(text, val):
                self.status_label.config(text=text)
                self.progress_var.set(val)
            
            self.root.after(0, update_status, "步骤 2/4: 正在处理图片...", 0)
            
            input_files = [f for f in os.listdir(dir_input) if f.lower().endswith(('.tif', '.tiff'))]
            total = len(input_files)
            if total == 0: raise ValueError("Input 文件夹为空")

            for i, fname in enumerate(input_files):
                if self.is_cancelled: raise InterruptedError()
                
                in_path = os.path.join(dir_input, fname)
                out_path = os.path.join(dir_output, fname)
                
                self.process_image(in_path, out_path, M_Final, black_level)
                
                prog = (i + 1) / total * 90
                self.root.after(0, update_status, f"正在处理: {fname}", prog)

            self.root.after(0, update_status, "步骤 3/4: 生成缩略图总览...", 90)
            self.create_contact_sheet(dir_output)
            self.root.after(0, update_status, "完成", 100)

            self.root.after(0, self.on_success, dir_output)

        except InterruptedError:
            self.root.after(0, lambda: self.safe_showwarning("取消", "用户取消处理"))
            self.root.after(0, self.reset_ui)
        except Exception as e:
            err_msg = str(e) if str(e) else f"未知错误: {type(e).__name__}"
            self.root.after(0, lambda: self.safe_showerror("错误", err_msg))
            self.root.after(0, self.reset_ui)

    def on_success(self, dir_output):
        self.reset_ui()
        messagebox.showinfo("完成", "处理完毕")
        
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

    def safe_showerror(self, title, msg):
        if not msg: msg = "发生未知错误"
        messagebox.showerror(title, msg)
        
    def safe_showwarning(self, title, msg):
        if not msg: msg = "警告"
        messagebox.showwarning(title, msg)

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
