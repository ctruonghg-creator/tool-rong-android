@file:OptIn(
    androidx.compose.material3.ExperimentalMaterial3Api::class
)

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
    MaterialTheme {
        content()
    }
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
            mapOf(
                1 to AccUi(),
                2 to AccUi(),
                3 to AccUi()
            )
        )
    }

    var logs by remember {
        mutableStateOf(emptyList<String>())
    }

    val snackbarHostState = remember {
        SnackbarHostState()
    }

    LaunchedEffect(selectedAcc) {
        while (true) {

            try {
                val status =
                    bridge.statusJson(selectedAcc)

                val current =
                    accs[selectedAcc] ?: AccUi()

                accs =
                    accs + (
                        selectedAcc to current.copy(
                            logged =
                                status.optBoolean("logged"),

                            connected =
                                status.optBoolean("connected"),

                            bag =
                                "${status.optInt("bag")}/${status.optInt("bag_max")}",

                            stone =
                                String.format(
                                    "%,d",
                                    status.optLong("thach_anh")
                                )
                        )
                    )

                val rawLogs =
                    bridge.drainLogs(selectedAcc)

                val newLogs =
                    rawLogs
                        .split("\n")
                        .filter {
                            it.isNotBlank()
                        }

                if (newLogs.isNotEmpty()) {
                    logs =
                        (logs + newLogs)
                            .takeLast(300)
                }

            } catch (_: Exception) {
                // Không để polling làm app crash.
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
                            text =
                                "Android 16 • Xiaomi • Python Engine",

                            style =
                                MaterialTheme
                                    .typography
                                    .labelSmall
                        )
                    }
                },

                colors =
                    TopAppBarDefaults.topAppBarColors(
                        containerColor =
                            MaterialTheme
                                .colorScheme
                                .surface
                    ),

                actions = {

                    val online =
                        accs[selectedAcc]
                            ?.connected == true

                    Text(

                        text =
                            if (online) {
                                "ONLINE"
                            } else {
                                "OFFLINE"
                            },

                        color =
                            if (online) {
                                MaterialTheme
                                    .colorScheme
                                    .primary
                            } else {
                                MaterialTheme
                                    .colorScheme
                                    .error
                            },

                        fontWeight =
                            FontWeight.Bold,

                        modifier =
                            Modifier.padding(
                                end = 12.dp
                            )
                    )
                }
            )
        },

        bottomBar = {

            NavigationBar(
                modifier =
                    Modifier.navigationBarsPadding()
            ) {

                NavigationBarItem(

                    selected =
                        page == 0,

                    onClick = {
                        page = 0
                    },

                    icon = {

                        Icon(
                            imageVector =
                                Icons.Filled.Dashboard,

                            contentDescription =
                                "Tổng quan"
                        )
                    },

                    label = {
                        Text("Tổng quan")
                    }
                )

                NavigationBarItem(

                    selected =
                        page == 1,

                    onClick = {
                        page = 1
                    },

                    icon = {

                        Icon(
                            imageVector =
                                Icons.Filled.AutoAwesome,

                            contentDescription =
                                "Auto"
                        )
                    },

                    label = {
                        Text("Auto")
                    }
                )

                NavigationBarItem(

                    selected =
                        page == 2,

                    onClick = {
                        page = 2
                    },

                    icon = {

                        Icon(
                            imageVector =
                                Icons.Filled.Event,

                            contentDescription =
                                "Sự kiện"
                        )
                    },

                    label = {
                        Text("Sự kiện")
                    }
                )

                NavigationBarItem(

                    selected =
                        page == 3,

                    onClick = {
                        page = 3
                    },

                    icon = {

                        Icon(
                            imageVector =
                                Icons.Filled.Terminal,

                            contentDescription =
                                "Log"
                        )
                    },

                    label = {
                        Text("Log")
                    }
                )
            }
        },

        snackbarHost = {
            SnackbarHost(
                hostState = snackbarHostState
            )
        }

    ) { paddingValues ->

        Column(

            modifier =
                Modifier
                    .fillMaxSize()
                    .padding(paddingValues)
        ) {

            AccountStrip(

                accs = accs,

                selected =
                    selectedAcc,

                onSelect = {
                    selectedAcc = it
                }
            )

            when (page) {

                0 -> {

                    OverviewPage(

                        bridge = bridge,

                        acc = selectedAcc,

                        state =
                            accs[selectedAcc]
                                ?: AccUi(),

                        onLogin = {
                            user,
                            pass,
                            server ->

                            accs =
                                accs + (
                                    selectedAcc to (
                                        accs[selectedAcc]
                                            ?: AccUi()
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
                    )
                }

                1 -> {

                    AutoPage(

                        bridge = bridge,

                        acc =
                            selectedAcc
                    )
                }

                2 -> {

                    EventPage(

                        bridge = bridge,

                        acc =
                            selectedAcc
                    )
                }

                3 -> {

                    LogPage(
                        logs = logs
                    )
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

        modifier =
            Modifier
                .fillMaxWidth()
                .horizontalScroll(
                    rememberScrollState()
                )
                .padding(
                    horizontal = 12.dp,
                    vertical = 8.dp
                ),

        horizontalArrangement =
            Arrangement.spacedBy(8.dp)
    ) {

        accs.keys
            .sorted()
            .forEach { id ->

                val state =
                    accs[id]

                FilterChip(

                    selected =
                        selected == id,

                    onClick = {
                        onSelect(id)
                    },

                    label = {

                        Text(

                            text =
                                "ACC $id" +
                                    if (
                                        state?.logged == true
                                    ) {
                                        " • ✓"
                                    } else {
                                        ""
                                    }
                        )
                    },

                    leadingIcon = {

                        Icon(

                            imageVector =
                                Icons.Filled.AccountCircle,

                            contentDescription =
                                null,

                            modifier =
                                Modifier.size(18.dp)
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

    var user by remember(acc, state.user) {
        mutableStateOf(state.user)
    }

    var pass by remember(acc, state.pass) {
        mutableStateOf(state.pass)
    }

    var server by remember(acc, state.server) {
        mutableStateOf(state.server)
    }

    LazyColumn(

        contentPadding =
            PaddingValues(12.dp),

        verticalArrangement =
            Arrangement.spacedBy(12.dp)
    ) {

        item {

            Card {

                Column(

                    modifier =
                        Modifier.padding(16.dp),

                    verticalArrangement =
                        Arrangement.spacedBy(10.dp)
                ) {

                    Text(

                        text =
                            "Tài khoản ACC $acc",

                        fontWeight =
                            FontWeight.Bold,

                        style =
                            MaterialTheme
                                .typography
                                .titleMedium
                    )

                    OutlinedTextField(

                        value =
                            user,

                        onValueChange = {
                            user = it
                        },

                        modifier =
                            Modifier.fillMaxWidth(),

                        label = {
                            Text("User")
                        },

                        singleLine = true
                    )

                    OutlinedTextField(

                        value =
                            pass,

                        onValueChange = {
                            pass = it
                        },

                        modifier =
                            Modifier.fillMaxWidth(),

                        label = {
                            Text("Password")
                        },

                        singleLine = true,

                        visualTransformation =
                            PasswordVisualTransformation()
                    )

                    Text(
                        text = "Server",

                        style =
                            MaterialTheme
                                .typography
                                .labelLarge
                    )

                    Row(

                        modifier =
                            Modifier.horizontalScroll(
                                rememberScrollState()
                            ),

                        horizontalArrangement =
                            Arrangement.spacedBy(8.dp)
                    ) {

                        servers.forEach { url ->

                            FilterChip(

                                selected =
                                    server == url,

                                onClick = {
                                    server = url
                                },

                                label = {

                                    Text(
                                        url.substringAfter(
                                            "//"
                                        )
                                    )
                                }
                            )
                        }
                    }

                    Row(

                        horizontalArrangement =
                            Arrangement.spacedBy(8.dp)
                    ) {

                        Button(

                            onClick = {

                                onLogin(
                                    user,
                                    pass,
                                    server
                                )
                            },

                            enabled =
                                user.isNotBlank() &&
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

                                bridge.disconnect(
                                    acc
                                )
                            },

                            enabled =
                                state.connected
                        ) {

                            Text("Ngắt")
                        }

                        OutlinedButton(

                            onClick = {

                                bridge.stopAutomations(
                                    acc
                                )
                            },

                            enabled =
                                state.connected
                        ) {

                            Text("Dừng Auto")
                        }
                    }

                    Text(

                        text =
                            when {

                                state.logged &&
                                    state.connected -> {
                                    "● Đã kết nối và đăng nhập"
                                }

                                state.connected -> {
                                    "● Đã kết nối"
                                }

                                else -> {
                                    "○ Chưa kết nối"
                                }
                            },

                        color =
                            if (state.connected) {

                                MaterialTheme
                                    .colorScheme
                                    .primary

                            } else {

                                MaterialTheme
                                    .colorScheme
                                    .error
                            },

                        fontWeight =
                            FontWeight.SemiBold
                    )
                }
            }
        }

        item {

            Row(

                modifier =
                    Modifier.fillMaxWidth(),

                horizontalArrangement =
                    Arrangement.spacedBy(10.dp)
            ) {

                StatCard(

                    title =
                        "Túi rồng",

                    value =
                        state.bag,

                    modifier =
                        Modifier.weight(1f)
                )

                StatCard(

                    title =
                        "Thạch Anh",

                    value =
                        state.stone,

                    modifier =
                        Modifier.weight(1f)
                )
            }
        }

        item {

            Text(

                text =
                    "Bảng điều khiển",

                fontWeight =
                    FontWeight.Bold,

                style =
                    MaterialTheme
                        .typography
                        .titleMedium
            )
        }

        item {

            ActionCard(

                title =
                    "Thu hoạch tất cả",

                subtitle =
                    "ThuHoachCT theo các đảo đã mở"
            ) {

                bridge.action(
                    acc,
                    "harvest_all"
                )
            }
        }

        item {

            ActionCard(

                title =
                    "Nhận quà",

                subtitle =
                    "Thu toàn bộ gói quà / thưởng"
            ) {

                bridge.action(
                    acc,
                    "claim_all"
                )
            }
        }

        item {

            ActionCard(

                title =
                    "Làm mới Lãi",

                subtitle =
                    "Tải danh sách lai hiện tại"
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

        contentPadding =
            PaddingValues(12.dp),

        verticalArrangement =
            Arrangement.spacedBy(12.dp)
    ) {

        item {

            Text(

                text =
                    "Tự động hóa",

                fontWeight =
                    FontWeight.Bold,

                style =
                    MaterialTheme
                        .typography
                        .headlineSmall
            )
        }

        item {

            ActionCard(
                title =
                    "Boss thế giới",

                subtitle =
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
                title =
                    "Dừng Boss",

                subtitle =
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
                title =
                    "Tẩy Tủy 1 lượt",

                subtitle =
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
                title =
                    "Viễn Chinh",

                subtitle =
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
                title =
                    "Đấu Trường",

                subtitle =
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
                title =
                    "Lôi Đài",

                subtitle =
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
                title =
                    "Feed Auto",

                subtitle =
                    "Chạy xử lý cho ăn theo đảo"
            ) {

                bridge.action(
                    acc,
                    "feed_auto"
                )
            }
        }

        item {

            ActionCard(
                title =
                    "Quét đảo",

                subtitle =
                    "Cập nhật danh sách đảo"
            ) {

                bridge.action(
                    acc,
                    "island_scan"
                )
            }
        }

        item {

            ActionCard(
                title =
                    "Dừng toàn bộ",

                subtitle =
                    "Dừng tất cả automation"
            ) {

                bridge.stopAll(
                    acc
                )
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

        contentPadding =
            PaddingValues(12.dp),

        verticalArrangement =
            Arrangement.spacedBy(12.dp)
    ) {

        item {

            Text(

                text =
                    "Sự kiện",

                fontWeight =
                    FontWeight.Bold,

                style =
                    MaterialTheme
                        .typography
                        .headlineSmall
            )
        }

        item {

            ActionCard(

                title =
                    "Điểm danh sự kiện",

                subtitle =
                    "Chạy luồng điểm danh / nhiệm vụ"
            ) {

                bridge.action(
                    acc,
                    "event_checkin"
                )
            }
        }

        item {

            ActionCard(

                title =
                    "Event đang chọn",

                subtitle =
                    "Chạy EventSocketController"
            ) {

                bridge.action(
                    acc,
                    "event_flow"
                )
            }
        }

        item {

            ActionCard(

                title =
                    "Ải Thí Luyện",

                subtitle =
                    "Chạy 1 lượt"
            ) {

                bridge.action(
                    acc,
                    "ai_once"
                )
            }
        }

        item {

            ActionCard(

                title =
                    "Thủy Quái",

                subtitle =
                    "Chạy 1 lượt"
            ) {

                bridge.action(
                    acc,
                    "thuyquai_once"
                )
            }
        }

        item {

            ActionCard(

                title =
                    "Sinh nhật AI",

                subtitle =
                    "Chạy 1 lượt"
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

        contentPadding =
            PaddingValues(12.dp),

        verticalArrangement =
            Arrangement.spacedBy(4.dp)
    ) {

        item {

            Text(

                text =
                    "Nhật ký realtime",

                fontWeight =
                    FontWeight.Bold,

                style =
                    MaterialTheme
                        .typography
                        .headlineSmall
            )
        }

        items(logs) { line ->

            Text(

                text =
                    line,

                style =
                    MaterialTheme
                        .typography
                        .bodySmall,

                modifier =
                    Modifier
                        .fillMaxWidth()
                        .background(
                            MaterialTheme
                                .colorScheme
                                .surfaceVariant
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

    modifier: Modifier =
        Modifier
) {

    Card(
        modifier =
            modifier
    ) {

        Column(

            modifier =
                Modifier.padding(14.dp)
        ) {

            Text(

                text =
                    title,

                style =
                    MaterialTheme
                        .typography
                        .labelMedium
            )

            Spacer(
                modifier =
                    Modifier.height(4.dp)
            )

            Text(

                text =
                    value,

                style =
                    MaterialTheme
                        .typography
                        .titleLarge,

                fontWeight =
                    FontWeight.Bold
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

        colors =
            CardDefaults.cardColors(
                containerColor =
                    MaterialTheme
                        .colorScheme
                        .surfaceVariant
            )
    ) {

        Row(

            modifier =
                Modifier
                    .fillMaxWidth()
                    .padding(14.dp),

            verticalAlignment =
                Alignment.CenterVertically,

            horizontalArrangement =
                Arrangement.SpaceBetween
        ) {

            Column(

                modifier =
                    Modifier.weight(1f)
            ) {

                Text(

                    text =
                        title,

                    fontWeight =
                        FontWeight.Bold
                )

                Text(

                    text =
                        subtitle,

                    style =
                        MaterialTheme
                            .typography
                            .bodySmall
                )
            }

            Spacer(
                modifier =
                    Modifier.width(12.dp)
            )

            Button(
                onClick =
                    action
            ) {
                Text("Chạy")
            }
        }
    }
}
