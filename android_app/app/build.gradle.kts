import org.gradle.api.tasks.Sync

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val repoRoot = rootProject.projectDir.parentFile
val appSourceRoot = layout.projectDirectory.dir("src/main")
val pythonSourceDir = appSourceRoot.dir("python")
val assetSourceDir = appSourceRoot.dir("assets/static")

val syncPythonSources by tasks.registering(Sync::class) {
    from(repoRoot) {
        include("api.py")
        include("android_launcher.py")
        include("cache.py")
        include("datasource_finmind.py")
        include("datasource_goodinfo.py")
        include("datasource_mops.py")
        include("series_builder.py")
    }
    into(pythonSourceDir)
}

val syncWebAssets by tasks.registering(Sync::class) {
    from(repoRoot.resolve("static"))
    into(assetSourceDir)
}

android {
    namespace = "com.microeconomicdashboard.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.microeconomicdashboard.app"
        minSdk = 28
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
    }

    buildFeatures {
        buildConfig = true
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

chaquopy {
    defaultConfig {
        version = "3.11"
        pip {
            install("-r", repoRoot.resolve("requirements-android.txt").absolutePath)
        }
    }
}

tasks.named("preBuild") {
    dependsOn(syncPythonSources, syncWebAssets)
}

tasks.matching { it.name.startsWith("merge") && it.name.endsWith("PythonSources") }.configureEach {
    dependsOn(syncPythonSources)
}

tasks.matching { it.name.startsWith("merge") && it.name.endsWith("Assets") }.configureEach {
    dependsOn(syncWebAssets)
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.webkit:webkit:1.11.0")
}
