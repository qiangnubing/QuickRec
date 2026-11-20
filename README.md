# QuickRec

**中文** | [English](README_EN.md)

Windows 桌面屏幕区域录制工具。框选屏幕任意区域，按 10 / 20 / 40 / 60 秒或 MAX 录制，支持自定义时长、画质、系统声音与麦克风，并提供中文 / English 界面。

![QuickRec 主界面](docs/screenshot-main-zh.png)

## 功能

- 紧凑现代的圆角界面：主界面、框选后确认操作条、右下角倒计时浮层及提示弹窗均采用纯 Tkinter + NumPy 抗锯齿圆角控件绘制，风格统一且不增加额外 UI 依赖。
- 全屏拖动框选录制区域，完整支持高 DPI 与多显示器扩展模式（包括主屏左侧、上方等负坐标副屏），每块屏幕均显示操作提示，框选确认条自动停留在选区所在屏幕。
- 时长挡位：10 / 20 / 40 / 60 秒；MAX 表示不限时，直到点击停止。
- 也可在“自定义”中输入 1–3600 秒之间的任意整数（最长 1 小时）。
- 界面语言：中文 / English，切换后立即重建界面并保存选择。
- 画面质量：5–60 FPS、0.5–50 Mbps 视频码率可调。
- 鼠标指针默认绘制进录制画面，可在“画面与声音”中关闭。
- 声音：可单独或同时录制“系统声音（扬声器环回）”和“麦克风”，可选择具体设备，并设置 AAC 音频码率。
- 视频使用 H.264 编码（自动检测显卡硬件加速，支持 NVIDIA NVENC / Intel QSV / AMD AMF，无硬件支持或小尺寸时自动无缝回退到 CPU 编码；支持通过环境变量 QUICKREC_VIDEO_ENCODER 强制指定），音频使用 AAC，最终合成为带音轨的 MP4。
- 性能优化与低 CPU 占用：采用 Win32 GDI 共享内存（DIBSection）零拷贝屏幕采集、局部光标修补（避免每帧全图拷贝）、高效管道缓冲与高精度计时，大幅降低录像时的 CPU 占用率。
- 保存目录可自由选择，默认使用 Windows“视频”文件夹；若该目录不可写，启动时自动切换到程序目录的 `output` 并在底部提示行显示。
- 目录选择使用内置 Tk 选择器，不依赖 Windows Shell 对话框，避免在受限环境中卡死。
- 录制开始后主窗口自动隐藏到系统托盘，对应录制屏幕的右下角显示倒计时；倒计时框内提供“停止 / Stop”按钮，可随时结束录制并保存。
- 点击窗口右上角最小化时也会隐藏到系统托盘；托盘菜单可恢复主界面。
- 倒计时浮层已做捕获排除（Windows 10 2004+），尽量不出现在成片中。
- 录制完成后自动恢复主界面，并弹出居中于主界面的纯 Tk 模态提示窗，显示文件名、时长、大小、画质、音频信息，并可打开所在文件夹。
- 自动避开重名文件；记住上次的时长、画质、音频设备、语言与保存目录。
- 全程本地运行，不联网、不上传任何内容。

## 界面语言

在窗口右上角选择“中文”或“English”。语言选择会保存到设置文件，下次启动继续使用；录制倒计时、框选界面、目录选择器、托盘菜单和提示框也会跟随切换。

![QuickRec English UI](docs/screenshot-main-en.png)

## 环境要求

- Windows 10 / 11
- Python 3.12+
- 依赖：`mss`、`numpy`、`soundcard`、`imageio-ffmpeg`
- 打包 exe 需要额外安装 `pyinstaller`（仅构建机需要）
- `soundcard` 使用 Windows WASAPI，提供系统声音环回与麦克风采集。
- `imageio-ffmpeg` 自带 FFmpeg 可执行文件，无需另外安装 FFmpeg。
- Tkinter 随 Windows 版 Python 一起安装。

## 安装与启动

在项目目录中执行：

```powershell
python -m pip install -e .
python run.py
```

不希望保留控制台窗口时，可以使用 `pythonw run.py` 启动；运行日志仍会写入 `%LOCALAPPDATA%\QuickRec\app.log`。

如果项目内存在已安装依赖的 `.venv`，而当前 `python` 缺少音视频组件，`run.py` 会自动切换到 `.venv\Scripts\pythonw.exe` 启动，避免出现“音频组件未安装”的提示。也可以手动执行：

```powershell
.\.venv\Scripts\python run.py
```

安装后也可以直接运行命令：

```powershell
quickrec
```

## 打包为 exe

