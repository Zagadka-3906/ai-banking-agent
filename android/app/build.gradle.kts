plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

val scenarioTests = providers.gradleProperty("bankScenarioTests").orNull == "true"

// Keep the host test engine, listener and result protos on the same patch.
// AGP 9.4.1 brings 1.0.1, whose listener races while collecting test results
// (b/565986136). Remove this override when AGP bundles the fixed engine family.
val androidTestEngineVersion = libs.versions.androidTestEngine.get()
configurations.configureEach {
    resolutionStrategy.eachDependency {
        if (requested.group == "com.android.tools.androidtest" && requested.version == "1.0.1") {
            useVersion(androidTestEngineVersion)
            because("Fix concurrent Android test result collection (b/565986136)")
        }
    }
}

// Endpoints are resolved at build time so a checkout can target something other
// than the emulator loopback without editing sources: pass -PbankGatewayUrl=... /
// -PbankCoreUrl=..., or set them in android/gradle.properties or
// ~/.gradle/gradle.properties. The defaults reach the local Core and Gateway
// from the emulator through 10.0.2.2.
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
// Explicit opt-in for HTTPS hostnames resolved to LAN addresses by internal DNS.
// false (or omitted) keeps automatic literal/.local/single-label detection.
val forceLocalNetworkPermission = providers.gradleProperty("bankLocalNetwork").orNull?.let {
    require(it == "true" || it == "false") { "bankLocalNetwork must be true or false" }
    it.toBoolean()
} ?: false

android {
    namespace = "com.aibanking.agent"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.aibanking.agent"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "1.0.0"

        // The emulator reaches the developer machine through 10.0.2.2, so the
        // local services (core :8000, gateway :8100)
        // are addressable without a reverse proxy during development.  A real
        // deployment overrides these with its TLS endpoints (see
        // resolvedEndpoint above).
        buildConfigField("String", "GATEWAY_BASE_URL", "\"$gatewayUrl\"")
        buildConfigField("String", "CORE_BASE_URL", "\"$coreUrl\"")
        buildConfigField("boolean", "FORCE_LOCAL_NETWORK_PERMISSION", forceLocalNetworkPermission.toString())

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
        create("scenario") {
            initWith(getByName("debug"))
            applicationIdSuffix = ".scenariotest"
            matchingFallbacks += listOf("debug")
            buildConfigField("String", "GATEWAY_BASE_URL", "\"http://127.0.0.1:18810/\"")
            buildConfigField("String", "CORE_BASE_URL", "\"http://127.0.0.1:18800/\"")
            // This isolated variant always uses loopback, even when a developer
            // opts their normal HTTPS endpoints into LAN permission handling.
            buildConfigField("boolean", "FORCE_LOCAL_NETWORK_PERMISSION", "false")
        }
    }
    testBuildType = if (scenarioTests) "scenario" else "debug"

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    // AGP's built-in Kotlin inherits the Java target compatibility above.

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
    implementation(libs.mlkit.text.recognition)

    // Compose: the BOM keeps every artifact on one compatible release.
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    // Material 3 1.4 no longer brings the icons used by existing screens.
    implementation(libs.androidx.compose.material.icons.core)
    implementation(libs.compose.shimmer)
    implementation(libs.commonmark)
    debugImplementation(libs.androidx.compose.ui.tooling)

    // The red authorization tier needs a real device-side biometric prompt.
    implementation(libs.androidx.biometric)

    implementation(libs.retrofit)
    implementation(libs.retrofit.converter.gson)
    implementation(libs.okhttp.logging)
    testImplementation(libs.junit4)

    // Instrumented UI tests need the local banking services running on the host.
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.espresso.core)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    add("scenarioImplementation", libs.androidx.compose.ui.test.manifest)
}
