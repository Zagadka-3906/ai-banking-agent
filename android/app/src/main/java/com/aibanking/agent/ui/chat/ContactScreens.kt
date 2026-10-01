package com.aibanking.agent.ui.chat

import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.aibanking.agent.data.ContactView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.PredictiveBackPreview
import com.aibanking.agent.ui.anim.ShimmerSwitch
import com.aibanking.agent.ui.anim.SkeletonRow
import com.aibanking.agent.ui.anim.cascadeItem
import com.aibanking.agent.ui.anim.pageSlideSpec
import com.aibanking.agent.ui.anim.rememberCascadeEntrance
import com.aibanking.agent.ui.components.*
import com.aibanking.agent.ui.theme.BankSpacing
import com.aibanking.agent.ui.theme.LocalBankSemanticColors
import kotlinx.coroutines.launch

/** Contact destinations share the app-level message host. */
@Composable
fun ContactsUi(state: ChatUiState, viewModel: ChatViewModel) {
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        PredictiveBackPreview(
            enabled = state.contactsScreen != ContactsScreen.LIST,
            onBack = viewModel::backToContactList,
            beneath = { ContactListScreen(state, viewModel) },
        ) {
            AnimatedContent(
                targetState = state.contactsScreen,
                transitionSpec = { pageSlideSpec(ContactsScreen::ordinal) },
                label = "contactsScreen",
            ) { screen ->
                when (screen) {
                    ContactsScreen.LIST -> ContactListScreen(state, viewModel)
                    ContactsScreen.DETAIL -> ContactDetailScreen(state, viewModel)
                    ContactsScreen.ADD -> ContactAddScreen(state, viewModel)
                }
            }
        }
    }
}

