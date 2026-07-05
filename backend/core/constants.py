"""全库共享的钉子常量(叶子模块,禁止 import 任何 core 兄弟)。"""

# 现役 TTS 模型 —— 单一真相(M3,计划 #13)。
# config 缺 model 字段时全库唯一回退;storage 默认 config 与 quiet 档同源引用。
# 历史教训(§4l):此前 6 处裸回退写的是未量化 "Qwen3-TTS-0.6B"(RTF 1.08,
# 追不上实时),config 一缺字段就静默跑回卡顿模型且极难排查。
# 换模型前先跑 backend/tools/profile_gen.py 重新归因,再改这一处。
DEFAULT_TTS_MODEL = "Qwen3-TTS-0.6B-4bit"
