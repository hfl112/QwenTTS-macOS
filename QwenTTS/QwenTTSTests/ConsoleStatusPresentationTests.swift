import XCTest

/// M6(计划 #13):Console 状态徽章/菜单栏状态行纯映射——原 render() 状态机的首批测试。
final class ConsoleStatusPresentationTests: XCTestCase {

    private func make(
        _ backend: BackendState,
        snap: Bool = true,
        status: PlaybackStatus = .idle,
        podcasts: Int = 0
    ) -> ConsoleStatusPresentation {
        ConsoleStatusPresentation(
            backendState: backend, hasSnapshot: snap,
            playbackStatus: status, activePodcastProcesses: podcasts
        )
    }

    func testBackendNotReadyStates() {
        XCTAssertEqual(make(.stopped).statusText, "BACKEND OFFLINE")
        XCTAssertEqual(make(.launching).statusText, "BACKEND NOT READY")
        XCTAssertEqual(make(.launching).statusColor, .orange)
        XCTAssertEqual(make(.failed).statusText, "BACKEND ERROR")
        XCTAssertEqual(make(.failed).statusColor, .red)
        XCTAssertFalse(make(.failed).isPlaying)
    }

    func testReadyWithoutSnapshotIsConnecting() {
        let p = make(.ready, snap: false, status: .playing)
        XCTAssertEqual(p.statusText, "CONNECTING...")
        XCTAssertEqual(p.statusColor, .orange)
        XCTAssertFalse(p.isPlaying) // 无快照时不派生播放旗标
    }

    func testPlaybackStates() {
        let playing = make(.ready, status: .playing)
        XCTAssertEqual(playing.statusText, "PLAYING")
        XCTAssertEqual(playing.statusColor, .green)
        XCTAssertTrue(playing.isPlaying)

        let paused = make(.ready, status: .paused)
        XCTAssertEqual(paused.statusText, "PAUSED")
        XCTAssertEqual(paused.statusColor, .yellow)
        XCTAssertTrue(paused.isPlaying)   // 沿用口径:paused 也算 playing
        XCTAssertTrue(paused.isPaused)

        let generating = make(.ready, status: .generating)
        XCTAssertEqual(generating.statusText, "GENERATING")
        XCTAssertEqual(generating.statusColor, .blue)
        XCTAssertTrue(generating.showSpinner(chunksEmpty: true))
        XCTAssertFalse(generating.showSpinner(chunksEmpty: false))
    }

    func testIdleWithBackgroundPodcastShowsBadge() {
        let p = make(.ready, status: .idle, podcasts: 1)
        XCTAssertEqual(p.statusText, JobPresentation.backgroundGeneratingBadge)
        XCTAssertEqual(p.statusColor, .purple)
        let idle = make(.ready, status: .idle)
        XCTAssertEqual(idle.statusText, "IDLE")
        XCTAssertEqual(idle.statusColor, .gray)
    }

    func testTimeLabelBranches() {
        let playing = make(.ready, status: .playing)
        XCTAssertEqual(playing.timeLabel(mainProgress: "3/10"), "句段 3/10")
        XCTAssertEqual(playing.timeLabel(mainProgress: ""), "准备中...")
        XCTAssertEqual(make(.ready, status: .idle).timeLabel(mainProgress: ""), "未在播放")
    }

    func testMenuStatusTextTable() {
        XCTAssertEqual(ConsoleStatusPresentation.menuStatusText(.playing), "正在朗读")
        XCTAssertEqual(ConsoleStatusPresentation.menuStatusText(.generating), "生成中")
        XCTAssertEqual(ConsoleStatusPresentation.menuStatusText(.paused), "已暂停")
        XCTAssertEqual(ConsoleStatusPresentation.menuStatusText(.idle), "空闲")
        XCTAssertEqual(ConsoleStatusPresentation.menuStatusText(.unknown), "未知")
    }
}
