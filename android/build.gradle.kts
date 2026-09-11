// Root build script: declares the plugins once, with versions coming from the
// version catalog so every module agrees on them.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    // Declared with `apply false` so it is on the build classpath, but only
    // *applied* by :app when a google-services.json is actually present (see
    // app/build.gradle.kts).  The plugin fails the whole build when the file is
    // missing, and a checkout without a Firebase project must still compile.
    alias(libs.plugins.google.services) apply false
}
