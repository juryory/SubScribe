# SRT 字幕拆分 & AI处理工具

一款用于处理长时间直播/视频字幕的桌面工具。可以将SRT字幕文件按时长拆分，并调用AI进行内容总结处理。

## ✨ 功能特点

- 📄 **字幕拆分** - 按指定时长将长字幕拆分为多个片段
- 🔄 **重叠支持** - 支持设置片段间的重叠时长，确保内容连贯
- 🤖 **AI处理** - 调用DeepSeek API对每个片段进行智能处理
- 📝 **自定义提示词** - 可自定义AI处理指令
- 📊 **实时输出** - 流式显示AI处理过程
- 🖱️ **拖拽上传** - 支持直接拖拽SRT文件到窗口
- 💾 **配置保存** - 自动保存API密钥和提示词配置

## 📁 输出结构

处理完成后，会在SRT文件所在目录生成以下文件：

```
[SRT文件目录]/
├── 拆分字幕/
│   ├── 文件名-Part01.md
│   ├── 文件名-Part02.md
│   └── ...
├── 分段总结/
│   ├── 文件名-Part01.md
│   ├── 文件名-Part02.md
│   └── ...
└── 文件名-总结.md
```

## 🚀 快速开始

### 方式一：使用打包好的EXE（推荐）

1. 下载 `dist/SRT字幕处理工具.exe`
2. 双击运行即可，无需安装Python环境

### 方式二：从源码运行

```bash
# 1. 克隆项目
git clone <repository_url>
cd auto-srt

# 2. 创建虚拟环境
python -m venv venv

# 3. 激活虚拟环境
# Windows CMD:
venv\Scripts\activate.bat
# Windows PowerShell:
.\venv\Scripts\Activate.ps1

# 4. 安装依赖
pip install -r requirements.txt

# 5. 运行程序
python srt_splitter_gui.py
```

## 📖 使用说明

### 1️⃣ 配置API密钥

1. 打开程序，切换到「🔑 API设置」标签页
2. 输入DeepSeek API密钥
3. 可选：点击「🔗 测试连接」验证密钥是否有效
4. 可选：点击「🔄 获取模型」获取可用模型列表
5. 点击「💾 保存设置」

### 2️⃣ 设置提示词

1. 切换到「📝 提示词」标签页
2. 输入AI处理指令（例如：总结要点、提取关键信息等）
3. 点击「💾 保存提示词」

### 3️⃣ 处理字幕

1. 切换到「🏠 主界面」标签页
2. 拖拽SRT文件到窗口，或点击「浏览」选择文件
3. 设置拆分时长（默认30分钟）
4. 设置重叠时长（默认1分钟）
5. 点击「🚀 开始处理」

## ⚙️ 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 拆分时长 | 每个片段的时长（分钟） | 30 |
| 重叠时长 | 相邻片段的重叠时长（分钟） | 1 |

**示例**：2小时字幕，拆分时长30分钟，重叠时长1分钟

- Part01: 00:00 - 00:31
- Part02: 00:30 - 01:01
- Part03: 01:00 - 01:31
- Part04: 01:30 - 02:01

## 🔧 技术栈

- **Python 3.10+**
- **CustomTkinter** - 现代化GUI界面
- **httpx** - HTTP客户端（支持流式响应）
- **windnd** - Windows拖拽支持
- **PyInstaller** - 打包为EXE

## 📋 依赖清单

```
httpx>=0.25.0
customtkinter>=5.2.0
windnd>=1.0.7
```

## 🗂️ 项目结构

```
auto-srt/
├── srt_splitter_gui.py    # 主程序（GUI版本）
├── requirements.txt       # Python依赖
├── config.json           # 配置文件（运行后自动生成）
├── README.md             # 说明文档
├── dist/                 # 打包输出目录
│   └── SRT字幕处理工具.exe
├── build/                # 构建临时文件
└── venv/                 # Python虚拟环境
```

## 🔐 配置文件说明

`config.json` 会自动保存以下配置：

```json
{
    "api_keys": {
        "deepseek": "your-api-key",
        "deepseek_model": "deepseek-chat",
        "deepseek_url": "https://api.deepseek.com/chat/completions"
    },
    "prompt": "你的提示词内容"
}
```

## ❓ 常见问题

### Q: 程序无法启动？
A: 确保系统为Windows 10/11，如果使用源码运行请确保Python版本 >= 3.10

### Q: API测试连接失败？
A: 检查API密钥是否正确，网络是否正常，可尝试更换API地址

### Q: 拖拽功能不工作？
A: 可以使用「浏览」按钮选择文件，或直接复制文件路径粘贴到输入框

### Q: 配置没有保存？
A: 确保EXE所在目录有写入权限，配置文件会保存在EXE同目录下

## 📄 许可证

MIT License

## 🙏 致谢

- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) - 现代化Tkinter主题
- [DeepSeek](https://www.deepseek.com/) - AI API服务

