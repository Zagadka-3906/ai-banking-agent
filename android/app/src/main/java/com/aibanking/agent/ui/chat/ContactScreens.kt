package com.aibanking.agent.ui.chat

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.aibanking.agent.data.ContactView
import com.aibanking.agent.ui.UiTags
import kotlinx.coroutines.launch
import kotlin.math.abs

/**
 * 通讯录式联系人界面：列表（搜索 + 拼音首字母分组 + 字母索引条 + FAB）→
 * 详情（大头像 + 资料行 + 直接转账 + 删除确认）→ 新增表单。
 *
 * 是全屏覆盖层而非底部弹层：聊天页的 Scaffold（含全局 Snackbar）在它后面，
 * 所以这里自带一份 SnackbarHost，错误与提示才不会静默丢失。
 */

@Composable
fun ContactsUi(state: ChatUiState, viewModel: ChatViewModel) {
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(state.error, state.notice) {
        val message = state.error ?: state.notice
        if (message != null) {
            snackbar.showSnackbar(message)
            viewModel.dismissMessages()
        }
    }
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        when (state.contactsScreen) {
            ContactsScreen.LIST -> ContactListScreen(state, viewModel, snackbar)
            ContactsScreen.DETAIL -> ContactDetailScreen(state, viewModel, snackbar)
            ContactsScreen.ADD -> ContactAddScreen(state, viewModel, snackbar)
        }
    }
}

// ---------------------------------------------------------------------------
// list
// ---------------------------------------------------------------------------

private data class ContactSection(val letter: String, val contacts: List<ContactView>)

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
private fun ContactListScreen(state: ChatUiState, viewModel: ChatViewModel, snackbar: SnackbarHostState) {
    val sections = remember(state.contacts, state.contactSearch) {
        groupContacts(state.contacts, state.contactSearch)
    }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    // Flat position of every section header, so the alphabet rail can jump.
    val headerIndex = remember(sections) {
        val map = mutableMapOf<String, Int>()
        var running = 0
        sections.forEach { section ->
            map[section.letter] = running
            running += 1 + section.contacts.size
        }
        map
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("联系人") },
                navigationIcon = {
                    IconButton(onClick = viewModel::closeContacts) { Icon(Icons.Default.Close, contentDescription = "返回聊天") }
                },
                actions = {
                    Text(
                        "${state.contacts.size} 位",
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.padding(end = 16.dp),
                    )
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        floatingActionButton = {
            FloatingActionButton(
                onClick = viewModel::openContactEditor,
                modifier = Modifier.testTag(UiTags.CONTACT_OPEN_ADD),
            ) { Icon(Icons.Default.Add, contentDescription = "添加联系人") }
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            OutlinedTextField(
                value = state.contactSearch,
                onValueChange = viewModel::onContactSearchChange,
                placeholder = { Text("搜索姓名或电话") },
                leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                singleLine = true,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp)
                    .testTag(UiTags.CONTACT_SEARCH),
            )
            Box(Modifier.fillMaxSize()) {
                LazyColumn(state = listState, modifier = Modifier.fillMaxSize()) {
                    sections.forEach { section ->
                        stickyHeader(key = section.letter) { SectionHeader(section.letter) }
                        items(section.contacts, key = { it.contactId }) { contact ->
                            ContactRow(contact) { viewModel.showContactDetail(contact.contactId) }
                        }
                    }
                    if (sections.isEmpty() && !state.panelBusy) {
                        item {
                            Text(
                                if (state.contactSearch.isBlank()) {
                                    "还没有联系人。点右下角添加，之后可直接说“给 <姓名> 转 300 元”。"
                                } else {
                                    "没有匹配“${state.contactSearch}”的联系人。"
                                },
                                style = MaterialTheme.typography.bodyMedium,
                                modifier = Modifier.padding(24.dp),
                            )
                        }
                    }
                }
                if (sections.size > 1) {
                    AlphabetRail(
                        letters = sections.map { it.letter },
                        onSelect = { letter ->
                            headerIndex[letter]?.let { index ->
                                scope.launch { listState.animateScrollToItem(index) }
                            }
                        },
                        modifier = Modifier.align(Alignment.CenterEnd),
                    )
                }
                if (state.panelBusy && sections.isEmpty()) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator()
                    }
                }
            }
        }
    }
}

@Composable
private fun SectionHeader(letter: String) {
    Text(
        text = letter,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(horizontal = 16.dp, vertical = 4.dp),
    )
}

