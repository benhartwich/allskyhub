import java.io.File

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "org.allskyhub.allskyhub_app"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // flutter_local_notifications (reminders on "Himmel") needs java.time on old Android.
        isCoreLibraryDesugaringEnabled = true
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "org.allskyhub.app"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        manifestPlaceholders["appLabel"] = "allskyhub"
    }

    // Release key from the environment, both from the hub server's builds
    // (deploy/android/README.md): the hub's upload key (PKCS#12) for the store, the test
    // key for test builds. Without one,
    // the debug key, so `flutter run --release` works.
    val keystore = System.getenv("ALLSKYHUB_KEYSTORE") ?: System.getenv("CM_KEYSTORE_PATH")
    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = File(keystore)
                storePassword = System.getenv("ALLSKYHUB_KEYSTORE_PASSWORD")
                    ?: System.getenv("CM_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ALLSKYHUB_KEY_ALIAS") ?: System.getenv("CM_KEY_ALIAS")
                keyPassword = System.getenv("ALLSKYHUB_KEY_PASSWORD")
                    ?: System.getenv("CM_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
            // Test builds from the hub server sit next to the store app.
            if (System.getenv("ALLSKYHUB_CHANNEL") == "test") {
                applicationIdSuffix = ".test"
                manifestPlaceholders["appLabel"] = "allskyhub Test"
            }
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")
}
