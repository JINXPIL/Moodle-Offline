import java.io.File

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// Версия приложения берётся из moodle_offline.py (там строка VERSION = "1.9.0"), чтобы не вести её в двух местах.
val repoRoot: File = rootDir.parentFile
val appVersion: String = Regex("""^VERSION\s*=\s*"([^"]+)"""", RegexOption.MULTILINE)
    .find(File(repoRoot, "moodle_offline.py").readText())?.groupValues?.get(1) ?: "0.0.0"
val vp = appVersion.split(".").map { it.toIntOrNull() ?: 0 } + listOf(0, 0, 0)

// Python-файлы программы и окно (ui/) копируются из корня репозитория при каждой сборке.
val pyOut = layout.buildDirectory.dir("generated/moodle-python").get().asFile
val assetsOut = layout.buildDirectory.dir("generated/moodle-assets").get().asFile
val copyPython by tasks.registering(Sync::class) {
    from(repoRoot) { include("*.py") }
    into(pyOut)
}
val copyUi by tasks.registering(Sync::class) {
    from(File(repoRoot, "ui")) { into("ui") }
    into(assetsOut)
}
tasks.named("preBuild") { dependsOn(copyPython, copyUi) }

// Подпись: ключ из переменных окружения (в GitHub Actions — из секретов репозитория). Без ключа берётся временный отладочный:
// такое приложение ставится, но следующую версию поверх него поставить нельзя (Android увидит другую подпись).
val keystorePath: String? = System.getenv("ANDROID_KEYSTORE_FILE")
val haveKey = keystorePath != null && File(keystorePath).exists()
if (!haveKey) println("ВНИМАНИЕ: ключ подписи не задан, приложение будет подписано временным ключом (обновление поверх невозможно).")

android {
    namespace = "ru.sevsu.moodleoffline"
    compileSdk = 35

    defaultConfig {
        applicationId = "ru.sevsu.moodleoffline"
        minSdk = 24
        targetSdk = 34
        versionCode = vp[0] * 10000 + vp[1] * 100 + vp[2]
        versionName = appVersion
        // Python 3.13 в Chaquopy есть только для 64-битных процессоров: arm64-v8a (почти все телефоны) и x86_64 (эмулятор)
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }

    signingConfigs {
        if (haveKey) {
            create("release") {
                storeFile = File(keystorePath!!)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS") ?: "moodle-offline"
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD") ?: System.getenv("ANDROID_KEYSTORE_PASSWORD")
                storeType = System.getenv("ANDROID_KEYSTORE_TYPE") ?: "pkcs12"
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (haveKey) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }

    sourceSets { getByName("main") { assets.srcDir(assetsOut) } }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    lint { checkReleaseBuilds = false; abortOnError = false }
    packaging { resources { excludes += listOf("META-INF/*.version") } }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            // те же библиотеки, что в requirements.txt, кроме truststore (в Android свои сертификаты) и pypdfium2 (для Android нет сборки)
            install("requests")
            install("beautifulsoup4")
            install("tqdm")
            install("python-dotenv")
        }
    }
    sourceSets { getByName("main") { srcDir(pyOut) } }
}

dependencies {
    implementation("androidx.core:core:1.13.1")   // FileProvider: открыть скачанный файл в приложении телефона
}
