import Foundation

struct Snapshot: Codable {
    // M4-②(计划 #13):只声明前端真在读的字段——解码了从不读的
    // (status_code/podcast_generation_paused/pause_reason/last_active_time)
    // 已裁,接口诚实;wire 上后端照发,JSONDecoder 忽略多余 key。
    var main_title: String?
    var main_progress: String?
    var main_is_playing: Bool?
    var is_paused: Bool?
    var playback_status: String?        // ADR-003: single computed playback truth
    var current_article_chunks: [String]?
    var current_article_index: Int?

    // 注意：podcast_jobs（对象数组）刻意不纳入，播客任务列表走 fetchPodcastJobs() 专门端点
    var current_podcast_file: String?   // #12-③:正在播放行高亮
    var current_playing_md5: String?
    var active_podcast_processes: Int?  // 侧栏指示的旧字段回退
    // 队列口径:进行中+排队中+暂停的播客任务数(侧栏"生成中"指示用它,反映完整队列)
    var active_podcast_jobs: Int?
    var active_url_tasks: [String]?     // #12-①:停在内容中心也能感知新任务
    var instance_id: String?            // AppDelegate --mock-backend 冒烟判定在读
}

struct SettingsModel: Codable {
    // M4-②:instruct/top_k/lang_code 解码了从不读(设置页写侧走手拼 body),已裁
    var model: String?
    var voice: String?
    var temperature: Double?
    var top_p: Double?
    var seed: Int?
    var repetition_penalty: Double?
    var speed: Double?
    var performance_profile: String?
    // 播客生成专用档位,与朗读档位独立;后端缺键时播客域按 quiet 处理
    var podcast_performance_profile: String?
    var battery_podcast_policy: String?
    var extension_pairing_token: String?
}

// MARK: - AI 引擎 / 翻译配置
// 字段全部 Optional，宽松解析；tiers 这页不编辑，读出后原样回传以免丢字段。

struct EngineTranslateConfig: Codable {
    var selected: String?           // 当前所选 MT 供应商：google / microsoft / deepl
    var target_lang: String?        // 目标语言代码：zh / en / ja ...
    var order: [String]?
    var microsoft_key: String?
    var microsoft_region: String?
    var deepl_key: String?
}

struct EngineLLMConfig: Codable {
    var selected: String?           // 当前所选 LLM 供应商：gemini / claude / openai / deepseek / local
    var order: [String]?
    var keys: [String: String]?
    var local_model_path: String?
    // tiers 形状不固定，仅用于原样读出/回传，不在 UI 编辑
    var tiers: [String: [String: String]]?
}

struct EngineConfig: Codable {
    var translate: EngineTranslateConfig?
    var llm: EngineLLMConfig?

    /// M6:「所选 LLM 是否已配置」的唯一判定(local=有模型路径,其余=有 key)。
    /// 此前该知识内联在 ConsoleVC.ensureLLMConfigured 里,与 EngineSettings 各自
    /// 理解 config 形状。
    var isLLMReady: Bool {
        let selected = llm?.selected ?? "gemini"
        if selected == "local" {
            return !((llm?.local_model_path ?? "").isEmpty)
        }
        return !((llm?.keys?[selected] ?? "").isEmpty)
    }
}

// MARK: - 引擎连通性检测返回
// POST /engines/check 的响应：{ "ok": Bool, "message": String }
struct EngineCheckResult: Codable {
    var ok: Bool
    var message: String?
}

// MARK: - 小型响应模型(#10 C6.4:替掉 client 里手工 JSONSerialization 解析)

struct HealthResponse: Codable {
    var status: String?
    var instance_id: String?
}

struct SelfTestResponse: Codable {
    var ok: Bool?
    var error: String?
}

/// ADR-003:播放命令回传的权威状态载荷。
struct PlaybackCommandResponse: Codable {
    var playback_status: String?
}

struct GenerateSinglePodcastResponse: Codable {
    var status: String?      // "generating" / "exists"
    var md5: String?
    var filename: String?
}

/// FastAPI 4xx 的标准错误体 {"detail": "..."}。
struct ErrorDetailResponse: Codable {
    var detail: String?
}

