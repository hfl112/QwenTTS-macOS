import SwiftUI
import AppKit

// A true macOS Settings-style row where label is on the left, right aligned.
struct SettingsRow<Content: View>: View {
    let title: String
    let content: Content
    
    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }
    
    var body: some View {
        HStack(alignment: .center, spacing: 16) {
            Text(title)
                .frame(width: 140, alignment: .trailing)
                .foregroundColor(.primary)
            
            content
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 4)
    }
}

struct SettingsCard<Content: View>: View {
    let title: String
    let content: Content
    
    init(title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.headline)
                .foregroundColor(.primary)
            
            VStack(spacing: 8) {
                content
            }
            .padding()
            .background(.regularMaterial)
            .cornerRadius(12)
            .shadow(color: Color.black.opacity(0.04), radius: 8, x: 0, y: 2)
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.white.opacity(0.12), lineWidth: 1)
            )
        }
    }
}

struct SettingsView: View {
    weak var coordinator: ApplicationCoordinator?

    @State private var showingRuntimeConfig = false
    @State private var showAdvanced = false

    // States
    @State private var defaultVoice = "Serena"
    @State private var performanceMode = "Balanced"
    // 播客生成档位(独立于朗读档位):播客是后台批量推理,默认最凉的 Quiet
    @State private var podcastPerformanceMode = "Quiet"
    @State private var batteryPolicy = true

    @State private var temperature = 0.2
    @State private var topP = 0.5
    @State private var repPenalty = 1.1
    @State private var seed = "42"
    @State private var extensionPairingToken = ""

    // 保存/加载状态
    @State private var isSaving = false
    @State private var saveStatus: String? = nil
    @State private var saveOK = true
    @State private var loadError: String? = nil

    // 本地模型管理(#12+ 用户反馈):只保留 0.6B-4bit(现役)与 1.7B-8bit;
    // 下载走真实 ModelManager(huggingface_hub),支持切换使用中的模型。
    private static let localModels: [(name: String, repoID: String, note: String)] = [
        (ModelManager.defaultModelName, ModelManager.defaultModelRepoID,
         "Recommended · fast & real-time"),
        ("Qwen3-TTS-1.7B-8bit", "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
         "Higher quality · slower"),
    ]
    @State private var activeModel = ""
    @State private var modelInstalled: [String: Bool] = [:]
    @State private var downloadingModel: String? = nil
    @State private var switchingModel = false
    @State private var modelActionError: String? = nil

