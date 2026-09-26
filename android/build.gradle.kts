// Root build script: declares the plugins once, with versions coming from the
// version catalog so every module agrees on them.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
}
