import SwiftUI
import AppKit

struct LibraryItem: Identifiable, Hashable {
    let rawTitle: String
    let source: String
    let status: String
    let time: String
    let isPlaying: Bool
    let type: ItemType
    var savedIndex: Int? = nil   // saved/instant：在完整 saved_items 列表里的原始下标
    var md5: String? = nil       // saved/cache：用于删除/播放缓存
    var filename: String? = nil  // podcast：文件名
    var fullText: String = ""    // 完整文本（双击查看）
    var isPinned: Bool = false   // podcast：是否已置顶
    var timestamp: Double = 0.0
    var isPending: Bool = false
    var savedMode: String = "original"  // #8 S1:导入时定下的播客生成模式
    var modeLabel: String? = nil        // #11 N2:后端 mode_label(有则优先于前缀反推)

    /// 稳定身份(复核 #10 P1-2):此前 `id = UUID()` 每次 rebuildItems 全换,
    /// 后台 10s 轮询触发重建时用户的多选/悬浮态每次被清空。改为按业务键派生:
    /// 同一条目跨刷新同 id,SwiftUI 才能保住选中与行状态。
    var id: String {
        switch type {
        case .podcast:
            return "podcast-\(filename ?? rawTitle)"
        case .cache:
            return "cache-\(md5 ?? rawTitle)"
        case .saved, .instant:
            return "saved-\(md5 ?? "idx-\(savedIndex ?? -1)")"
        }
    }

    var title: String {
        let t = rawTitle
        if t.hasPrefix("[双人总结]") {
            return t.replacingOccurrences(of: "[双人总结]", with: "")
        } else if t.hasPrefix("[双人翻译]") {
            return t.replacingOccurrences(of: "[双人翻译]", with: "")
        } else if t.hasPrefix("[翻译]") {
            return t.replacingOccurrences(of: "[翻译]", with: "")
        } else if t.hasPrefix("[译·") {
            if let range = t.range(of: "]") {
                return String(t[range.upperBound...])
            }
        }
        return t
    }

    var modeTag: String {
        if let label = modeLabel, !label.isEmpty { return label }
        let t = rawTitle
        if t.hasPrefix("[双人总结]") {
            return "双人总结"
        } else if t.hasPrefix("[双人翻译]") {
            return "双人翻译"
        } else if t.hasPrefix("[翻译]") || t.hasPrefix("[译·") {
            return "翻译"
        }
        return "原文"
    }

    enum ItemType {
        case instant, saved, podcast, cache
    }
}

// MARK: - 视图模型：注入 coordinator，从真实后端拉取各分类内容
@MainActor
final class LibraryViewModel: ObservableObject {
    weak var coordinator: ApplicationCoordinator?

    @Published var items: [LibraryItem] = []
    @Published var isLoading = false

    /// 当前展示的 tab(store 列表变更回调时按它重建 items)。
    private(set) var currentTab: Int = 1
    private var listListenerToken: Int?

