import AppKit

/// #10 C8.2:黑胶囊瞬时提示的唯一实现。此前 AppKit(ConsoleVC.showTransientHint)
/// 与 SwiftUI(LibraryView.showToast)各写一份视觉相同的胶囊——收成一个挂宿主
/// NSView 的 presenter,两个世界共用。
@MainActor
enum ToastPresenter {
    static func show(
        _ message: String,
        in hostView: NSView?,
        bottomOffset: CGFloat = 90,
        holdSeconds: TimeInterval = 1.6
    ) {
        guard let host = hostView else { return }
        let pill = NSView()
        pill.wantsLayer = true
        pill.layer?.backgroundColor = NSColor.black.withAlphaComponent(0.75).cgColor
        pill.layer?.cornerRadius = 8
        pill.translatesAutoresizingMaskIntoConstraints = false
        let label = NSTextField(labelWithString: message)
        label.font = .systemFont(ofSize: 12, weight: .medium)
        label.textColor = .white
        label.translatesAutoresizingMaskIntoConstraints = false
        pill.addSubview(label)
        host.addSubview(pill)
        NSLayoutConstraint.activate([
            label.leadingAnchor.constraint(equalTo: pill.leadingAnchor, constant: 14),
            label.trailingAnchor.constraint(equalTo: pill.trailingAnchor, constant: -14),
            label.topAnchor.constraint(equalTo: pill.topAnchor, constant: 8),
            label.bottomAnchor.constraint(equalTo: pill.bottomAnchor, constant: -8),
            pill.centerXAnchor.constraint(equalTo: host.centerXAnchor),
            pill.bottomAnchor.constraint(equalTo: host.bottomAnchor, constant: -bottomOffset),
        ])
        pill.alphaValue = 0
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.2
            pill.animator().alphaValue = 1
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + holdSeconds) {
            NSAnimationContext.runAnimationGroup({ ctx in
                ctx.duration = 0.4
                pill.animator().alphaValue = 0
            }, completionHandler: { pill.removeFromSuperview() })
        }
    }

    /// SwiftUI 场景(无自持 NSView)可投递到当前 key window。
    /// #12-④:keyWindow 可能为 nil(菜单栏 app 无焦点窗口)或是面板——
    /// 兜底链:key → main → 首个可见普通窗口,提示不再静默丢失。
    static func showInKeyWindow(_ message: String) {
        let host = NSApp.keyWindow?.contentView
            ?? NSApp.mainWindow?.contentView
            ?? NSApp.windows.first(where: { $0.isVisible && $0.contentView != nil })?.contentView
        show(message, in: host)
    }
}
