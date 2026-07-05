import XCTest

/// #10 C7:AppStateStore 服务器列表状态——唯一取数路 + 变更通知 + pending 判定。
@MainActor
final class AppStateStoreListTests: XCTestCase {

    private func makeStoreWithMockClient() -> (AppStateStore, BackendAPIClient) {
        let store = AppStateStore()
        let client = BackendAPIClient(port: 0)
        client.mock = MockBackend()
        store.listClient = client
        return (store, client)
    }

    func testRefreshLibraryPopulatesCollectionsAndNotifies() async {
        let (store, _) = makeStoreWithMockClient()
        var notified = 0
        store.addListListener { notified += 1 }

        await store.refreshLibrary(tab: 1)
        XCTAssertFalse(store.savedItems.isEmpty)
        await store.refreshLibrary(tab: 2)
        XCTAssertFalse(store.podcastFiles.isEmpty)
        await store.refreshLibrary(tab: 3)
        XCTAssertFalse(store.cacheItems.isEmpty)
        XCTAssertEqual(notified, 3)
    }

    func testRefreshJobsPopulatesBothJobLists() async {
        let (store, client) = makeStoreWithMockClient()
        // 触发 mock 的 job fixtures 激活
        _ = client.mock!.respond(method: "POST", path: "/read_url", body: ["url": "https://e.com"])
        await store.refreshJobs()
        XCTAssertFalse(store.podcastJobs.isEmpty)
        XCTAssertFalse(store.urlJobs.isEmpty)
    }

    func testHasPendingServerWorkFollowsActiveJobs() async {
        let (store, client) = makeStoreWithMockClient()
        XCTAssertFalse(store.hasPendingServerWork)
        // mock 默认 fixture 是 done 状态 → 仍不算 pending
        _ = client.mock!.respond(method: "POST", path: "/read_url", body: ["url": "https://e.com"])
        await store.refreshJobs()
        XCTAssertFalse(store.hasPendingServerWork)
        // 失败注入无 running 任务;直接验证判定逻辑对 saved 伪行的敏感性:
        // MockBackend saved fixture 无 is_pending → false 分支已覆盖
        XCTAssertFalse(store.savedItems.contains(where: { $0.is_pending == true }))
    }

    func testListListenerRemovalStopsCallbacks() async {
        let (store, _) = makeStoreWithMockClient()
        var notified = 0
        let token = store.addListListener { notified += 1 }
        await store.refreshLibrary(tab: 1)
        XCTAssertEqual(notified, 1)
        store.removeListListener(token)
        await store.refreshLibrary(tab: 1)
        XCTAssertEqual(notified, 1)
    }
}

// #12-①:快照信号纳入 pending 判定——停在内容中心也能感知别处提交的新任务。
extension AppStateStoreListTests {
    func testHasPendingServerWorkSeesSnapshotSignals() throws {
        let (store, _) = makeStoreWithMockClient()
        XCTAssertFalse(store.hasPendingServerWork)

        let snap = try JSONDecoder().decode(
            Snapshot.self, from: Data(#"{"active_podcast_jobs": 2}"#.utf8)
        )
        store.updateSnapshot(snap)
        XCTAssertTrue(store.hasPendingServerWork)

        let idle = try JSONDecoder().decode(
            Snapshot.self, from: Data(#"{"active_podcast_jobs": 0, "active_url_tasks": []}"#.utf8)
        )
        store.updateSnapshot(idle)
        XCTAssertFalse(store.hasPendingServerWork)

        let urlBusy = try JSONDecoder().decode(
            Snapshot.self, from: Data(#"{"active_url_tasks": ["https://e.com"]}"#.utf8)
        )
        store.updateSnapshot(urlBusy)
        XCTAssertTrue(store.hasPendingServerWork)
    }
}

extension AppStateStoreListTests {
    /// M8(计划 #13):失败监视的静默刷新——数据照更新,但不广播全局重建
    /// (根治「Console 监视 8 秒 → 内容中心 2Hz 全量重建」的隐性联动)。
    func testRefreshJobsQuietModeSkipsListenerNotify() async {
        let (store, client) = makeStoreWithMockClient()
        _ = client.mock!.respond(method: "POST", path: "/read_url", body: ["url": "https://e.com"])
        var notified = 0
        store.addListListener { notified += 1 }

        await store.refreshJobs(notify: false)
        XCTAssertFalse(store.podcastJobs.isEmpty)  // 集合照更新
        XCTAssertEqual(notified, 0)                // 不触发重建

        await store.refreshJobs()
        XCTAssertEqual(notified, 1)                // 默认路径仍广播
    }
}
