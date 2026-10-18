// Copyright (C) 2026 Dr. Brett Addison
// SPDX-License-Identifier: GPL-3.0-or-later
import AppKit
import Foundation

// Native vector artwork rendered at every required macOS icon size.
let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
var images: [[String: String]] = []
for points in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = points * scale
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                   bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                   colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        let transform = NSAffineTransform(); transform.scale(by: CGFloat(pixels) / 1024); transform.concat()
        let tile = NSBezierPath(roundedRect: NSRect(x: 48, y: 48, width: 928, height: 928), xRadius: 212, yRadius: 212)
        NSGradient(starting: NSColor(calibratedRed: 0.08, green: 0.17, blue: 0.21, alpha: 1), ending: NSColor(calibratedRed: 0.02, green: 0.06, blue: 0.09, alpha: 1))!.draw(in: tile, angle: -70)
        let screen = NSBezierPath(roundedRect: NSRect(x: 203, y: 316, width: 618, height: 418), xRadius: 48, yRadius: 48)
        NSColor(calibratedRed: 0.31, green: 0.91, blue: 0.75, alpha: 1).setStroke()
        screen.lineWidth = 30; screen.stroke()
        let base = NSBezierPath(); base.move(to: NSPoint(x: 512, y: 312)); base.line(to: NSPoint(x: 512, y: 243))
        base.move(to: NSPoint(x: 397, y: 232)); base.line(to: NSPoint(x: 627, y: 232)); base.lineWidth = 30; base.lineCapStyle = .round; base.stroke()
        NSColor(calibratedRed: 0.31, green: 0.91, blue: 0.75, alpha: 0.22).setFill()
        NSBezierPath(roundedRect: NSRect(x: 286, y: 428, width: 231, height: 216), xRadius: 24, yRadius: 24).fill()
        NSColor(calibratedRed: 0.79, green: 1, blue: 0.93, alpha: 1).setStroke()
        let window = NSBezierPath(roundedRect: NSRect(x: 387, y: 391, width: 242, height: 224), xRadius: 24, yRadius: 24)
        window.lineWidth = 19; window.stroke()
        let arrow = NSBezierPath(); arrow.move(to: NSPoint(x: 650, y: 554)); arrow.line(to: NSPoint(x: 725, y: 554))
        arrow.move(to: NSPoint(x: 695, y: 584)); arrow.line(to: NSPoint(x: 725, y: 554)); arrow.line(to: NSPoint(x: 695, y: 524))
        arrow.lineWidth = 18; arrow.lineCapStyle = .round; arrow.lineJoinStyle = .round; arrow.stroke()
        NSGraphicsContext.restoreGraphicsState()
        let name = "icon_\(points)x\(points)@\(scale)x.png"
        try rep.representation(using: .png, properties: [:])!.write(to: root.appendingPathComponent(name))
        images.append(["idiom": "mac", "size": "\(points)x\(points)", "scale": "\(scale)x", "filename": name])
    }
}
let contents: [String: Any] = ["images": images, "info": ["author": "xcode", "version": 1]]
try JSONSerialization.data(withJSONObject: contents, options: [.prettyPrinted, .sortedKeys]).write(to: root.appendingPathComponent("Contents.json"))
