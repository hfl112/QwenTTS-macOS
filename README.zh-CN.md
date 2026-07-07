# QwenTTS macOS

QwenTTS 是一个面向 Apple Silicon Mac 的本地文字转语音应用。它把 Qwen3-TTS 和 MLX-Audio 封装成原生 macOS 客户端，支持粘贴文本朗读、网页/YouTube 内容朗读、稍后阅读、播客生成、内容库管理和菜单栏播放。

TTS 推理在本机完成，不需要把文本发送到云端 TTS 服务。

[下载最新 DMG](https://github.com/hfl112/QwenTTS-macOS/releases) · Apple Silicon only · macOS 14+ · Local-first

![QwenTTS reading a web article](docs/images/main-window-url-reading.png)

## 功能亮点

- 本地运行 Qwen3-TTS，基于 MLX 在 Apple Silicon 上推理。
- 支持直接粘贴文本、长文章、网页链接和 YouTube 内容朗读。
- 支持原文、翻译、讨论、双人翻译等阅读模式。
- 支持把文章保存到内容中心，稍后继续听。
- 支持生成单人音频或双人对话播客。
- 支持状态栏常驻播放、暂停、继续、上一段、下一段。
- 支持本地模型下载、选择已有模型目录、性能模式和电池策略设置。
- 配套 Chrome/Edge 扩展可把浏览器内容发送到本地 QwenTTS。

## 截图

### 朗读网页和长文

![QwenTTS reading console](docs/images/main-window-url-reading.png)

### 管理稍后阅读和播客

![QwenTTS library and podcasts](docs/images/library-podcasts.png)

### 管理本地模型

![QwenTTS local model settings](docs/images/settings-local-model.png)

## 系统要求

- Apple Silicon Mac，M1 及以上。
- macOS 14.0 Sonoma 及以上。
- 磁盘空间约 6 GB，包含应用本体和首次启动下载的模型权重。
- 建议 16 GB 内存及以上。
- 不支持 Intel Mac。
- 使用网页/YouTube 阅读功能需要 [Node.js](https://nodejs.org) 和
  [defuddle](https://github.com/kepano/defuddle) 命令行工具（把抓取的网页修剪成干净正文）。一次性安装：

  ```bash
  brew install node && npm install -g defuddle
  ```

  其余功能（直接朗读、稍后阅读、播客）不装它也能用。

## 安装

1. 打开 [Releases](https://github.com/hfl112/QwenTTS-macOS/releases)，下载最新的 `QwenTTS.dmg`。
2. 双击打开 DMG，把 `QwenTTS.app` 拖入 `Applications` 文件夹。
3. 首次启动前，如果 macOS 拦截未公证应用，请在终端运行：

   ```bash
   xattr -cr /Applications/QwenTTS.app
   ```

   也可以在系统设置的“隐私与安全性”里选择“仍要打开”。

4. 启动 `QwenTTS.app`。应用会常驻在 macOS 菜单栏，并打开主窗口。

## 本地模型

模型权重不会打包进 DMG。首次启动时，QwenTTS 会通过设置向导检查环境并引导下载模型。之后也可以在“设置”里的“Local Model”区域管理模型。

默认推荐模型：

- `Qwen3-TTS-0.6B-4bit`：推荐使用，速度较快，适合实时朗读。
- `Qwen3-TTS-1.7B-8bit`：音质更好但更慢，更适合离线生成较长音频或播客。

模型默认存放在：

```text
~/Library/Application Support/QwenTTS/Models/
```

如果你已经在其他位置下载过模型，可以在首次启动向导或设置页中选择已有模型目录。

模型权重来自 Hugging Face 上的社区量化版本，例如 [mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit)。模型权重不属于本项目，也不随仓库分发。下载和使用模型时请遵守对应模型卡中的上游许可。

## 浏览器扩展

QwenTTS 可以配合 Chrome/Edge 扩展朗读网页内容。应用内置配对码机制，避免任意网页直接调用本地 API。

1. 打开 QwenTTS 的“设置”。
2. 找到扩展配对码，点击生成配对码。
3. 保存设置。
4. 在 Chrome/Edge 中安装配套的 `qwen-tts-extension`。
5. 在扩展设置里填入配对码。

配对完成后，可以把浏览器中的网页内容发送给本地 QwenTTS 朗读或生成播客。

## 隐私说明

QwenTTS 的 TTS 推理在本地 Mac 上运行，朗读文本不会发送到云端 TTS 服务。

需要注意的是，网页抓取、翻译、摘要或讨论模式可能会使用你在“AI 引擎”中配置的第三方 LLM 或翻译服务。如果你没有启用这些服务，普通文本朗读仍然可以本地完成。

诊断导出不会主动包含保存的文章正文、播客任务正文等用户内容。

## 运行时文件

QwenTTS 的配置、模型和生成物存放在系统 Application Support 目录：

- 配置数据：`~/Library/Application Support/QwenTTS/Data/`
- 本地模型：`~/Library/Application Support/QwenTTS/Models/`
- 生成播客：`~/Library/Application Support/QwenTTS/Podcasts/`
- 临时缓存：`~/Library/Application Support/QwenTTS/Cache/`
- 诊断日志：`~/Library/Application Support/QwenTTS/Logs/`

## 从源码构建

构建 DMG 需要：

- 完整 Xcode，而不只是 Command Line Tools。
- [`uv`](https://github.com/astral-sh/uv)。
- [`xcodegen`](https://github.com/yonaskolb/XcodeGen)，修改 `project.yml` 后需要。
- 静态链接的 arm64 `ffmpeg`，通过 `TTS_FFMPEG_PATH` 指定。打包脚本会拒绝 Homebrew 的动态链接版本。

构建命令：

```bash
python package_release.py
python run_diagnostics.py dist/QwenTTS.app
```

默认使用 ad-hoc 签名，不需要 Apple Developer 证书。如果你有 Developer ID，可以设置 `TTS_SIGNING_IDENTITY` 启用正式签名，并使用 `notarize_dmg.py` 做公证。

## 许可

- 本项目采用 MIT License，见 [LICENSE](LICENSE)。
- 捆绑的 [MLX-Audio](https://github.com/Blaizzy/mlx-audio) 使用 MIT License，见 `backend/mlx_audio/LICENSE`。
- `backend/reference/` 中的参考音频为 AI 生成，见 `backend/reference/README.md`。
- 模型权重许可请参考对应 Hugging Face 模型卡。

