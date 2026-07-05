from pydantic import BaseModel, Field


class ReadRequest(BaseModel):
    text: str = ""
    voice: str | None = None
    source: str | None = None
    from_saved: bool = False
    performance_profile: str | None = None
    mode: str | None = None  # original/translate/dual-summary/dual-trans


class SeekRequest(BaseModel):
    direction: int = 1


class ReadUrlRequest(BaseModel):
    url: str = ""
    html: str = ""
    translate: bool = False
    # original / translate / auto / dual-summary / dual-trans(旧名 podcast-* 兼容).
    # "auto": backend translates to translate.target_lang only when the content's
    # language differs from it (else reads original) — see reader_service.resolve_auto_mode.
    mode: str = "original"
    save: bool = False
    podcast: bool = False

    def effective_mode(self) -> str:
        if self.mode == "original" and self.translate:
            return "translate"
        return self.mode

    def action(self) -> str:
        if self.podcast:
            return "podcast"
        if self.save:
            return "save"
        return "read"


class DeleteSavedRequest(BaseModel):
    md5: str | None = None
    index: int | None = None


class FilenameRequest(BaseModel):
    filename: str = ""


class SaveForLaterRequest(BaseModel):
    text: str = ""
    source: str = "web"
    voice: str | None = None
    title: str | None = None
    # #8 S1:导入时定下的播客生成模式(original/translate/dual-summary/dual-trans),
    # 之后一键生成播客直接按它走,不再弹窗问。
    mode: str = "original"
    # #12-②:正文的"内容形态"(纯展示)。URL→保存 路径存的是已处理好的脚本,
    # mode 必须是 original(生成播客时不再跑 LLM),但展示上它是双人总结/译文
    # ——此前靠往标题里烤 [双人总结] 前缀传递,现改为显式字段,前缀停烤。
    content_mode: str | None = None


class GenerateSinglePodcastRequest(BaseModel):
    text: str = ""
    source: str = "web"
    voice: str | None = None
    title: str | None = None
    # 已废弃(仅保留 wire 兼容):播客档位现由设置 podcast_performance_profile
    # 决定,worker 端 prepare_podcast_config 统一解析,此请求值不再生效。
    performance_profile: str = "quiet"
    # #8 复用去重:mode 参与内容身份(original/dual-summary/dual-trans)
    mode: str = "original"
    # true = 无视已有成品强制重新生成
    force: bool = False
    # true = text 已经是 LLM 处理过的脚本(read_url 路径),端点不再跑 LLM
    preprocessed: bool = False


class PlaySavedRequest(BaseModel):
    indices: list[int] = Field(default_factory=list)


class Md5Request(BaseModel):
    md5: str | None = None


class SettingsUpdateRequest(BaseModel):
    voice: str | None = None
    speed: float | None = None
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    repetition_penalty: float | None = None
    lang_code: str | None = None
    battery_podcast_policy: str | None = None
    performance_profile: str | None = None
    # 播客生成专用档位(fast/balanced/quiet),与读路径 performance_profile 独立;
    # 非法/缺失时播客域回落 quiet(podcast_service.DEFAULT_PODCAST_PROFILE)。
    podcast_performance_profile: str | None = None
    extension_pairing_token: str | None = None


class UpdateTitleRequest(BaseModel):
    title: str
    md5: str | None = None
    index: int | None = None


class RenamePodcastRequest(BaseModel):
    filename: str
    new_title: str

