# open_make_tiff 可迁移修复版源码说明

这个目录是从 `https://github.com/imdiot/open_make_tiff.git` 重新拉取并修复后的源码。

来源 commit:

```text
b80c54c79613b6664a3f244ebe3a293e89ad6e07
```

## 已修复的问题

原项目在 CLI 或非 Wails GUI 环境中调用 `Manager.SetConfig()` 时，会间接调用:

```go
wails_runtime.WindowSetAlwaysOnTop(...)
```

如果当前不是 Wails 生命周期传入的 GUI context，就会报错:

```text
cannot call 'github.com/wailsapp/wails/v2/pkg/runtime.WindowSetAlwaysOnTop':
An invalid context was passed.
```

本修复版在 `pkg/manager/manager.go` 中增加了 GUI 状态判断:

- `OnStartup()` 运行时标记 `m.gui = true`
- `SetConfig()`、`OnStartup()`、`OnSecondInstanceLaunch()` 不再直接调用 Wails 窗口 API
- 改为调用 `m.setAlwaysOnTop(...)`
- 非 GUI/CLI/嵌入式调用时，`setAlwaysOnTop()` 会直接返回

这样 `Manager` 可以在别的软件里作为普通 Go 代码调用，不会因为没有 Wails 窗口 context 而崩溃。

## 迁移建议

### 方式一：作为独立 Go module 集成

保留整个目录，把它放进你的新工程，例如:

```text
your_app/third_party/open_make_tiff/
```

然后在你的主工程 `go.mod` 中添加类似:

```go
require open-make-tiff v0.0.0

replace open-make-tiff => ./third_party/open_make_tiff
```

之后可以直接调用:

```go
import (
	"context"
	"sync/atomic"

	"open-make-tiff/pkg/manager"
)

func ConvertRawFiles(paths []string) int {
	ctx := context.Background()
	done := make(chan struct{})
	var failed atomic.Int32

	mgr := manager.New(
		manager.WithContext(ctx),
		manager.WithEventEmitter(func(event string, data ...any) {
			switch event {
			case "omt:convert:file:error":
				failed.Add(1)
			case "omt:convert:finished":
				close(done)
			}
		}),
	)

	mgr.SetConfig(&manager.Config{
		DisableAdobeDNGConverter: false,
		EnableSubfolder:         true,
		EnableCompression:       false,
		ICCProfile:              "",
		Workers:                 5,
	})

	mgr.Convert(paths)
	<-done
	mgr.Shutdown()

	return int(failed.Load())
}
```

这对应你当前使用的 open-make-tiff 配置:

- 使用 Adobe DNG Converter: `DisableAdobeDNGConverter: false`
- 输出到 `make_tiff` 子文件夹: `EnableSubfolder: true`
- 不启用 LZW 压缩: `EnableCompression: false`
- ICC profile 为 none: `ICCProfile: ""`
- workers 为 5: `Workers: 5`

### 方式二：只拷贝核心 package

如果你不想把 GUI/Wails 相关文件迁进去，核心转换逻辑主要在:

```text
pkg/dngconverter
pkg/exiftool
pkg/golibraw
pkg/icc
pkg/manager
pkg/runner
pkg/util
```

但这些 package 当前 import 路径是:

```go
open-make-tiff/pkg/...
```

如果拷贝进你的主工程，需要统一改成你的 module 路径，或者仍然用上面的 `replace open-make-tiff => ...` 方式保留原 import。

## 运行时外部依赖

迁移源码后，在目标软件/目标平台编译和运行时仍需要处理这些依赖:

- Go 版本: 项目 `go.mod` 当前为 `go 1.25`
- C/C++ 库: `libraw`、`tiff` 以及它们的相关依赖
- 如果使用 Adobe DNG Converter 路线，目标机器需要能找到并运行 Adobe DNG Converter
- ExifTool 需要随程序一起打包，或让 `pkg/util` 能在目标环境中找到它
- 跨平台编译时，需要按目标平台准备对应的 native library 和 third-party 可执行文件

## 当前没有做的事

这个目录只整理源码，不包含本机编译产物，也没有在这里重新编译。

