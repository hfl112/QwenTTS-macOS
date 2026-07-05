"""N1(计划 #11)——mode 改名(podcast-* → dual-*)的规范化与向后兼容。"""

import os
import tempfile

from core import reader_bridge
from core.services.podcast_jobs import content_key
from core.services.podcast_service import PodcastService
from core.state.runtime_state import RuntimeState


def test_normalize_mode_maps_legacy_names():
    assert reader_bridge.normalize_mode("podcast-discuss") == "dual-summary"
    assert reader_bridge.normalize_mode("podcast-trans") == "dual-trans"
    # 规范值/其它值原样;空 → original
    assert reader_bridge.normalize_mode("dual-summary") == "dual-summary"
    assert reader_bridge.normalize_mode("translate") == "translate"
    assert reader_bridge.normalize_mode(None) == "original"
    assert reader_bridge.normalize_mode("  ") == "original"
    assert reader_bridge.legacy_mode_equivalents("dual-summary") == ["podcast-discuss"]
    assert reader_bridge.legacy_mode_equivalents("original") == []


def test_find_reusable_output_falls_back_to_legacy_mode_key():
    """改名前生成的成品(content_key 用旧 mode 名)在改名后仍可被复用命中。"""
    with tempfile.TemporaryDirectory() as tmp:
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=RuntimeState(),
            active_url_tasks={},
            jobs_file=os.path.join(tmp, "podcast_jobs.json"),
            get_battery_policy=lambda: "allow",
        )
        wav = os.path.join(tmp, "old_era.wav")
        with open(wav, "wb") as f:
            f.write(b"RIFF")
        text = "改名前生成的这篇内容"
        # 旧时代 job:content_key 按 podcast-discuss 计算
        service.job_store.create(
            job_id="single_old_era", kind="single", md5="m", title="旧", source="web",
            mode="podcast-discuss", voice="Serena",
            content_key=content_key(text, "podcast-discuss", "Serena"),
        )
        service.job_store.update("single_old_era", status="done", output_path=wav)

        # 新名请求 → 规范 key 未命中 → legacy key 命中
        assert service.find_reusable_output(
            text=text, mode="dual-summary", voice="Serena", config={}
        ) == wav
        # 旧名请求(老客户端)同样命中
        assert service.find_reusable_output(
            text=text, mode="podcast-discuss", voice="Serena", config={}
        ) == wav
        # 不同模式不串
        assert service.find_reusable_output(
            text=text, mode="dual-trans", voice="Serena", config={}
        ) is None
