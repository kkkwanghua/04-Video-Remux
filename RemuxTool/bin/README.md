# ffmpeg 放置目录

请将静态编译的 `ffmpeg` 可执行文件放在本目录，文件名为：

```text
ffmpeg
```

工具启动时会优先使用 `RemuxTool/bin/ffmpeg`；若不存在，则会尝试系统 PATH 中的 ffmpeg。

由于二进制体积较大，该文件未纳入 Git 版本库。
