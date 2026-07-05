import Foundation

enum BackendState: String {
    case stopped
    case launching
    case waitingForHealth
    case ready
    case stopping
    case failed
}

/// 集中式状态管理。作为唯一的播放/快照数据源：BackendProcessManager 的单一轮询器
/// 写入这里并通知所有订阅者（如 ConsoleViewController），不再各自轮询 /snapshot。
///
/// 该类型上的状态始终在主线程读写；监听者回调也始终在主线程触发。
/// 用 `@MainActor` 让编译器强制这一契约（此前仅靠调用方恰好继承主线程，无强制）。
@MainActor
class AppStateStore {
    // MARK: - 集中式状态
    private(set) var backendState: BackendState = .stopped
    private(set) var currentTitle: String = ""
    private(set) var progressText: String = ""
    private(set) var isPlaying: Bool = false
    private(set) var isPaused: Bool = false

    // ADR-003: the single reconciled playback truth the UI renders. Updated
    // optimistically from command responses and from polls (stale polls dropped).
    private(set) var playbackStatus: PlaybackStatus = .idle
    /// When the last playback command's result was applied — polls issued before
    /// this are pre-command views and must not overwrite the optimistic status.
    private var lastCommandAt: Date?

    /// 最新一次拉到的完整 Snapshot（单一数据源）
    private(set) var lastSnapshot: Snapshot?

    // MARK: - 连接健康度 / 错误冒泡
    /// 轮询 / health 成功时为 true；传输失败时为 false，供 UI 显示“后端未连接/请求失败”。
    private(set) var connectionHealthy: Bool = true
    /// 最近一次传输错误描述（含 path 与 error）；连接恢复后清空。
    private(set) var lastError: String?

    // MARK: - 订阅机制
    /// 监听者表，按整型 token 索引，便于注销。每次有新 Snapshot 时在主线程回调。
    private var snapshotListeners: [Int: (Snapshot) -> Void] = [:]
    private var nextListenerToken = 0

    /// 注册一个快照监听者，返回用于注销的 token。
    @discardableResult
    func addSnapshotListener(_ listener: @escaping (Snapshot) -> Void) -> Int {
        let token = nextListenerToken
        nextListenerToken += 1
        snapshotListeners[token] = listener
        return token
    }

    /// 注销之前注册的监听者。
    func removeSnapshotListener(_ token: Int) {
        snapshotListeners.removeValue(forKey: token)
    }

    // MARK: - 状态更新入口
    func updateBackendState(_ state: BackendState) {
        self.backendState = state
        print("[AppStateStore] Backend state changed -> \(state.rawValue)")
    }

    func updatePlayback(title: String, progress: String, playing: Bool, paused: Bool) {
        self.currentTitle = title
        self.progressText = progress
        self.isPlaying = playing
        self.isPaused = paused
    }

    /// ADR-003: apply a playback command's returned status optimistically, so the
    /// UI flips immediately instead of waiting for the next ~500ms poll. Records
    /// the time so in-flight (pre-command) polls don't overwrite it.
    func applyCommandResult(_ status: PlaybackStatus, at now: Date = Date()) {
        self.playbackStatus = status
        self.lastCommandAt = now
    }

    /// 单一轮询器拉到 snapshot 后调用：更新派生字段并通知所有监听者。
    /// `issuedAt` = 该次轮询请求的发起时刻，用于丢弃命令之前的过期视图（ADR-003 #5）。
    func updateSnapshot(_ snapshot: Snapshot, issuedAt: Date = Date()) {
        self.lastSnapshot = snapshot
        let polled = PlaybackStatus(rawValue: snapshot.playback_status ?? "") ?? .unknown
        self.playbackStatus = PlaybackReconciler.reconcile(
            current: self.playbackStatus,
            polled: polled,
            polledIssuedAt: issuedAt,
            lastCommandAt: self.lastCommandAt
        )
        updatePlayback(
            title: snapshot.main_title ?? "",
            progress: snapshot.main_progress ?? "",
            playing: snapshot.main_is_playing ?? false,
            paused: snapshot.is_paused ?? false
        )
        // 拉到 snapshot 说明连接正常
        if !connectionHealthy || lastError != nil {
            connectionHealthy = true
            lastError = nil
        }
        // 通知订阅者（@MainActor 保证主线程）。先快照成数组再遍历：监听者回调可能
        // 在视图出现/消失时重入注册/注销，直接遍历字典会因迭代中改集合而崩溃。
        for listener in Array(snapshotListeners.values) {
            listener(snapshot)
        }
    }

