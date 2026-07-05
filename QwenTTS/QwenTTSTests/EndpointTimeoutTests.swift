import XCTest

/// #13 终局 smoke 修复:/read 的按端点超时——模式≠原文时后端同步跑翻译/LLM
/// (数十秒),全局 5s 超时会假报「朗读请求未被后端接受」而实际照常出声。
final class EndpointTimeoutTests: XCTestCase {
    func testDefaultEndpointsHaveNoOverride() {
        XCTAssertNil(Endpoint.post("/stop").timeoutInterval)   // 其余端点维持全局 5s
        XCTAssertNil(Endpoint.get("/snapshot").timeoutInterval)
    }

    func testPostCanCarryPerEndpointTimeout() {
        let e = Endpoint.post("/read", body: ["text": "x"], timeout: 180)
        XCTAssertEqual(e.timeoutInterval, 180)
    }
}
