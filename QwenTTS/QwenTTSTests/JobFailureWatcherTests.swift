import XCTest

/// M6(计划 #13):失败监视时序器——原 ConsoleVC 内 16×500ms 循环的首批测试。
@MainActor
final class JobFailureWatcherTests: XCTestCase {

    private func makeStore(failure: MockBackend.Failure) -> AppStateStore {
        let store = AppStateStore()
        let client = BackendAPIClient(port: 0)
        let mock = MockBackend()
        mock.failure = failure
        client.mock = mock
        // job fixtures 需先有一次任务提交才激活(与 AppStateStoreListTests 同法)
        _ = mock.respond(method: "POST", path: "/read_url", body: ["url": "https://e.com"])
        store.listClient = client
        return store
    }

    func testFailedJobsUnifiedView() async {
        let watcher = JobFailureWatcher(store: makeStore(failure: .llmNoKey))
        let pod = await watcher.failedJobs(isPodcast: true)
        XCTAssertEqual(pod.count, 1)
        XCTAssertTrue(pod.first?.error.contains("LLM key") == true)
        let url = await watcher.failedJobs(isPodcast: false)
        XCTAssertEqual(url.map { $0.id }, ["urljob-mock"])
    }

    func testWatchFiresOnlyForNewFailuresBeyondBaseline() async {
        // 基线已含该失败 → 不告警(老失败不重复弹)
        let watcher = JobFailureWatcher(store: makeStore(failure: .llmNoKey))
        let baseline = await watcher.baseline(isPodcast: true)
        XCTAssertEqual(baseline, ["podjob-mock"])

        var fired: [String] = []
        let task = watcher.watch(
            isPodcast: true, baseline: baseline,
            attempts: 2, interval: .milliseconds(1)
        ) { fired.append($0) }
        await task.value
        XCTAssertTrue(fired.isEmpty)

        // 空基线 → 同一失败被视为「新产生」,回调一次即停
        var hit: [String] = []
        let task2 = watcher.watch(
            isPodcast: true, baseline: [],
            attempts: 5, interval: .milliseconds(1)
        ) { hit.append($0) }
        await task2.value
        XCTAssertEqual(hit.count, 1)
        XCTAssertTrue(hit.first?.contains("LLM key") == true)
    }

    func testWatchStopsQuietlyWhenNothingFails() async {
        let watcher = JobFailureWatcher(store: makeStore(failure: .none))
        var fired: [String] = []
        let task = watcher.watch(
            isPodcast: false, baseline: [],
            attempts: 2, interval: .milliseconds(1)
        ) { fired.append($0) }
        await task.value
        XCTAssertTrue(fired.isEmpty)
    }
}
