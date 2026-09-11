plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

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
        // deployment overrides these with its TLS endpoints.
        buildConfigField("String", "GATEWAY_BASE_URL", "\"http://10.0.2.2:8100/\"")
        buildConfigField("String", "CORE_BASE_URL", "\"http://10.0.2.2:8000/\"")

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

    // Push notifications.  The google-services plugin is intentionally NOT
    // applied: it fails the build outright when google-services.json is missing,
    // and a checkout without a Firebase project must still compile and run.
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
