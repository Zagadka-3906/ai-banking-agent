package com.aibanking.agent.ui.chat

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedContentTransitionScope
import androidx.compose.animation.BoundsTransform
import androidx.compose.animation.ExperimentalSharedTransitionApi
import androidx.compose.animation.SharedTransitionLayout
import androidx.compose.animation.SharedTransitionScope
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
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
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.composed
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.animation.AnimatedVisibilityScope
import androidx.compose.runtime.compositionLocalOf
import com.aibanking.agent.data.ContactView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.LocalInBackPreviewBeneath
import com.aibanking.agent.ui.anim.PAGE_TRANSITION_MILLIS
import com.aibanking.agent.ui.anim.PredictiveBackPreview
import com.aibanking.agent.ui.anim.ShimmerSwitch
import com.aibanking.agent.ui.anim.SkeletonBlock
import com.aibanking.agent.ui.anim.SkeletonRow
import com.aibanking.agent.ui.anim.cascadeItem
import com.aibanking.agent.ui.anim.pageSlideSpec
import com.aibanking.agent.ui.anim.rememberCascadeEntrance
import com.aibanking.agent.ui.components.BankEmptyState
import com.aibanking.agent.ui.components.BankSectionCard
import com.aibanking.agent.ui.theme.BankSpacing
import kotlinx.coroutines.launch

/**
 * 通讯录式联系人界面：列表（搜索 + 拼音首字母分组 + 字母索引条 + FAB）→
 * 详情（大头像 + 资料行 + 直接转账 + 删除确认）→ 新增表单。
 *
 * 是全屏覆盖层而非底部弹层：聊天页的 Scaffold（含全局 Snackbar）在它后面，
 * 所以这里自带一份 SnackbarHost，错误与提示才不会静默丢失。
 */

// SharedTransitionLayout 与当前页的 AnimatedContent scope：列表行与详情页的
// 共享元素分属两个不同 composable，用 CompositionLocal 递交给它们，避免把
// scope 一路透传进每个子组件的签名里。
@OptIn(ExperimentalSharedTransitionApi::class)
private val LocalSharedTransitionScope = compositionLocalOf<SharedTransitionScope?> { null }
private val LocalNavAnimatedVisibilityScope = compositionLocalOf<AnimatedVisibilityScope?> { null }

/**
 * 列表行 ↔ 详情页之间的共享元素绑定。key 相同的一对修饰符在页面切换时由
 * SharedTransitionLayout 做位置/尺寸插值。null scope（理论上不会发生，防御
 * 测试或预览里单独渲染某一页）时静默退化为普通 Modifier。
 *
 * 统一走 sharedBounds（容器变换）而不是 sharedElement：本库版本里后者会在
 * 转场中按动画尺寸逐帧重测量布局，且不支持内容随 bounds 缩放；而
 * sharedBounds 默认的 ScaleToBounds 只按目标约束量一次布局，之后用
 * RenderNode 等比缩放跟住变形的 bounds——对文字友好，转场中零重布局。
 * 头像（40dp ↔ 88dp 同内容）与行容器→资料卡（内容不同）都适用：内容不同
 * 的两侧靠 /2 时长的交叉淡变衔接，不会在变形框里叠影。
 */
