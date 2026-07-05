"""ReadOrchestrator — 朗读编排的深模块(计划 #10 C1,新术语见 CONTEXT.md §2)。

拥有「接到朗读请求后按什么流程办」:LLM/翻译前处理(经 reader_bridge)、
RESTART_MODE 的 WAV 重播-vs-TTS 重读决策、chunk 解析与 state 写入、
交给 PlaybackService.play()。此前这些内联在 backend.py 的 /read 路由里,
且被 /selftest/voice、/play_saved、/cache/play、/read_url 以「路由调路由」
的方式复用 —— 现在它们统一调本模块,调用图离开 ASGI 层。

行为保持搬移:逻辑与原 /read 路由逐字等价,由 test_read_orchestration 的
characterization 测试 + test_week3 的 RESTART 测试钉住。
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from core import reader_bridge
from core.api_models import (
    GenerateSinglePodcastRequest,
    ReadRequest,
    ReadUrlRequest,
    SaveForLaterRequest,
)
from core.hashing import text_md5
from core.state.article_store import ArticleStore
from core.services.settings_service import SettingsService

# Seek tuning: a seek into not-yet-generated audio waits for ~SEEK_PREBUFFER_FRAMES
# of cushion instead of stuttering into an empty buffer.(M2:随 seek 编排从
# backend.py 迁入——调参和它服务的决策住在一起。)
SEEK_PREBUFFER_FRAMES = 6


class ReadOrchestrator:
    def __init__(
        self,
        *,
        playback_service: Any,
        podcast_service: Any,
        storage: Any,
        runtime_state: Any,
        shared_state: Any,
        processor: Any,
        event_log: Any,
        settings: Any = None,
        saved_items_service: Any = None,
        url_job_store: Any = None,
        active_url_tasks: dict | None = None,
        read_prebuffer_frames: int = 1,
        create_task: Any = None,
    ) -> None:
        self.playback_service = playback_service
        # podcast_service 允许传 zero-arg provider(晚绑定):backend 的全局实例
        # 可在运行期/测试中被整体替换,orchestrator 必须看到当前值而非 init 快照。
        self._podcast_service_ref = (
            podcast_service if callable(podcast_service) else (lambda: podcast_service)
        )
        self.storage = storage
        # M5:配置语义(含 profile 解析)归 SettingsService;未注入时自建(测试便利)
        self.settings = settings or SettingsService(storage)
        self.article_store = ArticleStore(storage)
        self.runtime_state = runtime_state
        self.shared_state = shared_state
        self.processor = processor
        self.event_log = event_log
        self.saved_items_service = saved_items_service
        self.url_job_store = url_job_store
        # URL 去重表:与 PodcastService/RuntimeSupervisor/若干只读端点共享同一
        # 可变 dict 实例(勿替换整个 dict,只能原地增删)。orchestrator 是唯一写者。
        self.active_url_tasks = active_url_tasks if active_url_tasks is not None else {}
        self.read_prebuffer_frames = read_prebuffer_frames
        # 后台任务派发(生产 = runtime_supervisor.create_task;None 时回退
        # asyncio.create_task,与原路由行为一致)
        self.create_task = create_task

    @property
    def podcast_service(self) -> Any:
        return self._podcast_service_ref()

    def _spawn_background(self, coro: Any, job_id: str) -> None:
        if self.create_task is not None:
            self.create_task(coro, job_id=job_id)
        else:
            asyncio.create_task(coro)

    async def generate_single_podcast(self, data: GenerateSinglePodcastRequest) -> dict:
        """单篇播客请求编排(原 /generate_single_podcast 路由本体):
        409 去重 → #8 exists 复用 → S1 的 LLM 门与后台 LLM→TTS → start_single。"""
        self.runtime_state.touch_activity()

        text = data.text.strip()
        source = data.source
        voice = data.voice
        title = data.title
        mode = reader_bridge.normalize_mode(data.mode)  # N1:旧名归一
        if not text:
            raise HTTPException(status_code=400, detail="Empty text")

        md5_val = text_md5(text)

        # 如果检测到相同内容的任务已在生成中，抛出 HTTP 409 冲突异常
        if self.podcast_service.is_generating(md5_val):
            raise HTTPException(status_code=409, detail="该内容已在后台生成中，无需重复提交！")

        config = await run_in_threadpool(self.storage.load_config)
        config["performance_profile"] = data.performance_profile
        if voice:
            config["voice"] = voice

        # #8 复用:同 内容+模式+音色 已有成品 → 不开工,把成品还给前端二选一(直接播/force 重做)
        if not data.force:
            reusable = self.podcast_service.find_reusable_output(
                text=text, mode=mode, voice=voice, config=config
            )
            if reusable:
                filename = os.path.basename(reusable)
                self.event_log.record(
                    "single_podcast_reused",
                    md5=md5_val,
                    mode=mode,
                    filename=filename,
                )
                return {"status": "exists", "md5": md5_val, "filename": filename}

        # #8 S1:mode≠original 且 text 是原文(非 read_url 已处理的脚本)→ 先 LLM 再 TTS。
        # LLM 在后台任务跑(可能数十秒,不能挂住 HTTP);失败落一条 failed job 让 UI 可见。
        if mode != "original" and not data.preprocessed:
            if mode in ("dual-summary", "dual-trans"):
                if not reader_bridge.llm_selected_available():
                    raise HTTPException(
                        status_code=400,
                        detail="该模式需要 LLM:请先在「AI 引擎」页配置并选定一个可用的 LLM 供应商。",
                    )

            async def llm_then_start():
                try:
                    script = await asyncio.to_thread(
                        reader_bridge.process_with_llm, text, mode
                    )
                    self.podcast_service.start_single(
                        text=script,
                        config=config,
                        md5=md5_val,
                        source=source,
                        title=title,
                        mode=mode,
                        voice=voice,
                        content_identity_text=text,  # 身份=原文,与上面 exists 查重同口径
                    )
                except Exception as e:
                    # LLM 失败:落 failed job(job_store 写入归 PodcastService,C1.4)
                    self.podcast_service.record_failed_single(
                        md5=md5_val,
                        title=title or text[:20],
                        source=source,
                        mode=mode,
                        error=f"LLM 处理失败: {e}",
                    )
                    self.event_log.record(
                        "single_podcast_llm_failed", md5=md5_val, mode=mode, error=str(e)
                    )

            self._spawn_background(llm_then_start(), job_id=f"llm_{md5_val[:12]}")
            self.event_log.record(
                "single_podcast_requested",
                md5=md5_val, source=source, voice=voice, title=title,
                mode=mode, text_chars=len(text),
            )
            return {"status": "generating", "md5": md5_val}

        self.podcast_service.start_single(
            text=text,
            config=config,
            md5=md5_val,
            source=source,
            title=title,
            mode=mode,
            voice=voice,
        )
        self.event_log.record(
            "single_podcast_requested",
            md5=md5_val,
            source=source,
            voice=voice,
            title=title,
            mode=mode,
            text_chars=len(text),
        )

        return {"status": "generating", "md5": md5_val}

    async def save_for_later(self, data: SaveForLaterRequest) -> dict:
        """稍后收藏(原 /save_for_later 路由本体)。"""
        self.runtime_state.touch_activity()
        text = data.text.strip()
        source = data.source
        voice = data.voice
        title = data.title
        if not text:
            raise HTTPException(status_code=400, detail="Empty text")

        mode = reader_bridge.normalize_mode(data.mode)  # N1:旧名归一后落盘
        content_mode = (
            reader_bridge.normalize_mode(data.content_mode) if data.content_mode else None
        )
        count = await run_in_threadpool(
            self.saved_items_service.save, text, source, voice, title, mode, content_mode
        )
        self.event_log.record(
            "saved_item_added",
            source=source, voice=voice, title=title, mode=mode, text_chars=len(text),
        )
        return {"status": "saved", "count": count}

    async def read_url(self, payload: ReadUrlRequest) -> dict:
        """URL 阅读任务编排(原 /read_url 路由本体,不含路由层的空值/SSRF 校验):
        60s 去重 → 建 job → 后台解析 → 按 action 分发到 播客/收藏/朗读。"""
        url = payload.url.strip()
        html = payload.html.strip()
        mode = reader_bridge.normalize_mode(payload.effective_mode())
        save = payload.save
        podcast = payload.podcast
        action = payload.action()

        current_time = time.time()
        for task_url, task_info in list(self.active_url_tasks.items()):
            if current_time - task_info["timestamp"] >= 60:
                self.active_url_tasks.pop(task_url, None)
        if url in self.active_url_tasks:
            return {"status": "error", "message": "该网页正处于后台解析抓取中，请不要重复点击，稍候可在下方收藏列表中查看！"}

        job_id = f"url_{uuid.uuid4().hex[:12]}"
        self.active_url_tasks[url] = {"timestamp": current_time, "is_podcast": podcast, "job_id": job_id}
        self.url_job_store.create(
            job_id=job_id,
            url=url,
            mode=mode,
            action=action,
            has_html=bool(html),
        )
        self.event_log.record(
            "read_url_dispatched",
            job_id=job_id,
            url=url,
            mode=mode,
            action=action,
            has_uploaded_html=bool(html),
        )

        async def run_cli_task():
            try:
                self.url_job_store.update(job_id, status="running", stage="starting")

                def update_stage(stage: str, fields: dict) -> None:
                    self.url_job_store.update(job_id, status="running", stage=stage, **fields)
                    self.event_log.record("url_job_stage", job_id=job_id, url=url, stage=stage, **fields)

                result = await asyncio.to_thread(
                    reader_bridge.process_url_job,
                    url=url,
                    html=html,
                    mode=mode,
                    base_dir=reader_bridge.reader_dir(),
                    cache_dir=os.path.join(reader_bridge.reader_dir(), "cache"),
                    stage_callback=update_stage,
                )
                self.url_job_store.update(
                    job_id,
                    status="dispatching",
                    stage="dispatching",
                    title=result.title,
                    source=result.source,
                    text_chars=len(result.text),
                    from_cache=result.from_cache,
                    error=None,
                )

                if podcast:
                    # #8:mode 透传进 job(身份文本=LLM 脚本,网页更新→脚本变→key 变,天然时效);
                    # preprocessed=True:text 已是 process_url_job 处理过的脚本,不再跑 LLM。
                    await self.generate_single_podcast(
                        GenerateSinglePodcastRequest(
                            text=result.text,
                            source=result.source,
                            voice=result.voice,
                            title=result.title,
                            mode=mode,
                            preprocessed=True,
                        )
                    )
                elif save:
                    await self.save_for_later(
                        SaveForLaterRequest(
                            text=result.text,
                            source=result.source,
                            voice=result.voice,
                            title=result.title,
                            # #12-②:正文已按 mode 处理完毕 → mode 存 original
                            # (生成播客不再跑 LLM),内容形态用 content_mode 展示。
                            content_mode=mode if mode != "original" else None,
                        )
                    )
                else:
                    # URL 阅读统一标记 source="url",方便在缓存列表里区分"网页来源"。
                    await self.read(
                        ReadRequest(text=result.text, source="url", voice=result.voice)
                    )

                self.url_job_store.update(job_id, status="done", stage="done", error=None)
                self.event_log.record("read_url_finished", job_id=job_id, url=url, action=action)
            except Exception as e:
                self.url_job_store.update(job_id, status="failed", stage="failed", error=str(e))
                self.event_log.record("read_url_failed", job_id=job_id, url=url, error=str(e))
            finally:
                self.active_url_tasks.pop(url, None)

        if self.create_task is None:
            self.active_url_tasks.pop(url, None)
            self.url_job_store.update(
                job_id,
                status="failed",
                stage="failed",
                error="runtime supervisor is not ready",
            )
            raise HTTPException(status_code=503, detail="Backend is not ready")
        self.create_task(run_cli_task(), job_id=job_id)
        return {"status": "ok", "job_id": job_id, "message": "Read URL task dispatched"}

    async def read(self, data: ReadRequest) -> dict:
        """一次朗读请求的完整编排(原 /read 路由本体)。"""
        text = data.text
        voice = data.voice
        source = data.source

        # 非原文模式：先经翻译/LLM 引擎处理文本，再走正常朗读流程
        # N1:mode 一律先归一(旧名 podcast-* → dual-*)
        mode = reader_bridge.normalize_mode(data.mode)
        if text and text != "RESTART_MODE" and mode not in ("", "original"):

            def _process():
                return reader_bridge.process_with_llm(text, mode)

            try:
                processed = await run_in_threadpool(_process)
                if processed and processed.strip():
                    text = processed
            except Exception as e:
                self.event_log.record("read_mode_process_failed", mode=mode, error=str(e))
                raise HTTPException(status_code=500, detail=f"{mode} 处理失败: {e}")

        self.runtime_state.clear_current_media(keep_md5=data.from_saved)

        # 新一次朗读开始：清零出声计数与上一次的推理错误，使 /snapshot 与试音判定只反映本次。
        if self.shared_state is not None:
            self.shared_state.reset_run_signals()

        # M5:profile 解析(override > config > balanced)归 SettingsService
        config = self.settings.config_with_profile(data.performance_profile)
        if voice:
            config["voice"] = voice

        if text == "RESTART_MODE":
            # Restart the CURRENT article from the beginning (play button when idle).
            # Empty-state guard: no article → safe no-op.
            current_art = self.article_store.get()
            chunks = current_art.get("chunks", [])
            if not chunks:
                return {"status": "noop"}
            # 若当前文章是已生成的播客,从头重播直接读 WAV(不再用 TTS 在 GPU 上重合成)。
            # 用持久化到 state 的 podcast_filename 判定,冷启动/后端重启后仍有效
            # ——内存信号 runtime_state.current_playing_podcast 重启即失,不能依赖。
            pod_fn = current_art.get("podcast_filename")
            if pod_fn:
                fp = self.podcast_service.find_file(pod_fn)
                if fp:
                    if os.path.exists(fp):
                        self.playback_service.play_wav_file(fp, pod_fn, start_idx=0)
                        return {
                            "status": "ok",
                            "playback_status": self.playback_service.playback_status(),
                        }
            curr_idx = 0
            current_art["current_index"] = 0
            self.article_store.replace(current_art)
        else:
            # 朗读不再自动写入 saved_items —— 否则会挤占只有 5 条上限的"稍后收藏"。
            # 朗读产物本就自动进 /cache;saved_items 只由 /save_for_later 显式写入。
            chunks = self.processor.parse_dialogue_or_text(
                text, performance_profile=config["performance_profile"]
            )
            current_art = {
                "title": text[:15].replace("\n", " ") + "...",
                "chunks": chunks,
                "current_index": 0,
            }
            self.article_store.replace(current_art)
            curr_idx = 0

        # 标记本次朗读来源,供推理缓存分类展示(剪贴板 / url 等)。source 现仅用于
        # /cache 分类,不再触发任何自动保存。
        config["source"] = source or ""

        # Single playback entry (ADR-002): owns session + producer thread + runtime
        # 'playing' state. Routes only do the content prep above.
        session = self.playback_service.play(
            chunks,
            config,
            start_idx=curr_idx,
            title=current_art["title"],
            prebuffer_frames=self.read_prebuffer_frames,
        )
        self.event_log.record(
            "read_requested",
            source=source,
            voice=voice,
            text_chars=len(text),
            session_id=session.id,
        )
        return {"status": "ok"}

    def seek(self, direction: int) -> dict:
        """切句编排(原 /seek 路由本体,M2 收口)。

        wav-vs-TTS 回放决策与 read() 的 RESTART_MODE 分支**同源**:读持久化
        的 current_article.podcast_filename。此前 /seek 读内存信号
        runtime_state.current_playing_podcast(重启即失)——两份决策异源,
        冷启动后切句会退化成 TTS 重合成整篇播客(烧 GPU)。"""
        self.event_log.record("seek_requested", direction=direction)

        current_art = self.article_store.get()
        chunks = current_art.get("chunks", [])
        if not chunks:
            raise HTTPException(status_code=400, detail="No active article")

        curr = current_art.get("current_index", 0)
        new_idx = max(0, min(curr + direction, len(chunks) - 1))
        self.article_store.set_index(new_idx)

        pod_fn = current_art.get("podcast_filename")
        if pod_fn:
            fp = self.podcast_service.find_file(pod_fn)
            if fp and os.path.exists(fp):
                self.playback_service.play_wav_file(fp, pod_fn, start_idx=new_idx)
                return {
                    "status": "seeking",
                    "new_index": new_idx,
                    "playback_status": self.playback_service.playback_status(),
                }

        config = self.settings.config_with_profile()  # M5:同 read(),回退单源

        # seek = play() from the new index, applied IMMEDIATELY so the UI flips to
        # "playing" at once (the pause button stays usable right after pressing
        # next/prev). The larger prebuffer means a seek into not-yet-generated audio
        # waits for a cushion instead of stuttering into an empty buffer; during a
        # 疯狂跳 burst, each press restarts before the cushion fills, so it simply
        # stays silent until the presses stop and then plays from the final index —
        # which gives the debounce effect without deferring play() (ADR-002: play()'s
        # start_new_session invalidates the prior session first).
        self.playback_service.play(
            chunks, config, start_idx=new_idx, prebuffer_frames=SEEK_PREBUFFER_FRAMES
        )
        return {
            "status": "seeking",
            "new_index": new_idx,
            "playback_status": self.playback_service.playback_status(),
        }

    def generate_batch_podcast(self) -> dict:
        """合集播客编排(原 /generate_podcast 路由本体,M2 收口——与单篇
        generate_single_podcast 对称):拼 saved 全文 → 生成中去重 → quiet 档
        + 首条 voice → start_batch → 清空 saved。"""
        saved_items = self.saved_items_service.load()
        if not saved_items:
            raise HTTPException(status_code=400, detail="No saved items")

        text = "\n\n".join(item.get("text", "") for item in saved_items)
        md5_val = text_md5(text)

        # 如果检测到相同内容的任务已在生成中，直接返回成功并清空原列表
        if self.podcast_service.is_generating(md5_val):
            self.saved_items_service.clear()
            return {
                "status": "generating",
                "message": "该合集内容已在后台生成中，无需重复提交！",
            }

        podcasts_dir = self.podcast_service.podcasts_dir
        os.makedirs(podcasts_dir, exist_ok=True)
        filename = os.path.join(
            podcasts_dir, f"podcast_合集_web_大合集播客_{int(time.time())}.wav"
        )

        config = self.storage.load_config()
        config["performance_profile"] = "quiet"
        first_voice = saved_items[0].get("voice") if saved_items else None
        if first_voice:
            config["voice"] = first_voice

        self.podcast_service.start_batch(
            filename=filename,
            text=text,
            config=config,
            md5=md5_val,
        )
        self.event_log.record(
            "batch_podcast_requested",
            md5=md5_val,
            filename=filename,
            item_count=len(saved_items),
            text_chars=len(text),
        )

        self.saved_items_service.clear()
        return {"status": "generating", "filename": filename}
