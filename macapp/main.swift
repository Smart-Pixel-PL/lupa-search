// Lupa.app — native window for the local Lupa search server (http://localhost:7766).
import Cocoa
import WebKit
import Carbon.HIToolbox

let serverURL = URL(string: "http://localhost:7766/")!
let statusURL = URL(string: "http://localhost:7766/api/status")!

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var hotKeyRef: EventHotKeyRef?

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        let cfg = WKWebViewConfiguration()
        cfg.mediaTypesRequiringUserActionForPlayback = []
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")
        web = WKWebView(frame: .zero, configuration: cfg)
        web.uiDelegate = self
        web.navigationDelegate = self
        web.allowsBackForwardNavigationGestures = true
        web.setValue(false, forKey: "drawsBackground")

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1500, height: 950),
                          // No .fullSizeContentView: the web view must not cover the title bar, or the window can't be dragged.
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Lupa"
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(name: nil) { $0.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            ? NSColor(red: 0.102, green: 0.102, blue: 0.114, alpha: 1) : .white }  // = page header (--panel)
        window.contentView = web
        window.isMovableByWindowBackground = false
        window.tabbingMode = .disallowed
        window.center()
        window.setFrameAutosaveName("LupaMain")
        window.isReleasedWhenClosed = false
        window.makeKeyAndOrderFront(nil)

        showLoading()
        ensureServer()
        registerHotKey()
    }

    // Keep running in the background so the hotkey / Dock click brings the window back instantly.
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ app: NSApplication, hasVisibleWindows: Bool) -> Bool { showWindow(); return true }

    func showWindow() {
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        web.evaluateJavaScript("var q=document.getElementById('q'); if(q){q.focus(); q.select();}")
    }

    // MARK: server
    func showLoading() {
        web.loadHTMLString("""
        <html><body style="margin:0;height:100vh;display:grid;place-items:center;background:#121214;color:#bbb;
        font:15px -apple-system">🔎 Uruchamiam Lupę…</body></html>
        """, baseURL: nil)
    }

    func ensureServer(attempt: Int = 0) {
        var req = URLRequest(url: statusURL); req.timeoutInterval = 1
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            DispatchQueue.main.async {
                if (resp as? HTTPURLResponse)?.statusCode == 200 {
                    self.web.load(URLRequest(url: serverURL))
                } else {
                    if attempt == 0 { self.kickServer() }
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.ensureServer(attempt: attempt + 1) }
                }
            }
        }.resume()
    }

    // The app owns the server process, so macOS privacy prompts / Full Disk Access apply to "Lupa"
    // (child processes inherit the app as their "responsible process").
    var server: Process?
    // Written into Info.plist by build.sh (the folder the app was built from).
    let project = Bundle.main.object(forInfoDictionaryKey: "LupaProjectPath") as? String
        ?? (NSHomeDirectory() + "/Lupa")

    func kickServer() {
        if let s = server, s.isRunning { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["\(project)/scripts/serve.sh"]
        p.currentDirectoryURL = URL(fileURLWithPath: project)
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "\(NSHomeDirectory())/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        p.environment = env
        let logURL = URL(fileURLWithPath: "\(project)/data/server.log")
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
        if let fh = try? FileHandle(forWritingTo: logURL) { fh.seekToEndOfFile(); p.standardOutput = fh; p.standardError = fh }
        p.terminationHandler = { [weak self] _ in
            // restart automatically if the server crashes while the app is running
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { if self?.quitting == false { self?.kickServer() } }
        }
        try? p.run()
        server = p
    }

    var quitting = false
    func applicationWillTerminate(_ note: Notification) {
        quitting = true
        server?.terminate()
    }

    // MARK: global hotkey ⌥⌘L
    func registerHotKey() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
            DispatchQueue.main.async { (NSApp.delegate as? AppDelegate)?.toggle() }
            return noErr
        }, 1, &spec, nil, nil)
        let id = EventHotKeyID(signature: OSType(0x4C555041), id: 1) // 'LUPA'
        RegisterEventHotKey(UInt32(kVK_ANSI_L), UInt32(cmdKey | optionKey), id, GetApplicationEventTarget(), 0, &hotKeyRef)
    }

    func toggle() {
        if NSApp.isActive && window.isVisible { NSApp.hide(nil) } else { showWindow() }
    }

    // MARK: JS dialogs (prompt/confirm/alert) and file picker
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler done: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = m; a.runModal(); done()
    }
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler done: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = m; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Anuluj")
        done(a.runModal() == .alertFirstButtonReturn)
    }
    func webView(_ w: WKWebView, runJavaScriptTextInputPanelWithPrompt p: String, defaultText d: String?, initiatedByFrame f: WKFrameInfo, completionHandler done: @escaping (String?) -> Void) {
        let a = NSAlert(); a.messageText = p; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Anuluj")
        let tf = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24)); tf.stringValue = d ?? ""
        a.accessoryView = tf; a.window.initialFirstResponder = tf
        done(a.runModal() == .alertFirstButtonReturn ? tf.stringValue : nil)
    }
    func webView(_ w: WKWebView, runOpenPanelWith params: WKOpenPanelParameters, initiatedByFrame f: WKFrameInfo, completionHandler done: @escaping ([URL]?) -> Void) {
        let p = NSOpenPanel(); p.allowsMultipleSelection = params.allowsMultipleSelection; p.canChooseDirectories = false
        p.begin { done($0 == .OK ? p.urls : nil) }
    }
    // External links open in the default browser.
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction, decisionHandler h: @escaping (WKNavigationActionPolicy) -> Void) {
        if let u = a.request.url, a.navigationType == .linkActivated, u.host != "localhost" { NSWorkspace.shared.open(u); h(.cancel); return }
        h(.allow)
    }
    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) {
        showLoading(); DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.ensureServer() }
    }
    func webViewWebContentProcessDidTerminate(_ w: WKWebView) { w.reload() }

    // MARK: menu (Edit menu is required for ⌘C/⌘V in the search field)
    func buildMenu() {
        let main = NSMenu()
        func add(_ title: String, _ items: [NSMenuItem]) {
            let it = NSMenuItem(); let m = NSMenu(title: title); items.forEach { m.addItem($0) }; it.submenu = m; main.addItem(it)
        }
        func mi(_ t: String, _ s: Selector?, _ k: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: s, keyEquivalent: k); i.keyEquivalentModifierMask = mods; return i
        }
        add("Lupa", [mi("O Lupie", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""), .separator(),
                     mi("Ukryj Lupę", #selector(NSApplication.hide(_:)), "h"), .separator(),
                     mi("Zakończ Lupę", #selector(NSApplication.terminate(_:)), "q")])
        add("Edycja", [mi("Cofnij", Selector(("undo:")), "z"), mi("Ponów", Selector(("redo:")), "Z"), .separator(),
                       mi("Wytnij", #selector(NSText.cut(_:)), "x"), mi("Kopiuj", #selector(NSText.copy(_:)), "c"),
                       mi("Wklej", #selector(NSText.paste(_:)), "v"), mi("Zaznacz wszystko", #selector(NSText.selectAll(_:)), "a")])
        add("Widok", [mi("Odśwież", #selector(reload), "r"), mi("Pełny ekran", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control])])
        add("Okno", [mi("Zminimalizuj", #selector(NSWindow.performMiniaturize(_:)), "m"), mi("Zamknij okno", #selector(NSWindow.performClose(_:)), "w")])
        NSApp.mainMenu = main
    }
    @objc func reload() { web.reload() }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
