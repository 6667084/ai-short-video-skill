// facebox: 用 Apple Vision 检测视频中的人脸位置，输出 JSON（归一化坐标，原点左上）。
// 用法: facebox <video> [samples_per_second=4]
// 输出: {"width":W,"height":H,"duration":D,"frames":[{"t":0.25,"faces":[[x,y,w,h],...],"bodies":[[x,y,w,h],...]},...]}
import AVFoundation
import Foundation
import Vision

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: facebox <video> [samples_per_second]\n".data(using: .utf8)!)
    exit(2)
}
let url = URL(fileURLWithPath: args[1])
let sps = args.count > 2 ? Double(args[2]) ?? 4 : 4

let asset = AVURLAsset(url: url)
let sem = DispatchSemaphore(value: 0)
var duration = 0.0
var natural = CGSize.zero
Task {
    duration = (try? await asset.load(.duration).seconds) ?? 0
    if let track = try? await asset.loadTracks(withMediaType: .video).first,
       let size = try? await track.load(.naturalSize),
       let tf = try? await track.load(.preferredTransform) {
        let r = CGRect(origin: .zero, size: size).applying(tf)
        natural = CGSize(width: abs(r.width), height: abs(r.height))
    }
    sem.signal()
}
sem.wait()

let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.requestedTimeToleranceBefore = CMTime(value: 1, timescale: 30)
gen.requestedTimeToleranceAfter = CMTime(value: 1, timescale: 30)
gen.maximumSize = CGSize(width: 720, height: 720)

var frames: [[String: Any]] = []
var t = 0.5 / sps
while t < duration {
    if let cg = try? gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600), actualTime: nil) {
        let req = VNDetectFaceRectanglesRequest()
        let body = VNDetectHumanRectanglesRequest()
        body.upperBodyOnly = false
        try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req, body])
        let bodies: [[Double]] = (body.results ?? []).filter { $0.confidence > 0.3 }.map { o in
            let b = o.boundingBox
            let v: [Double] = [Double(b.minX), 1.0 - Double(b.maxY), Double(b.width), Double(b.height)]
            return v.map { ($0 * 1000).rounded() / 1000 }
        }
        let faces: [[Double]] = (req.results ?? []).filter { $0.confidence > 0.5 }.map { o in
            let b = o.boundingBox  // Vision: 原点左下
            let v: [Double] = [Double(b.minX), 1.0 - Double(b.maxY), Double(b.width), Double(b.height)]
            return v.map { ($0 * 1000).rounded() / 1000 }
        }
        frames.append(["t": (t * 1000).rounded() / 1000, "faces": faces, "bodies": bodies])
    }
    t += 1 / sps
}
let out: [String: Any] = ["width": Double(natural.width), "height": Double(natural.height), "duration": duration, "frames": frames]
let data = try JSONSerialization.data(withJSONObject: out)
FileHandle.standardOutput.write(data)
