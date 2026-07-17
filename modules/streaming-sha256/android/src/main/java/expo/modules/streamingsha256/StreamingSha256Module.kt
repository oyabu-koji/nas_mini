package expo.modules.streamingsha256

import android.net.Uri
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.io.File
import java.io.FileInputStream
import java.io.InputStream
import java.security.MessageDigest

class StreamingSha256Module : Module() {
  override fun definition() = ModuleDefinition {
    Name("StreamingSha256")

    AsyncFunction("sha256File") { uri: String ->
      sha256(uri, 0, null)
    }

    AsyncFunction("sha256Range") { uri: String, offset: Double, length: Double ->
      if (offset < 0 || length <= 0 || offset % 1 != 0.0 || length % 1 != 0.0) {
        throw IllegalArgumentException("Invalid hash range")
      }
      sha256(uri, offset.toLong(), length.toLong())
    }
  }

  private fun sha256(uriText: String, offset: Long, length: Long?): String {
    val input = openInput(Uri.parse(uriText))
    input.use { stream ->
      skipExactly(stream, offset)
      val digest = MessageDigest.getInstance("SHA-256")
      val buffer = ByteArray(1_048_576)
      var remaining = length
      while (remaining == null || remaining > 0) {
        val requested = minOf(buffer.size.toLong(), remaining ?: buffer.size.toLong()).toInt()
        val count = stream.read(buffer, 0, requested)
        if (count < 0) {
          if (remaining == null) break
          throw IllegalArgumentException("Unexpected end of file")
        }
        digest.update(buffer, 0, count)
        if (remaining != null) remaining -= count.toLong()
      }
      return digest.digest().joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
    }
  }

  private fun openInput(uri: Uri): InputStream {
    return if (uri.scheme == "content") {
      appContext.reactContext?.contentResolver?.openInputStream(uri)
        ?: throw IllegalArgumentException("Unable to open content URI")
    } else {
      val path = uri.path ?: throw IllegalArgumentException("Invalid local URI")
      FileInputStream(File(path))
    }
  }

  private fun skipExactly(stream: InputStream, offset: Long) {
    var remaining = offset
    val buffer = ByteArray(8192)
    while (remaining > 0) {
      val skipped = stream.skip(remaining)
      if (skipped > 0) {
        remaining -= skipped
      } else if (stream.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt()) >= 0) {
        remaining -= minOf(buffer.size.toLong(), remaining)
      } else {
        throw IllegalArgumentException("Invalid hash range")
      }
    }
  }
}
