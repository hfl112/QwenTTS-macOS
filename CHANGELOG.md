# Changelog

## v0.1.1 — first public build (unreleased)

First public DMG. Apple Silicon (M1+), macOS 14+.

### Fixed
- **URL reader:** lazy-import `youtube-transcript-api` — a missing package no longer
  breaks reading plain web pages (it is only needed for YouTube transcripts).
- **URL reader:** resolve the `defuddle` CLI against Homebrew/npm bin dirs. GUI-launched
  apps inherit a minimal `PATH`, so the packaged backend previously failed every webpage
  parse with `No such file or directory: 'defuddle'`.
- **Setup wizard:** downloads the actual default model (`Qwen3-TTS-0.6B-4bit`, the only
  tier that reads in real time) instead of the bf16 build, with honest download progress.
- **Player:** stop/close the PortAudio stream outside the lock (shutdown deadlock).

### Added
- **Podcast quality guard:** detects the rare autoregressive "code degeneration" garble
  (sustained noise-like low-energy tail) and re-synthesizes the chunk.
- **Independent podcast performance profile** (fast/balanced/quiet) — podcast generation
  no longer inherits the live-read profile.
- Security hardening: constant-time token comparison, FastAPI docs endpoints disabled,
  Hugging Face telemetry disabled.

### Docs
- README: Node.js + `defuddle` documented as a prerequisite for the web/YouTube reader.
- Screenshots on the landing page (sensitive content pixelated).

## v0.1.0

Initial public source release (no binary).
