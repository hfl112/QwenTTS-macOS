import AppKit

/// AI 引擎 / 翻译配置页（精简版）：
/// 每个分组用「下拉选供应商 + 动态显示对应 key 框 + 检测连通性」三件套。
/// 通过后端 GET/PATCH /engines（带管理令牌）读写配置；POST /engines/check 做连通性检测。
/// order / tiers 字段本页不编辑，读出后原样缓存并在保存时回传，避免丢字段。
@MainActor
class EngineSettingsViewController: NSViewController {
    weak var coordinator: ApplicationCoordinator?

    // 读出的完整配置缓存（用于保存时原样回传不编辑的字段：order / tiers）
    private var loadedConfig: EngineConfig?

    // MARK: - 供应商标识（与后端契约一致）
    private let translateProviders = ["google", "microsoft", "deepl"]
    private let translateProviderTitles = ["Google 翻译（免费）", "微软翻译", "DeepL"]
    private let llmProviders = ["gemini", "claude", "openai", "deepseek", "local"]
    private let llmProviderTitles = ["Gemini", "Claude", "OpenAI", "DeepSeek", "本地 MLX"]

    // MARK: - 目标语言（翻译生成时应用）
    private let langCodes = ["zh", "en"]
    private let langTitles = ["简体中文", "English"]
    private let langPopup = NSPopUpButton()

    // MARK: - 翻译分组控件
    private let translatePopup = NSPopUpButton()
    private let googleHint = NSTextField(labelWithString: "免费，无需配置")
    private let microsoftKeyField = NSSecureTextField()
    private let microsoftRegionField = NSTextField()
    private let deeplKeyField = NSSecureTextField()
    private var translateProviderBoxes: [String: NSView] = [:]   // provider -> 对应 key 框容器
    private let translateCheckButton = NSButton()
    private let translateStatusLabel = NSTextField(labelWithString: "")

    // MARK: - LLM 分组控件
    private let llmPopup = NSPopUpButton()
    private let geminiKeyField = NSSecureTextField()
    private let claudeKeyField = NSSecureTextField()
    private let openaiKeyField = NSSecureTextField()
    private let deepseekKeyField = NSSecureTextField()
    private let localPathField = NSTextField()
    private var llmProviderBoxes: [String: NSView] = [:]         // provider -> 对应 key 框容器
    private let llmCheckButton = NSButton()
    private let llmStatusLabel = NSTextField(labelWithString: "")

    // MARK: - 底部
    private let saveButton = NSButton()
    private let saveStatusLabel = NSTextField(labelWithString: "")

    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func loadView() {
        self.view = NSView(frame: NSRect(x: 0, y: 0, width: 600, height: 600))
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
    }

    override func viewWillAppear() {
        super.viewWillAppear()
        loadEngines()
    }

    // MARK: - UI 构建

