package com.truong.darongmobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

private data class AccUi(
    val user: String = "",
    val pass: String = "",
    val server: String = "https://daorongsv1.shop",
    val logged: Boolean = false,
    val connected: Boolean = false,
    val bag: String = "0/0",
    val stone: String = "0"
)

private val servers = listOf(
    "https://daorongsv1.shop",
    "https://daorongsv1.shop:52345",
    "https://daorongsv1.shop:52346"
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
private fun DaRongTheme(
    content: @Composable () -> Unit
) {
    MaterialTheme(content = content)
}

@Composable
private fun DaRongApp(
    bridge: PythonBridge
) {
    var selectedAcc by remember {
        mutableIntStateOf(1)
    }

    var page by remember {
        mutableIntStateOf(0)
    }

    var accs by remember {
        mutableStateOf(
            (1..3).associateWith { AccUi() }
        )
    }

    var logs by remember {
        mutableStateOf(emptyList<String>())
    }

    val snack = remember {
        SnackbarHostState()
    }

    LaunchedEffect(selectedAcc) {
        while (true) {
            try {
                val st = bridge.statusJson(selectedAcc)

                val current = accs[selectedAcc] ?: AccUi()

                accs = accs + (
                    selectedAcc to current.copy(
                        logged = st.optBoolean("logged"),
                        connected = st.optBoolean("connected"),
                        bag = "${st.optInt("bag")}/${st.optInt("bag_max")}",
                        stone = String.format(
                            "%,d",
                            st.optLong("thach_anh")
                        )
                    )
                )

                val rawLogs = bridge.drainLogs(selectedAcc)

                val newLogs = rawLogs
                    .split("\\n")
                    .filter { it.isNotBlank() }

                if (newLogs.isNotEmpty()) {
                    logs = (logs + newLogs).takeLast(300)
                }
            } catch (_: Exception) {
                // Giữ UI hoạt động nếu Python bridge chưa sẵn sàng.
            }

            delay(800)
        }
    }

    Scaffold(
        modifier = Modifier.fillMaxSize(),

        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(
                            text = "Đảo Rồng Mobile",
                            fontWeight = FontWeight.Bold
                        )

                        Text(
                            text = "Android 16 • Xiaomi • Engine Python",
                            style = MaterialTheme.typography.labelSmall
                        )
                    }
                },

                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface
                ),

                actions = {
                    val online = accs[selectedAcc]?.connected == true

                    Text(
                        text = if (online) "ONLINE" else "OFFLINE",
                        color = if (online) {
                            MaterialTheme.colorScheme.primary
                        } else {
                            MaterialTheme.colorScheme.error
                        },
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(end = 12.dp)
                    )
                }
            )
        },

        bottomBar = {
            NavigationBar(
                modifier = Modifier.navigationBarsPadding()
            ) {
                NavigationBarItem(
                    selected = page == 0,
                    onClick = { page = 0 },
                    icon = {
                        Icon(
                            imageVector = Icons.Filled.Dashboard,
                            contentDescription = "Tổng quan"
                        )
                    },
                    label = {
                        Text("Tổng quan")
                    }
                )

                NavigationBarItem(
                    selected = page == 1,
                    onClick = { page = 1 },
                    icon = {
                        Icon(
                            imageVector = Icons.Filled.AutoAwesome,
                            contentDescription = "Auto"
                        )
                    },
                    label = {
                        Text("Auto")
                    }
                )

                NavigationBarItem(
                    selected = page == 2,
                    onClick = { page = 2 },
                    icon = {
                        Icon(
                            imageVector = Icons.Filled.Event,
                            contentDescription = "Sự kiện"
                        )
                    },
                    label = {
                        Text("Sự kiện")
                    }
                )

                NavigationBarItem(
                    selected = page == 3,
                    onClick = { page = 3 },
                    icon = {
                        Icon(
                            imageVector = Icons.Filled.Terminal,
                            contentDescription = "Log"
                        )
                    },
                    label = {
                        Text("Log")
                    }
                )
            }
        },

        snackbarHost = {
            SnackbarHost(snack)
        }
    ) { padding ->

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {

            AccountStrip(
                accs = accs,
                selected = selectedAcc,
                onSelect = { selectedAcc = it }
            )

            when (page) {

                0 -> {
                    OverviewPage(
                        bridge = bridge,
                        acc = selectedAcc,
                        state = accs[selectedAcc] ?: AccUi()
                    ) { user, pass, server ->

                        accs = accs + (
                            selectedAcc to (
                                accs[selectedAcc] ?: AccUi()
                            ).copy(
                                user = user,
                                pass = pass,
                                server = server
                            )
                        )

                        bridge.login(
                            selectedAcc,
                            user.trim(),
                            pass,
                            server
                        )
                    }
                }

                1 -> {
                    AutoPage(
                        bridge = bridge,
                        acc = selectedAcc
                    )
                }

                2 -> {
                    EventPage(
                        bridge = bridge,
                        acc = selectedAcc
                    )
                }

                3 -> {
                    LogPage(logs)
                }
            }
        }
    }
}

