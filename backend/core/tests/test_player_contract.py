"""C4(计划 #10)——PCMPlayer 方法契约单测(不开真实音频流)。"""

from core.player import PCMPlayer


def _player(monkeypatch) -> PCMPlayer:
    p = PCMPlayer(sample_rate=24000)
    monkeypatch.setattr(p, "_ensure_stream_started", lambda: None)
    return p


def test_start_accepts_prebuffer_frames(monkeypatch):
    p = _player(monkeypatch)
    p.start(prebuffer_frames=6)
    assert p.min_chunks_to_start == 6
    # 0/负数钳到 1(与旧调用方 max(1, …) 语义一致)
    p.start(prebuffer_frames=0)
    assert p.min_chunks_to_start == 1
    # 不传 → 保持原值(兼容旧的两步默契调用方)
    p.min_chunks_to_start = 4
    p.start()
    assert p.min_chunks_to_start == 4
    p.finish()


def test_queue_depth_and_playing_index(monkeypatch):
    p = _player(monkeypatch)
    assert p.queue_depth() == 0
    p.audio_queue.put(b"chunk")
    assert p.queue_depth() == 1

    assert p.playing_index() is None  # 初始 -1 → None
    p.currently_playing_index = None
    assert p.playing_index() is None
    p.currently_playing_index = 3
    assert p.playing_index() == 3


def test_finish_marks_not_running(monkeypatch):
    p = _player(monkeypatch)
    p.playback_finished_event.clear()
    assert p.is_running()
    p.finish()
    assert not p.is_running()
