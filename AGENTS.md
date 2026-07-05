# Repository Guidelines

Project instructions for **QwenTTS** — a native macOS (AppKit) TTS app with a FastAPI Python backend doing on-device MLX inference. This file is the single source of truth; `CLAUDE.md` just imports it.

## Repository layout

- `QwenTTS/` — AppKit app + Xcode project. Swift under `QwenTTS/QwenTTS/`, grouped `Application/`, `Backend/`, `StatusBar/`, `Windows/`, `UI/`, `Models/`, `State/`.
- `backend/` — the independent Python backend snapshot the app runs. It **bundles its own pinned copies** of `mlx_audio/` (upstream: [Blaizzy/mlx-audio](https://github.com/Blaizzy/mlx-audio), MIT), `URL-Reader/`, and `reference/`.
- `package_release.py`, `make_dmg.py`, `notarize_dmg.py`, `run_diagnostics.py` — release packaging/verification.

## Build, test & run

Dev Python env: any Python 3.11+ with `backend/requirements.prod.txt` installed (e.g. a conda/venv env of your choice).

macOS app (from `QwenTTS/`):
```bash
xcodegen generate     # regenerate project from project.yml after adding files
xcodebuild -project QwenTTS.xcodeproj -scheme QwenTTS -configuration Release -derivedDataPath build/DerivedData clean build
xcodebuild -project QwenTTS.xcodeproj -scheme QwenTTS CODE_SIGNING_ALLOWED=NO build   # dev, unsigned
```
Full Xcode lives in `/Applications`; if only Command Line Tools are active, build via `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer` rather than giving up.

Backend (from `backend/`):
```bash
python core/backend.py                                   # standalone run (binds 127.0.0.1:8001)
python -m pytest core/tests/ -v                          # all backend tests
python -m pytest core/tests/test_services_smoke.py -v    # one file
python -m pytest core/tests/ -k "test_podcast" -v        # by pattern
```
Tests use mock/stub inference (no real MLX weights). Swift logic tests run via the `QwenTTS` scheme's test action (`xcodebuild ... test`).

Release (from repo root):
```bash
python package_release.py                    # builds app + standalone python runtime + DMG
python run_diagnostics.py dist/QwenTTS.app   # verify a built bundle
```
The standalone runtime is built by `package_release.py` (`create_python_runtime`, via python-build-standalone). Do **not** reintroduce a `uv venv` / non-relocatable runtime for packaging.

## Architecture (the big picture)

Three process layers, HTTP-connected, with crash-safe lifecycle:

**AppKit app** boots and owns the backend as an **independent process group** (`posix_spawn`), talks to it over `localhost:8001`, and holds the write end of a **watchdog pipe**. Key Swift classes: `ApplicationCoordinator` (boot/coordinate), `BackendProcessManager` (process group state machine), `BackendAPIClient` (HTTP + auth token), `AppStateStore` (central state mirroring backend snapshot + optimistic UI updates), `MainWindowController` (tabs: Console/Saved/Podcasts/URL Reader/Cache/Settings). The app does no MLX inference or multiprocessing itself.

**FastAPI backend** (`backend/core/backend.py`) runs independently of the app's lifecycle and owns all Python subprocesses. `RuntimeSupervisor` centralizes shutdown (workers → threads → queues → cleanup, SIGTERM→SIGKILL fallback). On startup it writes `~/Library/Application Support/QwenTTS/runtime.json` (port/pid/instance_id) for dynamic-port discovery; removed on shutdown.

**Workers & services** (`backend/core/services/`): `PlaybackService`, `PodcastService` (+ `podcast_jobs.py`), `SavedItemsService`, `CacheService`, `UrlJobStore`, `RuntimeEventLog`, `performance.py` (thermal profiles). Multiprocessing workers do MLX inference; background threads do PortAudio playback, device monitoring, caching.

### Central seams (ADRs — read before touching these)

- **InferenceEngine seam (ADR-001):** `backend/core/inference/engine.py` is the *single* entry for both read and podcast synthesis (`engine.synthesize(request)`), owning the worker main loop, priority queue, param building, normalization, and read-through cache. `model_backend.py` is the narrow adapter over MLX models. **There is no `tts_engine.py` anymore** — do not reintroduce a second inference path or a separate podcast process/`gpu_lock`.
- **PlaybackService as sole playback owner (ADR-002):** single `play()` entry; audio feeder lives inside PlaybackService. Don't add parallel playback paths.
- **Playback-truth seam (ADR-003):** playback state is one computed `playback_status` value. `/snapshot` and `/status` expose it and derive legacy `is_playing`/`is_paused`/`main_is_playing` **from** it (single source, no racy re-derivation). Swift mirrors it via `PlaybackStatus`/`PlaybackPresentation`/reconciler pure functions. When changing playback state, change the predicate, not each field.

### Provider-agnostic engine layer (`backend/URL-Reader/`)

Config-driven, fully decoupled from `.env` — keys come **only** from the `engines` section of `config.json`. `translation_engine.py` = machine translation for `translate` mode (Google free / Microsoft / DeepL, no LLM). `llm_engine.py` = `call_llm()` for `podcast-discuss`/`podcast-trans` (Gemini/Claude/OpenAI/DeepSeek/local MLX, one model per provider, `selected`-first with cross-provider fallback, `probe_provider()` for `/engines/check`). `reader_service.process_with_llm(text, mode)` is the dispatch entry; `process_url_job` handles YouTube transcript / HTML → markdown → process. **To run AI summary / dual-podcast you must set an LLM key in the "AI 引擎" page** (no `.env` fallback).

### API security & runtime paths

- **Management token** (`X-Management-Token`): random UUID per launch, required for `/control/*`, `/settings`, `/engines*`. Only the app has it (`TTS_MANAGEMENT_TOKEN` env). **Extension pairing token**: user-provided, for the Chrome extension. Default: localhost-only bind, debug/CORS off in release.
- All runtime paths are resolved once at startup from env vars set by the app (`TTS_APP_SUPPORT_PATH`, `TTS_DATA_PATH`, `TTS_CACHE_PATH`, `TTS_PODCASTS_PATH`, `TTS_MODELS_PATH`, `TTS_REFERENCE_PATH`, `MLX_AUDIO_PATH`, `TTS_FFMPEG_PATH`). **Never hardcode paths or infer them from repo depth** — the app must run from any install location. Use `paths.runtime_paths` in Python.

### Performance profiles (`services/performance.py`) & the model

`fast` / `balanced` / `quiet` are the **single source of truth**, used verbatim by `processor.smart_split`, `podcast_service`, and the AppKit Settings picker. Each defines `chunk_sleep`, `sentence_sleep`, `buffer_high_sec`, `buffer_low_sec`, `podcast_pause_poll_sec`, `model`. Unknown names fall back to `balanced`. Do not rename a key without updating all three callers.

Values are **derived from `backend/tools/profile_gen.py` probe data, not folklore** (2026-07-02): balanced = live-read default, quiet = podcast default (~40% duty). **The active model is `Qwen3-TTS-0.6B-4bit`** (pinned in storage default config + quiet profile) — the bf16 0.6B cannot do realtime (RTF 1.08 vs 0.35; ICL cloning costs 2.75×, thermal downclock up to 1.7×). If you change model or hardware, re-run the probe (`TTS_MODELS_PATH=<your-models-dir> python tools/profile_gen.py`) and re-derive. `build_generate_kwargs` honors `config["use_icl"]=False` to skip ICL cloning (emergency ~3× lever, unwired by default).

## Coding style & conventions

Swift: standard AppKit — `UpperCamelCase` types, `lowerCamelCase` members, `…ViewController` suffixes. All UI mutation on the main thread (`DispatchQueue.main.async`); data binding flows through `AppStateStore`. Python: Black/isort-compatible, type-oriented service boundaries, Pydantic models in `core/api_models.py`. New endpoints: define models in `api_models.py` → add route in `backend.py` → `check_management_token(...)` if state-changing → call the service → add a `core/tests/test_*.py`.

## Commit & PR guidelines

Short imperative (often scoped) subjects, e.g. `fix+refactor(backend): playback-status truth`. PRs: concise behavior summary, test/build commands run, screenshots for visible UI, packaging/migration impact. Commit or push only when asked; branch first if on `main`.

## Guardrails

Model weights (~5.2 GB, user-downloaded on first run) are read-only external data and must never be committed. `backend/mlx_audio/` is a pinned upstream snapshot — its architecture source (including the `**/models/` dirs, deliberately re-included in `.gitignore`) is tracked; keep local patches minimal and documented.
