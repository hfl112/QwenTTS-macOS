<div align="center">

# Local QwenTTS for macOS

**把任何文字变成自然语音 —— 全程在你的 Mac 本地完成。**

一个常驻菜单栏的原生 App:朗读文本、文章、网页和 YouTube,还能把它们做成双人对话播客——
Qwen3-TTS 模型**完全在本机推理**。不上云、不登录、不订阅。

![macOS 14+](https://img.shields.io/badge/macOS-14+-000000?logo=apple&logoColor=white)
&nbsp;![Apple Silicon](https://img.shields.io/badge/Apple%20Silicon-M1+-333333)
&nbsp;![本地推理](https://img.shields.io/badge/TTS-100%25%20本地-brightgreen)
&nbsp;![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

[English](./README.md) · **简体中文**

<!-- 提示:正式发布前在这里放一张主界面截图 / 演示 GIF。 -->

</div>

---

## 为什么做 QwenTTS

市面上的 TTS 工具,要么把你的文字发去云端,要么听起来很机械。QwenTTS 围绕三点来做:

- **🔒 默认隐私。** 核心的文字转语音**完全在本机运行**(苹果 MLX 框架),你的文本绝不上传到任何 TTS 云服务。
- **⚡ Apple Silicon 上实时。** 4-bit 量化的 Qwen3-TTS 在普通 M 系列芯片上**快于实时**合成——点一下就能开始听。
- **🎙️ 不只是"读"。** 长文清洗、双人播客生成、网页/YouTube 朗读——不是简单的粘贴即读。

免费、开源(MIT)。无需注册,无任何遥测。

## 你能用它做什么

| | 功能 | 用途 |
|---|---|---|
| 🗣️ | **即时朗读** | 粘贴文字、点播放——一键得到自然的中/英文语音。 |
| 📄 | **长文模式** | 清洗并保存长文档,从头到尾完整收听。 |
| 🎙️ | **双人播客** | 把任意文章变成自然的双主持人对话,可离线播放。 |
| 🌐 | **网页 & YouTube 朗读** | 通过配套 Chrome 扩展把网页或 YouTube 送进 App,听清洗后的版本。 |
| ♻️ | **智能复用** | 成品音频、清洗文稿、句级音频片段都会缓存——做过的事绝不重复烧算力。 |
| 🤖 | **可选 AI(自带 key)** | 摘要、翻译、播客文稿由 Gemini / Claude / OpenAI / DeepSeek 或本地模型驱动。见[隐私说明](#隐私)。 |

## 系统要求

- **Apple Silicon Mac(M1 及以上)。** 推理基于 MLX,不支持 Intel 芯片。
- **macOS 14.0(Sonoma)及以上。**
- **约 6 GB 可用磁盘**(应用本体 + 首次启动下载的 ~5.2 GB 模型)。
- 建议 **16 GB 及以上内存**。

## 快速上手

### 方式 A —— 下载应用

1. 从 [**Releases**](../../releases) 页下载最新的 `QwenTTS.dmg`。
2. 打开 DMG,把 **QwenTTS.app** 拖入你的**应用程序 (Applications)** 文件夹。
3. **首次启动(绕过 Gatekeeper)。** 公开版尚未做苹果公证,首次打开会被 macOS 拦截。运行一次以下命令清除隔离标记:
   ```bash
   xattr -cr /Applications/QwenTTS.app
   ```
   (或在**系统设置 → 隐私与安全性**中点"仍要打开"。)
4. 启动即可——QwenTTS 常驻在 macOS 顶部菜单栏。

### 方式 B —— 从源码构建

需要:**完整 Xcode**(非仅 Command Line Tools)、[`uv`](https://github.com/astral-sh/uv)、
[`xcodegen`](https://github.com/yonaskolb/XcodeGen),以及一个**静态链接的 arm64 ffmpeg**
(通过环境变量 `TTS_FFMPEG_PATH` 指定)。首次构建会联网下载 python-build-standalone 运行时。

```bash
python package_release.py                    # 构建 app + 独立 Python 运行时 + DMG
python run_diagnostics.py dist/QwenTTS.app   # 验证打包产物
```

默认 ad-hoc 签名(无需开发者证书)。若有 Developer ID,设置 `TTS_SIGNING_IDENTITY` 启用正式签名,再用 `notarize_dmg.py` 公证。

## 配置本地模型

为减小安装包,~5.2 GB 的模型权重**没有**打包在应用内。首次启动时设置向导会引导下载
(也可稍后在**设置 → 本地模型管理**中操作):

- 默认模型是 **`Qwen3-TTS-0.6B-4bit`**——它是当前唯一能在普通 M 系列芯片上**实时朗读**的档位。
  `Qwen3-TTS-1.7B-8bit` 音质更好但更慢,适合离线生成播客。
- 权重下载到 `~/Library/Application Support/QwenTTS/Models/`;若你已在别处下载过,向导里可直接"选择已有模型目录"。

> **模型许可:** 权重来自 Hugging Face
> ([mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit)),
> 是 Qwen(通义千问)系列的社区量化版。权重**不属于**本项目、不随仓库分发,使用需遵循其模型卡标注的上游许可。

## 接入 Chrome 扩展(可选)

朗读网页和 YouTube 时,App 通过一个**安全本地令牌**与配套扩展(已随仓库提供,见 [`qwen-tts-extension/`](./qwen-tts-extension))配对——这样任何网页都无法擅自驱动你的本地 API。

1. 在 App 的**"系统设置"**中找到**"扩展配对码"**,点击**"生成配对码"**得到 8 位令牌,点击**"保存修改"**。
2. 构建内置扩展:
   ```bash
   cd qwen-tts-extension
   npm install
   npm run build          # 产物(已解压的扩展)在 .output/chrome-mv3
   ```
3. 在 Chrome/Edge 打开 `chrome://extensions`,开启**开发者模式**,点**"加载已解压的扩展程序"**,选择 `qwen-tts-extension/.output/chrome-mv3`。
4. 把 8 位配对码填入扩展的 **Pairing Token** 栏并保存。

## 可选 AI 功能(自带 API key)

摘要、翻译、双人播客文稿由大语言模型驱动。QwenTTS 支持
**Gemini、Claude、OpenAI、DeepSeek,以及本地 MLX 模型**。

- API key 在 App 的**「AI 引擎」设置页**填写,存在本地配置里——**没有 `.env` 回退**,除了你选定的供应商外不会发往任何地方。
- **本地模型**选项**不需要 key、不联网**。

## 隐私

- **文字转语音 100% 在本机完成。** 你朗读的文本绝不离开你的 Mac。
- **唯一的联网发生在可选 AI 功能。** 当你用*云端*供应商做摘要/翻译/播客文稿时,该文本会用你自己的 key 发给你配置的供应商;想完全离线就选本地模型。QwenTTS 本身无任何遥测。

## 文件都存在哪

全部存于 macOS Application Support 下:

| 路径 | 内容 |
|---|---|
| `~/Library/Application Support/QwenTTS/Data/` | 配置与已保存条目 |
| `~/Library/Application Support/QwenTTS/Models/` | 下载的模型权重 |
| `~/Library/Application Support/QwenTTS/Podcasts/` | 生成的播客 |
| `~/Library/Application Support/QwenTTS/Cache/` | 临时缓存 |
| `~/Library/Application Support/QwenTTS/Logs/` | 诊断日志 |

## 技术架构

原生 **AppKit** 菜单栏 App 启动并守护一个本地 **FastAPI** 后端(仅监听 localhost、每次启动随机鉴权令牌),
后端通过 [MLX-Audio](https://github.com/Blaizzy/mlx-audio) 在苹果 **MLX** 上运行 **Qwen3-TTS**。
所有推理与音频播放都在后端完成,App 是一个轻量原生客户端。

## 许可与致谢

- **QwenTTS** © 2026 QwenTTS contributors,以 **MIT License** 发布——见 [LICENSE](LICENSE)。
- 内置的 Chrome/Edge 扩展(`qwen-tts-extension/`)是本项目的一部分(MIT),基于 [WXT](https://wxt.dev) 构建。
- 捆绑的 [mlx-audio](https://github.com/Blaizzy/mlx-audio) 为 MIT 许可(见 `backend/mlx_audio/LICENSE`),
  完整署名见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- `backend/reference/` 下的音色参考音频均为 **AI 生成**——非真人录音、无第三方版权。
- 模型权重单独授权(见上文"配置本地模型")。
