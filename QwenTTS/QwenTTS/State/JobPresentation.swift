import Foundation

/// #10 C8.1:后台任务状态 → 统一展示文案的纯映射(照 PlaybackPresentation 的样板)。
/// 此前 Console 与 Library 各推一套词表且已分歧("🎙️ 播客生成中" vs "生成中 (x%)"),
/// 收成一张表:改文案只动这里,两处同时生效。接口即测试面(JobPresentationTests)。
enum JobPresentation {
    /// 单个任务的状态徽章文案。
    static func statusText(
        status: String?,
        progressPercent: Int? = nil,
        completedChunks: Int? = nil,
        totalChunks: Int? = nil
    ) -> String {
        switch status {
        case "queued":
            return "排队中..."
        case "paused":
            return "已暂停"
        case "running":
            if let pct = progressPercent { return "生成中 (\(pct)%)" }
            if let tot = totalChunks, tot > 0 { return "生成中 (\(completedChunks ?? 0)/\(tot))" }
            return "生成中..."
        case "failed":
            return "失败"
        case "done":
            return "完成"
        default:
            return status ?? ""
        }
    }

    static func statusText(for job: PodcastJob) -> String {
        statusText(
            status: job.status,
            progressPercent: job.progress_percent,
            completedChunks: job.completed_chunks,
            totalChunks: job.total_chunks
        )
    }

    /// 全局徽章:朗读空闲但后台有播客 worker 在跑(Console 状态灯用)。
    static let backgroundGeneratingBadge = "🎙️ 播客生成中"
}
