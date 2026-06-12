// cutout.swift — remove background using Apple Vision foreground mask
// usage: swift cutout.swift input.png output.png
import Vision
import CoreImage
import Foundation

let args = CommandLine.arguments
guard args.count == 3 else { print("usage: swift cutout.swift in.png out.png"); exit(1) }

let inputURL  = URL(fileURLWithPath: args[1])
let outputURL = URL(fileURLWithPath: args[2])

guard let ciImage = CIImage(contentsOf: inputURL) else { print("cannot read input"); exit(1) }

let request = VNGenerateForegroundInstanceMaskRequest()
let handler = VNImageRequestHandler(ciImage: ciImage)
try handler.perform([request])

guard let result = request.results?.first else { print("no foreground subject found"); exit(1) }

// keep the full canvas so multi-pose frames stay pixel-aligned for animation
let buffer = try result.generateMaskedImage(
    ofInstances: result.allInstances,
    from: handler,
    croppedToInstancesExtent: false
)

let outCI = CIImage(cvPixelBuffer: buffer)
let ctx = CIContext()
guard let png = ctx.pngRepresentation(of: outCI, format: .RGBA8,
        colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!) else {
    print("png encode failed"); exit(1)
}
try png.write(to: outputURL)
print("saved \(outputURL.path)")
