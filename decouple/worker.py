import os
import shutil
import numpy as np
import tifffile

from PySide6.QtCore import QThread, Signal

from .icc import CUSTOM_ICC_OPTION, ICC_PROFILE_FILES
from .paths import get_app_base_path
from .raw_convert import (
    IMAGE_EXTENSIONS,
    RAW_EXTENSIONS,
    TIFF_EXTENSIONS,
    convert_raw_to_tiff,
    is_raw_path,
    output_tiff_name,
)


# =========================================================================
# 后台工作线程 (Worker)
# =========================================================================
class ProcessingWorker(QThread):
    progress_updated = Signal(int, str)  # 进度信号 (百分比, 状态文本)
    finished_success = Signal(str)       # 成功信号 (输出目录)
    finished_error = Signal(str)         # 失败信号 (错误信息)
    request_confirmation = Signal(str, str) # 请求确认信号 (标题, 内容)
    
    def __init__(self, dir_rgb, input_files, dir_output, dir_contactsheet, icc_mode="none", custom_icc_path="", use_cache_override=None):
        super().__init__()
        self.dir_rgb = dir_rgb
        self.input_files = input_files # 文件路径列表
        self.dir_output = dir_output
        self.dir_contactsheet = dir_contactsheet 
        self.icc_mode = icc_mode
        self.custom_icc_path = custom_icc_path
        self.use_cache_override = use_cache_override 
        self._is_cancelled = False
        self._selected_icc_bytes = None
        self._temp_dirs = []
        
        # 线程同步工具
        import threading
        self._confirm_event = threading.Event()
        self._confirm_result = False

    def cancel(self):
        self._is_cancelled = True
        self._confirm_result = False
        self._confirm_event.set()

    def _cleanup_temp_dirs(self):
        for path in self._temp_dirs:
            shutil.rmtree(path, ignore_errors=True)
        self._temp_dirs = []

    def _wait_for_user_choice(self, title, message):
        """辅助函数：发送信号给UI并阻塞等待结果"""
        self._confirm_event.clear()
        self.request_confirmation.emit(title, message)
        self._confirm_event.wait()
        return self._confirm_result

    def run(self):
        try:
            black_level = 0
            
            try:
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

                    calibration_paths = self.get_calibration_paths()
                    vecs = []
                    file_names = []
                    
                    for idx, source_path in enumerate(calibration_paths):
                        if self._is_cancelled: return
                        display_name = os.path.basename(source_path)
                        self.progress_updated.emit(0, f"正在读取校正图片: {display_name} ...")
                        path = self.prepare_readable_image(source_path, f"转换校正 RAW: {display_name}")
                        vec = self.get_roi_average(path, black_level)
                        vecs.append(vec)
                        file_names.append(display_name)
                    
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
                    read_path = self.prepare_readable_image(in_path, f"转换输入 RAW: {fname}")
                    out_path = os.path.join(self.dir_output, output_tiff_name(in_path))
                    
                    self.process_image(read_path, out_path, M_Final, black_level)
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
            finally:
                self._cleanup_temp_dirs()

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.finished_error.emit(str(e))

    def get_calibration_paths(self):
        all_files = [
            os.path.join(self.dir_rgb, f)
            for f in os.listdir(self.dir_rgb)
            if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
        ]
        tiff_files = sorted([p for p in all_files if os.path.splitext(p)[1].lower() in TIFF_EXTENSIONS])
        raw_files = sorted([p for p in all_files if os.path.splitext(p)[1].lower() in RAW_EXTENSIONS])

        if len(tiff_files) == 3:
            return tiff_files
        if len(raw_files) == 3:
            return raw_files
        raise ValueError(
            "RGB 文件夹必须包含且仅包含 3 张 TIFF 校正图片，或 3 张 RAW 校正图片；"
            f"当前找到 TIFF {len(tiff_files)} 张，RAW {len(raw_files)} 张"
        )

    def prepare_readable_image(self, path, status_message):
        if not is_raw_path(path):
            return path

        self.progress_updated.emit(0, status_message)
        converted = convert_raw_to_tiff(path, is_cancelled=lambda: self._is_cancelled)
        self._temp_dirs.append(converted.temp_dir)
        return converted.tiff_path

    def get_roi_average(self, path, black_lvl):
        img = tifffile.imread(path)
        arr = img.astype(np.float64)
        arr = arr - black_lvl
        if arr.ndim != 3 or arr.shape[2] != 3:
            raise ValueError(f"需要 RGB 三通道图片，当前形状为 {arr.shape}: {path}")
        h, w = arr.shape[:2]
        if h > 10 and w > 10:
            roi = arr[int(h*0.4):int(h*0.6), int(w*0.4):int(w*0.6)]
        else:
            roi = arr
        return np.mean(roi, axis=(0, 1))

    def process_image(self, in_path, out_path, M, black_lvl):
        arr = tifffile.imread(in_path).astype(np.float64)
        arr = arr - black_lvl
        if arr.ndim != 3 or arr.shape[2] != 3:
            raise ValueError(f"需要 RGB 三通道图片，当前形状为 {arr.shape}: {in_path}")
        h, w, c = arr.shape
        pixels = arr.reshape(-1, 3)
        pixels_corr = pixels @ M.T
        pixels_corr = np.clip(pixels_corr, 0, 65535)
        img_out_arr = pixels_corr.reshape(h, w, c).astype(np.uint16)
        tifffile.imwrite(out_path, img_out_arr, **self.get_tiff_save_kwargs(in_path))

    def get_tiff_save_kwargs(self, in_path):
        save_kwargs = {"compression": "zlib"}
        icc_bytes = self.get_icc_profile_bytes(in_path)
        if icc_bytes:
            save_kwargs["extratags"] = [(34675, "B", len(icc_bytes), icc_bytes, False)]
        return save_kwargs

    def get_icc_profile_bytes(self, in_path):
        if self.icc_mode == "none":
            return None

        if self._selected_icc_bytes is None:
            if self.icc_mode == CUSTOM_ICC_OPTION:
                icc_path = self.custom_icc_path
            else:
                profile_name = ICC_PROFILE_FILES.get(self.icc_mode)
                if not profile_name:
                    raise ValueError(f"未知 ICC 选项: {self.icc_mode}")
                icc_path = os.path.join(get_app_base_path(), "icc", profile_name)

            if not os.path.exists(icc_path):
                raise FileNotFoundError(f"找不到 ICC 文件: {icc_path}")

            with open(icc_path, "rb") as f:
                self._selected_icc_bytes = f.read()

        return self._selected_icc_bytes

    def _center_crop_image(self, img, target_h, target_w):
        h, w = img.shape[:2]
        if h < target_h or w < target_w:
            raise ValueError("裁切尺寸不能大于原图尺寸")

        top = (h - target_h) // 2
        left = (w - target_w) // 2
        return img[top:top + target_h, left:left + target_w, :]

    def create_contact_sheet(self, image_paths, output_dir):
        if not image_paths: return
        imgs = []
        min_w, min_h = None, None

        for path in image_paths:
            if not os.path.exists(path): continue
            img = tifffile.imread(path)
            img_small = img[::5, ::5, :]
            imgs.append(img_small)
            h, w = img_small.shape[:2]
            min_w = w if min_w is None else min(min_w, w)
            min_h = h if min_h is None else min(min_h, h)

        if not imgs: return
        if min_w is None or min_h is None: return

        cropped_imgs = [self._center_crop_image(img, min_h, min_w) for img in imgs]
        cols = 6
        rows = int(np.ceil(len(cropped_imgs) / cols))
        canvas_w = cols * min_w
        canvas_h = rows * min_h
        contact_sheet = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint16)

        for idx, img in enumerate(cropped_imgs):
            row = idx // cols
            col = idx % cols
            x = col * min_w
            y = row * min_h
            contact_sheet[y:y + min_h, x:x + min_w, :] = img

        if not os.path.exists(output_dir):
            try: os.makedirs(output_dir)
            except: return

        save_path = os.path.join(output_dir, "contactsheet.tiff")
        tifffile.imwrite(save_path, contact_sheet, **self.get_tiff_save_kwargs(image_paths[0]))
