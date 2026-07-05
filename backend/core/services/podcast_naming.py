"""播客产物命名文法的唯一拥有者(计划 #10 C2)。

pending 占位文件、chunk 工作目录、成品 wav 文件名的编码与解析全部住在这里。
写端(podcast_service 两个 worker)与读端(list_jobs / list_files / rename_file、
/status 的 generating_title)一律经由本模块 —— 此前同一套文法在 3 处各自
硬编码/split 猜格式,progress.json 的写读目录甚至已经错位(写 single_{md5:12},
读 {job_id});收口后改文法只动这一个文件。
"""
from __future__ import annotations

import os

SINGLE_PENDING_PREFIX = ".pending_单篇_"
BATCH_PENDING_SUFFIX = ".pending_合集"
BATCH_TITLE = "大合集播客"


def safe_title_for_output(title: str | None, text: str) -> str:
    """成品/pending 文件名里的标题段(worker 口径:CJK/字母数字/[]_-)。"""
    raw = title if title else text[:20]
    safe = "".join(
        c for c in raw if c.isalnum() or "一" <= c <= "鿿" or c in "[]_-"
    )
    return safe or "无标题"


def safe_title_for_rename(new_title: str) -> str:
    """重命名口径(与历史行为一致:isalnum 已覆盖 CJK,额外只留 '-')。"""
    safe = "".join(c for c in new_title if c.isalnum() or c in "-一-龥").strip()
    return safe or "未命名"


def single_pending_name(source: str, safe_title: str, md5: str) -> str:
    return f"{SINGLE_PENDING_PREFIX}{source}_{safe_title}_{md5[:8]}"


def batch_pending_path(wav_path: str) -> str:
    """合集 pending 是挂在成品路径上的后缀占位(历史格式,保持不变)。"""
    return wav_path.replace(".wav", "") + BATCH_PENDING_SUFFIX


def chunk_dir_name(kind: str, content_hash: str) -> str:
    """chunk 工作目录名。注意:带 uuid 后缀的 job_id ≠ 目录名 —— 目录只按
    内容 hash 前 12 位,进度读端必须经 job 记录的 chunk_dir 字段(C2.2)。"""
    prefix = "single" if kind == "single" else "batch"
    return f"{prefix}_{content_hash[:12]}"


def single_output_name(source: str, safe_title: str, md5: str, timestamp: int) -> str:
    return f"podcast_单篇_{source}_{safe_title}_{md5[:8]}_{timestamp}.wav"


def is_output_for_md5(filename: str, md5: str) -> bool:
    """该成品 wav 是否属于此内容 md5(CacheService「已导出」标记的唯一文法口径,
    M4-⑤)。行为保持自 cache_service 旧内联判断:pinned_ 改名后的成品不计。"""
    return (
        bool(md5)
        and filename.startswith("podcast_")
        and filename.endswith(".wav")
        and md5[:8] in filename
    )


def parse_output_filename(filename: str) -> dict[str, object]:
    """从(可能带 pinned_ 前缀的)文件名解出展示信息。

    与 list_files 的历史行为逐字对拍:非本文法的文件 → title=去前缀原名、
    source="web"。
    """
    is_pinned = "pinned_" in filename
    clean = filename.replace("pinned_", "")
    parts = clean.split("_")
    title: str = clean
    source = "web"
    if len(parts) >= 5 and (parts[0] == "podcast" or parts[0] == ".pending"):
        if parts[1] == "单篇":
            source = parts[2]
            title = parts[3]
        elif parts[1] == "合集":
            source = "web"
            title = BATCH_TITLE
    return {
        "title": title,
        "source": source,
        "is_pinned": is_pinned,
        "is_pending": ".pending_" in filename,
        "clean_filename": clean,
    }


def renamed_filename(filename: str, new_title: str) -> str:
    """rename_file 的纯文件名部分:替换标题段,保留 pinned_ 前缀与扩展名。"""
    safe_title = safe_title_for_rename(new_title)
    is_pinned = "pinned_" in filename
    clean = filename.replace("pinned_", "")
    parts = clean.split("_")
    new_clean = clean
    if len(parts) >= 5 and parts[0] == "podcast":
        if parts[1] in ("单篇", "合集"):
            parts[3] = safe_title
            new_clean = "_".join(parts)
    else:
        base, ext = os.path.splitext(clean)
        new_clean = f"podcast_单篇_web_{safe_title}_{base}{ext}"
    return ("pinned_" if is_pinned else "") + new_clean


def generating_title_from_listing(filenames: list[str]) -> str:
    """/status 的 generating_title:第一个 pending 占位对应的标题,无则 ""。

    单篇 pending 是前缀文法;合集 pending 是后缀文法(历史 /status 用
    startswith 匹配合集,永远打不中 —— 收口后按真实文法匹配)。
    """
    for f in filenames:
        if f.startswith(SINGLE_PENDING_PREFIX):
            parts = f.split("_")
            if len(parts) >= 4:
                return parts[3]
        elif f.endswith(BATCH_PENDING_SUFFIX):
            return BATCH_TITLE
    return ""
