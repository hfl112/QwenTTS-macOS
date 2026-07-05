import XCTest

/// M7(计划 #13):崩溃重启策略表驱动测试——退避序列、5 次上限、60s 稳定清零。
/// 此前这套算术只能靠真机连杀后端验证。
final class RestartPolicyTests: XCTestCase {
    private let t0 = Date(timeIntervalSince1970: 1_751_600_000)

    func testBackoffSequenceDoubles() {
        // 从未就绪(lastReadyAt=nil),连崩 1→5 次:2,4,8,16,32 秒
        var count = 0
        var delays: [Double] = []
        for _ in 0..<5 {
            let d = RestartPolicy.onAbnormalExit(lastReadyAt: nil, restartCount: count, now: t0)
            guard case .restart(let sec) = d.action else { return XCTFail("应重试") }
            delays.append(sec)
            count = d.newRestartCount
        }
        XCTAssertEqual(delays, [2, 4, 8, 16, 32])
        XCTAssertEqual(count, 5)
    }

    func testGivesUpAfterFiveAttempts() {
        let d = RestartPolicy.onAbnormalExit(lastReadyAt: nil, restartCount: 5, now: t0)
        XCTAssertEqual(d.action, .giveUp)
        XCTAssertEqual(d.newRestartCount, 5)
    }

    func testStableUptimeResetsCounter() {
        // 曾就绪 61s 前 → 计数清零,重新从 2s 退避开始
        let ready = t0.addingTimeInterval(-61)
        let d = RestartPolicy.onAbnormalExit(lastReadyAt: ready, restartCount: 4, now: t0)
        XCTAssertEqual(d.action, .restart(afterSeconds: 2))
        XCTAssertEqual(d.newRestartCount, 1)
        // 稳定运行也能把「已放弃」救回来(计数 5 清零后重试)
        let d2 = RestartPolicy.onAbnormalExit(lastReadyAt: ready, restartCount: 5, now: t0)
        XCTAssertEqual(d2.action, .restart(afterSeconds: 2))
    }

    func testShortUptimeDoesNotReset() {
        // 就绪仅 59s → 不算稳定,计数继续累加
        let ready = t0.addingTimeInterval(-59)
        let d = RestartPolicy.onAbnormalExit(lastReadyAt: ready, restartCount: 2, now: t0)
        XCTAssertEqual(d.action, .restart(afterSeconds: 8))
        XCTAssertEqual(d.newRestartCount, 3)
        let capped = RestartPolicy.onAbnormalExit(lastReadyAt: ready, restartCount: 5, now: t0)
        XCTAssertEqual(capped.action, .giveUp)
    }
}
