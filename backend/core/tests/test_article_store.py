"""C3.3(计划 #10)——ArticleStore 单一读写口的直测。"""

import tempfile

from core.state.article_store import ArticleStore
from core.storage import Storage


def _store():
    tmp = tempfile.mkdtemp()
    return ArticleStore(Storage(data_dir=tmp))


def test_replace_and_get_roundtrip_preserves_other_state_keys():
    s = _store()
    state = s.storage.load_state()
    state["history"] = ["其他键"]
    s.storage.save_state(state)

    s.replace({"title": "T", "chunks": ["a"], "current_index": 0})
    assert s.get()["title"] == "T"
    assert s.storage.load_state()["history"] == ["其他键"]  # 其它键不丢


def test_set_index_title_guard():
    s = _store()
    s.replace({"title": "T", "chunks": ["a", "b"], "current_index": 0})
    assert s.set_index(1, expect_title="T") is True
    assert s.get()["current_index"] == 1
    # 标题不匹配 → 拒写(播放线程防串写守卫)
    assert s.set_index(0, expect_title="别的") is False
    assert s.get()["current_index"] == 1
    # 无文章 → False 不炸
    s.replace({})
    assert s.set_index(5) is False


def test_view_live_index_override_and_out_of_range():
    s = _store()
    s.replace({
        "title": "T",
        "chunks": [{"text": "第一句", "config": {}}, "第二句"],
        "current_index": 0,
    })
    v = s.view(live_index=None)
    assert v["chunks_clean"] == ["第一句", "第二句"]
    assert v["current_index"] == 0 and v["progress_override"] is None
    v = s.view(live_index=1)
    assert v["current_index"] == 1 and v["progress_override"] == "2/2"
    v = s.view(live_index=9)  # 越界 → 持久化索引,不给进度串(历史行为)
    assert v["current_index"] == 0 and v["progress_override"] is None
