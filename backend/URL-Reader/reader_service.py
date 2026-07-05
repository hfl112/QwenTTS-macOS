import hashlib
import os
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from typing import Callable


StageCallback = Callable[[str, dict], None]


@dataclass
class UrlReaderResult:
    text: str
    title: str
    source: str
    voice: str | None
    mode: str
    from_cache: bool = False


def noop_stage(stage: str, fields: dict) -> None:
    return None


def cache_key(*parts: str) -> str:
    h = hashlib.md5()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def extract_youtube_video_id(url: str) -> str | None:
    pattern = r"(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})"
    match = re.search(pattern, url)
    return match.group(1) if match else None


def get_youtube_transcript(video_id: str) -> str:
    # Lazy import: youtube-transcript-api is only needed for YouTube URLs. A hard
    # top-level import made a missing package crash ALL URL Reader flows on import.
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        raise RuntimeError(
            "YouTube transcript support needs the 'youtube-transcript-api' package "
            "(declared in requirements.prod.txt)."
        ) from exc
    api = YouTubeTranscriptApi()
    transcript_list = api.fetch(
        video_id,
        languages=["zh", "zh-CN", "zh-TW", "zh-Hans", "zh-Hant", "en"],
    )
    return " ".join([item.text for item in transcript_list])


def cleanup_temp_file(path: str) -> None:
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


def fetch_html_with_proxy_fallback(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
    }
    direct_err_msg = ""
    html_content = ""

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html_content = response.read().decode("utf-8")
    except Exception as e:
        direct_err_msg = str(e)

    if not html_content or len(html_content) < 3000:
        for proxy in [
            "http://127.0.0.1:7890",
            "http://127.0.0.1:1087",
            "http://127.0.0.1:10809",
            "http://127.0.0.1:1080",
        ]:
            try:
                proxy_handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                opener = urllib.request.build_opener(proxy_handler)
                req = urllib.request.Request(url, headers=headers)
                with opener.open(req, timeout=8) as response:
                    temp_html = response.read().decode("utf-8")
                    if len(temp_html) >= 3000:
                        return temp_html
            except Exception:
                continue

    if html_content:
        return html_content

    raise RuntimeError(f"网络及代理均无法提取网页内容。最初错误: {direct_err_msg}")


def defuddle_html(html: str) -> str:
    temp_path = os.path.join(tempfile.gettempdir(), f"defuddle_{os.getpid()}.html")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(html)
        return defuddle_file(temp_path)
    finally:
        cleanup_temp_file(temp_path)


