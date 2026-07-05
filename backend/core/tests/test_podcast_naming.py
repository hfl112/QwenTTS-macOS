"""C2.1 播客命名拥有者:与三处历史硬编码格式逐字对拍(计划 #10)。"""
from core.services import podcast_naming as pn


MD5 = "0123456789abcdef0123456789abcdef"


def test_safe_title_for_output_matches_worker_filter():
    # worker 口径:isalnum / CJK / "[]_-",其余剔除;空 → 无标题
    assert pn.safe_title_for_output("[双人] 新闻-01!", "x") == "[双人]新闻-01"
    # text[:20] 先截 20 字(含空格标点),过滤后 = 7 个可保留字 + 10 个尾
    assert pn.safe_title_for_output(None, "中文 标题! abc" + "尾" * 30) == "中文标题abc" + "尾" * 10
    assert pn.safe_title_for_output("///", "!!!") == "无标题"


def test_single_pending_name_matches_legacy_format():
    # 历史写端: f".pending_单篇_{source}_{safe_title}_{md5[:8]}"
    assert (
        pn.single_pending_name("web", "标题", MD5)
        == f".pending_单篇_web_标题_{MD5[:8]}"
    )


def test_batch_pending_path_matches_legacy_format():
    # 历史写端: filename.replace(".wav","") + ".pending_合集"
    assert pn.batch_pending_path("/p/podcast_合集_web_大合集_1.wav") == (
        "/p/podcast_合集_web_大合集_1.pending_合集"
    )


def test_chunk_dir_name_matches_worker_dirs():
    # 写端: single_{md5[:12]} / batch_{hash[:12]} —— 无 uuid 后缀
    assert pn.chunk_dir_name("single", MD5) == f"single_{MD5[:12]}"
    assert pn.chunk_dir_name("batch", MD5) == f"batch_{MD5[:12]}"


def test_single_output_name_matches_legacy_format():
    assert (
        pn.single_output_name("web", "标题", MD5, 1700000000)
        == f"podcast_单篇_web_标题_{MD5[:8]}_1700000000.wav"
    )


def test_parse_output_filename_roundtrip_and_legacy_cases():
    name = pn.single_output_name("clipboard", "我的标题", MD5, 1700000000)
    parsed = pn.parse_output_filename(name)
    assert parsed["title"] == "我的标题"
    assert parsed["source"] == "clipboard"
    assert parsed["is_pinned"] is False and parsed["is_pending"] is False

    pinned = pn.parse_output_filename("pinned_" + name)
    assert pinned["is_pinned"] is True and pinned["title"] == "我的标题"
    assert pinned["clean_filename"] == name

    # 合集与非文法文件的历史行为
    assert pn.parse_output_filename("podcast_合集_web_x_1.wav")["title"] == "大合集播客"
    other = pn.parse_output_filename("random.wav")
    assert other["title"] == "random.wav" and other["source"] == "web"

    # 单篇 pending 占位也可解析(list_files 的 .pending 分支)
    pend = pn.parse_output_filename(pn.single_pending_name("web", "标题", MD5))
    assert pend["title"] == "标题" and pend["is_pending"] is True


def test_renamed_filename_matches_legacy_rename_logic():
    name = pn.single_output_name("web", "旧题", MD5, 1700000000)
    assert pn.renamed_filename(name, "新题") == name.replace("旧题", "新题")
    # pinned 前缀保留
    assert pn.renamed_filename("pinned_" + name, "新题") == "pinned_" + name.replace(
        "旧题", "新题"
    )
    # 非文法文件 → 包一层单篇文法(历史 fallback)
    assert pn.renamed_filename("old.wav", "新题") == "podcast_单篇_web_新题_old.wav"
    # 空标题 → 未命名
    assert pn.renamed_filename(name, "!!!") == name.replace("旧题", "未命名")


def test_generating_title_from_listing():
    single = pn.single_pending_name("web", "生成中标题", MD5)
    assert pn.generating_title_from_listing([single, "a.wav"]) == "生成中标题"
    # 合集 pending 是后缀文法(旧 /status 的 startswith 永远打不中;按真实文法必须命中)
    assert (
        pn.generating_title_from_listing(["podcast_合集_web_x_1.pending_合集"])
        == "大合集播客"
    )
    assert pn.generating_title_from_listing(["a.wav", "b.txt"]) == ""


def test_is_output_for_md5_owns_cache_export_grammar():
    """M4-⑤(计划 #13):「该成品 wav 是否属于此 md5」的文法归命名拥有者;
    CacheService 不再自猜文件名格式。行为保持自旧内联判断。"""
    name = pn.single_output_name("web", "标题", MD5, 1700000000)
    assert pn.is_output_for_md5(name, MD5)
    # 不同 md5 → 不命中
    assert not pn.is_output_for_md5(name, "f" * 32)
    # 非 wav / 非成品前缀 / 空 md5 → 不命中
    assert not pn.is_output_for_md5(name[:-4] + ".txt", MD5)
    assert not pn.is_output_for_md5("other_" + name, MD5)
    assert not pn.is_output_for_md5(name, "")
    # 历史行为保持:pinned_ 改名后的成品不计(如需连 pinned 算导出,另议勿顺手改)
    assert not pn.is_output_for_md5("pinned_" + name, MD5)