    private var lastPlayingKey: String = ""

    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
        // #10 C7:订阅 store 的列表变更——store 是唯一取数路,VM 只做展示映射。
        listListenerToken = coordinator?.stateStore.addListListener { [weak self] in
            self?.rebuildItems()
        }
        // #12-③:播放目标(saved/cache 的 md5、播客文件名)变化时重建,
        // 让"正在播放"行高亮即时点亮/熄灭,而不是等下一次列表刷新。
        coordinator?.stateStore.addSnapshotListener { [weak self] snap in
            guard let self else { return }
            let key = (snap.current_playing_md5 ?? "") + "|" + (snap.current_podcast_file ?? "")
            if key != self.lastPlayingKey {
                self.lastPlayingKey = key
                self.rebuildItems()
            }
        }
    }

    private var apiClient: BackendAPIClient? {
        coordinator?.processManager.apiClient
    }

    private var store: AppStateStore? {
        coordinator?.stateStore
    }

    // MARK: 时间格式化（timestamp 为秒级 Double）
    private func formatTime(_ timestamp: Double) -> String {
        let date = Date(timeIntervalSince1970: timestamp)
        let cal = Calendar.current
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        if cal.isDateInToday(date) {
            formatter.dateFormat = "今天 HH:mm"
        } else if cal.isDateInYesterday(date) {
            formatter.dateFormat = "昨天 HH:mm"
        } else {
            formatter.dateFormat = "M月d日"
        }
        return formatter.string(from: date)
    }

    private func truncate(_ text: String, _ n: Int = 40) -> String {
        if text.count <= n { return text }
        return String(text.prefix(n)) + "…"
    }

    private func filenameWithoutExtension(_ name: String) -> String {
        (name as NSString).deletingPathExtension
    }

    /// 缓存来源分类标签。后端 source:clipboard/url/web/video/selection…;
    /// 旧缓存无 source 时回退到 voice/model,保持兼容。
    private func cacheSourceLabel(_ source: String?, voice: String, model: String) -> String {
        switch source {
        case "clipboard": return "剪贴板"
        case "url", "web": return "网页"
        case "video": return "视频"
        case "selection": return "选中"
        case .some(let s) where !s.isEmpty: return s
        default:
            return voice.isEmpty ? (model.isEmpty ? "缓存" : model) : voice
        }
    }

    // MARK: 按分类加载
    func load(tab: Int) async {
        currentTab = tab
        guard let store = store else {
            items = []
            return
        }
        isLoading = true
        defer { isLoading = false }
        await store.refreshLibrary(tab: tab)
        rebuildItems()
    }

    /// store 集合 → LibraryItem 展示映射(纯函数式重建;store 变更回调也走这里)。
    func rebuildItems() {
        guard let store = store else {
            items = []
            return
        }
        // #12-③:正在播放的条目(#10 前 isPlaying 恒 false,过滤按钮永远筛空)
        let playingMd5 = store.lastSnapshot?.current_playing_md5
        let playingPodcast = store.lastSnapshot?.current_podcast_file
        switch currentTab {
        case 1:
            // 稍后阅读 = 全部 saved_items(朗读已不再写入此处,仅显式"稍后保存")。
            let raw = store.savedItems
            // 保留原始 index（用于 playSaved / deleteSaved）;#10 C6.3:类型化解码
            let mapped: [LibraryItem] = raw.enumerated().map { (idx, item) in
                let text = item.text ?? ""
                let title = item.title ?? ""
                // #11 N2:后端 display_title 优先(干净标题,已剥历史模式前缀)
                let displayTitle = item.display_title ?? (title.isEmpty ? truncate(text) : title)
                let timestamp = item.timestamp ?? 0
                let isPinned = item.is_pinned ?? false
                // 后端把"URL 抓取处理中"的任务以 is_pending=true 插到列表顶,
                // 映射过来驱动 10 秒轮询,完成后自动变成正式条目。
                let isPending = item.is_pending ?? false
                let source = item.source_label ?? item.source ?? ""
                return LibraryItem(
                    rawTitle: displayTitle,
                    source: source.isEmpty ? "保存" : source,
                    status: isPending ? "处理中" : (isPinned ? "已置顶" : "已保存"),
                    time: formatTime(timestamp),
                    isPlaying: item.md5 != nil && item.md5 == playingMd5,
                    type: .saved,
                    savedIndex: idx,
                    md5: item.md5,
                    fullText: text,
                    isPinned: isPinned,
                    timestamp: timestamp,
                    isPending: isPending,
                    savedMode: item.mode ?? "original",
                    modeLabel: item.mode_label
                )
            }
            items = mapped

        case 2:
            let rawFiles = store.podcastFiles
            // 过滤掉 is_pending 为 true 的临时生成文件，避免与 activeJobs 重复，也避免误显示为“就绪”
            let finishedFiles = rawFiles.filter { !($0.is_pending ?? false) }
            let fileItems: [LibraryItem] = finishedFiles.map { file in
                let filename = file.filename ?? ""
                let isPinned = file.is_pinned ?? false
                let timestamp = file.timestamp ?? 0
                let title = file.display_title ?? file.title ?? filenameWithoutExtension(filename)
                return LibraryItem(
                    rawTitle: title,
                    source: file.source_label ?? "播客",
                    status: isPinned ? "已置顶" : "就绪",
                    time: formatTime(timestamp),
                    isPlaying: !filename.isEmpty && filename == playingPodcast,
                    type: .podcast,
                    filename: filename,
                    isPinned: isPinned,
                    timestamp: timestamp,
                    isPending: false,
                    modeLabel: file.mode_label
                )
            }

            let rawJobs = store.podcastJobs
            // M3:活跃集合唯一口径 = JobStatus.isActive(与 store 轮询触发同源)
            let activeJobs = rawJobs.filter { JobStatus(wire: $0.status).isActive }

            let jobItems: [LibraryItem] = activeJobs.map { job in
                let createdAt = job.created_at ?? 0
                // C8.1:任务状态文案统一走 JobPresentation(与 Console 同词表)
                let statusText = JobPresentation.statusText(for: job)

                return LibraryItem(
                    rawTitle: job.display_title ?? job.title ?? "未命名播客",
                    source: job.source_label ?? job.source ?? "web",
                    status: statusText,
                    time: formatTime(createdAt),
                    isPlaying: false,
                    type: .podcast,
                    filename: job.job_id ?? "",
                    isPinned: false,
                    timestamp: createdAt,
                    isPending: true,
                    modeLabel: job.mode_label
                )
            }
            items = fileItems + jobItems

        case 3:
            let raw = store.cacheItems
            items = raw.map { row in
                let text = row.text ?? ""
                let durationStr = row.duration.map { String(format: "%.1fs", $0) } ?? ""
                // C6.3 顺手修:created_at 是数字时间戳,旧裸字典按 String 取永远为空
                // → 缓存列表时间列此前一直空白;类型化后正常显示。
                let timestamp = row.created_at ?? 0
                return LibraryItem(
                    rawTitle: row.display_title ?? truncate(text),
                    source: row.source_label ?? cacheSourceLabel(row.source, voice: row.voice ?? "", model: row.model ?? ""),
                    status: durationStr,
                    time: formatTime(timestamp),
                    isPlaying: row.md5 != nil && row.md5 == playingMd5,
                    type: .cache,
                    md5: row.md5,
                    fullText: text,
                    timestamp: timestamp
                )
            }

        default:
            items = []
        }
        // ADR-003 F4: pinned-first for DISPLAY ONLY. savedIndex was already
        // captured from the backend's original order above, so play/delete still
        // hit the right item; this only changes what the user sees.
        items = items.filter { $0.isPinned } + items.filter { !$0.isPinned }
    }

    // MARK: 操作
    func fetchTranscript(filename: String) async -> String? {
        guard let client = coordinator?.processManager.apiClient else { return nil }
        return await client.fetchPodcastTranscript(filename: filename)
    }

    func play(_ item: LibraryItem) {
        guard let client = apiClient else { return }
        Task {
            switch item.type {
            case .instant, .saved:
                if let idx = item.savedIndex { _ = await client.playSaved(indices: [idx]) }
            case .podcast:
                if let filename = item.filename { _ = await client.playPodcast(filename: filename) }
            case .cache:
                if let md5 = item.md5 { _ = await client.playCache(md5: md5) }
            }
        }
    }

    func delete(_ item: LibraryItem, currentTab: Int) {
        guard let client = apiClient else { return }
        Task {
            switch item.type {
            case .instant, .saved:
                _ = await client.deleteSaved(md5: item.md5, index: item.savedIndex)
            case .podcast:
                if let filename = item.filename { _ = await client.deletePodcast(filename: filename) }
            case .cache:
                if let md5 = item.md5 { _ = await client.deleteCache(md5: md5) }
            }
            // M8:变更后经 store 单一刷新路(推送自会 rebuild),不再走 VM 的 load 拉取。
            // 不做本地乐观删除:saved 行按位置 index 定位(F4-C2 教训),必须以服务器为准。
            await store?.refreshLibrary(tab: currentTab)
        }
    }

    /// 置顶/取消置顶播客（仅 .podcast 行有意义；后端 /podcasts/toggle_pin）。
    func togglePin(_ item: LibraryItem, currentTab: Int) {
        guard let client = apiClient else { return }
        Task {
            switch item.type {
            case .podcast:
                if let filename = item.filename { _ = await client.togglePodcastPin(filename: filename) }
            case .instant, .saved:
                if let md5 = item.md5 { _ = await client.toggleSavedPin(md5: md5) }
            default:
                return
            }
            await store?.refreshLibrary(tab: currentTab)  // M8:经 store 推送刷新
        }
    }

    /// ADR-003 F3: turn a 即时/稍后阅读 item into a background single-voice podcast
    /// (generate_single_podcast is pure TTS — no LLM key needed, so no gate).
    func generatePodcast(_ item: LibraryItem, force: Bool = false) async -> BackendAPIClient.PodcastGenerationOutcome {
        guard let client = apiClient, !item.fullText.isEmpty else { return .rejected(nil) }
        // #8 S1:按导入时的标记决定生成模式,不再弹窗问;F:exists 由视图层弹二选一
        return await client.generateSinglePodcast(
            text: item.fullText, source: item.source, voice: nil, title: item.title,
            mode: item.savedMode, force: force
        )
    }

    /// #8 F:「直接播」已有成品播客
    func playExistingPodcast(filename: String) {
        guard let client = apiClient else { return }
        Task { _ = await client.playPodcast(filename: filename) }
    }

    func clearCache() {
        guard let client = apiClient else { return }
        Task {
            _ = await client.clearCache()
            await store?.refreshLibrary(tab: 3)  // M8:经 store 推送刷新
        }
    }

    func rename(_ item: LibraryItem, newTitle: String, currentTab: Int) async {
        guard let client = apiClient else { return }
        let token = client.managementToken
        let success: Bool
        if item.type == .podcast, let fn = item.filename {
            success = await client.renamePodcast(filename: fn, newTitle: newTitle, token: token)
        } else {
            success = await client.renameSavedItem(md5: item.md5, index: item.savedIndex, newTitle: newTitle, token: token)
        }
        if success {
            await store?.refreshLibrary(tab: currentTab)  // M8:经 store 推送刷新
        }
    }
}

