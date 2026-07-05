import Foundation

/// M5(计划 #13):设置写侧的唯一前端口径(纯映射,XCTest 可测)。
///
/// 此前写者各自手拼 `[String: Any]`:SettingsView 表单/模型切换、
/// EngineSettingsViewController、Console 语速——perf/battery 映射表埋在 View 里
/// 零测试,engines 的 order/tiers round-trip 规则只活在一处闭包里。
/// 收口后:映射表与 body 组装住这里;网络发送仍走 BackendAPIClient 单一 send 缝。
enum SettingsWire {

    // 性能档 UI 词 ↔ 后端值(单一表;读侧回填与写侧组装共用)
    static let perfUIToBackend = ["Fast": "fast", "Balanced": "balanced", "Quiet": "quiet"]
    static let perfBackendToUI = ["fast": "Fast", "balanced": "Balanced", "quiet": "Quiet"]

    /// 设置表单 → PATCH /settings body。
    struct FormPatch {
        var voice: String
        var performanceModeUI: String
        // 播客生成档位(独立于朗读档);后端播客域缺省/兜底均为 quiet
        var podcastPerformanceModeUI: String
        var temperature: Double
        var topP: Double
        var repPenalty: Double
        var extensionPairingToken: String
        var batteryPause: Bool
        var seedText: String

        func body() -> [String: Any] {
            var body: [String: Any] = [
                "voice": voice,
                "performance_profile": SettingsWire.perfUIToBackend[performanceModeUI] ?? "balanced",
                "podcast_performance_profile": SettingsWire.perfUIToBackend[podcastPerformanceModeUI] ?? "quiet",
                "temperature": temperature,
                "top_p": topP,
                "repetition_penalty": repPenalty,
                "extension_pairing_token": extensionPairingToken
                    .trimmingCharacters(in: .whitespacesAndNewlines),
                // 后端合法值 {pause, quiet, allow};关闭=allow("continue" 是历史无效值)
                "battery_podcast_policy": batteryPause ? "pause" : "allow",
            ]
            if let sd = Int(seedText.trimmingCharacters(in: .whitespaces)) {
                body["seed"] = sd
            }
            return body
        }
    }

    /// 本地模型卡「使用」→ body。
    static func modelPatch(_ name: String) -> [String: Any] { ["model": name] }

    /// Console 语速菜单("1.25x" 等)→ body;非法串返回 nil(不发请求)。
    static func speedPatch(fromMenuTitle title: String) -> [String: Any]? {
        guard let v = Double(title.replacingOccurrences(of: "x", with: "")) else { return nil }
        return ["speed": v]
    }

    /// AI 引擎页 → PATCH /engines body。
    /// 规则:selected/keys/路径 来自表单;order/tiers 这页不编辑,从已加载配置
    /// **原样回传**以免后端 deep-merge 后丢字段(历史教训)。
    static func enginesPatch(
        translateSelected: String,
        targetLang: String,
        microsoftKey: String,
        microsoftRegion: String,
        deeplKey: String,
        llmSelected: String,
        llmKeys: [String: String],
        localModelPath: String,
        preserving loaded: EngineConfig?
    ) -> [String: Any] {
        var translate: [String: Any] = [
            "selected": translateSelected,
            "target_lang": targetLang,
            "microsoft_key": microsoftKey,
            "microsoft_region": microsoftRegion,
            "deepl_key": deeplKey,
        ]
        if let order = loaded?.translate?.order {
            translate["order"] = order
        }

        var llm: [String: Any] = [
            "selected": llmSelected,
            "keys": llmKeys,
            "local_model_path": localModelPath,
        ]
        if let order = loaded?.llm?.order {
            llm["order"] = order
        }
        if let tiers = loaded?.llm?.tiers {
            llm["tiers"] = tiers
        }

        return ["translate": translate, "llm": llm]
    }
}
