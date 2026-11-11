// Android-приложение Moodle Offline: окно (WebView) + встроенный Python (Chaquopy), в котором работает та же программа, что и на компьютере.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "MoodleOffline"
include(":app")