struct LibraryView: View {
    @ObservedObject var viewModel: LibraryViewModel

    @State private var selectedTab = 1   // 默认「稍后阅读」(即时阅读标签已移除)
    @State private var hoveredItem: String? = nil
    @State private var selectedItems: Set<String> = []
    @State private var searchText = ""
    @State private var showingClearCacheConfirm = false
    @State private var textPreviewItem: LibraryItem? = nil   // 双击查看文本
    
    @State private var sortOption: SortOption = .date
    @State private var showOnlyPlaying: Bool = false

    // #10 C7.2:10s pending 轮询搬进 AppStateStore.startListPolling(唯一拥有者);
    // 本视图只订阅(经 LibraryViewModel 的 store 监听),不再自带 Timer。

    enum SortOption {
        case date
        case name
    }

    var filteredItems: [LibraryItem] {
        var items = viewModel.items
        
        if !searchText.isEmpty {
            items = items.filter { $0.title.localizedCaseInsensitiveContains(searchText) }
        }
        
        if showOnlyPlaying {
            items = items.filter { $0.isPlaying }
        }
        
        items.sort { a, b in
            if a.isPinned != b.isPinned {
                return a.isPinned && !b.isPinned
            }
            if a.isPending != b.isPending {
                return a.isPending && !b.isPending
            }
            switch sortOption {
            case .date:
                return a.timestamp > b.timestamp
            case .name:
                return a.title.localizedCompare(b.title) == .orderedAscending
            }
        }
        
        return items
    }

