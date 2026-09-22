package com.aibanking.agent

import android.app.Application
import com.aibanking.agent.data.standalone.StandaloneBank

class BankAgentApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        if (BuildConfig.STANDALONE_MODE) StandaloneBank.initialize(this)
    }
}
