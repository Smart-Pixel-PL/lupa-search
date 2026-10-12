// lupa-rmbg: background removal with Apple Vision (subject lift, on-device). Usage: lupa-rmbg input output.png
import Foundation
import Vision
import CoreImage
import AppKit

let args = CommandLine.arguments
guard args.count == 3, let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("usage: lupa-rmbg input output.png\n".data(using: .utf8)!); exit(2)
}
let req = VNGenerateForegroundInstanceMaskRequest()
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([req])
    guard let obs = req.results?.first, !obs.allInstances.isEmpty else {
        FileHandle.standardError.write("no subject found\n".data(using: .utf8)!); exit(3)
    }
    let buf = try obs.generateMaskedImage(ofInstances: obs.allInstances, from: handler, croppedToInstancesExtent: false)
    let ci = CIImage(cvPixelBuffer: buf)
    let ctx = CIContext()
    try ctx.writePNGRepresentation(of: ci, to: URL(fileURLWithPath: args[2]), format: .RGBA8,
                                   colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
} catch {
    FileHandle.standardError.write("\(error)\n".data(using: .utf8)!); exit(1)
}
