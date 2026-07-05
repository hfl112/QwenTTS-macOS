# QwenTTS — 本地离线 TTS 的原生 macOS 客户端

QwenTTS 是一个 **完全在本机推理** 的文字转语音工具:AppKit 原生菜单栏 App + FastAPI Python 后端,基于 [MLX-Audio](https://github.com/Blaizzy/mlx-audio) 在 Apple Silicon 上运行 Qwen3-TTS 模型。支持即时朗读、长文保存朗读、双人对话播客生成、网页/YouTube 内容朗读(配套 Chrome 扩展),全程无需把文本发给任何云端 TTS 服务。

## 0. 系统要求

- **Apple Silicon Mac(M1 及以上)** — 推理基于 MLX,不支持 Intel 芯片。
- **macOS 14.0(Sonoma)及以上**。
- 磁盘空间约 **6 GB**(应用本体 + 首次启动下载的模型权重 ~5.2 GB)。
- 内存建议 16 GB 及以上。

## 1. 安装应用

1. 从 GitHub Releases 下载最新打包的 `QwenTTS.dmg` 安装包。
2. 双击打开 `QwenTTS.dmg`,将 `QwenTTS.app` 拖入您的 **应用程序 (Applications)** 文件夹中。
3. **绕过 Gatekeeper 拦截(必做)**:本应用未做苹果开发者公证,从网上下载后首次启动会被 macOS 拦截,且较新系统中"右键打开"也不再放行。请打开终端运行:
   ```bash
   xattr -cr /Applications/QwenTTS.app
   ```
   (或在 系统设置 → 隐私与安全性 中点"仍要打开"。)
4. 双击 `/Applications/QwenTTS.app` 启动。启动后它常驻在 macOS 顶部菜单栏中。

## 2. 本地模型配置

为减小安装包体积,模型权重(约 5.2 GB)**没有**打包在应用内。首次启动时,设置向导会引导您下载模型;也可以稍后在 **设置 → 本地模型管理** 中操作:

- 推荐(默认)模型为 **`Qwen3-TTS-0.6B-4bit`**——它是当前唯一能在普通 M 系列芯片上实时朗读的档位。`Qwen3-TTS-1.7B-8bit` 音质更好但合成速度慢,适合离线生成播客。
- 模型自动下载到 `~/Library/Application Support/QwenTTS/Models/`;若您已在别处下载过模型,向导里可直接"选择已有模型目录"。

**模型许可**:模型权重从 Hugging Face 的 [mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit) 下载,属 Qwen(通义千问)系列的社区量化版本。权重不属于本项目、不随本仓库分发,下载与使用需遵循其模型卡标注的上游许可条款。

## 3. 接入 Chrome 网页朗读扩展

应用自带安全配对机制,保障本地 API 不被恶意网页滥用。

1. 在应用的 **"系统设置"** 中,找到 **"扩展配对码"** 字段。
2. 点击 **"生成配对码"**,系统会随机生成一个 8 位安全令牌。
3. 点击底部的 **"保存修改"**。
4. 打开您的 Chrome/Edge 浏览器,安装配套的 `qwen-tts-extension` 扩展。
5. 在扩展的配置面板中,将生成的 8 位配对码填入 **Pairing Token** 栏中,保存即可成功配对连接。

## 4. 运行时文件夹结构

配置和生成物存储于系统 Application Support 中:
- **配置文件**: `~/Library/Application Support/QwenTTS/Data/`
- **下载模型**: `~/Library/Application Support/QwenTTS/Models/`
- **生成播客**: `~/Library/Application Support/QwenTTS/Podcasts/`
- **临时缓存**: `~/Library/Application Support/QwenTTS/Cache/`
- **诊断日志**: `~/Library/Application Support/QwenTTS/Logs/`

## 5. 从源码自行打包

打包机需要:**完整 Xcode**(非仅 Command Line Tools)、[`uv`](https://github.com/astral-sh/uv)、[`xcodegen`](https://github.com/yonaskolb/XcodeGen)(可选,改动 `project.yml` 后需要)、以及一个 **静态链接的 arm64 ffmpeg**(通过环境变量 `TTS_FFMPEG_PATH` 指定;打包脚本会拒绝 Homebrew 的动态链接版本)。首次打包需联网下载 python-build-standalone 运行时。

```bash
python package_release.py                    # 构建 app + 独立 Python 运行时 + DMG
python run_diagnostics.py dist/QwenTTS.app   # 验证打包产物
```

默认使用 ad-hoc 签名(无需开发者证书);如有 Developer ID,设置 `TTS_SIGNING_IDENTITY` 环境变量即可启用正式签名,再用 `notarize_dmg.py` 公证。

## 6. 许可与第三方

- 本项目采用 **MIT License**(见 [LICENSE](LICENSE))。
- 捆绑的 [mlx_audio](https://github.com/Blaizzy/mlx-audio) 为 MIT License(见 `backend/mlx_audio/LICENSE`)。
- `backend/reference/` 下的音色参考音频均为 **AI 生成**,不涉及真人录音与第三方版权(见 `backend/reference/README.md`)。
- 模型权重许可见上文第 2 节。