需要 64 位 Python 3.12+。如果本地还没有 `.venv` 虚拟环境，先在项目根目录创建虚拟环境并安装项目依赖与 `pyinstaller`：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pyinstaller
```

（若已存在安装好项目依赖的 `.venv`，只需确保安装了 `pyinstaller`：`.\.venv\Scripts\python.exe -m pip install pyinstaller`。）

项目包含单文件、目录两种 PyInstaller 配置和统一构建脚本：

目录模式（推荐，启动快，不需要每次解压依赖）：

```powershell
.\.venv\Scripts\python.exe packaging\build_exe.py --mode onedir
```

生成：

```text
dist\QuickRec\
├── QuickRec.exe
└── _internal\...
```

分发时保留整个 `dist\QuickRec` 文件夹，可压缩成 zip 后复制到其他电脑；目标电脑无需安装 Python、Tk 或 FFmpeg。

单文件模式：

```powershell
.\.venv\Scripts\python.exe packaging\build_exe.py --mode onefile
```

生成 `dist\QuickRec.exe` 单个文件。

构建脚本会先运行单元测试，再调用 PyInstaller，最后启动成品执行自检，检查依赖导入、应用图标、Tk、屏幕采集、内置 FFmpeg 的 H.264/AAC 编码以及音频设备枚举。

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--mode onedir` | 目录模式，推荐；产物为 `dist\QuickRec\` |
| `--mode onefile` | 单文件模式；产物为 `dist\QuickRec.exe`（默认） |
| `--skip-tests` | 跳过单元测试（不建议） |
| `--skip-smoke-test` | 跳过成品自检（受限沙箱或无法启动图形界面时使用） |

也可以直接调用 PyInstaller：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\short_video_capture_onedir.spec
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\short_video_capture.spec
```

打包说明：

- 目录模式不会在启动时解压依赖，启动更快，也更适合受限账户或低权限沙箱；复制时不要把 `_internal` 拆开。
- 单文件模式首次启动时会把运行时解压到系统临时目录，启动稍慢；在受限沙箱中可能报 `Failed to extract VCRUNTIME140.dll`，这不代表成品不可用，可在普通桌面验证或改用目录模式。
- 程序未做代码签名，复制到其他电脑首次运行时 Windows SmartScreen 可能提示“未知发布者”，选择“更多信息 → 仍要运行”即可。
- 少数杀毒软件可能对 PyInstaller 程序误报，可将程序加入信任列表，或改用源码方式运行。
- 不要把程序放在只读目录：若系统“视频”文件夹不可写，程序会把录制文件保存到程序同目录的 `output` 文件夹。

## 使用方法

> [!IMPORTANT]
> 强烈推荐您使用管理员权限运行此软件。否则软件将无法最小化到系统托盘，也无法使用系统默认的视频存储路径。

1. 选择录制时长：点击 10 / 20 / 40 / 60 秒快捷键，或点击 MAX 不限时录制，也可在“自定义”输入框中填写秒数（支持键盘上下键微调）。
2. 设置画面与声音：
   - 单行调整帧率（5–60 FPS）、视频码率（Mbps）和音频（kbps），均支持输入或键盘上下键微调。
   - 勾选“录制鼠标指针”可把光标画进视频。
   - 勾选“系统声音”并选择扬声器设备，可录制电脑正在播放的声音。
   - 勾选“麦克风”并选择麦克风设备。
   - 设备变化后可点击“刷新设备”重新枚举。
3. 确认“保存位置”，可点击“浏览...”选择其他文件夹。
4. 点击“开始录制”。
5. 屏幕变暗后按住鼠标左键拖动，框选要录制的区域；松开后确认操作条会出现在选区所在屏幕上，可点击重新框选。
6. 点击“开始录制”或按 `Enter` 开始；按 `Esc` 或右键取消。
7. 录制期间主窗口隐藏到系统托盘，被录制区域所在屏幕的右下角显示倒计时。MAX 模式显示已录制时长。
8. 到点自动停止；也可以点击右下角倒计时框里的“停止”按钮，或右键托盘图标选择“停止并保存”。主界面恢复后会在其正中央弹出“录制完成”提示。

框选阶段快捷键：

| 操作 | 快捷键 |
| --- | --- |
| 开始录制 | `Enter` |
| 取消框选 | `Esc` |
| 取消框选（鼠标） | 右键 |

窗口最小化：

- 空闲时点击右上角最小化按钮，窗口会从任务栏消失并隐藏到系统托盘。
- 右键托盘图标选择“显示主界面”可恢复。
- 录制期间同样隐藏到托盘；结束后自动恢复主界面。

## 输出与配置