@OptIn(ExperimentalSharedTransitionApi::class)
private fun Modifier.contactShared(
    key: String,
    zIndexInOverlay: Float = 0f,
    clipShape: Shape,
): Modifier = composed {
    // 预测性返回的手势预览期，本组件作为底层预览与顶层页面同屏：同 key 的
    // 共享元素在转场外同时可见并无意义，退化为普通绘制；真正提交后仍由
    // AnimatedContent 的转场做完整的共享元素动画。
    if (LocalInBackPreviewBeneath.current) return@composed this
    val shared = LocalSharedTransitionScope.current ?: return@composed this
    val animated = LocalNavAnimatedVisibilityScope.current ?: return@composed this
    with(shared) {
        // 显式与页面转场对齐（默认是刚度中低的 spring，时长不可控，元素会在
        // 页面已经落定之后还在飘）。
        val bounds = BoundsTransform { _, _ ->
            tween(PAGE_TRANSITION_MILLIS, easing = FastOutSlowInEasing)
        }
        val swap = tween<Float>(PAGE_TRANSITION_MILLIS / 2, easing = FastOutSlowInEasing)
        sharedBounds(
            rememberSharedContentState(key),
            animated,
            enter = fadeIn(swap),
            exit = fadeOut(swap),
            boundsTransform = bounds,
            zIndexInOverlay = zIndexInOverlay,
            clipInOverlayDuringTransition = OverlayClip(clipShape),
        )
    }
}

// 共享元素 key 以 contactId 命名空间隔离：同一联系人的头像与容器在列表行、
// 详情页两两配对，不同联系人之间互不干扰。
private fun contactAvatarKey(contactId: String) = "contact-avatar-$contactId"
private fun contactCardKey(contactId: String) = "contact-card-$contactId"

@OptIn(ExperimentalSharedTransitionApi::class)
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
        SharedTransitionLayout {
            // 预测性返回：详情/新增上滑返回时整层跟手缩放/圆角，列表页视差推入。
            // 预览底层里的列表行不带共享元素配对（见 contactShared），真正提交
            // 后仍走下方 AnimatedContent 的共享元素展开回程。
            PredictiveBackPreview(
                enabled = state.contactsScreen != ContactsScreen.LIST,
                onBack = viewModel::backToContactList,
                beneath = { ContactListScreen(state, viewModel, snackbar) },
            ) {
                // 列表 ↔ 详情：元素做共享展开，页面本体只做淡入淡出——整页平移会
                // 与共享元素的位置插值互相打架。列表 ↔ 新增没有共享元素，仍是推入/弹出。
                AnimatedContent(
                    targetState = state.contactsScreen,
                    transitionSpec = {
                        val fade = tween<Float>(PAGE_TRANSITION_MILLIS)
                        if (setOf(initialState, targetState) == setOf(ContactsScreen.LIST, ContactsScreen.DETAIL)) {
                            fadeIn(fade) togetherWith fadeOut(fade)
                        } else {
                            pageSlideSpec(ContactsScreen::ordinal)
                        }
                    },
                    label = "contactsScreen",
                ) { screen ->
                    CompositionLocalProvider(
                        LocalSharedTransitionScope provides this@SharedTransitionLayout,
                        LocalNavAnimatedVisibilityScope provides this,
                    ) {
                        when (screen) {
                            ContactsScreen.LIST -> ContactListScreen(state, viewModel, snackbar)
                            ContactsScreen.DETAIL -> ContactDetailScreen(state, viewModel, snackbar)
                            ContactsScreen.ADD -> ContactAddScreen(state, viewModel, snackbar)
                        }
                    }
                }
            }
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
                         modifier = Modifier.padding(end = BankSpacing.md),
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
                    .padding(horizontal = BankSpacing.md, vertical = BankSpacing.xs)
                    .testTag(UiTags.CONTACT_SEARCH),
            )
            // 流光骨架屏：加载中呈现搜索框/行形占位（45° 高光扫过），数据就绪
            // 200ms 交叉淡入真实列表（含字母索引条）。
            ShimmerSwitch(
                loading = state.panelBusy && sections.isEmpty(),
                modifier = Modifier.fillMaxSize(),
                skeleton = { ContactsSkeleton() },
            ) {
                Box(Modifier.fillMaxSize()) {
                    // 级联入场：首次加载/刷新完成时，可见区前 8 个子项自下而上波浪进场。
                    val cascade = rememberCascadeEntrance(ready = state.contacts.isNotEmpty())
                    LazyColumn(state = listState, modifier = Modifier.fillMaxSize()) {
                    // 扁平序号跨越分组标题与数据行，波浪按整表顺序推入。
                    var flat = 0
                    sections.forEach { section ->
                        val headerIndex = flat
                        flat += 1
                        val firstRowIndex = flat
                        flat += section.contacts.size
                        stickyHeader(key = section.letter) {
                            SectionHeader(section.letter, Modifier.cascadeItem(cascade, headerIndex))
                        }
                        itemsIndexed(section.contacts, key = { _, contact -> contact.contactId }) { offset, contact ->
                            ContactRow(contact, Modifier.cascadeItem(cascade, firstRowIndex + offset)) {
                                viewModel.showContactDetail(contact.contactId)
                            }
                        }
                    }
                    if (sections.isEmpty() && !state.panelBusy) {
                        item {
                            val loadFailure = state.panelError
                            if (loadFailure != null && state.contacts.isEmpty()) {
                                // 请求失败 ≠ 通讯录是空的：报错并可重试，不谎报“还没有联系人”。
                                PanelLoadFailure(loadFailure, viewModel::refreshContacts, Modifier.padding(horizontal = 24.dp))
                            } else {
                                BankEmptyState(
                                    if (state.contactSearch.isBlank()) {
                                        "还没有联系人。点右下角添加，之后可直接说“给 <姓名> 转 300 元”。"
                                    } else {
                                        "没有匹配“${state.contactSearch}”的联系人。"
                                    },
                                )
                            }
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
                }
            }
        }
    }
}