    private func setupUI() {
        // 纵向滚动容器
        let scrollView = NSScrollView()
        scrollView.hasVerticalScroller = true
        scrollView.hasHorizontalScroller = false
        scrollView.autohidesScrollers = true
        scrollView.drawsBackground = false
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(scrollView)

        let contentStack = NSStackView()
        contentStack.orientation = .vertical
        contentStack.alignment = .leading
        contentStack.spacing = 16
        contentStack.translatesAutoresizingMaskIntoConstraints = false
        contentStack.edgeInsets = NSEdgeInsets(top: 24, left: 30, bottom: 24, right: 30)

        // documentView 用一个 flipped 容器承载 stack，保证内容从顶部排布
        let documentView = FlippedView()
        documentView.translatesAutoresizingMaskIntoConstraints = false
        documentView.addSubview(contentStack)
        scrollView.documentView = documentView

        // 统一输入框样式
        for field in [microsoftKeyField, microsoftRegionField, deeplKeyField,
                      geminiKeyField, claudeKeyField, openaiKeyField,
                      deepseekKeyField, localPathField] {
            field.bezelStyle = .roundedBezel
            field.translatesAutoresizingMaskIntoConstraints = false
            field.widthAnchor.constraint(equalToConstant: 360).isActive = true
        }
        microsoftKeyField.placeholderString = "微软翻译 API Key"
        microsoftRegionField.placeholderString = "区域，如 eastasia"
        deeplKeyField.placeholderString = "DeepL API Key"
        geminiKeyField.placeholderString = "Gemini API Key"
        claudeKeyField.placeholderString = "Claude API Key"
        openaiKeyField.placeholderString = "OpenAI API Key"
        deepseekKeyField.placeholderString = "DeepSeek API Key"
        localPathField.placeholderString = "本地 MLX 模型目录路径"

        // ── 保存设置 ──
        saveButton.title = "保存设置"
        saveButton.bezelStyle = .rounded
        saveButton.target = self
        saveButton.action = #selector(saveClicked)
        configureStatusLabel(saveStatusLabel)
        let saveRow = NSStackView(views: [saveButton, saveStatusLabel])
        saveRow.orientation = .horizontal
        saveRow.alignment = .centerY
        saveRow.spacing = 15
        saveRow.translatesAutoresizingMaskIntoConstraints = false
        contentStack.addArrangedSubview(saveRow)

        // ── 顶部说明 ──
        let topHint = NSTextField(wrappingLabelWithString:
            "配置了 LLM 则翻译/总结优先用 LLM；只配翻译则仅用于翻译。")
        topHint.font = NSFont.systemFont(ofSize: 12)
        topHint.textColor = .secondaryLabelColor
        topHint.translatesAutoresizingMaskIntoConstraints = false
        topHint.widthAnchor.constraint(equalToConstant: 520).isActive = true
        contentStack.addArrangedSubview(topHint)

        // ── Section 1：翻译 ──
        let translateCardStack = NSStackView()
        translateCardStack.orientation = .vertical
        translateCardStack.alignment = .leading
        translateCardStack.spacing = 12
        translateCardStack.translatesAutoresizingMaskIntoConstraints = false

        translatePopup.translatesAutoresizingMaskIntoConstraints = false
        translatePopup.addItems(withTitles: translateProviderTitles)
        translatePopup.target = self
        translatePopup.action = #selector(translateProviderChanged)
        addRow("供应商:", translatePopup, to: translateCardStack)

        // 目标语言（始终显示，翻译生成时应用）
        langPopup.translatesAutoresizingMaskIntoConstraints = false
        langPopup.addItems(withTitles: langTitles)
        langPopup.target = self
        langPopup.action = #selector(langChanged)
        addRow("目标语言:", langPopup, to: translateCardStack)

        // 各供应商的 key 框容器（默认全部隐藏，按所选切换）
        googleHint.font = NSFont.systemFont(ofSize: 12)
        googleHint.textColor = .secondaryLabelColor
        translateProviderBoxes["google"] = makeBoxRow("说明:", [googleHint], to: translateCardStack)
        translateProviderBoxes["microsoft"] = makeBoxRows(
            [("API Key:", microsoftKeyField), ("Region:", microsoftRegionField)], to: translateCardStack)
        translateProviderBoxes["deepl"] = makeBoxRow("API Key:", [deeplKeyField], to: translateCardStack)

        translateCheckButton.title = "检测连通性"
        translateCheckButton.bezelStyle = .rounded
        translateCheckButton.target = self
        translateCheckButton.action = #selector(checkTranslateClicked)
        configureStatusLabel(translateStatusLabel)
        addCheckRow(translateCheckButton, translateStatusLabel, to: translateCardStack)

        let translateCard = createCardBox(stack: translateCardStack)
        let translateTitle = createSectionHeader("翻译")
        contentStack.addArrangedSubview(translateTitle)
        contentStack.addArrangedSubview(translateCard)

        // ── Section 2：LLM 总结及翻译 ──
        let llmCardStack = NSStackView()
        llmCardStack.orientation = .vertical
        llmCardStack.alignment = .leading
        llmCardStack.spacing = 12
        llmCardStack.translatesAutoresizingMaskIntoConstraints = false

        llmPopup.translatesAutoresizingMaskIntoConstraints = false
        llmPopup.addItems(withTitles: llmProviderTitles)
        llmPopup.target = self
        llmPopup.action = #selector(llmProviderChanged)
        addRow("供应商:", llmPopup, to: llmCardStack)

        llmProviderBoxes["gemini"] = makeBoxRow("API Key:", [geminiKeyField], to: llmCardStack)
        llmProviderBoxes["claude"] = makeBoxRow("API Key:", [claudeKeyField], to: llmCardStack)
        llmProviderBoxes["openai"] = makeBoxRow("API Key:", [openaiKeyField], to: llmCardStack)
        llmProviderBoxes["deepseek"] = makeBoxRow("API Key:", [deepseekKeyField], to: llmCardStack)
        llmProviderBoxes["local"] = makeBoxRow("模型路径:", [localPathField], to: llmCardStack)

        llmCheckButton.title = "检测连通性"
        llmCheckButton.bezelStyle = .rounded
        llmCheckButton.target = self
        llmCheckButton.action = #selector(checkLLMClicked)
        configureStatusLabel(llmStatusLabel)
        addCheckRow(llmCheckButton, llmStatusLabel, to: llmCardStack)

        let llmCard = createCardBox(stack: llmCardStack)
        let llmTitle = createSectionHeader("LLM 总结及翻译")
        contentStack.addArrangedSubview(llmTitle)
        contentStack.addArrangedSubview(llmCard)

        // 初始只显示默认所选供应商 of the boxes
        updateTranslateVisibility()
        updateLLMVisibility()

        // 约束
        NSLayoutConstraint.activate([
            scrollView.topAnchor.constraint(equalTo: view.topAnchor),
            scrollView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            scrollView.bottomAnchor.constraint(equalTo: view.bottomAnchor),

            documentView.topAnchor.constraint(equalTo: contentStack.topAnchor),
            documentView.leadingAnchor.constraint(equalTo: contentStack.leadingAnchor),
            documentView.trailingAnchor.constraint(equalTo: contentStack.trailingAnchor),
            documentView.bottomAnchor.constraint(equalTo: contentStack.bottomAnchor),
            documentView.widthAnchor.constraint(equalTo: scrollView.contentView.widthAnchor)
        ])
    }

