import Foundation

/// M6(计划 #13):提交后短时轮询、捕捉「本次新产生」失败任务的时序器。
/// 此前困在 ConsoleViewController 里(16×500ms 的 Task 循环 + 两个取数方法,
/// 零测试)。取数仍经 AppStateStore(唯一取数路,#10 C7.3);attempts/interval
/// 可注入,测试用毫秒级间隔驱动。
@MainActor
final class JobFailureWatcher {
    private let store: AppStateStore

    init(store: AppStateStore) {
        self.store = store
    }

    /// 两类 job 的失败视图统一口径。
    /// M8:静默刷新(notify: false)——监视突发不触发内容中心全量重建。
    func failedJobs(isPodcast: Bool) async -> [(id: String, error: String)] {
        await store.refreshJobs(notify: false)
        if isPodcast {
            return store.podcastJobs
                .filter { JobStatus(wire: $0.status) == .failed }
                .map { (id: $0.job_id ?? "", error: $0.error ?? "处理失败") }
        } else {
            return store.urlJobs
                .filter { JobStatus(wire: $0.status) == .failed }
                .map { (id: $0.job_id ?? "", error: $0.error ?? "处理失败") }
        }
    }

    /// 提交前基线:现有失败 id 集(之后只对「新增」失败告警)。
    func baseline(isPodcast: Bool) async -> Set<String> {
        Set(await failedJobs(isPodcast: isPodcast).map { $0.id })
    }

    /// 轮询至多 attempts 次,发现基线外的新失败即回调一次并停。
    /// 生产节奏 16×500ms ≈ 8s(#10 C7.3 保留);返回 Task 供测试 await。
    @discardableResult
    func watch(
        isPodcast: Bool,
        baseline: Set<String>,
        attempts: Int = 16,
        interval: Duration = .milliseconds(500),
        onNewFailure: @escaping (String) -> Void
    ) -> Task<Void, Never> {
        Task {
            for _ in 0..<attempts {
                let failed = await failedJobs(isPodcast: isPodcast)
                if let hit = failed.first(where: { !baseline.contains($0.id) }) {
                    onNewFailure(hit.error)
                    return
                }
                try? await Task.sleep(for: interval)
            }
        }
    }
}
