import XCTest

/// M5(计划 #13):设置写侧映射的首批测试——perf/battery 表、seed 解析、
/// 语速菜单解析、engines round-trip 保留,此前全部零覆盖。
final class SettingsWireTests: XCTestCase {

    func testPerfMappingRoundTrip() {
        for (ui, backend) in SettingsWire.perfUIToBackend {
            XCTAssertEqual(SettingsWire.perfBackendToUI[backend], ui)
        }
        XCTAssertEqual(SettingsWire.perfUIToBackend["Quiet"], "quiet")
    }

    func testFormPatchBody() {
        var patch = SettingsWire.FormPatch(
            voice: "Serena", performanceModeUI: "Quiet",
            podcastPerformanceModeUI: "Balanced", temperature: 0.2,
            topP: 0.5, repPenalty: 1.1, extensionPairingToken: " tok ",
            batteryPause: true, seedText: " 42 "
        )
        var body = patch.body()
        XCTAssertEqual(body["performance_profile"] as? String, "quiet")
        XCTAssertEqual(body["podcast_performance_profile"] as? String, "balanced")
        XCTAssertEqual(body["battery_podcast_policy"] as? String, "pause")
        XCTAssertEqual(body["extension_pairing_token"] as? String, "tok")  // 已 trim
        XCTAssertEqual(body["seed"] as? Int, 42)

        patch.batteryPause = false
        patch.seedText = "abc"          // 非法 seed → 不发该字段
        patch.performanceModeUI = "未知" // 未知 UI 词 → 兜底 balanced
        patch.podcastPerformanceModeUI = "未知" // 播客档未知 → 兜底 quiet(最凉)
        body = patch.body()
        XCTAssertEqual(body["battery_podcast_policy"] as? String, "allow")
        XCTAssertNil(body["seed"])
        XCTAssertEqual(body["performance_profile"] as? String, "balanced")
        XCTAssertEqual(body["podcast_performance_profile"] as? String, "quiet")
    }

    func testSpeedPatchParsesMenuTitle() {
        XCTAssertEqual(SettingsWire.speedPatch(fromMenuTitle: "1.25x")?["speed"] as? Double, 1.25)
        XCTAssertEqual(SettingsWire.speedPatch(fromMenuTitle: "2.0x")?["speed"] as? Double, 2.0)
        XCTAssertNil(SettingsWire.speedPatch(fromMenuTitle: "疯狂快"))
    }

    func testEnginesPatchPreservesOrderAndTiers() throws {
        // 已加载配置带 order/tiers(这页不编辑)→ 必须原样回传,防 merge 丢字段
        let loadedJSON = """
        {"translate": {"selected": "google", "order": ["google", "deepl"]},
         "llm": {"selected": "gemini", "order": ["gemini"],
                 "tiers": {"fast": {"gemini": "flash"}}}}
        """
        let loaded = try JSONDecoder().decode(EngineConfig.self, from: Data(loadedJSON.utf8))
        let body = SettingsWire.enginesPatch(
            translateSelected: "microsoft", targetLang: "zh",
            microsoftKey: "mk", microsoftRegion: "eastasia", deeplKey: "",
            llmSelected: "claude", llmKeys: ["claude": "sk"], localModelPath: "",
            preserving: loaded
        )
        let translate = try XCTUnwrap(body["translate"] as? [String: Any])
        XCTAssertEqual(translate["selected"] as? String, "microsoft")
        XCTAssertEqual(translate["order"] as? [String], ["google", "deepl"])
        let llm = try XCTUnwrap(body["llm"] as? [String: Any])
        XCTAssertEqual(llm["selected"] as? String, "claude")
        XCTAssertEqual(llm["order"] as? [String], ["gemini"])
        XCTAssertNotNil(llm["tiers"])

        // 未加载(nil)→ 不带 order/tiers,不发空值覆盖
        let bare = SettingsWire.enginesPatch(
            translateSelected: "google", targetLang: "zh",
            microsoftKey: "", microsoftRegion: "", deeplKey: "",
            llmSelected: "gemini", llmKeys: [:], localModelPath: "",
            preserving: nil
        )
        XCTAssertNil((bare["translate"] as? [String: Any])?["order"])
        XCTAssertNil((bare["llm"] as? [String: Any])?["tiers"])
    }
}
