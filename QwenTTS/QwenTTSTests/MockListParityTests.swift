import XCTest

/// #10 C6.3:MockBackend 的列表载荷必须能过类型化模型解码——否则
/// `--mock-backend` 跑起来列表会静默变空(request<T> 解码失败返回 nil)。
@MainActor
final class MockListParityTests: XCTestCase {

    private func decodeList<T: Decodable>(_ mock: MockBackend, path: String, as type: T.Type) throws -> [T] {
        let (status, data) = mock.respond(method: "GET", path: path, body: nil)
        XCTAssertEqual(status, 200, path)
        let payload = try XCTUnwrap(data, path)
        return try JSONDecoder().decode([T].self, from: payload)
    }

    func testAllMockListPayloadsDecodeThroughTypedModels() throws {
        let mock = MockBackend()
        // 触发 job fixtures 进入 active 状态
        _ = mock.respond(method: "POST", path: "/read_url", body: ["url": "https://e.com"])

        XCTAssertFalse(try decodeList(mock, path: "/saved_items", as: SavedItem.self).isEmpty)
        XCTAssertFalse(try decodeList(mock, path: "/podcasts/list", as: PodcastFile.self).isEmpty)
        XCTAssertFalse(try decodeList(mock, path: "/podcasts/jobs", as: PodcastJob.self).isEmpty)
        XCTAssertFalse(try decodeList(mock, path: "/url_jobs", as: UrlJob.self).isEmpty)
        XCTAssertFalse(try decodeList(mock, path: "/cache/items", as: CacheItem.self).isEmpty)
    }

    func testMockSavedItemCarriesLibraryEssentialFields() throws {
        let mock = MockBackend()
        let items = try decodeList(mock, path: "/saved_items", as: SavedItem.self)
        XCTAssertEqual(items.first?.md5, "mockmd5")
        XCTAssertNotNil(items.first?.text)
        XCTAssertNotNil(items.first?.timestamp)
    }
}
