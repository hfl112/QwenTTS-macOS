import AppKit

@MainActor
class ApplicationCoordinator {
    let stateStore = AppStateStore()
    let processManager = BackendProcessManager()
    var statusItemController: StatusItemController?
    var mainWindowController: MainWindowController?

    var setupWizardController: SetupWizardWindowController?

    func start() {
        print("[Coordinator] ApplicationCoordinator start hook.")
        CrashReporter.shared.checkPreviousCrash()
        
        // 0. 注入集中状态源，作为快照唯一数据源
        processManager.stateStore = stateStore

        // 1. 初始化 StatusItem 并渲染
        statusItemController = StatusItemController(coordinator: self)
        statusItemController?.setup()

        // 2. 挂载播放状态轮询回调
        processManager.onPlaybackUpdate = { [weak self] title, progress, playing, paused in
            DispatchQueue.main.async {
                self?.stateStore.updatePlayback(title: title, progress: progress, playing: playing, paused: paused)

            }
        }

        // DEBUG: 若能自动探测到 dev 环境（conda/.venv 后端 + 模型路径），跳过模型向导。
        // 后端启动本身不应依赖向导：双击 App 后先把本地语音服务拉起来，向导只负责
        // 首次配置/试音/模型提示，否则浏览器扩展也无法自动发现正在运行的服务。
        let devEnvironmentReady: Bool
        #if DEBUG
        devEnvironmentReady = processManager.seedDevEnvironmentIfNeeded()
        #else
        devEnvironmentReady = false
        #endif

        startBackend()

        if devEnvironmentReady {
            print("[Coordinator] Dev environment detected — skipping setup wizard.")
            openMainWindow()
            return
        }

        // 判断是否需要启动向导
        let hasCompletedWizard = UserDefaults.standard.bool(forKey: "hasCompletedWizard")
        let modelStatus = ModelManager.shared.checkModelStatus(name: ModelManager.defaultModelName)
        var needsWizard = !hasCompletedWizard
        if case .missing = modelStatus { needsWizard = true }
        
        if needsWizard {
            setupWizardController = SetupWizardWindowController(
                onContinue: { [weak self] done in
                    guard let self else { done("内部错误"); return }
                    // 后端已在 App 启动时自动拉起；这里仅保底处理失败/已停止状态。
                    self.startBackend()
                    Task { @MainActor in
                        let err = await self.waitReadyAndTestRead()
                        done(err)   // nil = 真的出声；否则为可展示的失败原因
                    }
                },
                onComplete: { [weak self] in
                    UserDefaults.standard.set(true, forKey: "hasCompletedWizard")
                    self?.setupWizardController?.close()
                    self?.setupWizardController = nil
                    self?.openMainWindow()   // 后端已在 onContinue 中启动
                }
            )
            setupWizardController?.showWindow(nil)
            NSApp.activate(ignoringOtherApps: true)
        } else {
            startBackend()
        }
    }

    private func startBackend() {
        guard processManager.state == .stopped || processManager.state == .failed else {
            return
        }
        // 3. 拉起并监控 Python 后端
        processManager.startBackend { [weak self] state in
            DispatchQueue.main.async {
                self?.stateStore.updateBackendState(state)
                self?.statusItemController?.updateStatus(state: state)
                

            }
        }
    }

    /// Wizard 末页一键试音：等后端就绪（最多 timeout），再调用 /selftest/voice，
    /// 该接口会**阻塞到真的产生音频或捕获到推理错误**才返回。
    /// 返回 nil 表示真的出声（成功）；否则返回可直接展示给用户的失败原因。
    private func waitReadyAndTestRead(timeout: TimeInterval = 40) async -> String? {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if processManager.state == .ready { break }
            if processManager.state == .failed { return "后端启动失败，请查看诊断或重试。" }
            try? await Task.sleep(for: .milliseconds(400))
        }
        guard processManager.state == .ready else {
            return "后端未在 \(Int(timeout)) 秒内就绪。"
        }
        guard let client = processManager.apiClient else { return "无法连接后端。" }
        return await client.selfTestVoice()
    }

    func stop() {
        print("[Coordinator] ApplicationCoordinator stop hook.")
        processManager.stopBackend()
    }

    func readClipboard() {
        if let text = NSPasteboard.general.string(forType: .string) {
            print("[Coordinator] Reading clipboard text: \(text.prefix(20))...")
            processManager.readClipboard(text: text)
        } else {
            print("[Coordinator] Clipboard empty or contains non-text data.")
        }
    }

    func stopPlayback() {
        processManager.triggerAction("stop")
    }

    func pausePlayback() {
        processManager.triggerAction("pause")
    }

    func resumePlayback() {
        processManager.triggerAction("resume")
    }

    func nextPlayback() {
        processManager.triggerAction("next")
    }

    func prevPlayback() {
        processManager.triggerAction("prev")
    }
    
    func openMainWindow() {
        if mainWindowController == nil {
            mainWindowController = MainWindowController(coordinator: self)
        }
        NSApp.setActivationPolicy(.regular)
        mainWindowController?.window?.setContentSize(NSSize(width: 850, height: 550))
        mainWindowController?.window?.center()
        mainWindowController?.showWindow(nil)
        mainWindowController?.window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func openSettings() {
        openMainWindow()
        // 活跃路径是 MainSplitViewController（旧 MainTabViewController 已弃用）。
        mainWindowController?.selectTab(MainSplitViewController.settingsTabIndex)
    }

    func openDiagnostics() {
        print("[Coordinator] Action: Open Diagnostics.")
        if let window = NSApp.keyWindow ?? NSApp.mainWindow {
            DiagnosticsManager.shared.exportDiagnostics(window: window)
        } else {
            // Fallback if no window is open
            openMainWindow()
            if let window = mainWindowController?.window {
                DiagnosticsManager.shared.exportDiagnostics(window: window)
            }
        }
    }
}
