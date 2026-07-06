# Follow-ups / Roadmap

Known improvements deferred after the initial public release. **None of these block
using the app** — the core read / podcast / URL-reader flows work today. Grouped by
theme, roughly in priority order. File references are 1-indexed.

## Reliability

- [ ] **Inference-child liveness + respawn.** The MLX inference subprocess is spawned
  once with no health check; the podcast chunk wait polls without a hard timeout, so a
  hard child death (OOM/segfault) can wedge a podcast job and leave reads idle.
  Add `is_alive()` monitoring + respawn/error-surfacing and a bounded (generous) wait
  timeout. — `backend/core/services/runtime_supervisor.py` (`start_inference`),
  `backend/core/services/podcast_service.py` (`_submit_chunk_and_wait`).
  *Deferred: needs real-runtime verification, not just static review.*

## Security hardening (defense-in-depth; backend is localhost-only, single-user)

- [ ] **Restrict spawned-backend environment + log permissions.** The backend is
  launched with the full parent environment and its `backend.log` is world-readable
  (`0o644`). Pass an explicit env allow-list and open the log `0o600`. —
  `QwenTTS/QwenTTS/Backend/BackendLauncher.swift`.
- [ ] **Token edge cases.** When `TTS_MANAGEMENT_TOKEN` is unset (standalone dev run)
  the management endpoints fall open; prefer failing closed or minting+printing a
  random token on startup. Re-evaluate the opt-in `TTS_LEGACY_LOOPBACK_CLIENTS`
  bypass. — `backend/core/backend.py` (auth middleware).
  *(Already done: constant-time token compare, `/docs` disabled.)*
- [ ] **Port selection.** The backend port is chosen once via bind-then-close (TOCTOU)
  and reused on crash-restart; prefer `port=0` + reading `runtime.json`. —
  `QwenTTS/QwenTTS/Backend/BackendProcessManager.swift`.

## Packaging & distribution

- [ ] **Signed + notarized DMG.** The public build is currently unsigned (ad-hoc);
  users must `xattr -cr` to bypass Gatekeeper. Sign with a Developer ID and
  notarize/staple for a friction-free download. — `package_release.py`,
  `notarize_dmg.py`.
- [ ] **App version discipline.** Bump `CFBundleVersion` per release (matters once
  auto-update is added) and add `LSApplicationCategoryType`; consider moving the
  version into `project.yml` (`MARKETING_VERSION`) as a single source. —
  `QwenTTS/QwenTTS/Info.plist`.
- [ ] **Auto-update (optional).** Sparkle is not wired; add an appcast + EdDSA-signed
  updates if in-app updates are wanted.

## Polish

- [ ] **Extension package name.** `qwen-tts-extension/package.json` still uses the WXT
  starter name `wxt-starter`; rename to `qwen-tts-extension`.
- [ ] **Show model license in-app.** Surface the Qwen3-TTS model license in an
  About / first-run screen. — `QwenTTS/QwenTTS/Models/ModelManager.swift`.
- [ ] **Deprecation warning.** `onChange(of:perform:)` is deprecated on macOS 14; move
  to the two/zero-parameter closure form. — `QwenTTS/QwenTTS/UI/Library/LibraryView.swift:729`.
