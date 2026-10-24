# 离线字体

四个 WOFF2 文件由 Google Fonts 仓库固定版本生成：
[45e3d5269e4a17d823f6d84470bc084dc5ae46f6](https://github.com/google/fonts/tree/45e3d5269e4a17d823f6d84470bc084dc5ae46f6)。
各文件继续使用各自的 SIL Open Font License 1.1，版权及许可全文随包保留；不将字体作为独立商品销售。

| 字体 | 文件 | 许可 |
|---|---|---|
| Figtree | `Figtree-Variable.woff2` | `Figtree-OFL.txt` |
| Lexend | `Lexend-Variable.woff2` | `Lexend-OFL.txt` |
| JetBrains Mono | `JetBrainsMono-Variable.woff2` | `JetBrainsMono-OFL.txt` |
| Noto Sans SC | `NotoSansSC-Variable.woff2` | `NotoSansSC-OFL.txt` |

前三个字体保留上游完整字集并转换为 WOFF2。Noto Sans SC 是中文子集：从当前 `host/` 与 `content/` 文本取字，并加入拉丁字符及常用标点；界面新增文字后重新生成。未覆盖的动态文字由现有系统字体栈补足。字体名称未使用上游许可保留的 “RevReading Lexend” 或 “Source”。

`sources.json` 记录源地址、固定提交、源/许可/产物 SHA-256、产物大小和实际保留的 Unicode 码位。构建工具不会从移动分支下载，也不会在页面运行期间连接 Google Fonts。

在工程根目录重建（仅构建环境需要额外依赖，版本以 `pyproject.toml` 为准）：

```powershell
python -m pip install -e ".[fonts]"
python tools/build_fonts.py
```

源文件缓存在系统临时目录，可用 `--cache` 指定工程外目录。已有来源清单时，下载和缓存均需通过原登记哈希；不会静默接受来源变化。只有本机重建时需要外网获取固定源，运行期字体完全同源。
