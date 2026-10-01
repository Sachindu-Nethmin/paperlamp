// PaperLamp.app: a native window around PaperLamp's local engine.
//
// The app starts the engine (app.py, bundled in Resources/app) as a child process,
// shows its UI in a WebKit window, and when it quits asks the engine to unload the
// language model and stop the voice model before it exits, so nothing is left
// holding memory. Everything stays on this computer.
import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var log: FileHandle?

    let port: Int = Int(Bundle.main.object(forInfoDictionaryKey: "PaperLampPort") as? String ?? "") ?? 8790
    var base: URL { URL(string: "http://127.0.0.1:\(port)/")! }

    // ~/Library/Application Support/PaperLamp holds jobs, settings and the engine log
    lazy var home: URL = {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("PaperLamp")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        web = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        web.uiDelegate = self
        web.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1360, height: 880),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "PaperLamp"
        window.contentView = web
        window.minSize = NSSize(width: 900, height: 600)
        window.center()
        window.setFrameAutosaveName("PaperLampMain")
        window.makeKeyAndOrderFront(nil)
        web.loadHTMLString(Self.loadingPage, baseURL: nil)
        startServer()
        waitForServer(tries: 120)
        NSApp.activate(ignoringOtherApps: true)
    }

    // ── the engine ──────────────────────────────────────────────────────────
    func startServer() {
        guard let res = Bundle.main.resourceURL?.appendingPathComponent("app") else { return }
        let python = Bundle.main.object(forInfoDictionaryKey: "PaperLampPython") as? String ?? "/usr/bin/python3"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [res.appendingPathComponent("app.py").path]
        var env = ProcessInfo.processInfo.environment
        // apps don't get the shell's PATH; poppler and ffmpeg live in Homebrew
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["PAPERLAMP_HOME"] = home.path
        env["PAPERLAMP_PORT"] = String(port)
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        let logURL = home.appendingPathComponent("engine.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        if let h = try? FileHandle(forWritingTo: logURL) {
            p.standardOutput = h
            p.standardError = h
            log = h
        }
        do {
            try p.run()
            server = p
        } catch {
            fail("PaperLamp's engine could not start: \(error.localizedDescription)")
        }
    }

    func waitForServer(tries: Int) {
        var req = URLRequest(url: base.appendingPathComponent("api/memory"))
        req.timeoutInterval = 1
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            DispatchQueue.main.async {
                if (resp as? HTTPURLResponse)?.statusCode == 200 {
                    self.web.load(URLRequest(url: self.base))
                } else if tries > 0 && self.server?.isRunning == true {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.waitForServer(tries: tries - 1) }
                } else {
                    self.fail("PaperLamp's engine did not start. Details are in \(self.home.path)/engine.log")
                }
            }
        }.resume()
    }

    func request(_ path: String, method: String = "GET", timeout: TimeInterval = 3) -> [String: Any]? {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = method
        req.timeoutInterval = timeout
        var out: [String: Any]?
        let done = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: req) { data, _, _ in
            if let d = data { out = try? JSONSerialization.jsonObject(with: d) as? [String: Any] }
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + timeout + 1)
        return out
    }

    /// Ask the engine to free the language model and voice model, then make sure it exits.
    func stopServer() {
        guard let p = server, p.isRunning else { return }
        _ = request("api/shutdown", method: "POST")
        let deadline = Date().addingTimeInterval(20)
        while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
        if p.isRunning { p.terminate() }                       // SIGTERM: the engine frees memory on that too
        let hard = Date().addingTimeInterval(5)
        while p.isRunning && Date() < hard { Thread.sleep(forTimeInterval: 0.1) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        try? log?.close()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if let cur = request("api/memory")?["current"] as? String, !cur.isEmpty {
            let a = NSAlert()
            a.messageText = "A video is being made"
            a.informativeText = "Quit anyway? It stops now and picks up where it left off next time you press Retry."
            a.addButton(withTitle: "Quit")
            a.addButton(withTitle: "Keep making it")
            if a.runModal() != .alertFirstButtonReturn { return .terminateCancel }
        }
        stopServer()
        return .terminateNow
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func fail(_ message: String) {
        let a = NSAlert()
        a.messageText = "PaperLamp"
        a.informativeText = message
        a.runModal()
    }

    // ── menu ────────────────────────────────────────────────────────────────
    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let app = NSMenu()
        app.addItem(withTitle: "About PaperLamp", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Free Memory Now", action: #selector(freeMemory), keyEquivalent: "")
        app.addItem(withTitle: "Open Data Folder", action: #selector(openData), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Hide PaperLamp", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        app.addItem(withTitle: "Quit PaperLamp", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = app
        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r")
        viewItem.submenu = view
        let winItem = NSMenuItem(); main.addItem(winItem)
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        winItem.submenu = win
        NSApp.mainMenu = main
    }

    @objc func reload() { web.load(URLRequest(url: base)) }
    @objc func openData() { NSWorkspace.shared.open(home) }
    @objc func freeMemory() {
        let r = request("api/memory/free", method: "POST", timeout: 40)
        let a = NSAlert()
        if let err = r?["error"] as? String {
            a.messageText = "Not now"
            a.informativeText = err
        } else {
            let gb = r?["ollama_gb"] as? Double ?? 0
            let voice = r?["voice_servers"] as? Int ?? 0
            a.messageText = "Memory freed"
            a.informativeText = "Language model: \(gb) GB unloaded. Voice servers stopped: \(voice)."
        }
        a.runModal()
    }

    // ── web view plumbing ───────────────────────────────────────────────────
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.beginSheetModal(for: window) { r in completionHandler(r == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.beginSheetModal(for: window) { _ in completionHandler() }
    }

    // links meant for a new tab (script, description) open in the default browser
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let u = navigationAction.request.url { NSWorkspace.shared.open(u) }
        return nil
    }

    // "Download" links: the file is already in the job folder, so show it in Finder
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if #available(macOS 11.3, *), navigationAction.shouldPerformDownload,
           let path = navigationAction.request.url?.path, path.hasPrefix("/jobs/") {
            let file = home.appendingPathComponent(String(path.dropFirst()).removingPercentEncoding ?? "")
            NSWorkspace.shared.activateFileViewerSelecting([file])
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    static let loadingPage = """
    <html><body style="margin:0;background:#0E1726;color:#F5F7FA;font:16px -apple-system;display:grid;place-items:center;height:100vh">
    <div style="text-align:center"><div style="font-size:28px;font-weight:800">Paper<span style="color:#F0A868">Lamp</span></div>
    <div style="color:#8FA0BC;margin-top:8px">Starting…</div></div></body></html>
    """
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
