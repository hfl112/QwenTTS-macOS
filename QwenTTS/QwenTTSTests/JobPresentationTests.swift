import XCTest

/// #10 C8.1:任务状态 → 统一文案的纯映射(接口即测试面,照 PlaybackPresentationTests)。
final class JobPresentationTests: XCTestCase {

    func testStatusVocabulary() {
        XCTAssertEqual(JobPresentation.statusText(status: "queued"), "排队中...")
        XCTAssertEqual(JobPresentation.statusText(status: "paused"), "已暂停")
        XCTAssertEqual(JobPresentation.statusText(status: "failed"), "失败")
        XCTAssertEqual(JobPresentation.statusText(status: "done"), "完成")
        // 未知状态原样透传、nil 不炸
        XCTAssertEqual(JobPresentation.statusText(status: "canceled"), "canceled")
        XCTAssertEqual(JobPresentation.statusText(status: nil), "")
    }

    func testRunningPrefersPercentThenChunksThenPlain() {
        XCTAssertEqual(
            JobPresentation.statusText(status: "running", progressPercent: 30, completedChunks: 3, totalChunks: 10),
            "生成中 (30%)"
        )
        XCTAssertEqual(
            JobPresentation.statusText(status: "running", completedChunks: 3, totalChunks: 10),
            "生成中 (3/10)"
        )
        XCTAssertEqual(JobPresentation.statusText(status: "running"), "生成中...")
    }

    func testPodcastJobConvenienceUsesWireFields() throws {
        let json = """
        {"job_id": "j", "status": "running", "progress_percent": 42,
         "completed_chunks": 4, "total_chunks": 10}
        """
        let job = try JSONDecoder().decode(PodcastJob.self, from: Data(json.utf8))
        XCTAssertEqual(JobPresentation.statusText(for: job), "生成中 (42%)")
    }
}
