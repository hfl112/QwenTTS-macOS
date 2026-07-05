"""N2(计划 #11)——展示三件套(display_title/source_label/mode_label)。"""

import os

from core import labels


def test_label_tables():
    assert labels.source_label("clipboard") == "剪贴板"
    assert labels.source_label("url") == "网页"
    assert labels.source_label("web") == "网页"
    assert labels.source_label(None) == ""
    assert labels.mode_label("original") == "原文"
    assert labels.mode_label("translate") == "译文"
    assert labels.mode_label("dual-summary") == "双人总结"
    # 旧名经归一后同样命中
    assert labels.mode_label("podcast-trans") == "双人翻译"


def test_clean_display_title_strips_legacy_prefixes_and_falls_back():
    assert labels.clean_display_title("[双人总结]萨特的观点") == "萨特的观点"
    assert labels.clean_display_title("[译·中文] Hello") == "Hello"
    assert labels.clean_display_title("", "正文第一行\n第二行") == "正文第一行"
    assert labels.clean_display_title(None, "x" * 200)[:5] == "xxxxx"
    assert len(labels.clean_display_title(None, "x" * 200)) == 80


def test_list_endpoints_expose_display_fields(monkeypatch):
    """三个列表端点都带展示三件套;播客成品的 mode 从 job 记录反查。"""
    from fastapi.testclient import TestClient

    import core.backend as backend_mod
    from core.backend import app, init_runtime_services

    monkeypatch.setenv("TTS_LEGACY_LOOPBACK_CLIENTS", "1")
    init_runtime_services()
    client = TestClient(app)

    # saved:种一条带旧前缀标题 + 旧 mode 名的条目
    backend_mod.saved_items_service.save(
        "正文内容", source="clipboard", voice=None,
        title="[双人总结]我的文章", mode="podcast-discuss",
    )
    try:
        items = client.get("/saved_items").json()
        it = next(i for i in items if i.get("title") == "[双人总结]我的文章")
        assert it["display_title"] == "我的文章"
        assert it["source_label"] == "剪贴板"
        assert it["mode_label"] == "双人总结"
    finally:
        backend_mod.saved_items_service.clear()

    # podcasts:落一个成品 wav + 对应 done job(带 mode)
    svc = backend_mod.podcast_service
    os.makedirs(svc.podcasts_dir, exist_ok=True)
    fn = "podcast_单篇_web_标题X_abcd1234_1700000000.wav"
    wav = os.path.join(svc.podcasts_dir, fn)
    with open(wav, "wb") as f:
        f.write(b"RIFF")
    svc.job_store.create(
        job_id="single_labels_test", kind="single", md5="m", title="标题X",
        source="web", mode="dual-trans",
    )
    svc.job_store.update("single_labels_test", status="done", output_path=wav)
    try:
        rows = client.get("/podcasts/list").json()
        row = next(r for r in rows if r["filename"] == fn)
        assert row["display_title"] == "标题X"
        assert row["source_label"] == "网页"
        assert row["mode_label"] == "双人翻译"
    finally:
        os.remove(wav)

    # cache 端点:字段存在性(空库也应返回 list)
    assert isinstance(client.get("/cache/items").json(), list)


def test_saved_content_mode_drives_mode_label(monkeypatch):
    """#12-②:URL→保存 的已处理脚本 mode=original(不再跑 LLM),
    展示形态走 content_mode;旧数据(前缀烤在标题里)由前缀推断。"""
    from fastapi.testclient import TestClient

    import core.backend as backend_mod
    from core.backend import app, init_runtime_services

    monkeypatch.setenv("TTS_LEGACY_LOOPBACK_CLIENTS", "1")
    init_runtime_services()
    client = TestClient(app)

    svc = backend_mod.saved_items_service
    svc.save("已处理的双人脚本", source="web", title="新条目", content_mode="dual-summary")
    svc.save("旧时代条目正文", source="web", title="[双人翻译]旧条目")  # 无 content_mode
    try:
        items = client.get("/saved_items").json()
        new_it = next(i for i in items if i.get("title") == "新条目")
        assert new_it["mode_label"] == "双人总结"     # content_mode 优先
        assert new_it["mode"] == "original"          # 生成语义不变:不再跑 LLM
        old_it = next(i for i in items if "旧条目" in (i.get("title") or ""))
        assert old_it["mode_label"] == "双人翻译"     # 旧数据从前缀推断
        assert old_it["display_title"] == "旧条目"    # 展示标题剥前缀
    finally:
        svc.clear()


def test_infer_mode_from_legacy_prefix():
    assert labels.infer_mode_from_legacy_prefix("[双人总结]X") == "dual-summary"
    assert labels.infer_mode_from_legacy_prefix("[译·中文]X") == "translate"
    assert labels.infer_mode_from_legacy_prefix("普通标题") is None
    assert labels.infer_mode_from_legacy_prefix(None) is None


def test_pending_url_tasks_filter_owned_by_labels():
    """M4-③(计划 #13):60s 窗口 + is_podcast 过滤的伪行规则收成一份
    (此前 /saved_items 与播客 list_files 各抄一份,窗口与文案都是复制的)。"""
    from core.labels import PENDING_FETCH_TITLE, pending_url_tasks

    now = 1000.0
    tasks = {
        "https://a": {"is_podcast": False, "timestamp": now - 10},   # 命中(非播客)
        "https://b": {"is_podcast": True, "timestamp": now - 10},    # 命中(播客)
        "https://c": {"is_podcast": False, "timestamp": now - 61},   # 过窗
        "https://d": {"timestamp": now - 5},                          # 无标记 = 非播客
    }
    non_podcast = pending_url_tasks(tasks, podcast=False, now=now)
    assert [u for u, _ in non_podcast] == ["https://a", "https://d"]
    podcast = pending_url_tasks(tasks, podcast=True, now=now)
    assert [u for u, _ in podcast] == ["https://b"]
    assert "正在抓取" in PENDING_FETCH_TITLE
