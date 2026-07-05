import XCTest

/// #10 C6.2:列表端点模型按 wire 真相解码——fixture 取自后端真实输出形状
/// (含 /saved_items 注入的无 md5 伪行、pending 播客占位行、旧缓存行 source=null)。
final class APIModelDecodingTests: XCTestCase {

    private func decode<T: Decodable>(_ json: String, as type: T.Type) throws -> T {
        try JSONDecoder().decode(T.self, from: Data(json.utf8))
    }

    func testSavedItemsDecodeIncludingPendingPseudoRow() throws {
        let json = """
        [
          {"timestamp": 1751600000.1, "text": "正文", "title": "标题", "source": "web",
           "voice": "Serena", "is_exported": false, "md5": "abc123", "mode": "podcast-discuss",
           "is_pinned": true},
          {"timestamp": 1751600001.0, "text": "https://example.com", "title": "⏳ 正在抓取网页正文...",
           "source": "web", "is_exported": false, "is_pending": true, "is_pinned": false}
        ]
        """
        let items = try decode(json, as: [SavedItem].self)
        XCTAssertEqual(items.count, 2)
        XCTAssertEqual(items[0].mode, "podcast-discuss")
        XCTAssertEqual(items[0].is_pinned, true)
        XCTAssertNil(items[1].md5)          // 伪行无 md5,不得解码失败
        XCTAssertEqual(items[1].is_pending, true)
    }

    func testPodcastFilesDecodeWavAndPendingRows() throws {
        let json = """
        [
          {"title": "我的标题", "filename": "pinned_podcast_单篇_web_我的标题_abcd1234_1.wav",
           "timestamp": 1751600000.0, "is_pending": false, "source": "web",
           "is_pinned": true, "size_mb": 12.5},
          {"title": "生成中 (正在生成中...)", "filename": ".pending_单篇_web_生成中_abcd1234",
           "timestamp": 1751600002.0, "is_pending": true, "source": "web", "is_pinned": false}
        ]
        """
        let files = try decode(json, as: [PodcastFile].self)
        // M4-②:size_mb 已裁(无前端读者);fixture 保留该 key 验证多余 key 不炸
        XCTAssertEqual(files[0].is_pinned, true)
        XCTAssertEqual(files[1].is_pending, true)
    }

    func testPodcastJobsDecodeWithAndWithoutProgress() throws {
        let json = """
        [
          {"job_id": "single_abc_12345678", "kind": "single", "md5": "abc", "title": "T",
           "source": "web", "status": "running", "created_at": 1.0, "updated_at": 2.0,
           "pid": 123, "output_path": null, "error": null, "mode": "original",
           "voice": "Serena", "content_key": "ck", "chunk_dir": "single_abc",
           "completed_chunks": 3, "total_chunks": 10, "progress_percent": 30},
          {"job_id": "old_style", "kind": "single", "md5": "d", "title": "旧",
           "source": "web", "status": "done", "created_at": 1.0, "updated_at": 2.0,
           "output_path": "/p/x.wav", "error": null}
        ]
        """
        let jobs = try decode(json, as: [PodcastJob].self)
        XCTAssertEqual(jobs[0].progress_percent, 30)
        XCTAssertNil(jobs[1].progress_percent)
        XCTAssertEqual(jobs[1].status, "done")
    }

    func testUrlJobsAndCacheItemsDecode() throws {
        let urlJson = """
        [{"job_id": "url_abc", "url": "https://e.com", "mode": "original", "action": "read",
          "has_html": false, "status": "failed", "stage": "failed", "created_at": 1.0,
          "updated_at": 2.0, "title": "", "source": "web", "text_chars": 0,
          "error": "boom", "from_cache": false}]
        """
        let jobs = try decode(urlJson, as: [UrlJob].self)
        XCTAssertEqual(jobs[0].error, "boom")

        let cacheJson = """
        [{"id": 1, "md5": "abc", "text": "缓存正文", "model": "Qwen3-TTS-0.6B-4bit",
          "voice": "Serena", "duration": 3.2, "created_at": 1751600000.0,
          "file_path": "/c/abc.npy", "source": null, "is_exported": true},
         {"id": 2, "md5": "def", "text": "旧行", "model": "m", "voice": "v",
          "duration": 1.0, "created_at": 1.0, "file_path": "/c/def.npy",
          "source": "clipboard", "is_exported": false}]
        """
        let items = try decode(cacheJson, as: [CacheItem].self)
        XCTAssertNil(items[0].source)       // 旧库迁移行 source=null 不得炸
        XCTAssertEqual(items[1].source, "clipboard")
        XCTAssertEqual(items[0].duration ?? 0, 3.2, accuracy: 0.001)
    }
}
