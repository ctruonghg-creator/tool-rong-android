package com.truong.darongmobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Event
import androidx.compose.material.icons.filled.StopCircle
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SmallTopAppBar
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONObject

private data class AccUi(
    val user: String = "",
    val pass: String = "",
    val server: String = "https://daorongsv1.shop",
    val logged: Boolean = false,
    val connected: Boolean = false,
    val bag: String = "0/0",
    val stone: String = "0",
)

private val servers = listOf(
    "https://daorongsv1.shop",
    "https://daorongsv1.shop:52345",
    "https://daorongsv1.shop:52346",
)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val bridge = PythonBridge(this)
        setContent {
            DaRongTheme {
                DaRongApp(bridge)
            }
        }
    }
}

@Composable
private fun DaRongTheme(content: @Composable () -> Unit) {
    MaterialTheme(content = content)
}

@Composable
private fun DaRongApp(bridge: PythonBridge) {
    var selectedAcc by remember { mutableIntStateOf(1) }
    var page by remember { mutableIntStateOf(0) }
    var accs by remember { mutableStateOf((1..3).associateWith { AccUi() }) }
    var logs by remember { mutableStateOf(listOf<String>()) }
    val snack = remember { SnackbarHostState() }

    LaunchedEffect(selectedAcc) {
        while (true) {
            try {
                val st = bridge.statusJson(selectedAcc)
                val current = accs[selectedAcc] ?: AccUi()
                accs = accs + (selectedAcc to current.copy(
                    logged = st.optBoolean("logged"),
                    connected = st.optBoolean("connected"),
                    bag = "${st.optInt("bag")}/${st.optInt("bag_max")}",
                    stone = String.format("%,d", st.optLong("thach_anh")),
                ))
                val newLogs = bridge.drainLogs(selectedAcc)
                    .split("\\n")
                    .filter { it.isNotBlank() }
                if (newLogs.isNotEmpty()) logs = (logs + newLogs).takeLast(300)
            } catch (_: Exception) { }
            delay(800)
        }
    }

    Scaffold(
        modifier = Modifier.fillMaxSize(),
        topBar = {
            SmallTopAppBar(
                title = {
                    Column {
                        Text("Đảo Rồng Mobile", fontWeight = FontWeight.Bold)
                        Text("Android 16 • Xiaomi • Engine Python", style = MaterialTheme.typography.labelSmall)
                    }
                },
                colors = TopAppBarDefaults.smallTopAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface
                ),
                actions = {
                    Text(if (accs[selectedAcc]?.connected == true) "ONLINE" else "OFFLINE",
                        color = if (accs[selectedAcc]?.connected == true)
                            MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(end = 12.dp))
                }
            )
        },
        bottomBar = {
            NavigationBar(modifier = Modifier.navigationBarsPadding()) {
                NavigationBarItem(page == 0, { page = 0 }, icon = { Icon(Icons.Filled.Dashboard, null) }, label = { Text("Tổng quan") })
                NavigationBarItem(page == 1, { page = 1 }, icon = { Icon(Icons.Filled.AutoAwesome, null) }, label = { Text("Auto") })
                NavigationBarItem(page == 2, { page = 2 }, icon = { Icon(Icons.Filled.Event, null) }, label = { Text("Sự kiện") })
                NavigationBarItem(page == 3, { page = 3 }, icon = { Icon(Icons.Filled.Terminal, null) }, label = { Text("Log") })
            }
        },
        snackbarHost = { SnackbarHost(snack) }
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            AccountStrip(accs, selectedAcc) { selectedAcc = it }
            when (page) {
                0 -> OverviewPage(bridge, selectedAcc, accs[selectedAcc] ?: AccUi()) { user, pass, server ->
                    accs = accs + (selectedAcc to (accs[selectedAcc] ?: AccUi()).copy(user = user, pass = pass, server = server))
                    bridge.login(selectedAcc, user.trim(), pass, server)
                }
                1 -> AutoPage(bridge, selectedAcc)
                2 -> EventPage(bridge, selectedAcc)
                3 -> LogPage(logs)
            }
        }
    }
}

@Composable
private fun AccountStrip(accs: Map<Int, AccUi>, selected: Int, onSelect: (Int) -> Unit) {
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        accs.keys.sorted().forEach { id ->
            val s = accs[id]
            FilterChip(
                selected = selected == id,
                onClick = { onSelect(id) },
                label = { Text("ACC $id${if (s?.logged == true) " • ✓" else ""}") },
                leadingIcon = { Icon(Icons.Filled.AccountCircle, null, modifier = Modifier.size(18.dp)) }
            )
        }
    }
}

