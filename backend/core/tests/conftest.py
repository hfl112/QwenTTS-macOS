"""测试沙箱(2026-07-01,M smoke 实测教训)。

端点级测试会 `init_runtime_services()`:若运行时路径落在真实用户目录
(~/Library/Application Support/QwenTTS),会发生真实伤害——
`PodcastJobStore.mark_unfinished_failed` 在构造时把**用户正在跑的**播客任务
标成 failed/canceled(与活后端共用同一份 podcast_jobs.json),测试还会读写
用户的 state.json/cache.db。

修法:在任何 `core.*` 导入之前(conftest 顶层先于测试模块执行),把全套
TTS_* 路径指到一次性临时目录。`core.paths.runtime_paths` 在 import 时解析
一次,所以必须赶在最早。
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="qwentts-tests-")

# 只设 APP_SUPPORT,其余(Data/Cache/Podcasts/…)由 core.paths 自然派生到沙箱下;
# setdefault:显式设置过的环境(如 CI 特殊布置)仍可覆盖。
# (不要单独设 TTS_DATA_PATH 等——test_week3 验证的正是"从 APP_SUPPORT 派生"。)
os.environ.setdefault("TTS_APP_SUPPORT_PATH", _TMP)