struct TranscriptResponse: Codable {
    var text: String?
}

// MARK: - 列表端点类型化模型(#10 C6.2)
// 字段以后端 wire 真相为准(saved_items_service / podcast_service.list_files/
// list_jobs / url_jobs / cache_metadata 表)。全部 Optional 容错:
// /saved_items 会注入「⏳ 正在抓取」伪行(无 md5),旧数据可能缺新字段。

struct SavedItem: Codable {
    var text: String?
    var title: String?
    var source: String?
    var voice: String?
    var mode: String?            // #8 S1:导入时定下的播客生成模式
    var md5: String?             // 伪行(抓取中占位)无此字段
    var timestamp: Double?
    var is_exported: Bool?
    var is_pending: Bool?        // URL 抓取中占位行
    var is_pinned: Bool?
    // #11 N2:展示三件套(后端单一真相,前端只渲染)
    var display_title: String?
    var source_label: String?
    var mode_label: String?
}

struct UrlJob: Codable {
    // M4-②:action/stage/text_chars/from_cache 无前端读者,已裁
    var job_id: String?
    var url: String?
    var mode: String?
    var status: String?          // queued/running/dispatching/done/failed
    var title: String?
    var source: String?
    var error: String?
    var created_at: Double?
    var updated_at: Double?
}

struct PodcastJob: Codable {
    var job_id: String?
    var kind: String?            // single/batch
    var md5: String?
    var title: String?
    var source: String?
    var status: String?          // queued/running/paused/done/failed/canceled
    var created_at: Double?
    var updated_at: Double?
    var error: String?
    var mode: String?
    var voice: String?
    // 进度(#10 C2.2 修复后才真实出现):仅活跃任务且 worker 已写 progress.json
    var completed_chunks: Int?
    var total_chunks: Int?
    var progress_percent: Int?
    var display_title: String?
    var source_label: String?
    var mode_label: String?
}

struct PodcastFile: Codable {
    var title: String?
    var filename: String?
    var timestamp: Double?
    var is_pending: Bool?
    var source: String?
    var is_pinned: Bool?
    var display_title: String?
    var source_label: String?
    var mode_label: String?      // job 记录反查;老文件无 → nil 不猜
}

struct CacheItem: Codable {
    var md5: String?
    var text: String?
    var model: String?
    var voice: String?
    var duration: Double?
    var created_at: Double?
    var source: String?          // 旧库迁移前的行可能为 null
    var is_exported: Bool?
    var display_title: String?
    var source_label: String?    // 空 source 的兜底(voice/model)已在后端做
}

// MARK: - 词表(M3,计划 #13):mode 与任务状态的唯一 Swift 口径

/// 朗读/播客内容模式。rawValue 即 wire 字符串(与后端 URL-Reader/modes.py 的
/// 规范名一致;旧名 podcast-* 由后端边界归一,Swift 只说规范名)。
/// 此前 "dual-summary" 等裸串散在 ConsoleVC/LibraryItem/client 签名——typo 会
/// 静默降级成 original,枚举后变编译错。
enum ReadMode: String, CaseIterable {
    case original
    case translate
    case dualSummary = "dual-summary"
    case dualTrans = "dual-trans"

    /// 需要 LLM key 的模式。translate 走机翻引擎不需要 key(#4 C3 教训:
    /// 误 gate 会无谓拦住没配 key 的用户)。
    var requiresLLM: Bool { self == .dualSummary || self == .dualTrans }
}

/// 后台任务状态词表(podcast/url job 共用)。未知串保底 .unknown,不崩不误判。
enum JobStatus: String {
    case running, queued, paused, failed, done, canceled
    case unknown

    init(wire: String?) {
        self = JobStatus(rawValue: (wire ?? "").lowercased()) ?? .unknown
    }

    /// 「还占着/等着服务器算力」的活跃态。轮询触发(AppStateStore)与
    /// "处理中"过滤(LibraryViewModel)共用此口径——此前两处各写一份集合字面量。
    var isActive: Bool { self == .running || self == .queued || self == .paused }
}
