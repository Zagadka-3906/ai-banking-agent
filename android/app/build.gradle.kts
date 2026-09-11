plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

// Firebase is enabled by *dropping in the config file*, not by editing this
// script: the google-services plugin refuses to configure the build at all when
// `google-services.json` is missing, so applying it unconditionally would make
// every checkout without a Firebase project fail.  Put the file in
// `android/app/` and the plugin activates on the next build; the app then
// registers a real push token (`push_enabled` flips to true server-side).
// Without the file, the app builds and runs with push simply disabled - see
// app/src/main/java/com/aibanking/agent/push/PushNotifications.kt.
val googleServicesConfig = file("google-services.json")
if (googleServicesConfig.exists()) {
    apply(plugin = libs.plugins.google.services.get().pluginId)
    logger.lifecycle("google-services.json found: Firebase push is enabled for this build.")
} else {
    logger.lifecycle("google-services.json not found: building without Firebase push.")
}

// Endpoints are resolved at build time so a checkout can target something other
// than the emulator loopback without editing sources: pass -PbankGatewayUrl=... /
// -PbankCoreUrl=..., or set them in android/gradle.properties or
// ~/.gradle/gradle.properties.  The defaults keep the docker-compose stack
// (core :8000, gateway :8100) reachable from an emulator through 10.0.2.2.
// Anything other than loopback should be TLS: network_security_config.xml only
// permits cleartext for 10.0.2.2/localhost/127.0.0.1.
fun resolvedEndpoint(property: String, fallback: String): String {
    val value = (project.findProperty(property) as String?)?.trim().orEmpty()
    if (value.isEmpty()) return fallback
    require(value.startsWith("http")) { "$property must be an http(s) URL" }
    return if (value.endsWith("/")) value else "$value/"
}
val gatewayUrl = resolvedEndpoint("bankGatewayUrl", "http://10.0.2.2:8100/")
val coreUrl = resolvedEndpoint("bankCoreUrl", "http://10.0.2.2:8000/")

android {
    namespace = "com.aibanking.agent"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.aibanking.agent"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"

        // The emulator reaches the developer machine through 10.0.2.2, so the
        // two backends defined in docker-compose (core :8000, gateway :8100)
        // are addressable without a reverse proxy during development.  A real
        // deployment overrides these with its TLS endpoints (see
        // resolvedEndpoint above).
        buildConfigField("String", "GATEWAY_BASE_URL", "\"$gatewayUrl\"")
        buildConfigField("String", "CORE_BASE_URL", "\"$coreUrl\"")

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}")
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(libs.kotlinx.coroutines.android)

    // Compose: the BOM keeps every artifact on one compatible release.
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    debugImplementation(libs.androidx.compose.ui.tooling)

    // The red authorization tier needs a real device-side biometric prompt.
    implementation(libs.androidx.biometric)

    implementation(libs.retrofit)
    implementation(libs.retrofit.converter.gson)
    implementation(libs.okhttp.logging)

    // Push notifications.  The google-services plugin is applied conditionally at
    // the top of this file: it fails the build outright when google-services.json
    // is missing, so it only activates once that file is present, and a checkout
    // without a Firebase project still compiles and runs (push degrades quietly).
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.messaging)

    // Instrumented UI tests.  They need the banking stack running on the host
    // (10.0.2.2); see app/src/androidTest/.../LoginAndChatUiTest.kt.
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.rules)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
}
