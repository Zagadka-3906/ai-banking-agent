# Retrofit + Gson reflect over the gateway DTOs; keep their members and the
# generic signatures they are parsed through.
-keepattributes Signature, InnerClasses, EnclosingMethod, *Annotation*

-keep class com.aibanking.agent.data.** { *; }

-dontwarn okhttp3.**
-dontwarn okio.**
-dontwarn retrofit2.**
-dontwarn javax.annotation.**