@Composable
private fun ContactRow(contact: ContactView, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ContactAvatar(contact.name, 40.dp)
        Column(Modifier.weight(1f).padding(start = 12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(contact.name, style = MaterialTheme.typography.bodyLarge)
            Text(
                buildString {
                    append(contact.phone)
                    contact.relationshipLabel.takeIf { it.isNotBlank() }?.let { append(" · $it") }
                    contact.birthday?.let { append(" · 生日 $it") }
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun AlphabetRail(letters: List<String>, onSelect: (String) -> Unit, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.padding(end = 2.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(1.dp),
    ) {
        letters.forEach { letter ->
            Text(
                text = letter,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier
                    .clickable { onSelect(letter) }
                    .padding(horizontal = 8.dp, vertical = 1.dp),
            )
        }
    }
}

// ---------------------------------------------------------------------------
// detail
// ---------------------------------------------------------------------------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ContactDetailScreen(state: ChatUiState, viewModel: ChatViewModel, snackbar: SnackbarHostState) {
    val contact = state.contacts.firstOrNull { it.contactId == state.selectedContactId }
    var confirmDelete by remember { mutableStateOf(false) }
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("联系人详情") },
                navigationIcon = {
                    IconButton(onClick = viewModel::backToContactList) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回列表") }
                },
                actions = {
                    IconButton(onClick = { confirmDelete = true }) { Icon(Icons.Default.Delete, contentDescription = "删除联系人") }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        if (contact == null) {
            // The book may have refreshed and dropped this row; say so honestly.
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                Text("联系人不存在或已被删除。", style = MaterialTheme.typography.bodyMedium)
            }
            return@Scaffold
        }
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Spacer(Modifier.height(8.dp))
            ContactAvatar(contact.name, 88.dp, MaterialTheme.typography.headlineMedium)
            Text(contact.name, style = MaterialTheme.typography.headlineSmall)
            FilledTonalButton(
                onClick = { viewModel.startTransferTo(contact.name) },
                modifier = Modifier.fillMaxWidth().testTag(UiTags.CONTACT_TRANSFER),
            ) { Text("给 ${contact.name} 转账") }
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(vertical = 4.dp)) {
                    DetailRow("电话", contact.phone, mono = true)
                    DetailRow("关系", contact.relationshipLabel.ifBlank { contact.relationship })
                    DetailRow("生日", contact.birthday ?: "未填写")
                    DetailRow(
                        "收款账户",
                        if (contact.linkedExisting) {
                            "本行账户${contact.accountName?.let { " · $it" } ?: ""}"
                        } else {
                            "已就绪（可转账）"
                        },
                    )
                    DetailRow("添加时间", contact.createdAt?.take(10) ?: "—")
                }
            }
            Text(
                "删除仅把联系人移出通讯录，不影响该账户已有流水。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
    if (confirmDelete) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("删除联系人") },
            text = { Text("确定删除「${contact?.name ?: ""}」吗？删除后可重新添加。") },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmDelete = false
                        contact?.contactId?.let(viewModel::removeContact)
                    },
                    modifier = Modifier.testTag(UiTags.CONTACT_REMOVE),
                ) { Text("删除") }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun DetailRow(label: String, value: String, mono: Boolean = false) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(88.dp),
        )
        Text(
            value.ifBlank { "—" },
            style = MaterialTheme.typography.bodyMedium,
            fontFamily = if (mono) FontFamily.Monospace else null,
        )
    }
}