@Composable
private fun AccountStrip(
    accs: Map<Int, AccUi>,
    selected: Int,
    onSelect: (Int) -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(
                horizontal = 12.dp,
                vertical = 8.dp
            ),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {

        accs.keys.sorted().forEach { id ->

            val state = accs[id]

            FilterChip(
                selected = selected == id,
                onClick = {
                    onSelect(id)
                },

                label = {
                    Text(
                        "ACC $id${if (state?.logged == true) " • ✓" else ""}"
                    )
                },

                leadingIcon = {
                    Icon(
                        imageVector = Icons.Filled.AccountCircle,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp)
                    )
                }
            )
        }
    }
}

@Composable
private fun OverviewPage(
    bridge: PythonBridge,
    acc: Int,
    state: AccUi,
    onLogin: (
        String,
        String,
        String
    ) -> Unit
) {
    var user by remember(acc) {
        mutableStateOf(state.user)
    }

    var pass by remember(acc) {
        mutableStateOf(state.pass)
    }

    var server by remember(acc) {
        mutableStateOf(state.server)
    }

    LazyColumn(
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {

        item {

            Card {

                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {

                    Text(
                        text = "Tài khoản ACC $acc",
                        fontWeight = FontWeight.Bold,
                        style = MaterialTheme.typography.titleMedium
                    )

                    OutlinedTextField(
                        value = user,
                        onValueChange = {
                            user = it
                        },
                        modifier = Modifier.fillMaxWidth(),
                        label = {
                            Text("User")
                        },
                        singleLine = true
                    )

                    OutlinedTextField(
                        value = pass,
                        onValueChange = {
                            pass = it
                        },
                        modifier = Modifier.fillMaxWidth(),
                        label = {
                            Text("Password")
                        },
                        singleLine = true,
                        visualTransformation = PasswordVisualTransformation()
                    )

                    Text(
                        text = "Server",
                        style = MaterialTheme.typography.labelLarge
                    )

                    Row(
                        modifier = Modifier
                            .horizontalScroll(
                                rememberScrollState()
                            ),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {

                        servers.forEach { url ->

                            FilterChip(
                                selected = server == url,
                                onClick = {
                                    server = url
                                },
                                label = {
                                    Text(
                                        url.substringAfter("//")
                                    )
                                }
                            )
                        }
                    }

                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {

                        Button(
                            onClick = {
                                onLogin(
                                    user,
                                    pass,
                                    server
                                )
                            },
                            enabled = user.isNotBlank() &&
                                pass.isNotBlank()
                        ) {
                            Text(
                                if (state.logged) {
                                    "Đăng nhập lại"
                                } else {
                                    "Đăng nhập"
                                }
                            )
                        }

                        OutlinedButton(
                            onClick = {
                                bridge.disconnect(acc)
                            },
                            enabled = state.connected
                        ) {
                            Text("Ngắt")
                        }

                        OutlinedButton(
                            onClick = {
                                bridge.stopAutomations(acc)
                            },
                            enabled = state.connected
                        ) {
                            Text("Dừng Auto")
                        }
                    }

                    Text(
                        text = when {
                            state.logged && state.connected ->
                                "● Đã kết nối và đăng nhập"

                            state.connected ->
                                "● Đã kết nối"

                            else ->
                                "○ Chưa kết nối"
                        },

                        color = if (state.connected) {
                            MaterialTheme.colorScheme.primary
                        } else {
                            MaterialTheme.colorScheme.error
                        },

                        fontWeight = FontWeight.SemiBold
                    )
                }
            }
        }

        item {

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {

                StatCard(
                    title = "Túi rồng",
                    value = state.bag,
                    modifier = Modifier.weight(1f)
                )

                StatCard(
                    title = "Thạch Anh",
                    value = state.stone,
                    modifier = Modifier.weight(1f)
                )
            }
        }

        item {
            Text(
                text = "Bảng điều khiển",
                fontWeight = FontWeight.Bold,
                style = MaterialTheme.typography.titleMedium
            )
        }

        item {
            ActionCard(
                title = "Thu hoạch tất cả",
                subtitle = "ThuHoachCT theo các đảo đã mở"
            ) {
                bridge.action(
                    acc,
                    "harvest_all"
                )
            }
        }

        item {
            ActionCard(
                title = "Nhận quà",
                subtitle = "Thu toàn bộ gói quà / thưởng đang có"
            ) {
                bridge.action(
                    acc,
                    "claim_all"
                )
            }
        }

        item {
            ActionCard(
                title = "Làm mới Lãi",
                subtitle = "Tải danh sách lai hiện tại"
            ) {
                bridge.action(
                    acc,
                    "refresh_lai"
                )
            }
        }
    }
}

@Composable
private fun AutoPage(
    bridge: PythonBridge,
    acc: Int
) {
    LazyColumn(
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {

        item {
            Text(
                text = "Tự động hóa",
                fontWeight = FontWeight.Bold,
                style = MaterialTheme.typography.headlineSmall
            )
        }

        item {
            ActionCard(
                "Boss thế giới",
                "Bật lịch Boss tự động"
            ) {
                bridge.action(
                    acc,
                    "boss_start"
                )
            }
        }

        item {
            ActionCard(
                "Dừng Boss",
                "Ngắt lịch Boss"
            ) {
                bridge.action(
                    acc,
                    "boss_stop"
                )
            }
        }

        item {
            ActionCard(
                "Tẩy Tủy 1 lượt",
                "Chạy một lượt Tẩy Tủy"
            ) {
                bridge.action(
                    acc,
                    "taytuy_once"
                )
            }
        }

        item {
            ActionCard(
                "Viễn Chinh",
                "TinhTheXanh1 • chế độ 0"
            ) {
                bridge.action(
                    acc,
                    "vienchinh"
                )
            }
        }

        item {
            ActionCard(
                "Đấu Trường",
                "Auto theo cấu hình mặc định"
            ) {
                bridge.action(
                    acc,
                    "dautruong_auto"
                )
            }
        }

        item {
            ActionCard(
                "Lôi Đài",
                "Đánh 1 lượt không mua lượt"
            ) {
                bridge.action(
                    acc,
                    "loidai_one"
                )
            }
        }

        item {
            ActionCard(
                "Feed Auto",
                "Chạy bộ xử lý cho ăn theo đảo"
            ) {
                bridge.action(
                    acc,
                    "feed_auto"
                )
            }
        }

        item {
            ActionCard(
                "Quét đảo",
                "Cập nhật danh sách đảo từ LoginSuccess"
            ) {
                bridge.action(
                    acc,
                    "island_scan"
                )
            }
        }

        item {
            ActionCard(
                "Dừng toàn bộ",
                "Dừng các auto controller của ACC"
            ) {
                bridge.stopAll(acc)
            }
        }
    }
}

@Composable
private fun EventPage(
    bridge: PythonBridge,
    acc: Int
) {
    LazyColumn(
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {

        item {
            Text(
                text = "Sự kiện",
                fontWeight = FontWeight.Bold,
                style = MaterialTheme.typography.headlineSmall
            )
        }

        item {
            ActionCard(
                "Điểm danh sự kiện",
                "Chạy luồng điểm danh/nhiệm vụ"
            ) {
                bridge.action(
                    acc,
                    "event_checkin"
                )
            }
        }

        item {
            ActionCard(
                "Event đang chọn",
                "Chạy EventSocketController với event mặc định"
            ) {
                bridge.action(
                    acc,
                    "event_flow"
                )
            }
        }

        item {
            ActionCard(
                "Ải Thí Luyện",
                "Chạy 1 lượt theo cấu hình mặc định"
            ) {
                bridge.action(
                    acc,
                    "ai_once"
                )
            }
        }

        item {
            ActionCard(
                "Thủy Quái",
                "Chạy 1 lượt theo cấu hình mặc định"
            ) {
                bridge.action(
                    acc,
                    "thuyquai_once"
                )
            }
        }

        item {
            ActionCard(
                "Sinh nhật AI",
                "Chạy 1 lượt theo cấu hình mặc định"
            ) {
                bridge.action(
                    acc,
                    "birthday_once"
                )
            }
        }
    }
}

@Composable
private fun LogPage(
    logs: List<String>
) {
    LazyColumn(
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp)
    ) {

        item {
            Text(
                text = "Nhật ký realtime",
                fontWeight = FontWeight.Bold,
                style = MaterialTheme.typography.headlineSmall
            )
        }

        items(logs) { line ->

            Text(
                text = line,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier
                    .fillMaxWidth()
                    .background(
                        MaterialTheme.colorScheme.surfaceVariant
                    )
                    .padding(8.dp)
            )
        }
    }
}

@Composable
private fun StatCard(
    title: String,
    value: String,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier
    ) {

        Column(
            modifier = Modifier.padding(14.dp)
        ) {

            Text(
                text = title,
                style = MaterialTheme.typography.labelMedium
            )

            Spacer(
                modifier = Modifier.height(4.dp)
            )

            Text(
                text = value,
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold
            )
        }
    }
}

@Composable
private fun ActionCard(
    title: String,
    subtitle: String,
    action: () -> Unit
) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor =
                MaterialTheme.colorScheme.surfaceContainerHighest
        )
    ) {

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),

            verticalAlignment = Alignment.CenterVertically,

            horizontalArrangement =
                Arrangement.SpaceBetween
        ) {

            Column(
                modifier = Modifier.weight(1f)
            ) {

                Text(
                    text = title,
                    fontWeight = FontWeight.Bold
                )

                Text(
                    text = subtitle,
                    style = MaterialTheme.typography.bodySmall
                )
            }

            Spacer(
                modifier = Modifier.width(12.dp)
            )

            Button(
                onClick = action
            ) {
                Text("Chạy")
            }
        }
    }
}