    private func createSectionHeader(_ title: String) -> NSTextField {
        let label = NSTextField(labelWithString: title)
        label.font = NSFont.systemFont(ofSize: 14, weight: .bold)
        label.textColor = .secondaryLabelColor
        label.translatesAutoresizingMaskIntoConstraints = false
        return label
    }

    private func createCardBox(stack: NSStackView) -> NSBox {
        let cardBox = NSBox()
        cardBox.boxType = .custom
        cardBox.wantsLayer = true
        cardBox.borderWidth = 0
        cardBox.borderColor = .clear
        cardBox.cornerRadius = 12
        cardBox.fillColor = NSColor(name: nil) { appearance in
            if appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua {
                return NSColor(red: 0.16, green: 0.16, blue: 0.16, alpha: 1.0)
            } else {
                return NSColor(red: 0.94, green: 0.94, blue: 0.94, alpha: 1.0)
            }
        }
        cardBox.translatesAutoresizingMaskIntoConstraints = false
        cardBox.widthAnchor.constraint(equalToConstant: 520).isActive = true
        
        cardBox.addSubview(stack)
        
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: cardBox.topAnchor, constant: 16),
            stack.leadingAnchor.constraint(equalTo: cardBox.leadingAnchor, constant: 16),
            stack.trailingAnchor.constraint(equalTo: cardBox.trailingAnchor, constant: -16),
            stack.bottomAnchor.constraint(equalTo: cardBox.bottomAnchor, constant: -16)
        ])
        
        return cardBox
    }

    // MARK: - UI 帮助函数

    private func addSectionHeader(_ title: String, to stack: NSStackView) {
        let header = NSTextField(labelWithString: title)
        header.font = NSFont.boldSystemFont(ofSize: 15)
        header.translatesAutoresizingMaskIntoConstraints = false
        stack.addArrangedSubview(header)
    }

    private func addSeparator(to stack: NSStackView) {
        let sep = NSBox()
        sep.boxType = .separator
        sep.translatesAutoresizingMaskIntoConstraints = false
        sep.widthAnchor.constraint(equalToConstant: 520).isActive = true
        stack.addArrangedSubview(sep)
    }

    private func configureStatusLabel(_ label: NSTextField) {
        label.font = NSFont.systemFont(ofSize: 12)
        label.textColor = .secondaryLabelColor
    }

    /// 单行：右对齐标签 + 控件，直接加入 stack。
    private func addRow(_ title: String, _ control: NSView, to stack: NSStackView) {
        stack.addArrangedSubview(makeRow(title, control))
    }

    /// 检测按钮行：按钮 + 状态 label，加 100pt 的左对齐空白占位以对齐表单。
    private func addCheckRow(_ button: NSButton, _ label: NSTextField, to stack: NSStackView) {
        let spacer = NSView()
        spacer.translatesAutoresizingMaskIntoConstraints = false
        spacer.widthAnchor.constraint(equalToConstant: 100).isActive = true
        
        let row = NSStackView(views: [spacer, button, label])
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 12
        stack.addArrangedSubview(row)
    }

    private func makeRow(_ title: String, _ control: NSView) -> NSStackView {
        let label = NSTextField(labelWithString: title)
        label.translatesAutoresizingMaskIntoConstraints = false
        label.widthAnchor.constraint(equalToConstant: 100).isActive = true
        label.alignment = .right
        let row = NSStackView(views: [label, control])
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 12
        return row
    }

    /// 生成一个「单行」的 key 框容器（标签 + 控件），加入 stack 并返回容器以便后续 isHidden 切换。
    private func makeBoxRow(_ title: String, _ controls: [NSView], to stack: NSStackView) -> NSView {
        let container = NSStackView()
        container.orientation = .vertical
        container.alignment = .leading
        container.spacing = 8
        container.translatesAutoresizingMaskIntoConstraints = false
        for c in controls {
            container.addArrangedSubview(makeRow(title, c))
        }
        stack.addArrangedSubview(container)
        return container
    }

    /// 生成包含多行的 key 框容器（每行各自标签），用于微软的 Key + Region。
    private func makeBoxRows(_ rows: [(String, NSView)], to stack: NSStackView) -> NSView {
        let container = NSStackView()
        container.orientation = .vertical
        container.alignment = .leading
        container.spacing = 8
        container.translatesAutoresizingMaskIntoConstraints = false
        for (title, control) in rows {
            container.addArrangedSubview(makeRow(title, control))
        }
        stack.addArrangedSubview(container)
        return container
    }

    // MARK: - 下拉切换 -> 动态显示对应 key 框

    private var selectedTranslateProvider: String {
        let idx = translatePopup.indexOfSelectedItem
        guard idx >= 0 && idx < translateProviders.count else { return translateProviders[0] }
        return translateProviders[idx]
    }

    private var selectedLLMProvider: String {
        let idx = llmPopup.indexOfSelectedItem
        guard idx >= 0 && idx < llmProviders.count else { return llmProviders[0] }
        return llmProviders[idx]
    }

    @objc private func translateProviderChanged() { updateTranslateVisibility() }
    @objc private func llmProviderChanged() { updateLLMVisibility() }

    /// 只显示当前所选翻译供应商对应的 key 框，其余 isHidden。
    private func updateTranslateVisibility() {
        let sel = selectedTranslateProvider
        for (provider, box) in translateProviderBoxes {
            box.isHidden = (provider != sel)
        }
    }

    private func updateLLMVisibility() {
        let sel = selectedLLMProvider
        for (provider, box) in llmProviderBoxes {
            box.isHidden = (provider != sel)
        }
    }

    private func selectTranslateProvider(_ provider: String?) {
        let idx = translateProviders.firstIndex(of: provider ?? "google") ?? 0
        translatePopup.selectItem(at: idx)
        updateTranslateVisibility()
    }

    private func selectLLMProvider(_ provider: String?) {
        let idx = llmProviders.firstIndex(of: provider ?? "gemini") ?? 0
        llmPopup.selectItem(at: idx)
        updateLLMVisibility()
    }

    // MARK: - 读取后端配置

    private func loadEngines() {
        guard let client = coordinator?.processManager.apiClient else {
            saveStatusLabel.stringValue = "后端未就绪"
            return
        }
        Task {
            guard let config = await client.fetchEngines() else {
                saveStatusLabel.stringValue = "读取配置失败"
                return
            }
            self.loadedConfig = config

            if let tr = config.translate {
                selectTranslateProvider(tr.selected)
                if let li = langCodes.firstIndex(of: tr.target_lang ?? "zh") {
                    langPopup.selectItem(at: li)
                } else {
                    langPopup.selectItem(at: 0)
                }
                microsoftKeyField.stringValue = tr.microsoft_key ?? ""
                microsoftRegionField.stringValue = tr.microsoft_region ?? ""
                deeplKeyField.stringValue = tr.deepl_key ?? ""
            }
            if let llm = config.llm {
                selectLLMProvider(llm.selected)
                geminiKeyField.stringValue = llm.keys?["gemini"] ?? ""
                claudeKeyField.stringValue = llm.keys?["claude"] ?? ""
                openaiKeyField.stringValue = llm.keys?["openai"] ?? ""
                deepseekKeyField.stringValue = llm.keys?["deepseek"] ?? ""
                localPathField.stringValue = llm.local_model_path ?? ""
            }
            saveStatusLabel.stringValue = "已加载"
        }
    }

    // 目标语言：下拉改变即时保存，避免漏点「保存」导致设置不生效
    @objc private func langChanged() {
        guard let client = coordinator?.processManager.apiClient else { return }
        let li = langPopup.indexOfSelectedItem
        let code = (li >= 0 && li < langCodes.count) ? langCodes[li] : "zh"
        let token = client.managementToken
        translateStatusLabel.stringValue = "目标语言保存中…"
        Task {
            let ok = await client.updateEngines(["translate": ["target_lang": code]], token: token)
            translateStatusLabel.stringValue = ok ? "目标语言已设为 \(langTitles[max(0, li)])" : "保存失败"
        }
    }

    // MARK: - 保存

    @objc private func saveClicked() {
        guard let client = coordinator?.processManager.apiClient else {
            saveStatusLabel.stringValue = "后端未就绪"
            return
        }

        // M5:body 组装(含 order/tiers round-trip 保留规则)归 SettingsWire
        let li = langPopup.indexOfSelectedItem
        let targetLang = (li >= 0 && li < langCodes.count) ? langCodes[li] : "zh"
        let body = SettingsWire.enginesPatch(
            translateSelected: selectedTranslateProvider,
            targetLang: targetLang,
            microsoftKey: microsoftKeyField.stringValue,
            microsoftRegion: microsoftRegionField.stringValue,
            deeplKey: deeplKeyField.stringValue,
            llmSelected: selectedLLMProvider,
            llmKeys: [
                "gemini": geminiKeyField.stringValue,
                "claude": claudeKeyField.stringValue,
                "openai": openaiKeyField.stringValue,
                "deepseek": deepseekKeyField.stringValue
            ],
            localModelPath: localPathField.stringValue,
            preserving: loadedConfig
        )
        let token = coordinator?.processManager.apiClient?.managementToken ?? ""
        saveStatusLabel.stringValue = "保存中…"
        Task {
            let success = await client.updateEngines(body, token: token)
            saveStatusLabel.stringValue = success ? "保存成功" : "保存失败"
        }
    }

    // MARK: - 检测连通性

    /// 取当前翻译分组所选供应商对应 key 框的值。
    private func currentTranslateKeyAndRegion() -> (key: String?, region: String?) {
        switch selectedTranslateProvider {
        case "microsoft":
            return (microsoftKeyField.stringValue, microsoftRegionField.stringValue)
        case "deepl":
            return (deeplKeyField.stringValue, nil)
        default: // google
            return (nil, nil)
        }
    }

    /// 取当前 LLM 分组所选供应商对应 key 框的值（本地用模型路径占 key 位）。
    private func currentLLMKey() -> String? {
        switch selectedLLMProvider {
        case "gemini": return geminiKeyField.stringValue
        case "claude": return claudeKeyField.stringValue
        case "openai": return openaiKeyField.stringValue
        case "deepseek": return deepseekKeyField.stringValue
        case "local": return localPathField.stringValue
        default: return nil
        }
    }

    @objc private func checkTranslateClicked() {
        guard let client = coordinator?.processManager.apiClient else {
            translateStatusLabel.stringValue = "后端未就绪"
            return
        }
        let provider = selectedTranslateProvider
        let (key, region) = currentTranslateKeyAndRegion()
        let token = coordinator?.processManager.apiClient?.managementToken ?? ""
        runCheck(button: translateCheckButton, statusLabel: translateStatusLabel) {
            await client.checkEngine(family: "translate", provider: provider, key: key, region: region, token: token)
        }
    }

    @objc private func checkLLMClicked() {
        guard let client = coordinator?.processManager.apiClient else {
            llmStatusLabel.stringValue = "后端未就绪"
            return
        }
        let provider = selectedLLMProvider
        let key = currentLLMKey()
        let token = coordinator?.processManager.apiClient?.managementToken ?? ""
        runCheck(button: llmCheckButton, statusLabel: llmStatusLabel) {
            await client.checkEngine(family: "llm", provider: provider, key: key, region: nil, token: token)
        }
    }

    /// 通用检测流程：禁用按钮 + 显示「检测中…」，完成后还原并展示结果。
    private func runCheck(button: NSButton, statusLabel: NSTextField,
                          _ work: @escaping () async -> (ok: Bool, message: String)) {
        button.isEnabled = false
        statusLabel.stringValue = "检测中…"
        statusLabel.textColor = .secondaryLabelColor
        Task {
            let result = await work()
            button.isEnabled = true
            if result.ok {
                statusLabel.stringValue = "✅ 验证成功，可以使用相关功能了"
                statusLabel.textColor = .secondaryLabelColor
            } else {
                statusLabel.stringValue = "❌ \(result.message)"
                statusLabel.textColor = .systemRed
            }
        }
    }
}

/// 翻转坐标系容器，使 NSScrollView 的 documentView 内容从顶部开始排布。
private class FlippedView: NSView {
    override var isFlipped: Bool { true }
}
