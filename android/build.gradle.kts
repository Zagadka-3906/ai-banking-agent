// Root build script: declares the plugins once, with versions coming from the
// version catalog so every module agrees on them.
buildscript {
    dependencies {
        // Override AGP's bundled KGP through its supported runtime dependency.
        // Keep built-in Kotlin enabled; do not apply kotlin-android separately.
        classpath(libs.kotlin.gradle.plugin)
    }
}

plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.compose) apply false
}