    /// 轮询 / health 失败时调用，记录错误并标记连接不健康。
    func reportConnectionError(_ message: String) {
        self.connectionHealthy = false
        self.lastError = message
        print("[AppStateStore] Connection error: \(message)")
    }

    // MARK: - 服务器列表状态(#10 C7:store 是唯一取数路,VC/VM 只订阅)

    /// 列表取数走这同一个 client(由 BackendProcessManager 每次 ready 时注入,
    /// 与快照轮询同源)。强引用:client 不回指 store,无引用环;旧实例随重注入释放。
    var listClient: BackendAPIClient?

    private(set) var savedItems: [SavedItem] = []
    private(set) var podcastFiles: [PodcastFile] = []
    private(set) var podcastJobs: [PodcastJob] = []
    private(set) var urlJobs: [UrlJob] = []
    private(set) var cacheItems: [CacheItem] = []

    private var listListeners: [Int: () -> Void] = [:]
    private var listPollTask: Task<Void, Never>?

    /// 注册列表变更监听(任一集合刷新后回调,主线程)。
    @discardableResult
    func addListListener(_ listener: @escaping () -> Void) -> Int {
        let token = nextListenerToken
        nextListenerToken += 1
        listListeners[token] = listener
        return token
    }

    func removeListListener(_ token: Int) {
        listListeners.removeValue(forKey: token)
    }

    private func notifyListListeners() {
        for listener in Array(listListeners.values) {
            listener()
        }
    }

    /// 刷新任务列表(播客 + URL)。
    /// M8:`notify: false` = 静默刷新(只更新集合不广播)——失败监视的 500ms 突发
    /// 轮询用它,否则 Console 的一次监视会让内容中心以 2Hz 全量重建 8 秒。
    func refreshJobs(notify: Bool = true) async {
        guard let client = listClient else { return }
        async let pods = client.fetchPodcastJobs()
        async let urls = client.fetchUrlJobs()
        let (p, u) = await (pods, urls)
        if let p { podcastJobs = p }
        if let u { urlJobs = u }
        if notify { notifyListListeners() }
    }

    /// 按内容中心 tab 刷新对应集合(1=稍后朗读 2=播客(成品+任务) 3=缓存)。
    func refreshLibrary(tab: Int) async {
        guard let client = listClient else { return }
        switch tab {
        case 1:
            if let items = await client.fetchSavedItems() { savedItems = items }
        case 2:
            async let files = client.fetchPodcasts()
            async let jobs = client.fetchPodcastJobs()
            let (f, j) = await (files, jobs)
            if let f { podcastFiles = f }
            if let j { podcastJobs = j }
        case 3:
            if let items = await client.fetchCacheItems() { cacheItems = items }
        default:
            break
        }
        notifyListListeners()
    }

    /// 是否存在"处理中"的服务器侧工作(URL 抓取占位 / 活跃播客任务)。
    /// fa52c18 规则:只有它为真时才周期刷新,平时零开销。
    /// #12-①:除列表集合外,还看 500ms 快照的 active_podcast_jobs/active_url_tasks
    /// ——用户停在内容中心不动、从主控台/扩展提交的新任务也能被感知到
    /// (此前列表集合是空的就永远不刷,新任务要手动切 tab 才出现)。
    var hasPendingServerWork: Bool {
        if savedItems.contains(where: { $0.is_pending == true }) { return true }
        // M3:活跃集合唯一口径 = JobStatus.isActive(与 LibraryViewModel 过滤同源)
        if podcastJobs.contains(where: { JobStatus(wire: $0.status).isActive }) { return true }
        if let snap = lastSnapshot {
            if (snap.active_podcast_jobs ?? 0) > 0 { return true }
            if !(snap.active_url_tasks ?? []).isEmpty { return true }
        }
        return false
    }

    /// 唯一的列表周期轮询器(#10 C7.2):每 10s,仅当有处理中条目时刷新。
    /// LibraryView 原先的 Timer 已删——轮询拥有者收口到 store。
    func startListPolling() {
        listPollTask?.cancel()
        listPollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(10))
                guard let self, !Task.isCancelled else { return }
                if self.hasPendingServerWork {
                    await self.refreshLibrary(tab: 1)
                    await self.refreshLibrary(tab: 2)
                }
            }
        }
    }

    func stopListPolling() {
        listPollTask?.cancel()
        listPollTask = nil
    }
}
