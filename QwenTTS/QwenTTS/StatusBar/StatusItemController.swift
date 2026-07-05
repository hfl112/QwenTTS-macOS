import AppKit

@MainActor
class StatusItemController: NSObject {
    weak var coordinator: ApplicationCoordinator?
    var statusItem: NSStatusItem?
    var menu: NSMenu?
    
    private var statusMenuItem: NSMenuItem?
    private var snapshotListenerToken: Int?

    init(coordinator: ApplicationCoordinator) {
        self.coordinator = coordinator
    }

    func setup() {
        // 创建状态栏 Item，分配动态宽度
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        guard let button = statusItem?.button else { return }

        // 菜单栏用扩展同款绿色图标(Resources/StatusBarIcon.png,拷自
        // qwen-tts-extension/public/icon);状态文字挪到 tooltip 和菜单首行。
        if let icon = NSImage(named: "StatusBarIcon") {
            icon.size = NSSize(width: 18, height: 18)
            icon.isTemplate = false  // 保留原色(绿色),不做单色模板
            button.image = icon
            button.imageScaling = .scaleProportionallyDown
        } else {
            button.title = "QwenTTS"  // 图标缺失时回退旧文字
        }
        
        setupMenu()
        statusItem?.menu = menu
        
        // 订阅 AppStateStore 状态更新，同步菜单 UI
        snapshotListenerToken = coordinator?.stateStore.addSnapshotListener { [weak self] _ in
            self?.updateMenuForPlayback()
        }
    }
    
    deinit {
        if let token = snapshotListenerToken {
            let store = coordinator?.stateStore
            Task { @MainActor in
                store?.removeSnapshotListener(token)
            }
        }
    }

    private func setupMenu() {
        let newMenu = NSMenu()
        
        // 状态展示栏（置灰）
        let statusItem = NSMenuItem(title: "QwenTTS (当前状态: 准备中)", action: nil, keyEquivalent: "")
        statusItem.isEnabled = false
        newMenu.addItem(statusItem)
        self.statusMenuItem = statusItem
        
        newMenu.addItem(NSMenuItem.separator())
        
        // 核心播放控制
        newMenu.addItem(NSMenuItem(title: "朗读剪贴板", action: #selector(readClipboard), keyEquivalent: "C")) // Shift+Cmd+C
        newMenu.addItem(NSMenuItem(title: "播放", action: #selector(playOrPause), keyEquivalent: ""))
        newMenu.addItem(NSMenuItem(title: "停止", action: #selector(stopPlayback), keyEquivalent: ""))
        
        newMenu.addItem(NSMenuItem.separator())
        
        // 应用控制
        newMenu.addItem(NSMenuItem(title: "打开主面板", action: #selector(openMainWindow), keyEquivalent: "m"))
        newMenu.addItem(NSMenuItem(title: "设置...", action: #selector(openSettings), keyEquivalent: ","))
        newMenu.addItem(NSMenuItem(title: "退出", action: #selector(quitApp), keyEquivalent: "q"))
        
        for item in newMenu.items {
            if item.action != nil {
                item.target = self
            }
        }
        self.menu = newMenu
    }

    func updateStatus(state: BackendState) {
        // 图标化后状态文字进 tooltip(悬停可见);仅在图标缺失的回退态才写标题
        if statusItem?.button?.image == nil {
            statusItem?.button?.title = "QwenTTS (\(state.rawValue))"
        }
        statusItem?.button?.toolTip = "QwenTTS (\(state.rawValue))"
        if state != .ready {
            statusMenuItem?.title = "QwenTTS (当前状态: 后端未就绪)"
        } else {
            updateMenuForPlayback()
        }
    }
    
    private func updateMenuForPlayback() {
        guard let stateStore = coordinator?.stateStore else { return }
        
        // 1. 更新第一行状态文本(M6:词表归 ConsoleStatusPresentation,不再独立派生)
        let stateStr = ConsoleStatusPresentation.menuStatusText(stateStore.playbackStatus)
        statusMenuItem?.title = "QwenTTS (当前状态: \(stateStr))"
        
        // 2. 动态更新“播放 / 暂停”项 of menu
        let presentation = PlaybackPresentation(stateStore.playbackStatus)
        if let playPauseItem = menu?.items.first(where: { $0.action == #selector(playOrPause(_:)) }) {
            playPauseItem.title = presentation.buttonLabel
        }
    }

    @objc func readClipboard(_ sender: Any?) {
        coordinator?.readClipboard()
    }
    
    @objc func playOrPause(_ sender: Any?) {
        guard let stateStore = coordinator?.stateStore else { return }
        switch PlaybackPresentation(stateStore.playbackStatus).action {
        case .pause: coordinator?.pausePlayback()
        case .resume: coordinator?.resumePlayback()
        case .read: coordinator?.readClipboard()
        }
    }
    
    @objc func stopPlayback(_ sender: Any?) {
        coordinator?.stopPlayback()
    }
    
    @objc func openMainWindow(_ sender: Any?) {
        coordinator?.openMainWindow()
    }
    
    @objc func openSettings(_ sender: Any?) {
        coordinator?.openSettings()
    }
    
    @objc func quitApp(_ sender: Any?) {
        NSApp.terminate(nil)
    }
}
