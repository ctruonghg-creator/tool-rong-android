package com.truong.darongmobile

import android.content.Context
import com.chaquo.python.Python
import org.json.JSONObject

class PythonBridge(context: Context) {
    private val module = Python.getInstance().getModule("android_bridge")

    init {
        module.callAttr("configure", context.filesDir.absolutePath)
    }

    fun login(account: Int, username: String, password: String, serverUrl: String) {
        module.callAttr("login", account, username, password, serverUrl)
    }

    fun disconnect(account: Int) {
        module.callAttr("disconnect", account)
    }

    fun stopAutomations(account: Int) {
        module.callAttr("stop_automations", account)
    }

    fun stopAll(account: Int) {
        module.callAttr("stop_all", account)
    }

    fun action(account: Int, name: String, args: JSONObject = JSONObject()) {
        module.callAttr("action", account, name, args.toString())
    }

    fun statusJson(account: Int): JSONObject =
        JSONObject(module.callAttr("status", account).toString())

    fun drainLogs(account: Int): String =
        module.callAttr("drain_logs", account, 120).toString()
}