    var body: some View {
        VStack(spacing: 0) {
            // Header: Tabs & Tools
            VStack(spacing: 12) {
                HStack {
                    HStack(spacing: 8) {
                        LibraryTabButton(title: "稍后朗读", isSelected: selectedTab == 1, activeColor: .blue) {
                            selectedTab = 1
                            selectedItems.removeAll()
                            Task { await viewModel.load(tab: 1) }
                        }
                        .help("Saved Text")
                        
                        LibraryTabButton(title: "我的播客", isSelected: selectedTab == 2, activeColor: .blue) {
                            selectedTab = 2
                            selectedItems.removeAll()
                            Task { await viewModel.load(tab: 2) }
                        }
                        .help("Podcasts")
                        
                        LibraryTabButton(title: "缓存", isSelected: selectedTab == 3, activeColor: .blue) {
                            selectedTab = 3
                            selectedItems.removeAll()
                            Task { await viewModel.load(tab: 3) }
                        }
                        .help("Temp Cache")
                    }
                    .frame(width: 320, alignment: .leading)
                    
                    Spacer()
                    
                    // Contextual Batch Action Bar or Search/Filter
                    if !selectedItems.isEmpty {
                        HStack(spacing: 12) {
                            Text("\(selectedItems.count) selected")
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(.secondary)
                            
                            Button(action: { selectedItems.removeAll() }) {
                                Text("Cancel")
                            }
                            .buttonStyle(.plain)
                            .foregroundColor(.blue)
                            
                            // 批量生成播客按钮 (仅在选中项包含可生成单人播客的条目时显示)
                            let eligibleToGenerate = filteredItems.filter { selectedItems.contains($0.id) && ($0.type == .instant || $0.type == .saved) && !$0.fullText.isEmpty }
                            if !eligibleToGenerate.isEmpty {
                                Button(action: {
                                    let itemsToProcess = eligibleToGenerate
                                    selectedItems.removeAll()
                                    Task {
                                        var successCount = 0
                                        var existingCount = 0
                                        for item in itemsToProcess {
                                            switch await viewModel.generatePodcast(item) {
                                            case .started: successCount += 1
                                            case .exists: existingCount += 1  // 已有成品,不重复烧
                                            case .rejected: break
                                            }
                                        }
                                        showToast(existingCount > 0
                                            ? "提交 \(successCount) 篇,另有 \(existingCount) 篇已有成品"
                                            : "成功提交 \(successCount) 篇播客任务")
                                    }
                                }) {
                                    Image(systemName: "mic.fill")
                                        .foregroundColor(Color(red: 0.66, green: 0.33, blue: 0.97))
                                }
                                .buttonStyle(.plain)
                                .help("批量生成播客")
                            }
                            
                            Button(action: {
                                let toDelete = filteredItems.filter { selectedItems.contains($0.id) }
                                for item in toDelete {
                                    viewModel.delete(item, currentTab: selectedTab)
                                }
                                selectedItems.removeAll()
                            }) {
                                Image(systemName: "trash")
                                    .foregroundColor(.red)
                            }
                            .buttonStyle(.plain)
                            .help("删除选中项")
                        }
                    } else {
                        HStack(spacing: 8) {
                            // Search Field
                            HStack {
                                Image(systemName: "magnifyingglass")
                                    .foregroundColor(.secondary)
                                TextField("Search...", text: $searchText)
                                    .textFieldStyle(.plain)
                            }
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(Color(NSColor.controlBackgroundColor))
                            .cornerRadius(6)
                            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color(NSColor.separatorColor), lineWidth: 1))
                            .frame(width: 160)
                            .help("搜索内容")
                            
                            // Filter/Sort
                            Menu {
                                Button(action: { sortOption = .date }) {
                                    HStack {
                                        Text("按日期排序")
                                        if sortOption == .date {
                                            Spacer()
                                            Image(systemName: "checkmark")
                                        }
                                    }
                                }
                                Button(action: { sortOption = .name }) {
                                    HStack {
                                        Text("按名称排序")
                                        if sortOption == .name {
                                            Spacer()
                                            Image(systemName: "checkmark")
                                        }
                                    }
                                }
                                Divider()
                                Button(action: { showOnlyPlaying.toggle() }) {
                                    HStack {
                                        Text("仅显示播放中")
                                        if showOnlyPlaying {
                                            Spacer()
                                            Image(systemName: "checkmark")
                                        }
                                    }
                                }
                            } label: {
                                Image(systemName: showOnlyPlaying ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
                                    .foregroundColor(showOnlyPlaying ? .accentColor : .primary)
                            }
                            .menuStyle(.borderlessButton)
                            .menuIndicator(.hidden)
                            .frame(width: 24)
                            .help("筛选 / 排序")
                        }
                    }
                }
                .padding(.horizontal, 24)
                .padding(.top, 16)
                
                // Cache Info Bar (Only visible in Cache tab)
                if selectedTab == 3 {
                    HStack {
                        Text("Storage Usage: \(viewModel.items.count) 项")
                            .font(.system(size: 12))
                            .foregroundColor(.secondary)
                        Text("•")
                            .foregroundColor(.secondary.opacity(0.5))
                        Text("\(filteredItems.count) Items")
                            .font(.system(size: 12))
                            .foregroundColor(.secondary)
                        
                        Spacer()
                        
                        Menu {
                            Button("Clear Selected", action: {
                                let toDelete = filteredItems.filter { selectedItems.contains($0.id) }
                                for item in toDelete {
                                    viewModel.delete(item, currentTab: selectedTab)
                                }
                                selectedItems.removeAll()
                            })
                                .disabled(selectedItems.isEmpty)
                            Divider()
                            Button("Clear All Cache", role: .destructive) {
                                showingClearCacheConfirm = true
                            }
                        } label: {
                            Text("Manage Cache...")
                                .font(.system(size: 12))
                        }
                        .menuStyle(.borderlessButton)
                        .confirmationDialog("Are you sure you want to clear all cache? This cannot be undone.", isPresented: $showingClearCacheConfirm) {
                            Button("Clear All", role: .destructive) {
                                viewModel.clearCache()
                            }
                            Button("Cancel", role: .cancel) {}
                        } message: {
                            Text("This will permanently delete all temporary audio files.")
                        }
                    }
                    .padding(.horizontal, 24)
                    .padding(.bottom, 8)
                } else {
                    Spacer().frame(height: 8)
                }
            }
            
            Divider()
            
            // Content Area
            if viewModel.isLoading {
                VStack {
                    Spacer()
                    ProgressView()
                        .scaleEffect(0.8)
                    Text("Loading items...")
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                        .padding(.top, 8)
                    Spacer()
                }
            } else if filteredItems.isEmpty {
                // Empty State
                VStack(spacing: 12) {
                    Spacer()
                    Image(systemName: "tray")
                        .font(.system(size: 48))
                        .foregroundColor(.secondary.opacity(0.5))
                    Text("No items found.")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(.secondary)
                    Spacer()
                }
            } else {
                // List
                ScrollView {
                    LazyVStack(spacing: 1) {
                        ForEach(filteredItems) { item in
                            LibraryRowView(
                                item: item,
                                isHovered: hoveredItem == item.id,
                                isSelected: selectedItems.contains(item.id),
                                onPlay: {
                                    viewModel.play(item)
                                    // 播客是现成 wav 直接播;只有文本朗读才需要"生成"
                                    showToast(item.type == .podcast ? "开始播放" : "音频生成中...")
                                },
                                onDelete: { viewModel.delete(item, currentTab: selectedTab) },
                                onPin: { viewModel.togglePin(item, currentTab: selectedTab) },
                                onGeneratePodcast: {
                                    Task {
                                        switch await viewModel.generatePodcast(item) {
                                        case .started:
                                            showToast("Podcast 开始生成")
                                        case .exists:
                                            // #8 F(用户拍板改轻量):不弹二选一对话框,
                                            // 胶囊提示即可;真要重做=删旧成品再生成(force 通道后端保留)
                                            showToast("已有播客,可直接播放")
                                        case .rejected(let message):
                                            showToast(message ?? "提交失败")
                                        }
                                    }
                                },
                                onRename: { newTitle in
                                    Task {
                                        await viewModel.rename(item, newTitle: newTitle, currentTab: selectedTab)
                                    }
                                },
                                onShowTranscript: {
                                    if !item.fullText.isEmpty {
                                        textPreviewItem = item
                                    } else if item.type == .podcast, let fn = item.filename {
                                        Task {
                                            let txt = await viewModel.fetchTranscript(filename: fn)
                                            var copy = item
                                            if let txt = txt, !txt.isEmpty {
                                                copy.fullText = txt
                                            } else {
                                                copy.fullText = "（该播客为旧版本生成，暂无关联文稿。音频已在后台为您播放。）"
                                                viewModel.play(item)
                                            }
                                            textPreviewItem = copy
                                        }
                                    } else {
                                        viewModel.play(item)
                                    }
                                }
                            )
                            .onHover { isHovered in
                                if isHovered {
                                    hoveredItem = item.id
                                } else if hoveredItem == item.id {
                                    hoveredItem = nil
                                }
                            }
                            .onTapGesture {
                                if selectedItems.contains(item.id) {
                                    selectedItems.remove(item.id)
                                } else {
                                    selectedItems.insert(item.id)
                                }
                            }
                        }
                    }
                    .padding(.vertical, 8)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.clear)
        .task {
            await viewModel.load(tab: selectedTab)
        }
        // 切 tab 立即重载——此前只有首次出现的 .task,切到"我的播客"看的是旧数据,
        // 生成完成后必须手动刷新(2026-07-01 用户两次反馈)。
        .onChange(of: selectedTab) { newTab in
            Task { await viewModel.load(tab: newTab) }
        }
        .sheet(item: $textPreviewItem) { item in
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(item.title).font(.headline).lineLimit(2)
                    Spacer()
                    Button("播放") { viewModel.play(item) }
                    Button("关闭") { textPreviewItem = nil }
                }
                Divider()
                ScrollView {
                    Text(item.fullText)
                        .font(.system(size: 13))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(20)
            .frame(width: 560, height: 460)
        }
    }

    private func showToast(_ msg: String) {
        // C8.2:胶囊唯一实现在 ToastPresenter(与 Console 共用)
        ToastPresenter.showInKeyWindow(msg)
    }
}

struct LibraryRowView: View {
    let item: LibraryItem
    let isHovered: Bool
    let isSelected: Bool
    var onPlay: () -> Void = {}
    var onDelete: () -> Void = {}
    var onPin: () -> Void = {}
    var onGeneratePodcast: () -> Void = {}
    var onRename: (String) -> Void = { _ in }
    var onShowTranscript: () -> Void = {}

