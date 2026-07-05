import Foundation

/// M6(计划 #13):Console 状态徽章 + 菜单栏状态行的纯映射(照 PlaybackPresentation
/// 样板)。此前这套 (backendState, snapshot, playbackStatus) → 文案/颜色/旗标 状态机
/// 困在 ConsoleViewController.render() 里零测试,且 StatusBar 用另一张中文表独立
/// 派生状态行——正是 ADR-003 决定 #7 消灭过的「三份词表各自分歧」在状态行上复发。
struct ConsoleStatusPresentation: Equatable {
    /// 颜色语义 token(VC 负责映射到 NSColor;纯结构不 import AppKit)
    enum ColorToken: Equatable { case gray, green, yellow, blue, purple, orange, red }

    let statusText: String
    let statusColor: ColorToken
    /// paused 也算 playing(徽章/时间标签的沿用口径,ADR-003 render 注释)
    let isPlaying: Bool
    let isPaused: Bool
    let isGenerating: Bool

    init(
        backendState: BackendState,
        hasSnapshot: Bool,
        playbackStatus: PlaybackStatus,
        activePodcastProcesses: Int
    ) {
        guard backendState == .ready else {
            switch backendState {
            case .stopped, .stopping:
                (statusText, statusColor) = ("BACKEND OFFLINE", .gray)
            case .launching, .waitingForHealth:
                (statusText, statusColor) = ("BACKEND NOT READY", .orange)
            case .failed:
                (statusText, statusColor) = ("BACKEND ERROR", .red)
            case .ready:
                (statusText, statusColor) = ("IDLE", .gray)
            }
            (isPlaying, isPaused, isGenerating) = (false, false, false)
            return
        }
        guard hasSnapshot else {
            (statusText, statusColor) = ("CONNECTING...", .orange)
            (isPlaying, isPaused, isGenerating) = (false, false, false)
            return
        }
        isPlaying = (playbackStatus == .playing || playbackStatus == .paused)
        isPaused = (playbackStatus == .paused)
        isGenerating = (playbackStatus == .generating)
        if isPlaying {
            (statusText, statusColor) = isPaused ? ("PAUSED", .yellow) : ("PLAYING", .green)
        } else if isGenerating {
            (statusText, statusColor) = ("GENERATING", .blue)
        } else if activePodcastProcesses > 0 {
            // 朗读空闲,但后台正在生成播客——让用户感知到播客在跑
            (statusText, statusColor) = (JobPresentation.backgroundGeneratingBadge, .purple)
        } else {
            (statusText, statusColor) = ("IDLE", .gray)
        }
    }

    /// 进度时间标签(原 render 三分支)。
    func timeLabel(mainProgress: String) -> String {
        if !mainProgress.isEmpty { return "句段 \(mainProgress)" }
        return isPlaying ? "准备中..." : "未在播放"
    }

    /// 加载转圈:生成中且还没有句子。
    func showSpinner(chunksEmpty: Bool) -> Bool { isGenerating && chunksEmpty }

    /// 菜单栏状态行的中文词表(M6:StatusBar 改读这张表,不再独立派生)。
    static func menuStatusText(_ status: PlaybackStatus) -> String {
        switch status {
        case .playing: return "正在朗读"
        case .generating: return "生成中"
        case .paused: return "已暂停"
        case .idle: return "空闲"
        default: return "未知"
        }
    }
}
