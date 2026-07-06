<div align="center">

# QwenTTS for macOS

**Turn anything into natural speech — privately, on your Mac.**

A native menu-bar app that reads text, articles, web pages and YouTube aloud, and
turns them into two-host podcasts — running the Qwen3-TTS model fully on-device.
No cloud, no account, no subscription.

![macOS 14+](https://img.shields.io/badge/macOS-14+-000000?logo=apple&logoColor=white)
&nbsp;![Apple Silicon](https://img.shields.io/badge/Apple%20Silicon-M1+-333333)
&nbsp;![On-device TTS](https://img.shields.io/badge/TTS-100%25%20on--device-brightgreen)
&nbsp;![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

**English** · [简体中文](./README.zh-CN.md)

<!-- Tip: drop a hero screenshot / demo GIF here before publishing. -->

</div>

---

## Why QwenTTS

Most text-to-speech tools either send your text to a cloud service or sound robotic.
QwenTTS is built around three ideas:

- **🔒 Private by default.** The core text-to-speech runs entirely on your Mac
  (Apple's MLX framework). Your text is never uploaded to a TTS cloud.
- **⚡ Real-time on Apple Silicon.** A 4-bit Qwen3-TTS model synthesizes speech
  faster than real time on ordinary M-series chips — start listening immediately.
- **🎙️ More than a reader.** Clean up long articles, generate two-host podcasts,
  and listen to web pages or YouTube videos — not just paste-and-play.

Free and open-source (MIT). No sign-up, no telemetry.

## What you can do

| | Feature | What it's for |
|---|---|---|
| 🗣️ | **Instant read-aloud** | Paste text, hit play — natural Chinese/English speech in a click. |
| 📄 | **Long-article mode** | Clean up and save long documents, then listen start-to-finish. |
| 🎙️ | **Two-host podcasts** | Turn any article into a natural dual-speaker conversation you can play offline. |
| 🌐 | **Web & YouTube reader** | Send a web page or YouTube video to the app (via the Chrome extension) and listen to a cleaned-up version. |
| ♻️ | **Smart reuse** | Finished audio, cleaned transcripts and sentence-level chunks are cached — the app never burns compute re-doing work it already did. |
| 🤖 | **Optional AI (your key)** | Summaries, translation and podcast scripting via Gemini / Claude / OpenAI / DeepSeek — or a local model. See [privacy](#privacy). |

## Requirements

- **Apple Silicon Mac (M1 or newer).** Inference uses MLX; Intel Macs are not supported.
- **macOS 14.0 (Sonoma) or later.**
- **~6 GB free disk** (app + a ~5.2 GB model downloaded on first launch).
- **16 GB RAM or more** recommended.

## Get started

### Option A — Download the app

1. Download the latest `QwenTTS.dmg` from the [**Releases**](../../releases) page.
2. Open the DMG and drag **QwenTTS.app** into your **Applications** folder.
3. **First launch (Gatekeeper).** The public build isn't Apple-notarized yet, so
   macOS will block it on first open. Clear the quarantine flag once:
   ```bash
   xattr -cr /Applications/QwenTTS.app
   ```
   (Or go to **System Settings → Privacy & Security** and click **Open Anyway**.)
4. Launch it — QwenTTS lives in your macOS menu bar.

### Option B — Build from source

You'll need **full Xcode** (not just Command Line Tools),
[`uv`](https://github.com/astral-sh/uv),
[`xcodegen`](https://github.com/yonaskolb/XcodeGen), and a **statically-linked arm64
ffmpeg** (pointed to via the `TTS_FFMPEG_PATH` env var). The first build downloads a
python-build-standalone runtime.

```bash
python package_release.py                    # builds the app + standalone Python runtime + DMG
python run_diagnostics.py dist/QwenTTS.app   # verifies the built bundle
```

Ad-hoc signing is the default (no developer certificate needed). With a Developer ID,
set `TTS_SIGNING_IDENTITY` for a proper signature, then notarize with `notarize_dmg.py`.

## Set up the local model

To keep the download small, the ~5.2 GB model weights are **not** bundled. A setup
wizard downloads them on first launch (or later under **Settings → Local Model**):

- The default model is **`Qwen3-TTS-0.6B-4bit`** — currently the only tier that reads
  in real time on a typical M-series chip. `Qwen3-TTS-1.7B-8bit` sounds better but is
  slower, so it's best for offline podcast generation.
- Weights download to `~/Library/Application Support/QwenTTS/Models/`. Already have
  them elsewhere? The wizard lets you point at an existing model folder.

> **Model license:** weights come from Hugging Face
> ([mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit)),
> a community quantization of Qwen (Tongyi Qianwen). They are **not** part of this
> project and are governed by the upstream license on their model card.

## Connect the Chrome extension (optional)

For reading web pages and YouTube, the app pairs with a companion extension over a
secure local token — so no random web page can drive your local API.

1. In the app's **Settings**, find **Extension Pairing Code** and click
   **Generate** — you'll get an 8-character token. Click **Save**.
2. Install the `qwen-tts-extension` in Chrome/Edge.
3. Paste the 8-character token into the extension's **Pairing Token** field and save.

## Optional AI features (bring your own key)

Summaries, translation and dual-host podcast scripting are powered by a large language
model. QwenTTS supports **Gemini, Claude, OpenAI, DeepSeek, and a local MLX model**.

- Keys are entered in the app's **AI Engines** settings page and stored locally in the
  app's config — there is **no `.env` fallback** and nothing is sent anywhere except the
  provider you choose.
- The **local model** option needs no key and no network.

## Privacy

- **Text-to-speech is 100% on-device.** The text you read aloud never leaves your Mac.
- **The optional AI features are the only network calls.** When you use summary,
  translation or podcast scripting with a *cloud* provider, that text is sent to the
  provider you configured (with your own API key). Choose the local model to stay fully
  offline. QwenTTS itself has no telemetry.

## Where your files live

Everything is stored under macOS Application Support:

| Path | Contents |
|---|---|
| `~/Library/Application Support/QwenTTS/Data/` | Config & saved items |
| `~/Library/Application Support/QwenTTS/Models/` | Downloaded model weights |
| `~/Library/Application Support/QwenTTS/Podcasts/` | Generated podcasts |
| `~/Library/Application Support/QwenTTS/Cache/` | Temporary cache |
| `~/Library/Application Support/QwenTTS/Logs/` | Diagnostic logs |

## Under the hood

A native **AppKit** menu-bar app boots and supervises a local **FastAPI** backend
(localhost only, per-launch auth token) that runs **Qwen3-TTS** on Apple's **MLX**
via [MLX-Audio](https://github.com/Blaizzy/mlx-audio). All inference and audio playback
happen in the backend; the app is a thin, native client.

## License & credits

- This project is licensed under the **MIT License** — see [LICENSE](LICENSE).
- Bundled [mlx-audio](https://github.com/Blaizzy/mlx-audio) is MIT-licensed
  (see `backend/mlx_audio/LICENSE`). Full attribution is in
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- Reference voice clips under `backend/reference/` are **AI-generated** — no real-person
  recordings, no third-party copyright.
- Model weights are licensed separately (see *Set up the local model* above).