    // performance_profile：后端用小写 fast/balanced/quiet，UI 用首字母大写。
    // M5:perf 映射表随写侧组装迁入 SettingsWire(首次获得测试),此处仅引用

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 32) {

                // 保存栏：把本页设置写回后端（PATCH /settings）。
                HStack(spacing: 12) {
                    Button(isSaving ? "保存中…" : "保存设置") {
                        Task { await saveSettings() }
                    }
                    .disabled(isSaving)
                    .keyboardShortcut("s", modifiers: .command)

                    if let saveStatus = saveStatus {
                        Text(saveStatus)
                            .font(.system(size: 12))
                            .foregroundColor(saveOK ? .secondary : .red)
                    }
                    Spacer()
                }

                // 加载失败提示（GET /settings 失败时不再静默）
                if let loadError = loadError {
                    HStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.orange)
                        Text(loadError).font(.system(size: 12)).foregroundColor(.secondary)
                        Button("重试") { Task { await loadSettings() } }.controlSize(.small)
                    }
                }

                // General Card
                SettingsCard(title: "General") {
                    SettingsRow("Default Voice:") {
                        Picker("", selection: $defaultVoice) {
                            Text("Serena（女声）").tag("Serena")
                            Text("Ryan（男声）").tag("Ryan")
                            Text("Vivian").tag("Vivian")
                        }
                        .pickerStyle(.menu)
                        .frame(width: 200)
                    }
                    
                    SettingsRow("Performance Mode:") {
                        Picker("", selection: $performanceMode) {
                            Text("Fast").tag("Fast")
                            Text("Balanced").tag("Balanced")
                            Text("Quiet").tag("Quiet")
                        }
                        .pickerStyle(.menu)
                        .frame(width: 160)
                    }

                    SettingsRow("Podcast Mode:") {
                        HStack(spacing: 8) {
                            Picker("", selection: $podcastPerformanceMode) {
                                Text("Fast").tag("Fast")
                                Text("Balanced").tag("Balanced")
                                Text("Quiet").tag("Quiet")
                            }
                            .pickerStyle(.menu)
                            .frame(width: 160)
                            Text("后台生成播客的档位，与上面的朗读档位互不影响")
                                .font(.system(size: 11))
                                .foregroundColor(.secondary)
                        }
                    }
                    .help("播客在后台连续推理,Quiet 档最凉(干一会歇一会);改档对排队中的任务生效")

                    SettingsRow("Battery Policy:") {
                        Toggle("Pause background generation on battery", isOn: $batteryPolicy)
                    }
                }
                
                // Local Model Card(#12+:两个模型,状态实测,真下载,可切换)
                SettingsCard(title: "Local Model") {
                    ForEach(Array(Self.localModels.enumerated()), id: \.element.name) { idx, spec in
                        if idx > 0 { Divider() }
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(spec.name).fontWeight(.medium)
                                    Text(spec.note)
                                        .font(.system(size: 11))
                                        .foregroundColor(.secondary)
                                }

                                if modelInstalled[spec.name] == true {
                                    Text("Installed")
                                        .font(.system(size: 11, weight: .semibold))
                                        .padding(.horizontal, 6).padding(.vertical, 2)
                                        .background(Color.green.opacity(0.1))
                                        .foregroundColor(.green).cornerRadius(4)
                                } else {
                                    Text("Not Installed")
                                        .font(.system(size: 11, weight: .semibold))
                                        .padding(.horizontal, 6).padding(.vertical, 2)
                                        .background(Color.orange.opacity(0.1))
                                        .foregroundColor(.orange).cornerRadius(4)
                                }

                                if activeModel == spec.name {
                                    Text("使用中")
                                        .font(.system(size: 11, weight: .semibold))
                                        .padding(.horizontal, 6).padding(.vertical, 2)
                                        .background(Color.blue.opacity(0.12))
                                        .foregroundColor(.blue).cornerRadius(4)
                                }

                                Spacer()

                                if downloadingModel == spec.name {
                                    ProgressView().controlSize(.small)
                                    Button("取消") { ModelManager.shared.pauseDownload(); downloadingModel = nil }
                                } else {
                                    if modelInstalled[spec.name] == true && activeModel != spec.name {
                                        Button(switchingModel ? "切换中…" : "使用") {
                                            Task { await switchModel(spec.name) }
                                        }
                                        .disabled(switchingModel || downloadingModel != nil)
                                        .help("切换为此模型(下一次朗读生效)")
                                    }
                                    Button(modelInstalled[spec.name] == true ? "重新下载" : "下载") {
                                        startModelDownload(spec)
                                    }
                                    .disabled(downloadingModel != nil)
                                    .help("从 Hugging Face(\(spec.repoID))下载到本地模型目录")
                                }
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    if let err = modelActionError {
                        Text(err).font(.system(size: 11)).foregroundColor(.red)
                    }
                }

                // Advanced Card
                SettingsCard(title: "Advanced Engine") {
                    DisclosureGroup("Advanced Parameters", isExpanded: $showAdvanced) {
                        VStack(spacing: 12) {
                            SettingsRow("Temperature:") {
                                HStack {
                                    Slider(value: $temperature, in: 0...1)
                                        .frame(width: 150)
                                    Text(String(format: "%.2f", temperature))
                                        .monospacedDigit()
                                        .frame(width: 40, alignment: .leading)
                                }
                            }
                            SettingsRow("Top P:") {
                                HStack {
                                    Slider(value: $topP, in: 0...1)
                                        .frame(width: 150)
                                    Text(String(format: "%.2f", topP))
                                        .monospacedDigit()
                                        .frame(width: 40, alignment: .leading)
                                }
                            }
                            SettingsRow("Rep. Penalty:") {
                                HStack {
                                    Slider(value: $repPenalty, in: 0...2)
                                        .frame(width: 150)
                                    Text(String(format: "%.2f", repPenalty))
                                        .monospacedDigit()
                                        .frame(width: 40, alignment: .leading)
                                }
                            }
                            SettingsRow("Seed:") {
                                TextField("", text: $seed)
                                    .textFieldStyle(.roundedBorder)
                                    .frame(width: 100)
                            }
                        }
                        .padding(.top, 12)
                    }
                    .help("高级参数")
                }

                SettingsCard(title: "Browser Extension") {
                    SettingsRow("Pairing Token:") {
                        HStack(spacing: 8) {
                            TextField("Paste or generate a token", text: $extensionPairingToken)
                                .textFieldStyle(.roundedBorder)
                                .frame(width: 170)  // 窄一点,给右侧 Generate/Copy 留足显示空间

                            Button("Generate") {
                                extensionPairingToken = Self.makePairingToken()
                            }

                            Button("Copy") {
                                copyPairingToken()
                            }
                            .disabled(extensionPairingToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        }
                    }

                    SettingsRow("") {
                        Text("Save settings after generating, then paste the same token into the extension popup.")
                            .font(.system(size: 12))
                            .foregroundColor(.secondary)
                    }
                }
                
                // Runtime Config
                SettingsCard(title: "Environment") {
                    SettingsRow("Runtime Paths:") {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Advanced configuration for Python, MLX, and system binaries.")
                                .foregroundColor(.secondary)
                                .font(.system(size: 12))
                            Button("Configure...") {
                                showingRuntimeConfig = true
                            }
                            .help("运行环境配置")
                            .padding(.top, 4)
                        }
                    }
                }
                
            }
            .padding(40)
            .padding(.bottom, 60) // Extra padding to allow smooth scrolling to the bottom
            .frame(maxWidth: 750, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.clear)
        .sheet(isPresented: $showingRuntimeConfig) {
            RuntimeConfigSheet()
        }
        .task {
            await loadSettings()
        }
    }

    /// 从后端拉取当前配置并填充到表单（GET /settings）。
    @MainActor
    private func loadSettings() async {
        guard let client = coordinator?.processManager.apiClient else {
            loadError = "无法加载设置：后端未就绪"; return
        }
        guard let s = await client.fetchSettings() else {
            loadError = "无法加载设置：请求失败（后端可能尚未就绪，可点重试）"; return
        }
        loadError = nil
        refreshModelStatuses()
        if let m = s.model { activeModel = m }
        if let v = s.voice { defaultVoice = v }
        if let p = s.performance_profile { performanceMode = SettingsWire.perfBackendToUI[p] ?? "Balanced" }
        // 缺键(老配置)保持默认 Quiet,与后端播客域的 quiet 兜底一致
        if let pp = s.podcast_performance_profile { podcastPerformanceMode = SettingsWire.perfBackendToUI[pp] ?? "Quiet" }
        if let t = s.temperature { temperature = t }
        if let tp = s.top_p { topP = tp }
        if let rp = s.repetition_penalty { repPenalty = rp }
        if let sd = s.seed { seed = String(sd) }
        if let bp = s.battery_podcast_policy { batteryPolicy = (bp == "pause") }
        if let ext = s.extension_pairing_token { extensionPairingToken = ext }
    }

    /// 把表单写回后端（PATCH /settings，需管理令牌）。
    @MainActor
    private func saveSettings() async {
        guard let client = coordinator?.processManager.apiClient else {
            saveOK = false; saveStatus = "后端未就绪"; return
        }
        isSaving = true
        saveStatus = nil
        // M5:body 组装归 SettingsWire(纯映射,SettingsWireTests 钉住)
        let body = SettingsWire.FormPatch(
            voice: defaultVoice,
            performanceModeUI: performanceMode,
            podcastPerformanceModeUI: podcastPerformanceMode,
            temperature: temperature,
            topP: topP,
            repPenalty: repPenalty,
            extensionPairingToken: extensionPairingToken,
            batteryPause: batteryPolicy,
            seedText: seed
        ).body()

        let token = client.managementToken
        let ok = await client.updateSettings(settings: body, token: token)
        isSaving = false
        saveOK = ok
        saveStatus = ok ? "已保存（性能/模型相关改动可能需重启后端生效）" : "保存失败，请确认后端已就绪"
    }

    private func refreshModelStatuses() {
        for spec in Self.localModels {
            if case .installed = ModelManager.shared.checkModelStatus(name: spec.name) {
                modelInstalled[spec.name] = true
            } else {
                modelInstalled[spec.name] = false
            }
        }
    }

    private func startModelDownload(_ spec: (name: String, repoID: String, note: String)) {
        modelActionError = nil
        downloadingModel = spec.name
        ModelManager.shared.startDownload(
            name: spec.name,
            repoID: spec.repoID,
            progress: { _ in }  // huggingface_hub 自管断点续传,进度用不确定态转圈
        ) { ok in
            downloadingModel = nil
            refreshModelStatuses()
            if !ok {
                modelActionError = "\(spec.name) 下载未完成(网络/磁盘?)——详见 Logs/model-download.log,可重试续传"
            }
        }
    }

    @MainActor
    private func switchModel(_ name: String) async {
        guard let client = coordinator?.processManager.apiClient else {
            modelActionError = "后端未就绪,无法切换"; return
        }
        modelActionError = nil
        switchingModel = true
        let ok = await client.updateSettings(settings: SettingsWire.modelPatch(name), token: client.managementToken)
        switchingModel = false
        if ok {
            activeModel = name
            saveOK = true
            saveStatus = "已切换模型为 \(name)(下一次朗读/生成生效)"
        } else {
            modelActionError = "切换失败,请确认后端已就绪"
        }
    }

    private static func makePairingToken() -> String {
        let alphabet = Array("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
        return String((0..<8).compactMap { _ in alphabet.randomElement() })
    }

    private func copyPairingToken() {
        let token = extensionPairingToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(token, forType: .string)
        saveOK = true
        saveStatus = "配对码已复制，保存设置后粘贴到扩展"
    }
}

class SettingsHostingController: NSHostingController<SettingsView> {
    weak var coordinator: ApplicationCoordinator?
    
    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
        super.init(rootView: SettingsView(coordinator: coordinator))
    }
    
    @MainActor required dynamic init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}