- 默认输出目录：Windows 已知文件夹“视频”，通常为 `C:\Users\<用户名>\Videos`。
- 文件名格式：`QuickRec_2026-10-19_08-30-05.mp4`；重名时自动追加 `_2`、`_3`。
- 视频：H.264，帧率与视频码率由主界面设置决定。
- 音频：48 kHz 立体声 AAC；系统声音和麦克风同时开启时会混合为一条音轨。
- 用户设置：程序目录下的 `settings.json`，首次启动即创建，可直接查看、备份或删除；旧版 `%APPDATA%\QuickRec` 或 `ShortVideoCapture` 设置会在首次启动时自动兼容读取并迁移到该文件。
- 运行日志：`%LOCALAPPDATA%\QuickRec\app.log`

## 已知限制

- “视频码率”是编码目标/上限，实际文件码率会随画面复杂度变化；静态画面通常明显低于设定值。
- 系统声音通过扬声器环回录制，依赖 Windows WASAPI 与声卡驱动；部分蓝牙设备或虚拟声卡可能不支持环回。
- 系统声音与麦克风会混合成一条音轨，不提供两条独立音轨。
- 鼠标指针由程序逐帧绘制到视频中；关闭“录制鼠标指针”后成片不包含光标。
- 高分辨率 + 高帧率（如 4K/60 FPS）在支持的显卡上会自动启用硬件编码大幅降低 CPU 负载；但高画质与长时间录制仍会占用较多磁盘空间，请确保目标磁盘有足够余量。
- “倒计时不出现在成片中”依赖 Windows 10 2004 及以上版本提供的捕获排除接口；旧系统上倒计时若与选区重叠，可能会被录进画面。
- 系统托盘依赖 Windows 资源管理器。在低完整性/受限进程中运行导致托盘不可用时，程序会退回到任务栏最小化继续录制，并按时自动完成。

## 项目结构

```text
.
├── run.py                         # 免安装启动入口
├── pyproject.toml                 # 依赖与打包配置
├── packaging/
│   ├── build_exe.py               # 单文件/目录构建与成品自检
│   ├── launcher.py                # PyInstaller 打包入口
│   ├── short_video_capture.spec   # 单文件模式配置
│   ├── short_video_capture_onedir.spec # 目录模式配置
│   └── version_info.txt           # exe 版本信息
├── src/short_video_capture/
│   ├── app.py                     # 主界面与录制流程
│   ├── capture.py                 # 屏幕采集与逐帧调度
│   ├── media.py                   # FFmpeg H.264 编码与命令构造
│   ├── audio.py                   # WASAPI 设备枚举与音频采集
│   ├── pipeline.py                # 视频、音频与混流编排
│   ├── config.py                  # 设置、输入校验
│   ├── i18n.py                    # 中英文界面文案
│   ├── directory_picker.py        # 纯 Tk 文件夹选择器
│   ├── dialogs.py                 # 居中于父窗口的纯 Tk 模态提示弹窗
│   ├── countdown.py               # 目标屏幕右下角倒计时与停止按钮
│   ├── cursor.py                  # 鼠标指针逐帧合成
│   ├── paths.py                   # 默认目录、文件名、打开文件夹
│   ├── region_selector.py         # 跨多屏全屏框选与确认工具条
│   ├── tray.py                    # Win32 系统托盘
│   ├── widgets.py                 # 纯 Tk + NumPy 抗锯齿圆角控件库
│   └── win32.py                   # DPI、多屏工作区、窗口定位、捕获排除
├── tests/                         # unittest 自动化测试
├── docs/screenshot-main-zh.png    # 中文主界面截图
├── docs/screenshot-main-en.png    # English UI screenshot
└── docs/screenshot-countdown.png  # 倒计时与停止按钮截图
```

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖录制逐帧调度、高帧率补齐、MAX 不限时停止、Win32 GDI 采集与局部鼠标修补、硬件编码器探测与小选区回退、首帧管道异常自动降级 libx264、鼠标指针逐帧合成、停止与取消、帧率/码率/时长校验、音视频混流命令、音频设备名称去重、多显示器虚拟桌面与负坐标映射、确认操作条与倒计时跨屏定位、主窗口居中模态弹窗、选区规范化、输出路径去重、写权限探测、目录选择器、中文/空格目录、配置读写与损坏配置回退、抗锯齿圆角控件与指示灯渲染、中英文文案与语言回退。

界面与真实录制已在 Windows 上手动验证：启动主界面、中英文切换、单屏与扩展双屏框选、60 秒与 MAX 挡位、倒计时停止按钮、自定义时长、H.264 视频、鼠标指针进画面、系统声音与麦克风混音、提前停止、取消、居中完成弹窗、最小化到托盘逻辑、受限目录自动回退、内置目录选择器及 MP4 可播放性。