    @State private var isEditing = false
    @State private var editingText = ""
    @FocusState private var isFocused: Bool

    private func commitRename() {
        guard isEditing else { return }
        isEditing = false
        isFocused = false
        let trimmed = editingText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty && trimmed != item.title {
            onRename(trimmed)
        }
    }

    private func tagColor(for tag: String) -> Color {
        switch tag {
        case "原文": return .gray
        case "翻译": return .blue
        case "双人总结": return .purple
        case "双人翻译": return .orange
        default: return .secondary
        }
    }

    var body: some View {
        HStack(spacing: 16) {
            // Selection / Status Icon
            Button(action: {
                if !item.isPending {
                    onShowTranscript()
                }
            }) {
                ZStack {
                    if item.isPending {
                        ProgressView()
                            .scaleEffect(0.6)
                            .frame(width: 32, height: 32)
                    } else if isSelected {
                        Circle()
                            .fill(Color.accentColor)
                            .frame(width: 24, height: 24)
                        Image(systemName: "checkmark")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(.white)
                    } else {
                        let baseColor: Color = {
                            switch item.type {
                            case .saved: return Color(red: 0.06, green: 0.73, blue: 0.51) // Green #10b981
                            case .podcast: return Color(red: 0.66, green: 0.33, blue: 0.97) // Purple #a855f7
                            default: return .gray
                            }
                        }()
                        let iconName = item.isPlaying ? "speaker.wave.2.fill" : (item.type == .podcast ? "waveform" : "doc.text.fill")
                        
                        Circle()
                            .fill(baseColor.opacity(0.12))
                            .frame(width: 32, height: 32)
                        
                        Image(systemName: iconName)
                            .foregroundColor(baseColor)
                    }
                }
            }
            .buttonStyle(.plain)
            .frame(width: 32)
            .disabled(item.isPending)
            .help(item.isPending ? "生成中..." : "查看文稿")
            
            // Text Content
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(item.modeTag)
                        .font(.system(size: 9, weight: .bold))
                        .padding(.horizontal, 4)
                        .padding(.vertical, 1)
                        .background(tagColor(for: item.modeTag).opacity(0.12))
                        .foregroundColor(tagColor(for: item.modeTag))
                        .cornerRadius(3)
                    
                    if isEditing {
                        TextField("", text: $editingText)
                            .textFieldStyle(.plain)
                            .font(.system(size: 14, weight: .medium))
                            .foregroundColor(.accentColor)
                            .focused($isFocused)
                            .onSubmit {
                                commitRename()
                            }
                            .onChange(of: isFocused) { _, newValue in
                                if !newValue {
                                    commitRename()
                                }
                            }
                            .onAppear {
                                isFocused = true
                            }
                    } else {
                        Text(item.title)
                            .font(.system(size: 14, weight: .medium))
                            .lineLimit(1)
                            .foregroundColor(isSelected ? .accentColor : .primary)
                            .contentShape(Rectangle())
                            .onTapGesture(count: 2) {
                                if item.type != .cache {
                                    editingText = item.title
                                    isEditing = true
                                }
                            }
                    }
                }
                
                HStack(spacing: 8) {
                    Text(item.source)
                        .font(.system(size: 11, weight: .semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.secondary.opacity(0.1))
                        .cornerRadius(4)
                        .foregroundColor(.secondary)
                    
                    Text("•")
                        .foregroundColor(.secondary.opacity(0.5))
                    
                    Text(item.status)
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                }
            }
            
            Spacer()
            
            // Trailing Actions / Time
            if isHovered && !isSelected && !item.isPending {
                HStack(spacing: 8) {
                    Button(action: { onPlay() }) {
                        Image(systemName: "play.fill")
                            .frame(width: 32, height: 32)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help("播放")

                    // 生成播客：仅即时/稍后阅读行——用该条 fullText 起一个后台单人
                    // 播客任务（generate_single_podcast，纯 TTS，不需 LLM key）。
                    if item.type == .instant || item.type == .saved {
                        Button(action: { onGeneratePodcast() }) {
                            Image(systemName: "mic.fill")
                                .frame(width: 32, height: 32)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .help("合成音频")
                    }

                    // 置顶：播客 + 即时/稍后阅读均可（缓存行不可）。
                    if item.type != .cache {
                        Button(action: { onPin() }) {
                            Image(systemName: item.isPinned ? "pin.fill" : "pin")
                                .frame(width: 32, height: 32)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .foregroundColor(item.isPinned ? .accentColor : .secondary)
                        .help(item.isPinned ? "取消置顶" : "置顶")
                    }

                    Button(action: { onDelete() }) {
                        Image(systemName: "trash")
                            .frame(width: 32, height: 32)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.red)
                    .help("删除")
                }
                .padding(.trailing, 8)
                .foregroundColor(.secondary)
            } else {
                Text(item.time)
                    .font(.system(size: 12))
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(isSelected ? Color.accentColor.opacity(0.1) : (isHovered ? Color.secondary.opacity(0.05) : Color.clear))
        .contentShape(Rectangle())
    }
}

// MARK: - Custom Segmented Tab Button
struct LibraryTabButton: View {
    let title: String
    let isSelected: Bool
    let activeColor: Color
    let action: () -> Void
    
    @State private var isHovered = false
    
    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 13, weight: .medium))
                .padding(.horizontal, 14)
                .padding(.vertical, 5)
                .background(
                    Capsule()
                        .fill(isSelected ? activeColor : (isHovered ? Color.secondary.opacity(0.12) : Color.clear))
                )
                .foregroundColor(isSelected ? .white : .primary)
        }
        .buttonStyle(.plain)
        .onHover { hovering in
            isHovered = hovering
        }
    }
}

class LibraryHostingController: NSHostingController<LibraryView> {
    weak var coordinator: ApplicationCoordinator?

    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
        let viewModel = LibraryViewModel(coordinator: coordinator)
        super.init(rootView: LibraryView(viewModel: viewModel))
    }
    
    @MainActor required dynamic init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}
