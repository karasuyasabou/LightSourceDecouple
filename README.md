胶片翻拍用的光源-传感器RGB解耦小工具。本人非计算机专业，不懂代码，整个工程是ai做的，自己一行代码没动。自用效果很好

## 使用方法

准备三个文件夹：
  1. RGB文件夹，包含三张照片，分别是光源仅开启R通道、B通道和G通道，相机对着光源拍摄的照片
  2. Input文件夹，包含所有需要解耦的照片
  3. Output文件夹，输出位置

上述所有要准备的文件可以是线性 TIFF，也可以直接选择 RAW（支持 ARW/DNG/CR2/CR3/NEF/RAF/ORF/RW2）。
RAW 输入会先用随 app 打包的 open-make-tiff 转为线性 TIFF，再执行原来的解耦流程。
输出文件夹中还会包含一个 contact sheet 文件，可以用于整卷统一去色罩。

## RAW 转换模式

界面中可以选择三种 RAW 转换模式：

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| 自动 | 检测到 Adobe DNG Converter 则优先使用，否则自动回退到 libraw | 默认，无需手动干预 |
| Adobe DNG Converter（推荐） | 强制使用 Adobe DNG Converter，找不到则报错 | 彩色 CMOS 拜耳阵列 RAW，成片噪点和色彩断层更少 |
| libraw（免安装） | 直接用内置的 libraw 解码，无需安装任何 Adobe 软件 | Linux 用户，或不想安装 Adobe 软件的用户 |

> **Linux 用户**：Adobe DNG Converter 不提供 Linux 版本，请选择 **libraw（免安装）** 或保持默认的**自动**模式，两者在 Linux 上效果相同。
> libraw 模式下 RAW 解码使用系统 Perl 运行 ExifTool 写入元数据，请确认系统已安装 Perl（大多数 Linux 发行版默认包含）。

## 输出 ICC 配置文件

解耦后的 TIFF 默认不嵌入任何色彩空间标记（选 `none`），可按需选择以下内置配置文件：

| 选项 | 色彩空间 | 说明 |
|------|----------|------|
| none | 无 | 不嵌入 ICC，由后续软件自行指定色彩配置文件 |
| ACESCG Linear | ACEScg (AP1) | ACES 标准宽色域线性空间，适合 DaVinci Resolve、Nuke 等支持 ACES 工作流的软件 |
| Kodak2383 Linear | 柯达 2383 印片色域 | 数字影院（DCP）工作流中使用的输出色域，标记图像色彩范围对应柯达 2383 印片 |
| KodakEnduraPremier Linear | 柯达 Endura Premier 相纸色域 | 标记图像色彩范围对应柯达 Endura Premier 冲印相纸，用于相纸输出模拟 |
| custom | 自定义 | 从本地加载任意 `.icc` / `.icm` 文件 |

> 所有内置配置文件均为**线性**（gamma = 1.0），与解耦输出的线性 TIFF 匹配。

## 支持平台

| 平台 | 架构 | 状态 |
|------|------|------|
| macOS | Apple Silicon (arm64) | ✅ |
| macOS | Intel (x64) | ✅ |
| Windows | x64 | ✅ |
| Linux | x64 | ✅ |

Linux 提供两种格式：
- **AppImage**（推荐）：单文件，`chmod +x DecoupleTool-Linux-x64.AppImage` 后直接运行，无需安装
- **zip**：解压后运行 `DecoupleTool/DecoupleTool`
