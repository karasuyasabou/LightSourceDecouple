胶片翻拍用的光源-传感器RGB解耦小工具。本人非计算机专业，不懂代码，整个工程是ai做的，自己一行代码没动。自用效果很好

准备三个文件夹：
  1. RGB文件夹，包含三张照片，分别是光源仅开启R通道、B通道和G通道，相机对着光源拍摄的照片
  2. Input文件夹，包含所有需要解耦的照片
  3. Outout文件夹，输出位置

上述所有要准备的文件可以是线性 TIFF，也可以直接选择 RAW（支持 ARW/DNG/CR2/CR3/NEF/RAF/ORF/RW2）。
RAW 输入会先用随 app 打包的 open-make-tiff 转为线性 TIFF，再执行原来的解耦流程。
为了保持和原 pipeline 一致的转换效果，处理 RAW 的电脑需要安装 Adobe DNG Converter。
输出文件夹中还会包含一个contact sheet文件，可以用于整卷统一去色罩
