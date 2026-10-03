package com.truong.darongmobile

import android.app.Application
import com.chaquo.python.android.AndroidPlatform
import com.chaquo.python.Python

class DaRongApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
    }
}
