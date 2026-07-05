import XCTest

/// M3(计划 #13):ReadMode / JobStatus 词表——wire 串、LLM gate、活跃集合的唯一口径。
final class WireVocabularyTests: XCTestCase {
    func testReadModeWireStrings() {
        XCTAssertEqual(ReadMode.original.rawValue, "original")
        XCTAssertEqual(ReadMode.translate.rawValue, "translate")
        XCTAssertEqual(ReadMode.dualSummary.rawValue, "dual-summary")
        XCTAssertEqual(ReadMode.dualTrans.rawValue, "dual-trans")
    }

    func testRequiresLLMOnlyForDualModes() {
        XCTAssertFalse(ReadMode.original.requiresLLM)
        XCTAssertFalse(ReadMode.translate.requiresLLM) // 机翻不需要 LLM key
        XCTAssertTrue(ReadMode.dualSummary.requiresLLM)
        XCTAssertTrue(ReadMode.dualTrans.requiresLLM)
    }

    func testJobStatusActiveMembership() {
        // 唯一活跃集合口径:running/queued/paused
        XCTAssertTrue(JobStatus(wire: "running").isActive)
        XCTAssertTrue(JobStatus(wire: "queued").isActive)
        XCTAssertTrue(JobStatus(wire: "paused").isActive)
        XCTAssertFalse(JobStatus(wire: "failed").isActive)
        XCTAssertFalse(JobStatus(wire: "done").isActive)
        XCTAssertFalse(JobStatus(wire: "canceled").isActive)
        XCTAssertFalse(JobStatus(wire: nil).isActive)
    }

    func testJobStatusUnknownFallbackAndCaseInsensitive() {
        XCTAssertEqual(JobStatus(wire: "枯萎"), .unknown)
        XCTAssertEqual(JobStatus(wire: "FAILED"), .failed) // 旧代码 lowercased() 口径保留
        XCTAssertFalse(JobStatus(wire: "枯萎").isActive)
    }
}
