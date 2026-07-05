import Foundation

/// M7(计划 #13):崩溃重启策略的纯算术——指数退避、5 次上限、60s 稳定清零。
/// 此前混在 BackendProcessManager.handleProcessExit 的 Process 退出回调里,
/// 「连崩 5 次会怎样」只能真机试;抽出后表驱动可测(RestartPolicyTests)。
enum RestartPolicy {
    struct Decision: Equatable {
        enum Action: Equatable {
            case restart(afterSeconds: Double)
            case giveUp
        }
        let action: Action
        /// 本次决策后的新计数(调用方回存)
        let newRestartCount: Int
    }

    static let maxAttempts = 5
    /// 曾健康就绪超过此时长 → 视为一次正常运行,崩溃计数清零。
    /// 以「上次真正进入 ready 的时刻」衡量,而非上次重启尝试时刻。
    static let stableUptimeSeconds: TimeInterval = 60

    /// 异常退出(非用户主动 stop)时的决策。lastReadyAt=nil 表示从未就绪过。
    static func onAbnormalExit(
        lastReadyAt: Date?,
        restartCount: Int,
        now: Date
    ) -> Decision {
        var count = restartCount
        if let ready = lastReadyAt, now.timeIntervalSince(ready) > stableUptimeSeconds {
            count = 0
        }
        guard count < maxAttempts else {
            return Decision(action: .giveUp, newRestartCount: count)
        }
        count += 1
        // 2, 4, 8, 16, 32 秒
        return Decision(
            action: .restart(afterSeconds: pow(2.0, Double(count))),
            newRestartCount: count
        )
    }
}

/// M7:launcher seam——BackendProcessManager 依赖此协议而非具体类。
/// 生产 adapter = `BackendLauncher`(包 posix_spawn/Process/watchdog 管道);
/// 测试可注入 fake 驱动崩溃/退出路径而不 spawn 真进程。
protocol BackendLaunching: AnyObject {
    var managementToken: String { get }
    func launch(
        pythonPath: String,
        scriptPath: String,
        port: Int,
        onExit: @escaping () -> Void
    ) -> Bool
    func closeWatchdogPipe()
    func terminateProcessGroup()
}
// 具体类的 conformance 在 BackendLauncher.swift(本文件保持纯净,可进 logic-only 测试包)