// ---------------------------------------------------------------------------
// add
// ---------------------------------------------------------------------------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ContactAddScreen(state: ChatUiState, viewModel: ChatViewModel, snackbar: SnackbarHostState) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("添加联系人") },
                navigationIcon = {
                    IconButton(onClick = viewModel::backToContactList) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回列表") }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            OutlinedTextField(
                value = state.contactName,
                onValueChange = { viewModel.onContactFormChange(name = it) },
                label = { Text("姓名") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = state.contactPhone,
                onValueChange = { viewModel.onContactFormChange(phone = it) },
                label = { Text("电话") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                modifier = Modifier.fillMaxWidth(),
            )
            Text("关系", style = MaterialTheme.typography.labelLarge)
            // 家人细分后选项变多，FlowRow 自动换行而不是挤在一行或溢出。
            @OptIn(ExperimentalLayoutApi::class)
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                RELATIONSHIP_OPTIONS.forEach { option ->
                    FilterChip(
                        selected = state.contactRelationship == option,
                        onClick = { viewModel.onContactFormChange(relationship = option) },
                        label = { Text(option) },
                    )
                }
            }
            OutlinedTextField(
                value = state.contactBirthday,
                onValueChange = { viewModel.onContactFormChange(birthday = it) },
                label = { Text("生日（可选，YYYY-MM-DD）") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                modifier = Modifier.fillMaxWidth(),
            )
            Button(
                onClick = viewModel::addContact,
                enabled = state.canAddContact && !state.panelBusy,
                modifier = Modifier.fillMaxWidth().testTag(UiTags.CONTACT_ADD),
            ) { Text(if (state.panelBusy) "保存中…" else "保存") }
            Text(
                "电话命中本行账户将直接关联；否则会开立收款账户，保存后即可转账。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

// ---------------------------------------------------------------------------
// shared pieces
// ---------------------------------------------------------------------------

// 关系选项与核心词表一致：家人细分为爱人/兄弟/儿子/女儿/父母；
// 直接说“给我爱人转 200”时，核心把称呼解析成联系人簿里对应关系的人。
private val RELATIONSHIP_OPTIONS = listOf("爱人", "兄弟", "儿子", "女儿", "父母", "家人", "朋友", "同事", "其他")

private val AVATAR_COLORS = listOf(
    Color(0xFF1565C0),
    Color(0xFF2E7D32),
    Color(0xFF6A1B9A),
    Color(0xFFE65100),
    Color(0xFFAD1457),
    Color(0xFF00838F),
)

@Composable
private fun ContactAvatar(name: String, size: Dp, textStyle: TextStyle = MaterialTheme.typography.titleMedium) {
    val initial = name.trim().firstOrNull()?.toString() ?: "?"
    Box(
        modifier = Modifier
            .size(size)
            .clip(CircleShape)
            .background(AVATAR_COLORS[abs(name.hashCode()) % AVATAR_COLORS.size]),
        contentAlignment = Alignment.Center,
    ) {
        Text(initial, color = Color.White, style = textStyle)
    }
}

/** 按姓名/电话过滤；用平台 ICU 中文 Collator 排序（本身即拼音序），再按首字母分段。 */
private fun groupContacts(contacts: List<ContactView>, query: String): List<ContactSection> {
    val filtered = if (query.isBlank()) {
        contacts
    } else {
        contacts.filter { it.name.contains(query, ignoreCase = true) || it.phone.contains(query) }
    }
    val sorted = filtered.sortedWith { a, b -> chinaCollator.compare(a.name, b.name) }
    val sections = mutableListOf<ContactSection>()
    for (contact in sorted) {
        val letter = contactLetter(contact.name)
        val last = sections.lastOrNull()
        if (last != null && last.letter == letter) {
            sections[sections.size - 1] = ContactSection(letter, last.contacts + contact)
        } else {
            sections.add(ContactSection(letter, listOf(contact)))
        }
    }
    return sections
}

// 中文 Collator 的顺序就是拼音序，列表排序直接用它。首字母不能用“与 A..Z
// 比较”推导：不同 ICU 版本里汉字与 Latin 字母的相对次序不一致（真机上汉字
// 可能整体排在 A 之前）。改用经典“边界字”法——每个拼音首字母取一个最小
// 常用字作哨兵，姓名落在哪个哨兵之后（且未到下一个）就归入该字母；哨兵
// 比较与列表排序用同一个 Collator，永远不会失配。
private val chinaCollator: java.text.Collator = java.text.Collator.getInstance(java.util.Locale.CHINA)
private val LETTER_BOUNDARIES: List<Pair<Char, String>> = listOf(
    'A' to "啊", 'B' to "巴", 'C' to "擦", 'D' to "搭", 'E' to "鹅", 'F' to "发",
    'G' to "噶", 'H' to "哈", 'J' to "击", 'K' to "喀", 'L' to "拉", 'M' to "妈",
    'N' to "拿", 'O' to "哦", 'P' to "趴", 'Q' to "七", 'R' to "然", 'S' to "撒",
    'T' to "塌", 'W' to "挖", 'X' to "西", 'Y' to "压", 'Z' to "杂",
)

private fun contactLetter(name: String): String {
    val probe = name.trim()
    if (probe.isEmpty()) return "#"
    val first = probe.first()
    if (first in 'A'..'Z' || first in 'a'..'z') return first.uppercaseChar().toString()
    if (first.isDigit()) return "#"
    var letter = "#"
    for ((boundaryLetter, boundaryChar) in LETTER_BOUNDARIES) {
        if (chinaCollator.compare(boundaryChar, probe) <= 0) letter = boundaryLetter.toString() else break
    }
    return letter
}