private data class ContactSection(val letter: String, val contacts: List<ContactView>)

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
private fun ContactListScreen(state: ChatUiState, viewModel: ChatViewModel) {
    val sections = remember(state.contacts, state.contactSearch) { groupContacts(state.contacts, state.contactSearch) }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val focus = LocalFocusManager.current
    val headerIndices = remember(sections) {
        buildMap {
            var index = 0
            sections.forEach { section ->
                put(section.letter, index)
                index += 1 + section.contacts.size
            }
        }
    }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("联系人", fontWeight = FontWeight.SemiBold) },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
                navigationIcon = {
                    IconButton(onClick = viewModel::closeContacts) {
                        BankIcon(BankIconKind.Close, contentDescription = "返回聊天")
                    }
                },
                actions = {
                    BankStatusChip("${state.contacts.size} 位", BankSemanticStatus.Neutral, Modifier.padding(end = 20.dp))
                },
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = { focus.clearFocus(); viewModel.openContactEditor() },
                icon = { BankIcon(BankIconKind.Add) },
                text = { Text("添加联系人") },
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
                modifier = Modifier.testTag(UiTags.CONTACT_OPEN_ADD),
            )
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding).imePadding(), contentAlignment = Alignment.TopCenter) {
            Column(Modifier.widthIn(max = BankSpacing.contentMaxWidth).fillMaxSize()) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    Surface(shape = RoundedCornerShape(18.dp), color = MaterialTheme.colorScheme.primaryContainer) {
                        BankIcon(BankIconKind.People, Modifier.padding(14.dp).size(26.dp), tint = MaterialTheme.colorScheme.onPrimaryContainer)
                    }
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("让每份心意，及时到达", style = MaterialTheme.typography.titleMedium)
                        Text("保存常用收款人，转账更从容", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                OutlinedTextField(
                    value = state.contactSearch,
                    onValueChange = viewModel::onContactSearchChange,
                    placeholder = { Text("搜索姓名或电话") },
                    leadingIcon = { BankIcon(BankIconKind.Search) },
                    trailingIcon = {
                        if (state.contactSearch.isNotEmpty()) IconButton(onClick = { viewModel.onContactSearchChange("") }) {
                            BankIcon(BankIconKind.Close, contentDescription = "清空搜索")
                        }
                    },
                    singleLine = true,
                    shape = RoundedCornerShape(20.dp),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = { focus.clearFocus() }),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 8.dp).testTag(UiTags.CONTACT_SEARCH),
                )
                ShimmerSwitch(
                    loading = state.panelBusy && sections.isEmpty(),
                    modifier = Modifier.weight(1f),
                    skeleton = { ContactsSkeleton() },
                ) {
                    Box(Modifier.fillMaxSize()) {
                        val cascade = rememberCascadeEntrance(ready = state.contacts.isNotEmpty())
                        LazyColumn(
                            state = listState,
                            modifier = Modifier.fillMaxSize(),
                            contentPadding = PaddingValues(start = 24.dp, end = if (sections.size > 1) 64.dp else 24.dp, bottom = 104.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            var flat = 0
                            sections.forEach { section ->
                                val headerIndex = flat++
                                val firstRowIndex = flat
                                flat += section.contacts.size
                                stickyHeader(key = "header-${section.letter}") {
                                    SectionHeader(section.letter, Modifier.cascadeItem(cascade, headerIndex))
                                }
                                itemsIndexed(section.contacts, key = { _, contact -> contact.contactId }) { offset, contact ->
                                    ContactRow(contact, Modifier.cascadeItem(cascade, firstRowIndex + offset)) {
                                        focus.clearFocus()
                                        viewModel.showContactDetail(contact.contactId)
                                    }
                                }
                            }
                            if (sections.isEmpty() && !state.panelBusy) {
                                item {
                                    val loadFailure = state.panelError
                                    if (loadFailure != null && state.contacts.isEmpty()) {
                                        PanelLoadFailure(loadFailure, viewModel::refreshContacts)
                                    } else {
                                        BankEmptyState(
                                            if (state.contactSearch.isBlank()) {
                                                "还没有联系人。添加常用收款人，转账时更好找。"
                                            } else {
                                                "没有匹配“${state.contactSearch}”的联系人。"
                                            },
                                            actionLabel = if (state.contactSearch.isBlank()) "添加第一位联系人" else "清空搜索",
                                            onAction = {
                                                if (state.contactSearch.isBlank()) viewModel.openContactEditor()
                                                else viewModel.onContactSearchChange("")
                                            },
                                        )
                                    }
                                }
                            }
                        }
                        if (sections.size > 1) {
                            AlphabetRail(
                                letters = sections.map { it.letter },
                                onSelect = { letter -> headerIndices[letter]?.let { index -> scope.launch { listState.animateScrollToItem(index) } } },
                                modifier = Modifier.align(Alignment.CenterEnd).padding(end = 8.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ContactsSkeleton() {
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        repeat(6) { SkeletonRow() }
    }
}

@Composable
private fun SectionHeader(letter: String, modifier: Modifier = Modifier) {
    Text(
        text = letter,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.primary,
        modifier = modifier.fillMaxWidth().background(MaterialTheme.colorScheme.background).padding(vertical = 12.dp, horizontal = 4.dp),
    )
}

@Composable
private fun ContactRow(contact: ContactView, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(24.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            ContactAvatar(contact.name, 48.dp)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                Text(contact.name, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(contact.phone, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (contact.relationshipLabel.isNotBlank() || contact.birthday != null) {
                    Text(
                        listOfNotNull(contact.relationshipLabel.takeIf { it.isNotBlank() }, contact.birthday?.let { "生日 $it" }).joinToString(" · "),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
            BankIcon(BankIconKind.ArrowForward, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** Independent scrolling keeps every letter reachable on short screens. */
@Composable
private fun AlphabetRail(letters: List<String>, onSelect: (String) -> Unit, modifier: Modifier = Modifier) {
    Surface(modifier = modifier, shape = RoundedCornerShape(24.dp), color = MaterialTheme.colorScheme.primaryContainer) {
        Column(Modifier.verticalScroll(rememberScrollState()).padding(vertical = 4.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            letters.forEach { letter ->
                Box(Modifier.size(48.dp).clickable { onSelect(letter) }, contentAlignment = Alignment.Center) {
                    Text(letter, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onPrimaryContainer)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ContactDetailScreen(state: ChatUiState, viewModel: ChatViewModel) {
    val contact = state.contacts.firstOrNull { it.contactId == state.selectedContactId }
    // Retain the contact only during exit; deleted live contacts still get an honest empty state.
    var lastRendered by remember { mutableStateOf<ContactView?>(null) }
    SideEffect { if (contact != null) lastRendered = contact }
    val rendered = if (state.contactsScreen == ContactsScreen.DETAIL) contact else lastRendered
    var confirmDelete by remember { mutableStateOf(false) }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("联系人详情") },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
                navigationIcon = {
                    IconButton(onClick = viewModel::backToContactList) { BankIcon(BankIconKind.ArrowBack, contentDescription = "返回列表") }
                },
                actions = {
                    IconButton(onClick = { confirmDelete = true }, enabled = rendered != null && !state.panelBusy) {
                        Icon(Icons.Default.Delete, contentDescription = "删除联系人", tint = MaterialTheme.colorScheme.error)
                    }
                },
            )
        },
    ) { padding ->
        if (rendered == null) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                BankEmptyState("联系人不存在或已被删除。", actionLabel = "返回列表", onAction = viewModel::backToContactList)
            }
            return@Scaffold
        }
        Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.TopCenter) {
            Column(
                modifier = Modifier.widthIn(max = BankSpacing.contentMaxWidth).fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
            ) {
                Surface(shape = RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.primaryContainer) {
                    Column(
                        Modifier.fillMaxWidth().padding(vertical = 28.dp, horizontal = 24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        ContactAvatar(rendered.name, 80.dp, MaterialTheme.typography.headlineLarge)
                        Text(rendered.name, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
                        BankStatusChip(rendered.relationshipLabel.ifBlank { rendered.relationship }, BankSemanticStatus.RiskGreen)
                        Text(rendered.phone, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant, fontFamily = FontFamily.Monospace)
                    }
                }
                BankPrimaryButton(
                    text = "给 ${rendered.name} 转账",
                    onClick = { viewModel.startTransferTo(rendered.name) },
                    enabled = !state.panelBusy,
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.CONTACT_TRANSFER),
                )
                BankSectionCard {
                    Text("收款人资料", style = MaterialTheme.typography.titleMedium)
                    DetailRow("电话", rendered.phone, mono = true)
                    DetailRow("关系", rendered.relationshipLabel.ifBlank { rendered.relationship })
                    DetailRow("生日", rendered.birthday ?: "未填写")
                    DetailRow("收款账户", if (rendered.linkedExisting) "本行账户${rendered.accountName?.let { " · $it" } ?: ""}" else "已就绪（可转账）")
                    DetailRow("添加时间", rendered.createdAt?.take(10) ?: "—", divider = false)
                }
                Text("删除仅把联系人移出通讯录，不影响该账户已有流水。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
    if (confirmDelete) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            icon = { Icon(Icons.Default.Delete, null, tint = MaterialTheme.colorScheme.error) },
            title = { Text("删除联系人") },
            text = { Text("确定删除「${rendered?.name ?: ""}」吗？删除后可重新添加。") },
            confirmButton = {
                TextButton(
                    onClick = { confirmDelete = false; rendered?.contactId?.let(viewModel::removeContact) },
                    modifier = Modifier.testTag(UiTags.CONTACT_REMOVE),
                ) { Text("删除", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun DetailRow(label: String, value: String, mono: Boolean = false, divider: Boolean = true) {
    Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.width(72.dp))
        Text(value.ifBlank { "—" }, style = MaterialTheme.typography.bodyMedium, fontFamily = if (mono) FontFamily.Monospace else null, modifier = Modifier.weight(1f))
    }
    if (divider) HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun ContactAddScreen(state: ChatUiState, viewModel: ChatViewModel) {
    val focus = LocalFocusManager.current
    val save = { focus.clearFocus(); viewModel.addContact() }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("添加联系人") },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
                navigationIcon = { IconButton(onClick = viewModel::backToContactList) { BankIcon(BankIconKind.ArrowBack, contentDescription = "返回列表") } },
            )
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding).imePadding(), contentAlignment = Alignment.TopCenter) {
            Column(
                modifier = Modifier.widthIn(max = BankSpacing.contentMaxWidth).fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
            ) {
                BankScreenHeading("记住重要的人", "填写资料，建立常用转账联系", "联系人管理")
                BankSectionCard {
                    Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        Text("基本信息", style = MaterialTheme.typography.titleMedium)
                        OutlinedTextField(
                            value = state.contactName,
                            onValueChange = { viewModel.onContactFormChange(name = it) },
                            label = { Text("姓名") },
                            singleLine = true,
                            enabled = !state.panelBusy,
                            shape = RoundedCornerShape(16.dp),
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                            keyboardActions = KeyboardActions(onNext = { focus.moveFocus(FocusDirection.Next) }),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        OutlinedTextField(
                            value = state.contactPhone,
                            onValueChange = { viewModel.onContactFormChange(phone = it) },
                            label = { Text("电话") },
                            singleLine = true,
                            enabled = !state.panelBusy,
                            shape = RoundedCornerShape(16.dp),
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone, imeAction = ImeAction.Next),
                            keyboardActions = KeyboardActions(onNext = { focus.moveFocus(FocusDirection.Next) }),
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
                BankSectionCard {
                    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("关系", style = MaterialTheme.typography.titleMedium)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            RELATIONSHIP_OPTIONS.forEach { option ->
                                val selected = state.contactRelationship == option
                                FilterChip(
                                    selected = selected,
                                    onClick = { viewModel.onContactFormChange(relationship = option) },
                                    enabled = !state.panelBusy,
                                    label = { Text(option) },
                                    leadingIcon = if (selected) ({ BankIcon(BankIconKind.Check, Modifier.size(16.dp)) }) else null,
                                    shape = RoundedCornerShape(14.dp),
                                )
                            }
                        }
                        OutlinedTextField(
                            value = state.contactBirthday,
                            onValueChange = { viewModel.onContactFormChange(birthday = it) },
                            label = { Text("生日（可选）") },
                            placeholder = { Text("YYYY-MM-DD") },
                            supportingText = { Text("例如 1995-08-16") },
                            singleLine = true,
                            enabled = !state.panelBusy,
                            shape = RoundedCornerShape(16.dp),
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text, imeAction = ImeAction.Done),
                            keyboardActions = KeyboardActions(onDone = { if (state.canAddContact && !state.panelBusy) save() else focus.clearFocus() }),
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
                BankPrimaryButton(
                    text = if (state.panelBusy) "保存中…" else "保存",
                    onClick = save,
                    enabled = state.canAddContact,
                    loading = state.panelBusy,
                    modifier = Modifier.fillMaxWidth().testTag(UiTags.CONTACT_ADD),
                )
                Text("电话命中本行账户将直接关联；否则会开立收款账户，保存后即可转账。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(8.dp))
            }
        }
    }
}

private val RELATIONSHIP_OPTIONS = listOf("爱人", "兄弟", "儿子", "女儿", "父母", "家人", "朋友", "同事", "其他")

@Composable
private fun ContactAvatar(name: String, size: Dp, textStyle: TextStyle = MaterialTheme.typography.titleMedium) {
    val semantic = LocalBankSemanticColors.current
    val colors = listOf(MaterialTheme.colorScheme.primary, semantic.chartBenchmark, semantic.cardActive, semantic.riskYellow)
    val accent = colors[Math.floorMod(name.hashCode(), colors.size)]
    Box(Modifier.size(size).clip(CircleShape).background(accent.copy(alpha = 0.12f)), contentAlignment = Alignment.Center) {
        Text(name.trim().firstOrNull()?.toString() ?: "?", color = accent, style = textStyle, fontWeight = FontWeight.SemiBold)
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
