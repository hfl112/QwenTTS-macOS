import Foundation

/// #10 C6:一个端点 = 一个值。传输/令牌/mock/解码全部藏在 BackendAPIClient 的
/// 单一 send/request 缝后面——新端点只需要一个 Endpoint 值,不再复制样板。
struct Endpoint {
    enum Method: String { case get = "GET", post = "POST", patch = "PATCH" }
    var method: Method
    var path: String
    var body: [String: Any]? = nil
    var requireToken: Bool = false
    /// 按端点覆盖超时(nil=走全局 5s)。/read 在模式≠原文时后端**同步**跑完
    /// 翻译/LLM 才返回(可达数十秒),全局 5s 会假报「未被后端接受」而后端
    /// 实际仍在处理、随后照常出声(#13 终局 smoke 实测抓获)。
    var timeoutInterval: TimeInterval? = nil

    static func get(_ path: String, requireToken: Bool = false) -> Endpoint {
        Endpoint(method: .get, path: path, requireToken: requireToken)
    }
    static func post(
        _ path: String,
        body: [String: Any]? = nil,
        requireToken: Bool = false,
        timeout: TimeInterval? = nil
    ) -> Endpoint {
        Endpoint(method: .post, path: path, body: body, requireToken: requireToken, timeoutInterval: timeout)
    }
    static func patch(_ path: String, body: [String: Any]) -> Endpoint {
        Endpoint(method: .patch, path: path, body: body, requireToken: true)
    }
}

/// 主 actor 隔离：所有调用方（@MainActor 的 BackendProcessManager 与各 ViewController）
/// 本就在主线程，借此让 `managementToken` 等可变状态的读写在主 actor 串行化，消除并发
/// Task（健康轮询 / 快照轮询 / 用户动作）对其的无同步写竞争。网络 `await session.data`
/// 在 URLSession 自有线程执行、挂起期间释放主 actor，不阻塞 UI。
@MainActor
class BackendAPIClient {
    let port: Int
    var managementToken: String = ""
    private let session: URLSession

    /// 非 nil 时(仅 `--mock-backend`)所有请求由内存 mock 应答,不发起网络请求。
    var mock: MockBackend?

    // MARK: - 错误冒泡（不再静默吞掉传输错误）
    /// 最近一次传输错误描述（含 path 与底层 error）。成功请求不会清空它，
    /// 由上层（BackendProcessManager / AppStateStore）根据轮询结果决定连接健康度。
    private(set) var lastTransportError: String?
    /// 发生传输错误时的回调（含描述信息），供上层更新连接状态/提示 UI。
    var onTransportError: ((String) -> Void)?

    /// 记录一次传输错误：保存到 lastTransportError 并触发回调。
    private func recordTransportError(path: String, error: Error) {
        let message = "\(path): \(error.localizedDescription)"
        lastTransportError = message
        print("[APIClient] transport error \(message)")
        onTransportError?(message)
    }

    init(port: Int) {
        self.port = port
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 5.0
        self.session = URLSession(configuration: config)
    }

    private var baseURL: URL {
        return URL(string: "http://127.0.0.1:\(port)")!
    }

    // MARK: - 单一传输缝(#10 C6):GET/POST/PATCH、令牌、mock、错误上报只此一份