def defuddle_file(html_file_path: str) -> str:
    try:
        result = subprocess.run(
            ["defuddle", "parse", html_file_path, "--md"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except Exception as e:
        raise RuntimeError(f"调用 defuddle 失败: {e}") from e


def extract_title(text: str) -> str:
    non_title_headings = {
        "references",
        "bibliography",
        "works cited",
        "参考文献",
        "参考资料",
        "参考书目",
        "引用文献",
    }
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            if title.lower() not in non_title_headings:
                return title
        if line.startswith("## "):
            title = line[3:].strip()
            if title.lower() not in non_title_headings:
                return title
    return ""


def clean_markdown_content(text: str) -> str:
    """Remove web extraction noise before Gemini/TTS sees the article body."""
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("&nbsp;", " ").replace("&amp;", "&")

    # Drop embedded widgets and raw HTML blocks that Defuddle may keep.
    text = re.sub(
        r"<(?:iframe|script|style|noscript)\b[\s\S]*?</(?:iframe|script|style|noscript)>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<iframe\b[\s\S]*?>", "", text, flags=re.IGNORECASE)

    # Cut bibliography/reference tails in both English and Chinese. Match exact headings only.
    reference_heading = re.compile(
        r"^\s*(?:#{1,6}\s*|\*\*\s*)?"
        r"(?:References|Bibliography|Works\s+Cited|参考文献|参考资料|参考书目|引用文献)"
        r"(?:\s*\*\*)?\s*[:：]?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = reference_heading.search(text)
    if match:
        text = text[: match.start()]

    # Remove footnote/link definitions and citation markers.
    text = re.sub(
        r"^\s*\[\^[^\]]+\]:\s+.*(?:\n(?:[ \t]+|\t).*)*",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^\s*\[[^\]]+\]:\s+https?://\S+.*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(r"\[\^\d+\]|\[\^[^\]]+\]", "", text)
    text = re.sub(r"\[\d+(?:\s*[-,]\s*\d+)*\]", "", text)

    # Keep visible link text, drop URL payloads and standalone URLs.
    text = re.sub(r"!\[[\s\S]*?\]\([\s\S]*?\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\(\s*https?://[^\)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(\s*#[^\)]*\)", r"\1", text)
    text = re.sub(r"[\(（]\s*https?://[^\s\)）]+[\)）]", "", text)
    text = re.sub(r"https?://\S+", "", text)

    # Drop remaining raw HTML tags, but keep their text content.
    text = re.sub(r"</?[^>\n]+>", "", text)

    # Normalize noisy blank lines and trailing spaces without flattening paragraphs.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_cache(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def write_cache(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# Podcast prompts, dispatched through the provider-agnostic LLM engine.
_PODCAST_DISCUSS_PROMPT = (
    "你是一个播客脚本创作专家。请将以下输入的文章或网页内容，改编为一个类似 NotebookLM 风格的双人对谈播客脚本。\n"
    "要求：\n"
    "1. 播客有两个主持人：[Serena] (女性，语气温和、好奇、擅长引导和总结) 和 [Ryan] (男性，语气幽默、博学、擅长解释专业概念和给出例证)。\n"
    "2. 他们需要交替对话，以通俗易懂、口语化、生动有趣的方式讨论和解释输入文章的核心内容，让听众像听故事一样理解这篇文章。\n"
    "3. 输出格式必须严格遵循以下格式（中英文冒号均可，但每行必须以 [Serena]: 或 [Ryan]: 开头，且只有这两个人，绝不能包含其他说话人）：\n"
    "   [Serena]: [说话内容]\n"
    "   [Ryan]: [说话内容]\n"
    "4. 对话内容应使用全中文进行（但技术术语和专有名词可保留英文，以便自然朗读）。\n"
    "5. 对话轮数建议在 8 到 15 轮之间，使内容充实。每一轮说话要口语化，不要过长（单次说话在 50~150 字以内为宜）。\n"
    "6. 仅输出最终的对话内容，绝对不能包含任何 ```markdown、多余的前言、后记或解释性段落。\n\n"
    "待对谈解释的原文内容如下：\n"
)
_PODCAST_TRANS_PROMPT = (
    "你是一个播客翻译和编辑专家。请将以下输入的内容，以双人对谈翻译（一问一答）的形式翻译并改编为中文播客脚本。\n"
    "要求：\n"
    "1. 播客有两个角色：[Serena] (负责用中文进行提问、引出段落主题或承上启下) 和 [Ryan] (负责对原文的具体内容进行直译、解释与解答)。\n"
    "2. 你需要梳理文章脉络，如果是访谈记录，则直接对应翻译；如果是单人文章，请将其解构为 [Serena] 提问/引导、[Ryan] 翻译/具体阐述的交替对话形式。\n"
    "3. 输出格式必须严格遵循以下格式（中英文冒号均可，但每行必须以 [Serena]: 或 [Ryan]: 开头）：\n"
    "   [Serena]: [用中文提问或引导]\n"
    "   [Ryan]: [对应的中文翻译与具体解释内容]\n"
    "4. 所有的对话和回答均使用中文进行。\n"
    "5. 仅输出最终的对话内容，绝对不能包含任何 ```markdown、多余的前言、后记或解释性段落。\n\n"
    "待翻译并解构的原文内容如下：\n"
)


def process_with_llm(
    text: str,
    mode: str,
    *,
    cache_dir: str | None = None,
    use_cache: bool = True,
) -> str:
    """Dispatch a reader mode through the provider-agnostic engines.

    translate -> machine-translation engine (with optional LLM fallback)
    dual-summary / dual-trans -> creative LLM engine(旧名 podcast-* 自动归一)

    #8 R4:podcast-* 的 LLM 文稿缓存下沉到这里(key=mode+text,文件名与
    process_url_job 外层缓存同款,跨入口天然共享)。同一篇文稿同一模式只烧
    一次 LLM。process_url_job 自带外层缓存,调用时传 use_cache=False 防双写。
    """
    from modes import legacy_equivalents, normalize_mode

    mode = normalize_mode(mode)
    if mode == "translate":
        from translation_engine import translate_text
        # target=None → 取配置里的 target_lang（默认 zh）;机器翻译不走 LLM 缓存
        return translate_text(text)
    if mode in ("dual-trans", "dual-summary"):
        from llm_engine import call_llm

        cache_path = None
        if use_cache:
            cdir = cache_dir or os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "cache"
            )
            os.makedirs(cdir, exist_ok=True)
            cache_path = os.path.join(cdir, f"{mode}_{cache_key(mode, text)}.md")
            cached = read_cache(cache_path)
            if cached is not None:
                return cached
            # N1 改名兼容:旧模式名(podcast-*)时代的文稿缓存仍可命中,
            # 命中后顺手写回规范 key,老缓存自然淘汰。
            for legacy in legacy_equivalents(mode):
                legacy_path = os.path.join(
                    cdir, f"{legacy}_{cache_key(legacy, text)}.md"
                )
                cached = read_cache(legacy_path)
                if cached is not None:
                    write_cache(cache_path, cached)
                    return cached

        if mode == "dual-trans":
            prompt = _PODCAST_TRANS_PROMPT + text
            step = "PodcastTranslation"
        else:
            prompt = _PODCAST_DISCUSS_PROMPT + text
            step = "PodcastDiscussion"
        result = call_llm(prompt, tier="standard", step_name=step)
        if cache_path is not None:
            write_cache(cache_path, result)
        return result
    return text


def _detect_lang(text: str) -> str:
    """Coarse zh/en detection for the auto-translate decision: 'zh' if the text
    is predominantly Chinese, else 'en'. Chinese is dense and English articles
    carry ~no CJK, so a simple CJK-vs-Latin count is robust for the zh<->en case.
    Any non-zh/en target therefore always triggers a translation (safe default)."""
    cjk = 0
    latin = 0
    for ch in text:
        if "一" <= ch <= "鿿":
            cjk += 1
        elif ch.isascii() and ch.isalpha():
            latin += 1
    return "zh" if cjk > latin else "en"


def resolve_auto_mode(text: str) -> str:
    """'auto' reading mode: translate to the configured translate.target_lang
    only when the content's language differs from it; otherwise read the
    original. target_lang=en forces English output, =zh forces Chinese, etc."""
    try:
        from engine_config import load_engines
        target = (load_engines().get("translate", {}) or {}).get("target_lang") or "zh"
    except Exception:
        target = "zh"
    target_family = "zh" if target.startswith("zh") else target
    return "original" if _detect_lang(text) == target_family else "translate"


class Fetcher:
    """「碰外界拿原始 markdown」的窄缝(M9,计划 #13;ADR-001 ModelBackend 同款
    手法)。三个来源分支——上传 HTML / YouTube 转写 / 网络抓取+代理回退——是它的
    实现;process_url_job 的两层缓存、auto-mode、LLM 分发全在缝**上面**,对着
    FakeFetcher 即可全测(test_url_fetcher_seam),不碰网络。"""

    def fetch_markdown(
        self,
        *,
        url: str,
        html: str,
        video_id: str | None,
        callback: StageCallback,
    ) -> str:
        if html.strip():
            callback("parsing", {"method": "uploaded_html"})
            return defuddle_html(html)
        if video_id:
            callback("fetching", {"method": "youtube_transcript"})
            return get_youtube_transcript(video_id)
        callback("fetching", {"method": "network"})
        fetched_html = fetch_html_with_proxy_fallback(url)
        callback("parsing", {"method": "network_html"})
        return defuddle_html(fetched_html)


_DEFAULT_FETCHER = Fetcher()


def process_url_job(
    *,
    url: str,
    html: str = "",
    mode: str = "dual-summary",
    base_dir: str | None = None,
    cache_dir: str | None = None,
    stage_callback: StageCallback | None = None,
    fetcher: Fetcher | None = None,
) -> UrlReaderResult:
    from modes import normalize_mode

    mode = normalize_mode(mode)
    callback = stage_callback or noop_stage
    base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
    cache_dir = cache_dir or os.path.join(base_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    video_id = extract_youtube_video_id(url)
    is_youtube = video_id is not None
    source_type = "video" if is_youtube else "web"

    source_key = cache_key(url, html[:2000])
    source_cache_path = os.path.join(cache_dir, f"source_{source_key}.md")
    markdown_content = read_cache(source_cache_path)
    from_cache = markdown_content is not None

    if markdown_content is None:
        callback("fetching", {"source": source_type, "has_html": bool(html)})
        # M9:抓取分支归 Fetcher 缝,这里只编排(缓存/清洗/落盘)
        markdown_content = (fetcher or _DEFAULT_FETCHER).fetch_markdown(
            url=url, html=html, video_id=video_id, callback=callback
        )
        markdown_content = clean_markdown_content(markdown_content)
        write_cache(source_cache_path, markdown_content)
    else:
        markdown_content = clean_markdown_content(markdown_content)

    if not markdown_content.strip():
        raise RuntimeError("抓取到的内容为空")

    temp_source_path = os.path.join(base_dir, "temp_source.md")
    write_cache(temp_source_path, markdown_content)

    # 'auto': decide translate-vs-original by comparing the content's language to
    # the configured target_lang. Resolve here so the cache key / title / LLM
    # dispatch below all see a concrete mode (translate or original).
    if mode == "auto":
        mode = resolve_auto_mode(markdown_content)
        callback("auto_resolved", {"mode": mode})

    processed_content = markdown_content
    if mode != "original":
        processed_key = cache_key(mode, markdown_content)
        processed_cache_path = os.path.join(cache_dir, f"{mode}_{processed_key}.md")
        cached_processed = read_cache(processed_cache_path)
        if cached_processed is not None:
            processed_content = clean_markdown_content(cached_processed)
            from_cache = True
        else:
            callback("gemini", {"mode": mode})
            # 外层已有本函数自己的缓存(上方 processed_cache_path),关掉内层防双写
            processed_content = process_with_llm(markdown_content, mode, use_cache=False)
            processed_content = clean_markdown_content(processed_content)
            write_cache(processed_cache_path, processed_content)

        temp_translated_path = os.path.join(base_dir, "temp_translated.md")
        write_cache(temp_translated_path, processed_content)

    raw_title = extract_title(processed_content)
    # #12-②:停止把模式烤进标题——内容形态由 saved_items 的 content_mode /
    # job 的 mode 显式承载,展示走 mode_label。旧数据的前缀由 labels 层剥/推断。
    full_title = raw_title
    voice = None
    if is_youtube and mode not in ("dual-trans", "dual-summary"):
        voice = "Ryan"

    callback(
        "processed",
        {
            "source": source_type,
            "title": full_title,
            "text_chars": len(processed_content),
            "from_cache": from_cache,
        },
    )
    return UrlReaderResult(
        text=processed_content,
        title=full_title,
        source=source_type,
        voice=voice,
        mode=mode,
        from_cache=from_cache,
    )
