package com.microeconomicdashboard.app

import android.content.Context
import java.io.File

object AssetCopier {
    fun copyDirectory(context: Context, assetPath: String, outputDir: File) {
        if (!outputDir.exists()) {
            outputDir.mkdirs()
        }

        val entries = context.assets.list(assetPath).orEmpty()
        if (entries.isEmpty()) {
            context.assets.open(assetPath).use { input ->
                outputDir.outputStream().use { output ->
                    input.copyTo(output)
                }
            }
            return
        }

        for (entry in entries) {
            val childAssetPath = if (assetPath.isEmpty()) entry else "$assetPath/$entry"
            val childOutput = File(outputDir, entry)
            val childEntries = context.assets.list(childAssetPath).orEmpty()
            if (childEntries.isEmpty()) {
                childOutput.parentFile?.mkdirs()
                context.assets.open(childAssetPath).use { input ->
                    childOutput.outputStream().use { output ->
                        input.copyTo(output)
                    }
                }
            } else {
                copyDirectory(context, childAssetPath, childOutput)
            }
        }
    }
}