    // 注：requireToken 在 managementToken 已被 checkHealth 种下后其实是冗余的——
    // 一旦 managementToken 非空，所有请求都会自动带上该头。保留仅为表达调用方意图。
    private func send(_ endpoint: Endpoint) async -> (statusCode: Int, data: Data?) {
        if let mock = mock {
            return mock.respond(method: endpoint.method.rawValue, path: endpoint.path, body: endpoint.body)
        }
        guard let url = URL(string: endpoint.path, relativeTo: baseURL) else { return (0, nil) }
        var request = URLRequest(url: url)
        request.httpMethod = endpoint.method.rawValue
        if let timeout = endpoint.timeoutInterval {
            request.timeoutInterval = timeout  // 按端点覆盖(默认全局 5s)
        }
        if endpoint.method != .get {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if endpoint.requireToken || !managementToken.isEmpty {
            request.setValue(managementToken, forHTTPHeaderField: "x-management-token")
        }
        if let body = endpoint.body {
            do {
                request.httpBody = try JSONSerialization.data(withJSONObject: body)
            } catch {
                return (0, nil)
            }
        }
        do {
            let (data, response) = try await session.data(for: request)
            if let httpResponse = response as? HTTPURLResponse {
                return (httpResponse.statusCode, data)
            }
        } catch {
            recordTransportError(path: "\(endpoint.method.rawValue) \(endpoint.path)", error: error)
        }
        return (0, nil)
    }

    /// 200 + 类型化解码;非 200 / 解码失败 → nil。
    func request<T: Decodable>(_ endpoint: Endpoint, as type: T.Type = T.self) async -> T? {
        let (status, data) = await send(endpoint)
        guard status == 200, let data = data else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    /// 只关心成功与否的命令端点。
    func succeed(_ endpoint: Endpoint) async -> Bool {
        await send(endpoint).statusCode == 200
    }

    // MARK: - Core Control APIs

    func checkHealth(token: String) async -> (alive: Bool, instanceId: String?) {
        self.managementToken = token
        guard let health: HealthResponse = await request(.get("/health", requireToken: true)),
              health.status == "ready", let instanceId = health.instance_id else {
            return (false, nil)
        }
        return (true, instanceId)
    }

    func requestShutdown(token: String) async -> Bool {
        self.managementToken = token
        return await succeed(.post("/control/shutdown", requireToken: true))
    }

    func readText(text: String, voice: String?, performanceProfile: String?, mode: String? = nil) async -> Bool {
        var body: [String: Any] = [
            "text": text,
            "source": "clipboard",
            "from_saved": false
        ]
        if let voice = voice { body["voice"] = voice }
        if let performanceProfile = performanceProfile { body["performance_profile"] = performanceProfile }
        if let mode = mode { body["mode"] = mode }

        // 模式≠原文时 /read 同步跑翻译/LLM 前处理,放宽到 180s(原文模式秒回,不受影响)
        return await succeed(.post("/read", body: body, timeout: 180))
    }

    /// 首启向导「一键试音」：朗读固定短句并**等待真实结果**。
    /// 返回 nil 表示真的出声（成功）；返回非 nil 字符串为失败原因（可直接展示）。
    /// 不同于 readText 只看 HTTP 200——后端会阻塞到产生音频帧或捕获到推理错误才回复，
    /// 因此能区分"听到声音"与"模型缺失/加载失败导致无声"。
    func selfTestVoice() async -> String? {
        let (status, data) = await send(.post("/selftest/voice", requireToken: true))
        guard status == 200, let data = data,
              let result = try? JSONDecoder().decode(SelfTestResponse.self, from: data) else {
            return "试音请求失败（后端无响应或返回异常，HTTP \(status)）"
        }
        if result.ok == true { return nil }
        return result.error ?? "试音失败：未产生音频"
    }

    /// ADR-003: playback commands return the new authoritative playback_status
    /// (nil on failure) so the caller can apply it optimistically.
    private static func parsePlaybackStatus(_ data: Data?) -> PlaybackStatus? {
        guard let data,
              let resp = try? JSONDecoder().decode(PlaybackCommandResponse.self, from: data),
              let raw = resp.playback_status else { return nil }
        return PlaybackStatus(rawValue: raw) ?? .unknown
    }

    func stopPlayback() async -> PlaybackStatus? {
        let (status, data) = await send(.post("/stop", requireToken: true))
        return status == 200 ? Self.parsePlaybackStatus(data) : nil
    }

    func pausePlayback() async -> PlaybackStatus? {
        let (status, data) = await send(.post("/pause"))
        return status == 200 ? Self.parsePlaybackStatus(data) : nil
    }

    func resumePlayback() async -> PlaybackStatus? {
        let (status, data) = await send(.post("/resume"))
        return status == 200 ? Self.parsePlaybackStatus(data) : nil
    }

    func seekPlayback(direction: Int) async -> PlaybackStatus? {
        let (status, data) = await send(.post("/seek", body: ["direction": direction]))
        return status == 200 ? Self.parsePlaybackStatus(data) : nil
    }

    func fetchSnapshot() async -> Snapshot? {
        return await request(.get("/snapshot"))
    }

    func updateSettings(settings: [String: Any], token: String) async -> Bool {
        self.managementToken = token
        return await succeed(.patch("/settings", body: settings))
    }

    func fetchSettings() async -> SettingsModel? {
        await request(.get("/settings"))
    }

    // MARK: - AI 引擎 / 翻译配置

    func fetchEngines() async -> EngineConfig? {
        await request(.get("/engines", requireToken: true))
    }

    func updateEngines(_ body: [String: Any], token: String) async -> Bool {
        self.managementToken = token
        return await succeed(.patch("/engines", body: body))
    }

    /// 检测某供应商连通性。POST /engines/check（带管理令牌）。
    /// 请求体：{ family, provider, key, region }；响应：{ ok, message }。
    /// 解析失败或传输错误时返回 (false, 友好错误文案)。
    func checkEngine(family: String, provider: String, key: String?, region: String?, token: String) async -> (ok: Bool, message: String) {
        self.managementToken = token
        var body: [String: Any] = [
            "family": family,
            "provider": provider,
            "key": key ?? ""
        ]
        if let region = region { body["region"] = region }

        let (status, data) = await send(.post("/engines/check", body: body, requireToken: true))
        guard let data = data else {
            return (false, "无法连接后端（HTTP \(status)）")
        }
        if let result = try? JSONDecoder().decode(EngineCheckResult.self, from: data) {
            return (result.ok, result.message ?? (result.ok ? "验证成功" : "验证失败"))
        }
        return (false, "返回解析失败（HTTP \(status)）")
    }

    // MARK: - Saved Items Queue

    func saveForLater(text: String, source: String = "web", voice: String? = nil, title: String? = nil, mode: String = "original") async -> Bool {
        var body: [String: Any] = ["text": text, "source": source, "mode": mode]
        if let voice = voice { body["voice"] = voice }
        if let title = title { body["title"] = title }

        return await succeed(.post("/save_for_later", body: body))
    }

    func fetchSavedItems() async -> [SavedItem]? {
        await request(.get("/saved_items"))
    }

    func playSaved(indices: [Int]) async -> Bool {
        return await succeed(.post("/play_saved", body: ["indices": indices]))
    }

    func deleteSaved(md5: String?, index: Int?) async -> Bool {
        var body: [String: Any] = [:]
        if let md5 = md5 { body["md5"] = md5 }
        if let index = index { body["index"] = index }
        return await succeed(.post("/delete_saved", body: body))
    }

    func clearSavedItems() async -> Bool {
        return await succeed(.post("/saved_items/clear"))
    }

    // MARK: - URL Reader

    func readUrl(url: String, html: String = "", translate: Bool = false, mode: String = "original", save: Bool = false, podcast: Bool = false) async -> Bool {
        let body: [String: Any] = [
            "url": url,
            "html": html,
            "translate": translate,
            "mode": mode,
            "save": save,
            "podcast": podcast
        ]
        return await succeed(.post("/read_url", body: body))
    }

    func fetchUrlJobs() async -> [UrlJob]? {
        await request(.get("/url_jobs"))
    }

    // MARK: - Podcast Management

    /// #8 F:生成播客的三态结果——开工 / 已有成品(直接播或 force 重做) / 被拒。
    enum PodcastGenerationOutcome {
        case started
        case exists(filename: String)
        case rejected(String?)

        var isAccepted: Bool {
            switch self {
            case .started, .exists: return true
            case .rejected: return false
            }
        }
    }

    func generateSinglePodcast(text: String, source: String = "web", voice: String? = nil, title: String? = nil, performanceProfile: String = "quiet", mode: String = "original", force: Bool = false) async -> PodcastGenerationOutcome {
        var body: [String: Any] = ["text": text, "source": source, "performance_profile": performanceProfile, "mode": mode, "force": force]
        if let voice = voice { body["voice"] = voice }
        if let title = title { body["title"] = title }

        let (status, data) = await send(.post("/generate_single_podcast", body: body))
        if status == 200 {
            if let data = data,
               let resp = try? JSONDecoder().decode(GenerateSinglePodcastResponse.self, from: data),
               resp.status == "exists", let filename = resp.filename {
                return .exists(filename: filename)
            }
            return .started
        } else if status == 409 {
            let detail = data.flatMap { try? JSONDecoder().decode(ErrorDetailResponse.self, from: $0) }?.detail
            return .rejected(detail ?? "该内容已在后台生成中，无需重复提交！")
        } else if status == 400,
                  let detail = data.flatMap({ try? JSONDecoder().decode(ErrorDetailResponse.self, from: $0) })?.detail {
            // S1:LLM 未配置等明确原因
            return .rejected(detail)
        }
        return .rejected("提交生成播客请求失败，请稍后重试")
    }

    func generatePodcast() async -> Bool {
        return await succeed(.post("/generate_podcast"))
    }

    func fetchPodcasts() async -> [PodcastFile]? {
        await request(.get("/podcasts/list"))
    }

    func fetchPodcastTranscript(filename: String) async -> String? {
        let encoded = filename.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? filename
        let resp: TranscriptResponse? = await request(.get("/podcasts/transcript?filename=\(encoded)"))
        return resp?.text
    }

    func fetchPodcastJobs() async -> [PodcastJob]? {
        await request(.get("/podcasts/jobs"))
    }

    func togglePodcastPin(filename: String) async -> Bool {
        return await succeed(.post("/podcasts/toggle_pin", body: ["filename": filename]))
    }

    // ADR-003 F4: pin/unpin a 即时/稍后阅读 item by md5 (storage order unchanged).
    func toggleSavedPin(md5: String) async -> Bool {
        return await succeed(.post("/saved/toggle_pin", body: ["md5": md5]))
    }

    func deletePodcast(filename: String) async -> Bool {
        return await succeed(.post("/podcasts/delete", body: ["filename": filename]))
    }

    func playPodcast(filename: String) async -> Bool {
        return await succeed(.post("/podcasts/play", body: ["filename": filename]))
    }

    func clearPodcasts() async -> Bool {
        return await succeed(.post("/podcasts/clear"))
    }

    // MARK: - Cache Management

    func fetchCacheItems() async -> [CacheItem]? {
        await request(.get("/cache/items"))
    }

    func playCache(md5: String) async -> Bool {
        return await succeed(.post("/cache/play", body: ["md5": md5]))
    }

    func exportCache(md5: String) async -> Bool {
        return await succeed(.post("/cache/export", body: ["md5": md5]))
    }

    func deleteCache(md5: String) async -> Bool {
        return await succeed(.post("/cache/delete", body: ["md5": md5]))
    }

    func clearCache() async -> Bool {
        return await succeed(.post("/cache/clear"))
    }

    func renameSavedItem(md5: String?, index: Int?, newTitle: String, token: String) async -> Bool {
        self.managementToken = token
        var body: [String: Any] = ["title": newTitle]
        if let md5 = md5 { body["md5"] = md5 }
        if let index = index { body["index"] = index }
        return await succeed(.post("/saved_items/update_title", body: body, requireToken: true))
    }

    func renamePodcast(filename: String, newTitle: String, token: String) async -> Bool {
        self.managementToken = token
        let body: [String: Any] = ["filename": filename, "new_title": newTitle]
        return await succeed(.post("/podcasts/rename", body: body, requireToken: true))
    }
}