/** 加载中的联系人骨架：搜索框 + 若干行占位（含高光扫过）。 */
@Composable
private fun ContactsSkeleton() {
    Column(Modifier.fillMaxSize()) {
        SkeletonBlock(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
            height = 56.dp,
            corner = 8.dp,
        )
        repeat(7) { SkeletonRow() }
    }
}

@Composable
private fun SectionHeader(letter: String, modifier: Modifier = Modifier) {
    Text(
        text = letter,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.primary,
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHighest)
            .padding(horizontal = BankSpacing.md, vertical = BankSpacing.xs),
    )
}

@Composable
private fun ContactRow(contact: ContactView, modifier: Modifier = Modifier, onClick: () -> Unit) {
    androidx.compose.material3.Card(
        modifier = modifier
            .fillMaxWidth()
            // 行容器 → 详情资料卡的容器变换起点。放在 clickable/padding 之前：
            // 共享 bounds 是整行（含内边距），点击行为与内容不参与插值。
            .contactShared(contactCardKey(contact.contactId), clipShape = MaterialTheme.shapes.medium)
            .clickable(onClick = onClick),
        shape = MaterialTheme.shapes.medium,
        colors = androidx.compose.material3.CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Row(Modifier.fillMaxWidth().heightIn(min = BankSpacing.minTouchTarget).padding(horizontal = BankSpacing.md, vertical = BankSpacing.xs), verticalAlignment = Alignment.CenterVertically) {
            ContactAvatar(contact.name, 40.dp, sharedKey = contactAvatarKey(contact.contactId))
            Column(Modifier.weight(1f).padding(start = BankSpacing.sm), verticalArrangement = Arrangement.spacedBy(2.dp)) {
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
                    .heightIn(min = BankSpacing.minTouchTarget)
                    .clickable { onSelect(letter) }
                    .padding(horizontal = BankSpacing.xs, vertical = 1.dp),
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
    // backToContactList()/removeContact() 清 selectedContactId 是立即生效的，
    // 而退场动画期间本页仍会按新 state 重组：不冻结的话页面会闪成“联系人不
    // 存在”，共享元素也会在半路丢掉配对对象。退场中（contactsScreen 已不是
    // DETAIL）改用最后渲染过的那份联系人；页面在台上时保持诚实——通讯录刷
    // 新后真的移除了该行，就照实提示。
    var lastRendered by remember { mutableStateOf<ContactView?>(null) }
    SideEffect { if (contact != null) lastRendered = contact }
    val rendered = if (state.contactsScreen == ContactsScreen.DETAIL) contact else lastRendered
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
        if (rendered == null) {
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
                .padding(BankSpacing.md),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Spacer(Modifier.height(8.dp))
            ContactAvatar(rendered.name, 88.dp, MaterialTheme.typography.headlineMedium, sharedKey = contactAvatarKey(rendered.contactId))
            Text(rendered.name, style = MaterialTheme.typography.headlineSmall)
            FilledTonalButton(
                onClick = { viewModel.startTransferTo(rendered.name) },
                modifier = Modifier.fillMaxWidth().testTag(UiTags.CONTACT_TRANSFER),
            ) { Text("给 ${rendered.name} 转账") }
            BankSectionCard(modifier = Modifier.contactShared(contactCardKey(rendered.contactId), clipShape = MaterialTheme.shapes.medium)) {
                Column {
                    DetailRow("电话", rendered.phone, mono = true)
                    DetailRow("关系", rendered.relationshipLabel.ifBlank { rendered.relationship })
                    DetailRow("生日", rendered.birthday ?: "未填写")
                    DetailRow(
                        "收款账户",
                        if (rendered.linkedExisting) {
                            "本行账户${rendered.accountName?.let { " · $it" } ?: ""}"
                        } else {
                            "已就绪（可转账）"
                        },
                    )
                    DetailRow("添加时间", rendered.createdAt?.take(10) ?: "—")
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
            text = { Text("确定删除「${rendered?.name ?: ""}」吗？删除后可重新添加。") },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmDelete = false
                        rendered?.contactId?.let(viewModel::removeContact)
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
    Row(Modifier.fillMaxWidth().padding(horizontal = BankSpacing.md, vertical = BankSpacing.xs)) {
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
                .padding(BankSpacing.md),
            verticalArrangement = Arrangement.spacedBy(BankSpacing.sm),
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
            Text("关系", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            // 家人细分后选项变多，FlowRow 自动换行而不是挤在一行或溢出。
            @OptIn(ExperimentalLayoutApi::class)
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(BankSpacing.xs),
                verticalArrangement = Arrangement.spacedBy(BankSpacing.xxs),
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
                modifier = Modifier.fillMaxWidth().heightIn(min = BankSpacing.minTouchTarget).testTag(UiTags.CONTACT_ADD),
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

/**
 * 头像：纯矢量绘制（纯色圆片 + 首字），共享转场零位图解码开销，转场只动
 * placement/alpha（RenderNode 层合成，不逐帧重组），60/120Hz 都能满帧。
 * 若以后换成真实照片，务必异步加载目标显示尺寸的缩略图（例如 Coil 的
 * size(w, h)），不要为 40dp 的列表行解码全尺寸原图——那是转场掉帧的头号来源。
 */
@Composable
private fun ContactAvatar(
    name: String,
    size: Dp,
    textStyle: TextStyle = MaterialTheme.typography.titleMedium,
    sharedKey: String? = null,
) {
    val initial = name.trim().firstOrNull()?.toString() ?: "?"
    val semantic = com.aibanking.agent.ui.theme.LocalBankSemanticColors.current
    val avatarColors = listOf(semantic.chartBenchmark, semantic.negative, semantic.cardVirtual, semantic.riskYellow, semantic.positive, semantic.cardActive)
    Box(
        modifier = (sharedKey?.let { Modifier.contactShared(it, zIndexInOverlay = 1f, clipShape = CircleShape) } ?: Modifier)
            .size(size)
            .clip(CircleShape)
            // floorMod rather than abs: abs(Int.MIN_VALUE) is still negative, so the
            // old expression indexed the palette out of bounds for that one hash.
            .background(avatarColors[Math.floorMod(name.hashCode(), avatarColors.size)]),
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
