import CommonCrypto
import ExpoModulesCore
import Foundation

public class StreamingSha256Module: Module {
  public func definition() -> ModuleDefinition {
    Name("StreamingSha256")

    AsyncFunction("sha256File") { (uri: String) throws -> String in
      return try sha256(uri: uri, offset: 0, length: nil)
    }

    AsyncFunction("sha256Range") { (uri: String, offset: Double, length: Double) throws -> String in
      guard offset >= 0, length > 0, offset.rounded() == offset, length.rounded() == length else {
        throw HashError.invalidRange
      }
      return try sha256(uri: uri, offset: UInt64(offset), length: UInt64(length))
    }
  }

  private func sha256(uri: String, offset: UInt64, length: UInt64?) throws -> String {
    guard let url = URL(string: uri), url.isFileURL else {
      throw HashError.unsupportedUri
    }
    let fileHandle = try FileHandle(forReadingFrom: url)
    defer { try? fileHandle.close() }
    try fileHandle.seek(toOffset: offset)

    var context = CC_SHA256_CTX()
    CC_SHA256_Init(&context)
    var remaining = length
    while remaining == nil || remaining! > 0 {
      let count = Int(min(1_048_576, remaining ?? 1_048_576))
      guard let data = try fileHandle.read(upToCount: count), !data.isEmpty else {
        if remaining == nil { break }
        throw HashError.unexpectedEndOfFile
      }
      data.withUnsafeBytes { buffer in
        _ = CC_SHA256_Update(&context, buffer.baseAddress, CC_LONG(data.count))
      }
      if remaining != nil {
        remaining! -= UInt64(data.count)
      }
    }

    var output = [UInt8](repeating: 0, count: Int(CC_SHA256_DIGEST_LENGTH))
    CC_SHA256_Final(&output, &context)
    return output.map { String(format: "%02x", $0) }.joined()
  }
}

private enum HashError: Error {
  case invalidRange
  case unsupportedUri
  case unexpectedEndOfFile
}
