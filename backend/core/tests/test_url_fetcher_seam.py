"""M9(计划 #13)——URL-Reader 的 Fetcher 窄缝。

「碰外界拿原始 markdown」(YouTube 转写/上传 HTML/网络抓取+代理回退)收进
Fetcher;process_url_job 的两层缓存/auto-mode/LLM 分发/标题/音色对着
FakeFetcher 即可全测,不碰网络——此前唯一触达它的测试把整个函数 monkeypatch
掉,承重的缓存命中与防双写逻辑零覆盖。
"""

import tempfile

from core import reader_bridge

reader_bridge._ensure_path()  # C5:URL-Reader 进 sys.path(bridge 是唯一接入 adapter)
import reader_service  # noqa: E402


class FakeFetcher:
    def __init__(self, markdown: str = "# 标题\n\n正文内容。"):
        self.markdown = markdown
        self.calls: list[dict] = []

    def fetch_markdown(self, *, url, html, video_id, callback):
        self.calls.append({"url": url, "html": html, "video_id": video_id})
        return self.markdown


def _run(fetcher, url="https://example.com/a", tmp=None, **kw):
    return reader_service.process_url_job(
        url=url, base_dir=tmp, cache_dir=tmp, fetcher=fetcher, **kw
    )


def test_source_cache_hit_skips_fetcher():
    """同 url 二次调用 → 源缓存命中,fetcher 不再被调,from_cache=True。"""
    with tempfile.TemporaryDirectory() as tmp:
        f = FakeFetcher()
        r1 = _run(f, tmp=tmp, mode="original")
        assert r1.text.strip() and not r1.from_cache
        assert len(f.calls) == 1
        r2 = _run(f, tmp=tmp, mode="original")
        assert len(f.calls) == 1  # 缓存命中,零抓取
        assert r2.from_cache is True


def test_llm_layer_cached_and_inner_cache_disabled(monkeypatch):
    """mode≠original:LLM 只跑一次(成品缓存),且外层调用关内层缓存防双写。"""
    with tempfile.TemporaryDirectory() as tmp:
        seen_kwargs = []

        def fake_llm(text, mode, use_cache=True, **kw):
            seen_kwargs.append({"mode": mode, "use_cache": use_cache})
            return "生成的双人脚本"

        monkeypatch.setattr(reader_service, "process_with_llm", fake_llm)
        f = FakeFetcher()
        r1 = _run(f, tmp=tmp, mode="dual-summary")
        assert "双人脚本" in r1.text
        assert seen_kwargs == [{"mode": "dual-summary", "use_cache": False}]
        r2 = _run(f, tmp=tmp, mode="dual-summary")
        assert len(seen_kwargs) == 1  # 成品缓存命中,LLM 不再跑
        assert r2.from_cache is True


def test_auto_mode_resolves_before_dispatch(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(reader_service, "resolve_auto_mode", lambda text: "original")
        r = _run(FakeFetcher(), tmp=tmp, mode="auto")
        assert r.mode == "original"


def test_youtube_gets_ryan_voice_and_video_id_passed():
    with tempfile.TemporaryDirectory() as tmp:
        f = FakeFetcher()
        r = _run(f, url="https://www.youtube.com/watch?v=abc12345678", tmp=tmp, mode="original")
        assert r.source == "video" and r.voice == "Ryan"
        assert f.calls[0]["video_id"]  # 分支判定在编排层,fetcher 拿到 video_id
        r2 = _run(FakeFetcher(), tmp=tmp, mode="original")
        assert r2.source == "web" and r2.voice is None


def test_empty_content_raises():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _run(FakeFetcher(markdown="   "), tmp=tmp, mode="original")
        except RuntimeError as e:
            assert "为空" in str(e)
        else:
            raise AssertionError("空内容应报错")
