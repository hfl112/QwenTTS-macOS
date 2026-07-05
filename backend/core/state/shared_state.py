"""跨进程共享状态(计划 #10 C3.1:从 backend.py 搬家归位 core/state/)。

SharedState 是 web 进程与推理子进程之间的 mp 原语集合(队列/计数/出声证据)。
实例经 mp spawn 跨进程 pickle——类的模块路径就是 pickle 身份,两侧都必须
从本模块 import。
"""
from __future__ import annotations

import multiprocessing as mp


class SharedState:
    def __init__(self):
        self.text_q = mp.Queue()
        self.audio_q = mp.Queue()
        # Low-priority lane for podcast chunk synthesis. The engine drains
        # text_q (reads) first so reads preempt podcast work at chunk
        # boundaries (ADR-001 #2). The engine signals each chunk's completion
        # by writing chunk_<idx>.npy (or chunk_<idx>.npy.err on failure); the
        # podcast subprocess polls those files — no shared result queue, so
        # concurrent jobs can't steal each other's signals.
        self.podcast_q = mp.Queue()
        # P0-1(#10a):播客取消代际。cancel_all 时 +1;引擎逐帧对照,发现代际
        # 变了就中途掐断正在合成的段——否则 quiet 档下一段能烧好几分钟才停。
        self.podcast_cancel_epoch = mp.Value('i', 0)
        self.stop_event = mp.Event()
        self.vram_mb = mp.Value('d', 0.0)
        self.status_code = mp.Value('i', 0) # 0:IDLE, 1:BUSY, 2:COOLING
        self.current_task_id = mp.Value('i', 0)
        # 真实出声证据：推理 worker 每推出一帧音频就 +1。播放/试音是否"真的出声"
        # 不能只看 status（加载失败时 status 仍会短暂 BUSY），必须看是否产生过音频帧。
        self.audio_frames = mp.Value('i', 0)
        # 推理子进程最近一次异常文本（如模型加载失败）。主进程据此把失败如实暴露给
        # /snapshot 与 /selftest/voice，避免"试音假阳性"。
        self.error_buf = mp.Array('c', 1024)

    def set_status(self, status):
        m = {"IDLE": 0, "BUSY": 1, "COOLING": 2}
        self.status_code.value = m.get(status, 0)

    def get_status(self):
        m = {0: "IDLE", 1: "BUSY", 2: "COOLING"}
        return m.get(self.status_code.value, "IDLE")

    def note_audio_frame(self):
        with self.audio_frames.get_lock():
            self.audio_frames.value += 1

    def reset_run_signals(self):
        """开始一次新的朗读/试音前，清零出声计数与上一次的错误。"""
        with self.audio_frames.get_lock():
            self.audio_frames.value = 0
        self.set_error("")

    def set_error(self, msg):
        b = (msg or "")[:1023].encode("utf-8", "replace")
        with self.error_buf.get_lock():
            self.error_buf.raw = b + b"\x00" * (1024 - len(b))

    def get_error(self):
        with self.error_buf.get_lock():
            return self.error_buf.value.decode("utf-8", "replace")
