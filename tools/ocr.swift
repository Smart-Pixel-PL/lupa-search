// lupa-ocr: on-device OCR with Apple Vision (Neural Engine). Usage: lupa-ocr image1 [image2 …]
// Prints one JSON line per image: {"path": …, "text": …, "conf": mean confidence}
import Foundation
import Vision
import AppKit

if CommandLine.arguments.count > 1 && CommandLine.arguments[1] == "--langs" {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    print((try? req.supportedRecognitionLanguages()) ?? [])
    exit(0)
}
for path in CommandLine.arguments.dropFirst() {
    autoreleasepool {
        var text = "", conf: Float = 0, n = 0
        if let img = NSImage(contentsOfFile: path), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) {
            let req = VNRecognizeTextRequest { r, _ in
                for o in (r.results as? [VNRecognizedTextObservation]) ?? [] {
                    if let c = o.topCandidates(1).first { text += c.string + "\n"; conf += c.confidence; n += 1 }
                }
            }
            req.recognitionLevel = .accurate
            req.usesLanguageCorrection = true
            req.recognitionLanguages = ["pl-PL", "en-US", "de-DE"]
            try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
        }
        let out: [String: Any] = ["path": path, "text": text, "conf": n > 0 ? conf / Float(n) : 0]
        if let d = try? JSONSerialization.data(withJSONObject: out), let s = String(data: d, encoding: .utf8) { print(s) }
        fflush(stdout)
    }
}
