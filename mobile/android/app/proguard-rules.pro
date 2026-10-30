# Flutter and plugin classes are kept by their own consumer rules; these cover
# reflection used by Firebase Messaging and background_downloader (WorkManager).
-keep class io.flutter.plugins.** { *; }
-keep class com.google.firebase.messaging.** { *; }
-keep class com.bbflight.background_downloader.** { *; }
-dontwarn com.google.android.play.core.**
