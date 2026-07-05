#!/usr/bin/env python3
"""Harness A — 生成吞吐 / underrun 归因探针(零生产行为改动,无需音频、无需耳朵听)。

它把"播放会不会顿"变成可打印的数字:用真实 MLX 模型对一段文本做一次真实合成
(use_cache=False,强制走 GPU,不污染缓存),给每一帧打时间戳,然后**离线模拟**
"实时播放消费 vs 模型喂入"的水位差,直接算出:

  - 每个 chunk 的 RTF(real-time factor = 生成耗时 / 产出音频秒数)
  - 每个 chunk 的首帧延迟(= chunk 边界空窗,对应假设 #5)
  - 在不同 chunk_sleep 节流(#4)与 prebuffer 帧数(#1)下:
      * 缓冲最低水位 min_margin_s(<0 即会 underrun)
      * 要避免 underrun 所需的预缓冲秒数 required_prebuffer_s
      * would_underrun 布尔判定

核心判据:如果节流=0 时不 underrun、但节流=0.08(balanced)时翻成 underrun,
则 chunk_sleep 节流(#4)就是元凶;如果连节流=0 都 underrun,则模型本身追不上
实时,问题在模型/分块而非缓冲管理。

用法:
    cd backend
    python tools/profile_gen.py                 # 用内置中文样例
    python tools/profile_gen.py path/to/text.txt
    TTS_PROFILE_TEXT_CHARS=600 python tools/profile_gen.py

环境变量(可选,均回退到 app 实际配置):
    PROFILE_MODEL / PROFILE_VOICE / PROFILE_PERF  覆盖模型/音色/性能档
"""

import json
import os
import sys
import time

import numpy as np

# 让 `import core...` 可用:tools/ 的上一级就是 backend/
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from core.inference.engine import InferenceEngine  # noqa: E402
from core.inference.model_backend import MLXBackend  # noqa: E402
from core.paths import runtime_paths  # noqa: E402
from core.processor import TextProcessor  # noqa: E402
from core.services.performance import PERFORMANCE_PROFILES, get_performance_profile  # noqa: E402
from core.storage import Storage  # noqa: E402

SR = 24000

# 内置代表性中文样例(~430 字),含长短句混排,贴近真实文章朗读。
SAMPLE_TEXT = (
    "人工智能的发展正在以前所未有的速度改变着我们的生活方式。从最初的规则系统，"
    "到如今基于深度学习的大模型，机器对语言、图像乃至声音的理解能力都有了质的飞跃。"
    "在语音合成这一领域，端到端的神经网络模型已经能够生成接近真人的自然语音，"
    "无论是音色、语调还是停顿，都越来越难以与真人区分。然而，真正要把这样的技术"
    "做成一个流畅、低延迟、可以长时间稳定运行的本地应用，工程上的挑战远比想象中复杂。"
    "首先是首声延迟，用户按下播放键之后，必须在一秒之内听到声音，否则就会觉得卡顿；"
    "其次是持续供给，模型生成音频的速度必须稳定地快于播放消费的速度，否则缓冲区一旦"
    "被掏空，播放器就只能填充静音，听感上就是恼人的顿挫。如何在这两者之间取得平衡，"
    "既保证起播够快，又保证后续不断流，是流式语音合成系统设计中最核心的权衡之一。"
)


def _load_config():
    """读 app 真实配置,使 model/voice/instruct 与实际朗读一致。"""
    storage = Storage(data_dir=runtime_paths.data_path)
    cfg = {}
    try:
        cfg = storage.load_config() or {}
    except Exception as e:
        print(f"[profile_gen] load_config 失败,使用默认: {e}")
    cfg["performance_profile"] = (
        os.environ.get("PROFILE_PERF")
        or cfg.get("performance_profile")
        or "balanced"
    )
    if os.environ.get("PROFILE_MODEL"):
        cfg["model"] = os.environ["PROFILE_MODEL"]
    if os.environ.get("PROFILE_VOICE"):
        cfg["voice"] = os.environ["PROFILE_VOICE"]
    cfg.setdefault("model", "Qwen3-TTS-0.6B")
    cfg.setdefault("voice", "Serena")
    return storage, cfg


def _pctl(xs, q):
    return float(np.percentile(np.asarray(xs, dtype=np.float64), q)) if xs else 0.0


