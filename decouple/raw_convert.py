import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

from .paths import get_app_base_path


RAW_EXTENSIONS = {".arw", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2"}
TIFF_EXTENSIONS = {".tif", ".tiff"}
IMAGE_EXTENSIONS = TIFF_EXTENSIONS | RAW_EXTENSIONS
OPEN_MAKE_TIFF_WORKERS = 5


class RawConversionError(RuntimeError):
    pass


@dataclass
class ConvertedRaw:
    source_path: str
    tiff_path: str
    temp_dir: str


def is_raw_path(path):
    return os.path.splitext(path)[1].lower() in RAW_EXTENSIONS


def is_tiff_path(path):
    return os.path.splitext(path)[1].lower() in TIFF_EXTENSIONS


def output_tiff_name(path):
    if is_raw_path(path):
        stem, _ = os.path.splitext(os.path.basename(path))
        return f"{stem}.tiff"
    return os.path.basename(path)


def image_file_filter():
    suffixes = " ".join(f"*{ext}" for ext in sorted(IMAGE_EXTENSIONS))
    return f"Images ({suffixes});;TIFF Images (*.tif *.tiff);;RAW Images (*.arw *.dng *.cr2 *.cr3 *.nef *.raf *.orf *.rw2)"


def find_open_make_tiff_executable():
    env_path = os.environ.get("DECOUPLE_OPEN_MAKE_TIFF", "").strip()
    if env_path and os.path.exists(env_path):
        return env_path

    base_path = get_app_base_path()
    exe_name = "open-make-tiff.exe" if sys.platform == "win32" else "open-make-tiff"
    candidates = [
        os.path.join(base_path, exe_name),
        os.path.join(base_path, "bin", exe_name),
        os.path.join(base_path, "build", "open_make_tiff", exe_name),
        os.path.join(base_path, "third_party", "open_make_tiff", "build", exe_name),
        os.path.join(base_path, "third_party", "open_make_tiff", "build", "bin", exe_name),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise RawConversionError(
        "找不到 open-make-tiff 转换器。\n"
        "请确认 app 打包时包含 open-make-tiff，或设置 DECOUPLE_OPEN_MAKE_TIFF 指向转换器。"
    )


def get_adobe_dng_converter_path():
    if sys.platform == "darwin":
        return "/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter"
    if sys.platform == "win32":
        return r"C:\Program Files\Adobe\Adobe DNG Converter\Adobe DNG Converter.exe"
    return ""


def ensure_adobe_dng_converter_available():
    path = get_adobe_dng_converter_path()
    if not path or not os.path.exists(path):
        raise RawConversionError(
            "找不到 Adobe DNG Converter。\n"
            "为了保持 RAW 转换效果与原 pipeline 一致，请先安装 Adobe DNG Converter 后再处理 RAW 文件。"
        )


def convert_raw_to_tiff(raw_path, is_cancelled=None):
    converted = convert_raws_to_tiffs([raw_path], is_cancelled=is_cancelled)
    return converted[0]


def convert_raws_to_tiffs(raw_paths, is_cancelled=None):
    if not raw_paths:
        return []

    seen_names = set()
    for raw_path in raw_paths:
        if not is_raw_path(raw_path):
            raise RawConversionError(f"不是支持的 RAW 文件: {raw_path}")
        if not os.path.exists(raw_path):
            raise RawConversionError(f"找不到 RAW 文件: {raw_path}")
        name = os.path.basename(raw_path)
        if name in seen_names:
            raise RawConversionError(f"同一批 RAW 中存在重名文件，无法安全转换: {name}")
        seen_names.add(name)

    ensure_adobe_dng_converter_available()
    converter = find_open_make_tiff_executable()
    temp_dir = tempfile.mkdtemp(prefix="decouple_raw_")
    staged_paths = []
    for raw_path in raw_paths:
        staged_raw = os.path.join(temp_dir, os.path.basename(raw_path))
        shutil.copy2(raw_path, staged_raw)
        staged_paths.append(staged_raw)

    cmd = [
        converter,
        "-subfolder",
        "-workers",
        str(OPEN_MAKE_TIFF_WORKERS),
    ]
    cmd.extend(staged_paths)

    proc = subprocess.Popen(
        cmd,
        cwd=temp_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        while proc.poll() is None:
            if is_cancelled and is_cancelled():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RawConversionError("用户取消处理")
            time.sleep(0.1)
        output, _ = proc.communicate()
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    output = (output or "").strip()
    if proc.returncode != 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        detail = f"\n\nopen-make-tiff 输出:\n{output}" if output else ""
        raise RawConversionError(f"RAW 转 TIFF 失败，共 {len(raw_paths)} 张{detail}")

    make_tiff_dir = os.path.join(temp_dir, "make_tiff")
    converted = []
    for raw_path, staged_raw in zip(raw_paths, staged_paths):
        expected = os.path.join(make_tiff_dir, f"{os.path.basename(staged_raw)}.tiff")
        if not os.path.exists(expected):
            shutil.rmtree(temp_dir, ignore_errors=True)
            detail = f"\n\nopen-make-tiff 输出:\n{output}" if output else ""
            raise RawConversionError(f"open-make-tiff 完成但没有找到输出 TIFF: {os.path.basename(raw_path)}{detail}")
        converted.append(ConvertedRaw(source_path=raw_path, tiff_path=expected, temp_dir=temp_dir))

    return converted