@Composable
private fun OverviewPage(bridge: PythonBridge, acc: Int, state: AccUi, onLogin: (String, String, String) -> Unit) {
    var user by remember(acc) { mutableStateOf(state.user) }
    var pass by remember(acc) { mutableStateOf(state.pass) }
    var server by remember(acc) { mutableStateOf(state.server) }

    LazyColumn(contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Card {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Tài khoản ACC $acc", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium)
                    OutlinedTextField(user, { user = it }, Modifier.fillMaxWidth(), label = { Text("User") }, singleLine = true)
                    OutlinedTextField(pass, { pass = it }, Modifier.fillMaxWidth(), label = { Text("Password") }, singleLine = true,
                        visualTransformation = PasswordVisualTransformation())
                    Text("Server", style = MaterialTheme.typography.labelLarge)
                    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        servers.forEach { url ->
                            FilterChip(selected = server == url, onClick = { server = url }, label = { Text(url.substringAfter("//")) })
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { onLogin(user, pass, server) }, enabled = user.isNotBlank() && pass.isNotBlank()) {
                            Text(if (state.logged) "Đăng nhập lại" else "Đăng nhập")
                        }
                        OutlinedButton(onClick = { bridge.disconnect(acc) }, enabled = state.connected) { Text("Ngắt") }
                        OutlinedButton(onClick = { bridge.stopAutomations(acc) }, enabled = state.connected) { Text("Dừng Auto") }
                    }
                    Text(
                        when {
                            state.logged && state.connected -> "● Đã kết nối và đăng nhập"
                            state.connected -> "● Đã kết nối"
                            else -> "○ Chưa kết nối"
                        },
                        color = if (state.connected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
                        fontWeight = FontWeight.SemiBold
                    )
                }
            }
        }
        item {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                StatCard("Túi rồng", state.bag, Modifier.weight(1f))
                StatCard("Thạch Anh", state.stone, Modifier.weight(1f))
            }
        }
        item { Text("Bảng điều khiển", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium) }
        item {
            ActionCard("Thu hoạch tất cả", "ThuHoachCT theo các đảo đã mở") { bridge.action(acc, "harvest_all") }
        }
        item {
            ActionCard("Nhận quà", "Thu toàn bộ gói quà / thưởng đang có") { bridge.action(acc, "claim_all") }
        }
        item {
            ActionCard("Làm mới Lãi", "Tải danh sách lai hiện tại") { bridge.action(acc, "refresh_lai") }
        }
    }
}

@Composable
private fun AutoPage(bridge: PythonBridge, acc: Int) {
    LazyColumn(contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Text("Tự động hóa", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.headlineSmall) }
        item { ActionCard("Boss thế giới", "Bật lịch Boss tự động") { bridge.action(acc, "boss_start") } }
        item { ActionCard("Dừng Boss", "Ngắt lịch Boss") { bridge.action(acc, "boss_stop") } }
        item { ActionCard("Tẩy Tủy 1 lượt", "Chạy một lượt Tẩy Tủy") { bridge.action(acc, "taytuy_once") } }
        item { ActionCard("Viễn Chinh", "TinhTheXanh1 • chế độ 0") { bridge.action(acc, "vienchinh") } }
        item { ActionCard("Đấu Trường", "Auto theo cấu hình mặc định") { bridge.action(acc, "dautruong_auto") } }
        item { ActionCard("Lôi Đài", "Đánh 1 lượt không mua lượt") { bridge.action(acc, "loidai_one") } }
        item { ActionCard("Feed Auto", "Chạy bộ xử lý cho ăn theo đảo") { bridge.action(acc, "feed_auto") } }
        item { ActionCard("Quét đảo", "Cập nhật danh sách đảo từ LoginSuccess") { bridge.action(acc, "island_scan") } }
        item { ActionCard("Dừng toàn bộ", "Dừng các auto controller của ACC") { bridge.stopAll(acc) } }
    }
}

@Composable
private fun EventPage(bridge: PythonBridge, acc: Int) {
    LazyColumn(contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Text("Sự kiện", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.headlineSmall) }
        item { ActionCard("Điểm danh sự kiện", "Chạy luồng điểm danh/nhiệm vụ") { bridge.action(acc, "event_checkin") } }
        item { ActionCard("Event đang chọn", "Chạy EventSocketController với event mặc định") { bridge.action(acc, "event_flow") } }
        item { ActionCard("Ải Thí Luyện", "Chạy 1 lượt theo cấu hình mặc định") { bridge.action(acc, "ai_once") } }
        item { ActionCard("Thủy Quái", "Chạy 1 lượt theo cấu hình mặc định") { bridge.action(acc, "thuyquai_once") } }
        item { ActionCard("Sinh nhật AI", "Chạy 1 lượt theo cấu hình mặc định") { bridge.action(acc, "birthday_once") } }
    }
}

@Composable
private fun LogPage(logs: List<String>) {
    LazyColumn(contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        item { Text("Nhật ký realtime", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.headlineSmall) }
        items(logs) { line ->
            Text(line, style = MaterialTheme.typography.bodySmall, modifier = Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant).padding(8.dp))
        }
    }
}

@Composable
private fun StatCard(title: String, value: String, modifier: Modifier = Modifier) {
    Card(modifier) {
        Column(Modifier.padding(14.dp)) {
            Text(title, style = MaterialTheme.typography.labelMedium)
            Spacer(Modifier.height(4.dp))
            Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun ActionCard(title: String, subtitle: String, action: () -> Unit) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerHighest)) {
        Row(
            Modifier.fillMaxWidth().padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Column(Modifier.weight(1f)) {
                Text(title, fontWeight = FontWeight.Bold)
                Text(subtitle, style = MaterialTheme.typography.bodySmall)
            }
            Spacer(Modifier.width(12.dp))
            Button(onClick = action) { Text("Chạy") }
        }
    }
}
