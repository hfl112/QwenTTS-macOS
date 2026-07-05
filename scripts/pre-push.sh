#!/bin/bash
# 本地快检 pre-push 钩子(计划 #12):30 秒级子集,完整 clean test 交给 GitHub Actions。
# 安装:cp localTTS_macOS/scripts/pre-push.sh "$(git rev-parse --git-dir)/hooks/pre-push" && chmod +x 同路径
# 跳过一次:git push --no-verify
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"

PY="${TTS_DEV_PYTHON:-$HOME/miniconda3/envs/gemini/bin/python}"
[ -x "$PY" ] || PY=python3

echo "[pre-push] 1/3 backend pytest…"
(cd "$ROOT/localTTS_macOS/backend" && "$PY" -m pytest core/tests/ -q)

echo "[pre-push] 2/3 extension tsc…"
(cd "$ROOT/qwen-tts-extension" && npx tsc --noEmit)

echo "[pre-push] 3/3 app incremental build…"
(cd "$ROOT/localTTS_macOS/QwenTTS" && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcodebuild -project QwenTTS.xcodeproj -scheme QwenTTS \
  -destination 'platform=macOS' CODE_SIGNING_ALLOWED=NO build -quiet)

echo "[pre-push] ✅ 全绿(完整 clean test 由 GitHub Actions 把关)"
