# 04-Video Remux

批量视频无损重封装工具（macOS），当前版本 v1.2.8。

只拷贝音视频码流（`-c copy`），不重新编码；支持 MOV / MP4 / MKV 输出，扫描后可按格式和逐行勾选筛选文件，支持 macOS 原生文件夹拖放。

## 主要功能

- 批量扫描视频文件，无损重封装到 MOV / MP4 / MKV。
- 扫描后按扩展名筛选（如只转 MXF，或 MXF + MKV），也可逐行勾选。
- 保留子目录层级，跳过已存在文件，单文件失败不影响整批。
- MXF → MOV：保留格式级时间码，同时跳过辅助数据轨；多条 mono 音轨保持独立并全部标记为默认/启用。
- HEVC 视频自动修正为 `hvc1` 标签，解决 macOS 快速查看无法预览的问题。
- 完全离线运行，内置 Python 运行时、Tk 与 ffmpeg（构建 .app 时打包）。

## 目录结构

```text
04-Video Remux/
├── README.md
├── .gitignore
├── RemuxTool/
│   ├── remux_gui.py          # 主程序
│   ├── drop_target.py        # macOS 原生拖放
│   ├── 启动工具.command      # 源码方式启动
│   ├── 使用说明.md
│   ├── bin/
│   │   └── README.md         # 放置 ffmpeg 的位置说明
│   └── _build/               # .app 构建脚本
└── 视频重封装工具*.app       # 构建产物，未纳入版本库
```

## 使用

### 使用已构建 .app

构建产物 `视频重封装工具.app`（Intel）与 `视频重封装工具-arm64.app`（Apple Silicon）未提交到版本库，可在本地用 `_build/` 下的脚本重建，或从发布包获取。

### 源码方式

1. 将 `ffmpeg` 可执行文件放到 `RemuxTool/bin/ffmpeg`。
2. 双击 `RemuxTool/启动工具.command`，或用带 tkinter 的 Python 3.13 运行 `remux_gui.py`。
3. 选择源目录、目标目录和输出格式，扫描后勾选文件，开始转换。

## 构建 .app

`RemuxTool/_build/` 中提供 x86 与 arm64 的构建脚本。构建时会把 Python 运行时、Tk、ffmpeg 与 `.pyc` 主程序打包成自包含 `.app`。

## 版本记录

- v1.2.8：MOV/MP4 输出时把所有音轨标记为 default/enabled，修复多 mono 音轨只显示一条的问题。
- v1.2.7：扫描后格式筛选与逐行勾选。
- v1.2.6：修复 macOS 拖放文件夹到路径栏导致 native 崩溃的问题。