def main() -> int:
    # 1) 取文本
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = SAMPLE_TEXT
    cap = int(os.environ.get("TTS_PROFILE_TEXT_CHARS", "0") or 0)
    if cap > 0:
        text = text[:cap]

    storage, config = _load_config()
    profile_name = config["performance_profile"]
    profile = get_performance_profile(profile_name)
    # quiet 档会强制小模型;mirror run_loop 不做,这里也不强制,保持与朗读一致。

    # 2) 真实分块(与 /read 同路径)
    processor = TextProcessor()
    chunks = processor.parse_dialogue_or_text(text, performance_profile=profile_name)
    if not chunks:
        print("[profile_gen] 文本切分后为空")
        return 1

    # 3) 接真实 backend + engine
    backend = MLXBackend(mlx_audio_path=runtime_paths.mlx_audio_path)
    engine = InferenceEngine(
        backend=backend,
        cache_dir=runtime_paths.cache_path,
        storage=None,  # 不写缓存元数据,纯探针
        reference_base=runtime_paths.reference_path,
        models_path=runtime_paths.models_path,
    )

    print("=" * 72)
    print(f"模型      : {config['model']}")
    print(f"音色      : {config['voice']}")
    print(f"性能档    : {profile_name}  (chunk_sleep={profile.get('chunk_sleep')})")
    print(f"chunk 数  : {len(chunks)}    总字数: {len(text)}")
    print(f"参考音目录: {runtime_paths.reference_path}")
    print("=" * 72)

    # 4) 加载模型并预热(预热不计入,避免首个 chunk 把一次性加载算进 RTF)
    hardened0 = InferenceEngine._apply_model_hardening(dict(config))
    engine.ensure_model(hardened0.get("model", "Qwen3-TTS-0.6B"))
    print("[profile_gen] 预热中(忽略首块计时)...")
    warm = chunks[0]
    warm_text = warm["text"] if isinstance(warm, dict) else warm
    for _ in engine.synthesize_local(warm_text, hardened0, use_cache=False):
        pass
    print("[profile_gen] 预热完成,开始正式计时\n")

    # 5) 逐块合成,单一全局时钟,捕捉块间首帧延迟
    per_chunk = []          # 每块汇总
    global_frames = []      # [(t_avail_raw, audio_s)] 跨全文连续时间线
    g0 = time.perf_counter()
    for idx, chunk in enumerate(chunks):
        if isinstance(chunk, dict):
            c_text = chunk["text"]
            c_cfg = dict(config)
            c_cfg.update(chunk.get("config", {}))
        else:
            c_text = chunk
            c_cfg = dict(config)
        hardened = InferenceEngine._apply_model_hardening(c_cfg)

        c_start = time.perf_counter()
        first_frame_t = None
        n_frames = 0
        audio_samples = 0
        for frame in engine.synthesize_local(c_text, hardened, use_cache=False):
            now = time.perf_counter()
            if first_frame_t is None:
                first_frame_t = now
            n = len(frame)
            audio_samples += n
            n_frames += 1
            global_frames.append((now - g0, n / SR))
        c_end = time.perf_counter()
        audio_s = audio_samples / SR
        gen_wall = c_end - c_start
        per_chunk.append({
            "idx": idx,
            "chars": len(c_text),
            "frames": n_frames,
            "audio_s": audio_s,
            "gen_wall_s": gen_wall,
            "rtf_raw": (gen_wall / audio_s) if audio_s > 0 else float("inf"),
            "first_frame_lat_s": (first_frame_t - c_start) if first_frame_t else float("inf"),
        })
        print(
            f"  chunk {idx:2d} | {len(c_text):4d}字 | {n_frames:3d}帧 | "
            f"音频 {audio_s:5.2f}s | 生成 {gen_wall:5.2f}s | "
            f"RTF {per_chunk[-1]['rtf_raw']:.3f} | 首帧 {per_chunk[-1]['first_frame_lat_s']:.3f}s"
        )

    # 6) 离线播放模拟:实时消费 vs 喂入水位
    #    t'_i = 原始可用时刻 + i*throttle(每帧后 sleep,run_loop 行为)
    #    起播 = 第 N 帧就绪(prebuffer_frames=N);消费速率 1x。
    #    margin_pre_i = 帧 i 到达前的可用音频 - 已消费 = cum_{i-1} - (t'_i - t_play)
    #    min(margin_pre) < 0 即 underrun;required_prebuffer = -min(margin_pre)
    cum = np.cumsum([a for (_, a) in global_frames])  # 帧 i 就绪后累计音频
    t_raw = np.array([t for (t, _) in global_frames])
    n_total = len(global_frames)

    def simulate(throttle: float, prebuffer_n: int):
        if n_total < prebuffer_n:
            return None
        t_av = t_raw + np.arange(n_total) * throttle
        t_play = t_av[prebuffer_n - 1]
        min_margin = float("inf")
        for i in range(prebuffer_n, n_total):
            avail_before = cum[i - 1]          # 帧 i 到达前已就绪音频
            consumed = max(0.0, t_av[i] - t_play)
            margin = avail_before - consumed
            if margin < min_margin:
                min_margin = margin
        finish_wall = float(t_av[-1])
        total_audio = float(cum[-1])
        return {
            "throttle": throttle,
            "prebuffer_n": prebuffer_n,
            "min_margin_s": min_margin,
            "required_prebuffer_s": max(0.0, -min_margin),
            "would_underrun": min_margin < 0,
            "finish_wall_s": finish_wall,
            "total_audio_s": total_audio,
            "overall_rtf": finish_wall / total_audio if total_audio > 0 else float("inf"),
        }

    throttles = sorted({
        0.0,
        PERFORMANCE_PROFILES["balanced"]["chunk_sleep"],
        PERFORMANCE_PROFILES["quiet"]["chunk_sleep"],
        profile.get("chunk_sleep", 0.0),
    })
    grid = []
    print("\n" + "=" * 72)
    print("离线播放模拟:would_underrun(✗=会顿) / min_margin_s / 所需预缓冲s")
    print(f"{'throttle':>9} | {'prebuf=1':>22} | {'prebuf=2':>22} | {'prebuf=3':>22}")
    print("-" * 72)
    for thr in throttles:
        cells = []
        for n in (1, 2, 3):
            r = simulate(thr, n)
            grid.append(r)
            if r is None:
                cells.append("n/a")
                continue
            mark = "✗顿" if r["would_underrun"] else "✓稳"
            cells.append(f"{mark} m={r['min_margin_s']:+.2f} need={r['required_prebuffer_s']:.2f}")
        print(f"{thr:>9.3f} | {cells[0]:>22} | {cells[1]:>22} | {cells[2]:>22}")

    # 7) 判据小结
    rtfs = [c["rtf_raw"] for c in per_chunk if np.isfinite(c["rtf_raw"])]
    ffls = [c["first_frame_lat_s"] for c in per_chunk if np.isfinite(c["first_frame_lat_s"])]
    base = simulate(0.0, 1)
    bal = simulate(PERFORMANCE_PROFILES["balanced"]["chunk_sleep"], 1)
    print("\n" + "=" * 72)
    print("判据小结")
    print(f"  RTF(raw)        p50={_pctl(rtfs,50):.3f}  p95={_pctl(rtfs,95):.3f}  "
          f"max={max(rtfs) if rtfs else 0:.3f}")
    print(f"  首帧延迟        p50={_pctl(ffls,50):.3f}s p95={_pctl(ffls,95):.3f}s  (chunk边界空窗/#5)")
    if base and bal:
        if not base["would_underrun"] and bal["would_underrun"]:
            print("  → 节流(#4)是元凶:throttle=0 不顿,balanced 0.08 翻成 underrun。优先关 read 节流。")
        elif base["would_underrun"]:
            print("  → 即使 throttle=0 也 underrun:模型追不上实时(RTF≥1),问题在模型/分块,"
                  "缓冲只能缓解。考虑更小模型 / 更大 prebuffer。")
        else:
            print("  → throttle=0 与 balanced 都不 underrun(prebuf=1):生成够快;"
                  "若仍有顿,根因在播放端(blocksize 放大/中途无再缓冲),需跑 Harness B。")
        print(f"  当前 balanced 下要稳:prebuffer 需 ≥ {bal['required_prebuffer_s']:.2f}s 音频")

    # 8) dump JSON
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(runtime_paths.logs_path, f"profile_gen_{ts}.json")
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({
                "model": config["model"], "voice": config["voice"],
                "profile": profile_name, "text_chars": len(text),
                "per_chunk": per_chunk, "grid": grid,
            }, f, ensure_ascii=False, indent=2, default=lambda o: bool(o) if isinstance(o, np.bool_) else str(o))
        print(f"\n报告已写入: {out}")
    except Exception as e:
        print(f"[profile_gen] 写报告失败: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
