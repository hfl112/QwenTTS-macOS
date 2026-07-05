# Third-Party Notices

QwenTTS for macOS is distributed under the MIT License (see `LICENSE`). It
redistributes and depends on the following third-party components, each under
its own license.

## Redistributed in this repository (vendored source)

### mlx-audio
- **License:** MIT — Copyright (c) 2024 Prince Canuma
- **Upstream:** https://github.com/Blaizzy/mlx-audio
- **Location:** `backend/mlx_audio/` (full architecture source; model *weights* are
  excluded and downloaded at runtime — see below).
- **License text:** preserved at `backend/mlx_audio/LICENSE`.
- **Notes:** This is a vendored copy and may contain local modifications made for
  this application. mlx-audio itself bundles several model architectures (Qwen3-TTS,
  Encodec, SNAC, Mimi, Descript, Chatterbox, Vocos, etc.); any component that ships
  its own license retains it at its path (e.g.
  `backend/mlx_audio/mlx_audio/vad/models/smart_turn/LICENSE`).

## Downloaded at runtime — NOT redistributed here

### Qwen3-TTS model weights
- The TTS model weights are **not** included in this repository. They are downloaded
  on first run from Hugging Face (`mlx-community/Qwen3-TTS-*`) and are subject to the
  model's own license as published there. Review and accept that license before use.

## Installed as dependencies — NOT redistributed here

The Python backend declares its dependencies in `requirements.prod.txt` /
`requirements.prod.lock`; they are installed (not vendored) and each remains under
its own license. Principal runtime dependencies include: `mlx`, `mlx-lm`,
`transformers`, `huggingface_hub`, `fastapi`, `uvicorn`, `pydantic`, `numpy`,
`scipy`, `sounddevice`, `miniaudio`, and the optional LLM/URL providers
`anthropic`, `openai`, `google-genai`, and `youtube-transcript-api`.
