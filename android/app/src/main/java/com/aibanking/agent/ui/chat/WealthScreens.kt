package com.aibanking.agent.ui.chat

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibilityScope
import androidx.compose.animation.EnterExitState
import androidx.compose.animation.ExperimentalSharedTransitionApi
import androidx.compose.animation.SharedTransitionLayout
import androidx.compose.animation.SharedTransitionScope
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
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
import androidx.compose.runtime.State
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.addOutline
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.lerp
import androidx.compose.foundation.layout.ColumnScope
import com.aibanking.agent.data.ProductView
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.EmphasizedAccelerateEasing
import com.aibanking.agent.ui.anim.EmphasizedDecelerateEasing
import com.aibanking.agent.ui.anim.LocalInBackPreviewBeneath
import com.aibanking.agent.ui.anim.PredictiveBackPreview
import com.aibanking.agent.ui.anim.ShimmerSwitch
import com.aibanking.agent.ui.anim.SkeletonBlock
import com.aibanking.agent.ui.anim.cascadeItem
import com.aibanking.agent.ui.anim.rememberCascadeEntrance
import java.util.Locale
import java.time.LocalDate
import kotlinx.coroutines.launch

/**
 * 银行风格理财页：产品超市 → 产品详情 → 申购。
 *
 * 布局仿手机银行“理财”频道：顶部风险档案横幅、我的持仓、在售产品卡片
 * （类别角标 + 风险等级徽标 + 业绩比较基准大字 + 起购/期限）。申购不在
 * 页面里做资金操作——填金额后预填聊天话术，回到对话走红色风险审批
 * （短信 + 设备签名），与服务端“所有变更必须过统一授权网关”一致。
 *
 * 业绩比较基准不是收益承诺，页面固定展示风险提示（银行规范口径）。
 */

// SharedTransitionLayout 与当前页 AnimatedContent scope（与联系人页同一套模式）：
// 列表卡片与详情容器分属两个 composable，用 CompositionLocal 递交，避免把
// scope 一路透传进每个子组件的签名里。
@OptIn(ExperimentalSharedTransitionApi::class)
private val LocalWealthSharedTransitionScope = compositionLocalOf<SharedTransitionScope?> { null }
private val LocalWealthNavAnimatedVisibilityScope = compositionLocalOf<AnimatedVisibilityScope?> { null }

// 容器变换规格：350ms 强调曲线；卡片 16dp 圆角 → 详情直角；展开时阴影抬升。
private const val CONTAINER_TRANSFORM_MILLIS = 350
private val CONTAINER_CORNER = 16.dp
private val CONTAINER_MAX_SHADOW = 14.dp

/** 列表卡片 ↔ 详情容器的共享元素 key，以 productId 命名空间隔离。 */
private fun wealthProductKey(productId: String) = "wealth-product-$productId"

/**
 * 容器变换（Material 3 Container Transform）两侧共用的修饰链：
 * sharedBounds 做位置/尺寸插值 + 自定义 OverlayClip 做圆角形状渐变（16dp →
 * 直角，绘制期插值）+ graphicsLayer 投影抬升（“从底层悬浮并展开”）。
 *
 * [expanded] 标记本侧是展开端（详情）还是收拢端（列表卡片）：形变进度按
 * 本侧的 enter/exit 转场计算，两端的圆角/阴影插值方向相反——任意时刻两侧
 * 的遮罩值恒等（展开中 16dp→0，收回中 0→16dp），所以无论转场把哪一侧的
 * OverlayClip 提供给 overlay，视觉都一致。
 *
 * 内容尺寸用 RemeasureToBounds：两侧都是满宽布局，详情内容按动画约束重
 * 布局、只被变形容器逐步裁出，文字不会像 ScaleToBounds 那样整层等比拉伸。
 */
@OptIn(ExperimentalSharedTransitionApi::class)
private fun Modifier.wealthContainerTransform(
    key: String,
    expanded: Boolean,
): Modifier = composed {
    if (LocalInBackPreviewBeneath.current) return@composed this
    val shared = LocalWealthSharedTransitionScope.current ?: return@composed this
    val animated = LocalWealthNavAnimatedVisibilityScope.current ?: return@composed this
    // 形变进度：展开端 Visible=1，收拢端 Visible=0；曲线与 bounds 插值同向。
    val progress = animated.transition.animateFloat(
        transitionSpec = {
            if (targetState == EnterExitState.Visible) {
                tween(CONTAINER_TRANSFORM_MILLIS, easing = EmphasizedDecelerateEasing)
            } else {
                tween(CONTAINER_TRANSFORM_MILLIS, easing = EmphasizedAccelerateEasing)
            }
        },
        targetValueByState = { if ((it == EnterExitState.Visible) == expanded) 1f else 0f },
    )
    val cornerStart = if (expanded) CONTAINER_CORNER else 0.dp
    val cornerEnd = if (expanded) 0.dp else CONTAINER_CORNER
    with(shared) {
        sharedBounds(
            rememberSharedContentState(key),
            animated,
            enter = fadeIn(tween(220, delayMillis = 60, easing = EmphasizedDecelerateEasing)),
            exit = fadeOut(tween(140)),
            resizeMode = SharedTransitionScope.ResizeMode.RemeasureToBounds,
            boundsTransform = { initial, target ->
                // 展开（前进）用 M3 强调减速；收回（返回）用强调加速。
                if (target.height >= initial.height) {
                    tween(CONTAINER_TRANSFORM_MILLIS, easing = EmphasizedDecelerateEasing)
                } else {
                    tween(CONTAINER_TRANSFORM_MILLIS, easing = EmphasizedAccelerateEasing)
                }
            },
            clipInOverlayDuringTransition = MorphingCornerClip(progress, cornerStart, cornerEnd),
        )
    }.graphicsLayer {
        val f = progress.value
        shape = RoundedCornerShape(lerp(cornerStart, cornerEnd, f))
        // 投影随展开抬升（收拢端按 1-f），容器读作从列表层悬浮、铺向详情层。
        shadowElevation = lerp(2.dp, CONTAINER_MAX_SHADOW, if (expanded) f else 1f - f).toPx()
        clip = false
    }
}

/**
 * 容器变换的圆角遮罩：圆角随形变进度在两端值之间插值，在绘制期计算路径
 * （State 只被 draw 读取，不触发重组），并复用同一个 Path 对象。
 */
@OptIn(ExperimentalSharedTransitionApi::class)
private class MorphingCornerClip(
    private val progress: State<Float>,
    private val cornerStart: Dp,
    private val cornerEnd: Dp,
) : SharedTransitionScope.OverlayClip {
    private val path = Path()

    override fun getClipPath(
        state: SharedTransitionScope.SharedContentState,
        bounds: Rect,
        layoutDirection: LayoutDirection,
        density: Density,
    ): Path? {
        val outline = RoundedCornerShape(lerp(cornerStart, cornerEnd, progress.value))
            .createOutline(Size(bounds.width, bounds.height), layoutDirection, density)
        path.rewind()
        path.addOutline(outline)
        path.translate(Offset(bounds.left, bounds.top))
        return path
    }
}

@OptIn(ExperimentalSharedTransitionApi::class)
@Composable
fun WealthUi(state: ChatUiState, viewModel: ChatViewModel) {
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
            // 预测性返回：详情上滑返回时整层跟手缩放/圆角，产品超市视差推入。
            PredictiveBackPreview(
                enabled = state.wealthScreen != WealthScreen.LIST,
                onBack = viewModel::backToWealthList,
                beneath = { WealthListScreen(state, viewModel, snackbar) },
            ) {
                // 产品超市 ↔ 详情：容器变换承载主要演出（卡片展开铺满详情），
                // 页面本体只做淡入淡出——整页平移会与共享容器的位置插值打架。
                AnimatedContent(
                    targetState = state.wealthScreen,
                    transitionSpec = {
                        fadeIn(tween(CONTAINER_TRANSFORM_MILLIS, delayMillis = 40)) togetherWith
                            fadeOut(tween(CONTAINER_TRANSFORM_MILLIS))
                    },
                    label = "wealthScreen",
                ) { screen ->
                    CompositionLocalProvider(
                        LocalWealthSharedTransitionScope provides this@SharedTransitionLayout,
                        LocalWealthNavAnimatedVisibilityScope provides this,
                    ) {
                        when (screen) {
                            WealthScreen.LIST -> WealthListScreen(state, viewModel, snackbar)
                            WealthScreen.DETAIL -> WealthDetailScreen(state, viewModel, snackbar)
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun WealthListScreen(state: ChatUiState, viewModel: ChatViewModel, snackbar: SnackbarHostState) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("理财") },
                navigationIcon = {
                    IconButton(onClick = viewModel::closeWealth) { Icon(Icons.Default.Close, contentDescription = "返回聊天") }
                },
                actions = {
                    Text(
                        "${state.wealthProducts.size} 款在售",
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.padding(end = 16.dp),
                    )
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        // 流光骨架屏：加载中呈现横幅/持仓/产品卡形占位（45° 高光扫过），数据
        // 就绪 200ms 交叉淡入真实列表。
        ShimmerSwitch(
            loading = state.panelBusy && state.wealthProducts.isEmpty(),
            modifier = Modifier.fillMaxSize(),
            skeleton = { WealthSkeleton(Modifier.fillMaxSize().padding(padding)) },
        ) {
            val loadFailure = state.panelError
            if (loadFailure != null && state.wealthProducts.isEmpty()) {
                // 一次失败的加载会把产品、持仓、风险档案一起留空；此时页面若继续渲染
                // “尚未完成风险测评 / 暂无持仓”，就是把“没问到核心”说成了用户的资产状况。
                Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                    PanelLoadFailure(loadFailure, viewModel::refreshWealth)
                }
                return@ShimmerSwitch
            }
            // 级联入场：数据就绪/刷新完成时，可见区前 8 个子项自下而上波浪进场。
            val cascade = rememberCascadeEntrance(
                ready = state.wealthProducts.isNotEmpty() || state.wealthHoldings.isNotEmpty(),
            )
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).testTag(UiTags.WEALTH_LIST),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // 扁平序号跨越横幅、标题与卡片，波浪按整页顺序推入。
            var flat = 0
            item {
                RiskProfileBanner(state, viewModel, Modifier.cascadeItem(cascade, flat++))
            }
            item {
                SectionHeader("我的持仓", Modifier.cascadeItem(cascade, flat++))
            }
            if (state.wealthHoldings.isEmpty()) {
                item {
                    Text(
                        "暂无持仓。选择下方产品申购后，这里会展示持仓与本金。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                items(
                    count = state.wealthHoldings.size,
                    key = { state.wealthHoldings[it].holdingId },
                ) { index ->
                    val holding = state.wealthHoldings[index]
                    Card(Modifier.fillMaxWidth().cascadeItem(cascade, flat++)) {
                        Row(
                            Modifier.fillMaxWidth().padding(12.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(holding.productName, style = MaterialTheme.typography.titleSmall)
                                Text(
                                    holding.holdingId,
                                    style = MaterialTheme.typography.labelSmall,
                                    fontFamily = FontFamily.Monospace,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Text(
                                "¥%,.2f".format(Locale.CHINA, holding.principal),
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                            )
                        }
                    }
                }
            }
            item {
                SectionHeader("在售产品", Modifier.cascadeItem(cascade, flat++))
            }
            items(
                count = state.wealthProducts.size,
                // item key 稳定身份：数据刷新后共享元素仍能按 productId 找到卡片。
                key = { state.wealthProducts[it].productId },
            ) { index ->
                val product = state.wealthProducts[index]
                ProductCard(product, Modifier.cascadeItem(cascade, flat++)) {
                    viewModel.showProductDetail(product.productId)
                }
            }
            item {
                Text(
                    "业绩比较基准不代表未来收益承诺，理财非存款、产品有风险、投资须谨慎。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.cascadeItem(cascade, flat++).padding(top = 4.dp),
                )
            }
            }
        }
    }
}

/** 加载中的理财骨架：横幅/持仓/标题/产品卡的排版占位（含高光扫过）。 */
@Composable
private fun WealthSkeleton(modifier: Modifier = Modifier) {
    Column(modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        SkeletonBlock(height = 64.dp, corner = 12.dp)
        SkeletonBlock(width = 88.dp, height = 16.dp, corner = 4.dp)
        SkeletonBlock(height = 56.dp, corner = 12.dp)
        SkeletonBlock(width = 88.dp, height = 16.dp, corner = 4.dp)
        repeat(2) {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    SkeletonBlock(width = 76.dp, height = 18.dp, corner = 4.dp)
                    SkeletonBlock(width = 30.dp, height = 18.dp, corner = 4.dp)
                }
                SkeletonBlock(width = 210.dp, height = 18.dp)
                SkeletonBlock(width = 150.dp, height = 14.dp)
            }
        }
    }
}

@Composable
private fun ProductCard(product: ProductView, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Card(
        modifier = modifier
            .fillMaxWidth()
            .testTag(UiTags.wealthProduct(product.productId))
            .wealthContainerTransform(wealthProductKey(product.productId), expanded = false)
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(CONTAINER_CORNER),
        // 容器变换两端用同一底色：卡片白底形变铺满成详情白底，交叉淡变期间
        // 容器看起来始终不透明。
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.fillMaxWidth().padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                CategoryChip(product.category ?: "理财产品")
                RiskBadge(product.riskLevel)
            }
            Text(product.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text(
                product.benchmark ?: "业绩比较基准以产品说明书为准",
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.primary,
                fontWeight = FontWeight.Bold,
            )
            Text(
                buildString {
                    append("起购 ¥%,.0f".format(Locale.CHINA, product.minAmount))
                    product.term?.let { append(" · $it") }
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun RiskProfileBanner(state: ChatUiState, viewModel: ChatViewModel, modifier: Modifier = Modifier) {
    val profile = state.riskProfile
    Card(
        colors = CardDefaults.cardColors(
            containerColor = if (profile == null) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.primaryContainer,
        ),
        modifier = modifier.fillMaxWidth(),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    if (profile == null) "尚未完成风险测评" else "风险档案：${profileLabel(profile.riskProfile)}",
                    style = MaterialTheme.typography.titleSmall,
                )
                Text(
                    if (profile == null) "申购前需完成风险评估问卷" else "测评结果决定可申购的产品风险等级",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            TextButton(
                onClick = {
                    viewModel.onInputChange(if (profile == null) "我想做风险测评" else "我想重新做风险测评")
                    viewModel.closeWealth()
                },
            ) { Text(if (profile == null) "去测评" else "重新测评") }
        }
    }
}

// ---------------------------------------------------------------------------
// detail
// ---------------------------------------------------------------------------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun WealthDetailScreen(state: ChatUiState, viewModel: ChatViewModel, snackbar: SnackbarHostState) {
    val product = state.selectedProduct
    // backToWealthList() 清 selectedProductId 是立即生效的，退场动画期间本页
    // 会按新 state 重组：不冻结的话页面闪成“产品不存在”，共享容器也会在半
    // 路丢掉配对对象、收拢形变直接消失。退场中（wealthScreen 已不是 DETAIL）
    // 改用最后渲染过的那份产品；页面在台上时保持诚实——真的下架就照实提示。
    var lastRendered by remember { mutableStateOf<ProductView?>(null) }
    SideEffect { if (product != null) lastRendered = product }
    val rendered = if (state.wealthScreen == WealthScreen.DETAIL) product else lastRendered
    var selectedRange by remember { mutableStateOf("近1年") }
    var favorite by remember { mutableStateOf(false) }
    var historyExpanded by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    fun notice(message: String) { scope.launch { snackbar.showSnackbar(message) } }
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column(Modifier.testTag(UiTags.WEALTH_HEADER)) {
                        Text(rendered?.name ?: "产品详情", maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                        Text(rendered?.productId ?: "", style = MaterialTheme.typography.labelSmall)
                    }
                },
                navigationIcon = {
                    IconButton(onClick = viewModel::backToWealthList) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回列表") }
                },
                actions = {
                    TextButton(onClick = { notice("分享功能为演示版入口") }) { Text("分享") }
                    TextButton(onClick = { favorite = !favorite; notice(if (favorite) "已加入自选" else "已取消自选") }) { Text(if (favorite) "★" else "☆") }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            if (rendered != null) {
                PurchaseBar(state, viewModel, rendered)
            }
        },
    ) { padding ->
        if (rendered == null) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                Text("产品不存在或已下架。", style = MaterialTheme.typography.bodyMedium)
            }
            return@Scaffold
        }
        // 详情容器：列表卡片形变铺满的落点（顶栏与申购栏不属于容器，随页面
        // 淡入）。画与卡片相同的底色，位置/尺寸由 sharedBounds 与卡片插值；
        // 滚动容器在共享节点内，内容随展开被逐步裁出而不是拉伸。
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .wealthContainerTransform(wealthProductKey(rendered.productId), expanded = true)
                .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.22f))
                .verticalScroll(rememberScrollState())
                .testTag(UiTags.WEALTH_DETAIL)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            SectionCard("核心收益", Modifier.testTag(UiTags.WEALTH_SUMMARY)) {
                Row(Modifier.fillMaxWidth().testTag(UiTags.WEALTH_SNAPSHOT), horizontalArrangement = Arrangement.SpaceBetween) {
                    Metric(
                        rendered.annualReturnPct?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—",
                        "近1年涨跌幅",
                        returnColor(rendered.annualReturnPct),
                    )
                    Metric(
                        rendered.recentMonthReturnPct?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—",
                        "近1月涨幅",
                        returnColor(rendered.recentMonthReturnPct),
                    )
                    Metric(
                        rendered.latestNav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—",
                        "最新单位净值",
                        MaterialTheme.colorScheme.primary,
                    )
                }
                Text(
                    "净值日期 ${rendered.netValueDate ?: rendered.asOfDate ?: "暂无"} · 日涨跌 ${rendered.dailyChangePct?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "暂无数据"}",
                    color = returnColor(rendered.dailyChangePct),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    CategoryChip(rendered.riskLevel)
                    CategoryChip(rendered.term ?: "期限以说明书为准")
                    CategoryChip("%,.0f元起购".format(Locale.CHINA, rendered.minAmount))
                }
                Text("数据来源：${rendered.dataSource ?: "未提供"}（非实时）", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Card(Modifier.fillMaxWidth().testTag(UiTags.WEALTH_PROMO).clickable { notice(rendered.promoTitle ?: "财富日限时加息，申购费率1折") }) {
                Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.SpaceBetween) { Column(Modifier.weight(1f)) { Text(rendered.promoTitle ?: "财富日限时加息", fontWeight = FontWeight.Bold); Text(rendered.promoSubtitle ?: "申购费率1折，限时专享", style = MaterialTheme.typography.labelSmall) }; Text("›") }
            }
            SectionCard(
                "业绩走势",
                headerAction = {
                    TextButton(onClick = { notice("全屏与产品对比为演示版入口") }) {
                        Text("查看全屏 / 对比", style = MaterialTheme.typography.labelSmall)
                    }
                },
            ) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                    listOf("近1月", "近3月", "近6月", "近1年", "成立以来").forEach { range ->
                        TextButton(
                            onClick = { selectedRange = range },
                            modifier = Modifier.weight(1f).testTag(UiTags.wealthRange(range)),
                            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 1.dp),
                        ) {
                            Text(if (range == selectedRange) "● $range" else range, style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
                WealthChart(rendered, selectedRange, Modifier.fillMaxWidth().testTag(UiTags.WEALTH_CHART))
                Text("归一化走势：产品净值 / 业绩比较基准 · Mock 数据，非实时", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            SectionCard("历史业绩", Modifier.testTag(UiTags.WEALTH_HISTORY)) {
                Row(Modifier.fillMaxWidth()) {
                    listOf("阶段区间", "本产品收益", "同类平均", "同类排名").forEach { Text(it, Modifier.weight(1f), style = MaterialTheme.typography.labelSmall) }
                }
                if (rendered.performanceHistory.isEmpty()) Text("暂无历史业绩数据")
                rendered.performanceHistory.take(if (historyExpanded) Int.MAX_VALUE else 3).forEach { row ->
                    Row(Modifier.fillMaxWidth()) {
                        Text(row.period.ifBlank { "—" }, Modifier.weight(1f))
                        Text(row.productReturnPct?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—", Modifier.weight(1f), color = returnColor(row.productReturnPct))
                        val peer = row.peerAveragePct ?: row.benchmarkReturnPct
                        Text(peer?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—", Modifier.weight(1f), color = returnColor(peer))
                        Text(row.rank ?: "—", Modifier.weight(1f))
                    }
                }
                TextButton(onClick = { historyExpanded = !historyExpanded }, modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_HISTORY_TOGGLE)) { Text(if (historyExpanded) "收起" else "展开更多") }
            }
            SectionCard("投资特征", Modifier.testTag(UiTags.WEALTH_PROFILE)) {
                if (rendered.analysisMetrics.isEmpty()) {
                    Text("暂无分析指标")
                } else {
                    rendered.analysisMetrics.chunked(2).forEach { pair ->
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            pair.forEach { metric -> Card(Modifier.weight(1f)) { Column(Modifier.padding(8.dp)) { Text(metric.label.ifBlank { "未命名指标" }); Text(metric.value ?: "${metric.score ?: 0}/5", fontWeight = FontWeight.Bold); Text(metric.description ?: "暂无说明", style = MaterialTheme.typography.labelSmall) } } }
                            if (pair.size == 1) Spacer(Modifier.weight(1f))
                        }
                    }
                }
            }
            SectionCard("投资经理与管理机构", Modifier.testTag(UiTags.WEALTH_MANAGER)) {
                Box(Modifier.width(44.dp).height(44.dp).clip(androidx.compose.foundation.shape.CircleShape).background(MaterialTheme.colorScheme.primaryContainer), contentAlignment = Alignment.Center) { Text(rendered.manager?.name?.take(1) ?: "经", fontWeight = FontWeight.Bold) }
                Text(rendered.manager?.name ?: "暂无经理信息", fontWeight = FontWeight.Bold)
                Text(rendered.manager?.title ?: rendered.manager?.bio ?: "暂无履历说明", style = MaterialTheme.typography.bodySmall)
                Text(
                    "任职：${rendered.manager?.tenureSince ?: "—"} · 从业：${rendered.manager?.experienceYears?.let { "%.1f 年".format(Locale.CHINA, it) } ?: "—"} · 管理规模 ${rendered.manager?.managedScaleBillion?.let { "%.2f 亿元".format(Locale.CHINA, it) } ?: "—"} · 年化 ${rendered.manager?.annualizedYieldPct?.let { "%.2f%%".format(Locale.CHINA, it) } ?: "—"}",
                    style = MaterialTheme.typography.labelSmall,
                )
                Text("管理人：${rendered.managerCompany ?: "—"} · 成立${rendered.institutionFoundedYears ?: "—"}年 · AUM ${rendered.institutionAumBillion?.let { "%.2f 亿元".format(Locale.CHINA, it) } ?: "—"} · ${rendered.institutionRating ?: "综合评级—"}", style = MaterialTheme.typography.bodySmall)
            }
            SectionCard("资产配置与持仓", Modifier.testTag(UiTags.WEALTH_ALLOCATION)) {
                if (rendered.assetAllocation.isEmpty()) {
                    Text("暂无资产配置数据")
                } else {
                    rendered.assetAllocation.forEach { allocation ->
                        Text("${allocation.name.ifBlank { "未分类" }}  ${allocation.percentage?.let { "%.2f%%".format(Locale.CHINA, it) } ?: "—"}")
                        Box(Modifier.fillMaxWidth().height(5.dp).background(MaterialTheme.colorScheme.primary.copy(alpha = .18f), RoundedCornerShape(4.dp))) {
                            Box(Modifier.fillMaxWidth(((allocation.percentage ?: 0.0).coerceIn(0.0, 100.0) / 100.0).toFloat()).height(5.dp).background(MaterialTheme.colorScheme.primary, RoundedCornerShape(4.dp)))
                        }
                    }
                    Text("重仓前五项", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    rendered.topHoldings.take(5).forEachIndexed { index, holding ->
                        Text("${index + 1}. ${holding.name} · ${holding.category ?: "其他"}  ${holding.percentage?.let { "%.2f%%".format(Locale.CHINA, it) } ?: "—"}", style = MaterialTheme.typography.bodySmall, modifier = Modifier.testTag(if (index == 0) UiTags.WEALTH_TOP_HOLDINGS else "wealth_holding_$index"))
                    }
                }
            }
            SectionCard("产品档案") {
                DetailRow("成立日期", rendered.establishedAt ?: "暂无")
                DetailRow("产品规模", rendered.scaleBillion?.let { "%.2f 亿元".format(Locale.CHINA, it) } ?: "暂无")
                DetailRow("管理人", rendered.managerCompany ?: "暂无")
            }
            SectionCard("合规与披露", Modifier.testTag(UiTags.WEALTH_DISCLOSURE)) {
                listOf("产品公告与运作季报" to (rendered.announcement ?: "暂无公告"), "申赎规则" to "买入确认${rendered.confirmTime ?: "T+1"} · ${rendered.redemptionRule ?: "以说明书为准"} · 费率${rendered.feeRate?.let { "%.2f%%".format(Locale.CHINA, it) } ?: "—"}", "风险揭示书与法律文件" to (rendered.summary ?: "请阅读完整法律文件")).forEachIndexed { index, (title, body) ->
                    DisclosureRow(
                        title = title,
                        body = body,
                        rowTestTag = if (index == 1) UiTags.WEALTH_DISCLOSURE_RULES else null,
                        bodyTestTag = if (index == 1) UiTags.WEALTH_DISCLOSURE_BODY else null,
                        alwaysShowBody = index == 1,
                    )
                }
            }
            SectionCard("交易规则", Modifier.testTag(UiTags.WEALTH_RULES)) {
                DetailRow("买入", rendered.purchaseRule ?: "暂无说明")
                DetailRow("赎回", rendered.redemptionRule ?: "暂无说明")
                DetailRow("确认时间", rendered.confirmTime ?: "暂无说明")
                DetailRow("费率", rendered.feeRate?.let { "%.2f%%".format(Locale.CHINA, it) } ?: "暂无")
            }
            SectionCard("产品概要") {
                Text(rendered.summary ?: "暂无产品概要", style = MaterialTheme.typography.bodySmall)
                Text("公告：${rendered.announcement ?: "暂无公告"}", style = MaterialTheme.typography.bodySmall)
            }
            Text(
                "风险提示：页面行情、排名与持仓均为 Mock 演示数据，不构成投资建议。" +
                    "业绩比较基准不代表未来收益承诺，理财产品过往业绩不代表未来表现。" +
                    "申购需通过风险测评并完成服务端多因子验证。",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag(UiTags.WEALTH_DISCLAIMER),
            )
        }
    }
}

@Composable
private fun DisclosureRow(
    title: String,
    body: String,
    rowTestTag: String?,
    bodyTestTag: String?,
    alwaysShowBody: Boolean = false,
) {
    var expanded by remember(title) { mutableStateOf(false) }
    Column {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(8.dp))
                .clickable { expanded = !expanded }
                .then(rowTestTag?.let { Modifier.testTag(it) } ?: Modifier)
                .padding(horizontal = 8.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(title)
            Text(if (expanded) "⌄" else "›")
        }
        if (expanded || alwaysShowBody) {
            Text(
                body,
                style = MaterialTheme.typography.bodySmall,
                modifier = (bodyTestTag?.let { Modifier.testTag(it) } ?: Modifier)
                    .padding(horizontal = 8.dp, vertical = 4.dp),
            )
        }
    }
}

@Composable
private fun SectionCard(
    title: String,
    modifier: Modifier = Modifier,
    headerAction: (@Composable () -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(
            Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                Spacer(Modifier.weight(1f))
                headerAction?.invoke()
            }
            content()
        }
    }
}

@Composable
private fun Metric(value: String, label: String, color: Color) {
    Column(Modifier.width(96.dp)) {
        Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, color = color)
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun WealthChart(product: ProductView, range: String, modifier: Modifier = Modifier) {
    val all = product.navSeries.filter { it.nav?.isFinite() == true && it.benchmarkNav?.isFinite() == true }
    val points = rangePoints(all, range)
    var selectedIndex by remember(points) { mutableStateOf(points.lastIndex.coerceAtLeast(0)) }
    Column(modifier) {
        if (points.isNotEmpty()) {
            val point = points[selectedIndex.coerceIn(0, points.lastIndex)]
            Text("${point.date}  净值 ${point.nav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—"}  基准 ${point.benchmarkNav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—"}", style = MaterialTheme.typography.labelSmall)
        }
        Canvas(Modifier.fillMaxWidth().height(150.dp).pointerInput(points) {
            detectDragGestures { change, _ ->
                change.consume()
                if (points.size > 1) selectedIndex = ((change.position.x / size.width) * (points.size - 1)).toInt().coerceIn(0, points.lastIndex)
            }
        }) {
        if (points.size < 2) return@Canvas
        val min = points.minOf { minOf(it.nav!!, it.benchmarkNav!!) }
        val span = (points.maxOf { maxOf(it.nav!!, it.benchmarkNav!!) } - min)
            .takeIf { it > 0.0 } ?: 1.0
        fun x(i: Int) = i * size.width / (points.size - 1)
        fun y(value: Double) = size.height - ((value - min) / span * size.height).toFloat()
        for (i in 0 until points.lastIndex) {
            drawLine(
                Color(0xFFE5484D),
                Offset(x(i), y(points[i].nav!!)),
                Offset(x(i + 1), y(points[i + 1].nav!!)),
                strokeWidth = 4f,
            )
            drawLine(
                Color(0xFF78909C),
                Offset(x(i), y(points[i].benchmarkNav!!)),
                Offset(x(i + 1), y(points[i + 1].benchmarkNav!!)),
                strokeWidth = 3f,
            )
        }
        val index = selectedIndex.coerceIn(0, points.lastIndex)
        drawLine(Color.DarkGray, Offset(x(index), 0f), Offset(x(index), size.height), strokeWidth = 2f)
        drawCircle(Color(0xFFE5484D), 6f, Offset(x(index), y(points[index].nav!!)))
        drawCircle(Color(0xFF78909C), 5f, Offset(x(index), y(points[index].benchmarkNav!!)))
        }
    }
}

private fun rangePoints(points: List<com.aibanking.agent.data.NavPointView>, range: String): List<com.aibanking.agent.data.NavPointView> {
    if (points.isEmpty()) return emptyList()
    val parsed = points.mapNotNull { point ->
        runCatching { LocalDate.parse(point.date) to point }.getOrNull()
    }
    if (parsed.size != points.size) {
        val fallbackCount = when (range) {
            "近1月" -> 5
            "近3月" -> 10
            else -> points.size
        }.coerceAtLeast(1)
        return points.takeLast(fallbackCount)
    }
    val end = parsed.maxOf { it.first }
    val start = when (range) {
        "近1月" -> end.minusMonths(1)
        "近3月" -> end.minusMonths(3)
        "近6月" -> end.minusMonths(6)
        "近1年" -> end.minusMonths(12)
        "成立以来" -> LocalDate.MIN
        else -> end.minusMonths(12)
    }
    return parsed.filter { it.first >= start && it.first <= end }.map { it.second }.ifEmpty { points.takeLast(1) }
}

private fun returnColor(value: Double?): Color = when {
    value == null || !value.isFinite() || value == 0.0 -> Color.Unspecified
    value > 0.0 -> Color(0xFFE5484D)
    else -> Color(0xFF2E8B57)
}

@Composable
private fun PurchaseBar(state: ChatUiState, viewModel: ChatViewModel, product: ProductView) {
    val profile = state.riskProfile
    Surface(shadowElevation = 8.dp, color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(Modifier.fillMaxWidth().testTag(UiTags.WEALTH_BOTTOM_BAR), horizontalArrangement = Arrangement.SpaceEvenly) {
                TextButton(onClick = { viewModel.onInputChange("请根据我的风险档案提供${product.name}智能投顾建议"); viewModel.closeWealth() }) { Text("智能投顾") }
                TextButton(onClick = { viewModel.onInputChange("请帮我试算${product.name}收益"); viewModel.closeWealth() }) { Text("收益试算") }
                TextButton(onClick = { viewModel.onInputChange("我需要理财客服咨询"); viewModel.closeWealth() }) { Text("客服咨询") }
            }
            when {
                profile == null -> {
                    Text("申购前需完成风险测评问卷。", style = MaterialTheme.typography.bodySmall)
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = { viewModel.onInputChange("请设置${product.name}定投"); viewModel.closeWealth() }, modifier = Modifier.weight(1f)) { Text("定投买入") }
                        Button(onClick = { viewModel.onInputChange("我想做风险测评"); viewModel.closeWealth() }, modifier = Modifier.weight(1f).testTag(UiTags.WEALTH_TO_TEST)) { Text("立即购买") }
                    }
                }
                (profileRank(profile.riskProfile) ?: 0) < productRank(product.riskLevel) -> {
                    Text(
                        "当前风险档案（${profileLabel(profile.riskProfile)}）不适合该产品的风险等级 ${product.riskLevel}。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = { viewModel.onInputChange("请设置${product.name}定投"); viewModel.closeWealth() }, modifier = Modifier.weight(1f)) { Text("定投买入") }
                        Button(onClick = {}, enabled = false, modifier = Modifier.weight(1f)) { Text("立即购买") }
                    }
                }
                else -> {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(
                            value = state.buyAmount,
                            onValueChange = viewModel::onBuyAmountChange,
                            label = { Text("申购金额（起购 ¥%,.0f）".format(Locale.CHINA, product.minAmount)) },
                            singleLine = true,
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            modifier = Modifier.weight(1f).testTag(UiTags.WEALTH_AMOUNT),
                        )
                        TextButton(onClick = { viewModel.onInputChange("请设置${product.name}定投"); viewModel.closeWealth() }) { Text("定投买入") }
                        Button(
                            onClick = { viewModel.startPurchase(product, state.buyAmount) },
                            enabled = state.canBuySelected && !state.panelBusy,
                            modifier = Modifier.testTag(UiTags.WEALTH_BUY),
                        ) { Text("立即购买") }
                    }
                    Text(
                        "点击后回到对话确认：申购属于高风险操作，需短信验证码与设备签名。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// shared pieces
// ---------------------------------------------------------------------------

@Composable
private fun SectionHeader(title: String, modifier: Modifier = Modifier) {
    Text(
        text = title,
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.Bold,
        modifier = modifier.padding(top = 6.dp),
    )
}

@Composable
private fun CategoryChip(text: String) {
    Box(
        Modifier
            .background(MaterialTheme.colorScheme.secondaryContainer, RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun RiskBadge(level: String) {
    val color = when (level) {
        "R1" -> Color(0xFF2E7D32)
        "R2" -> Color(0xFF1565C0)
        "R3" -> Color(0xFFE65100)
        else -> Color(0xFFC62828)
    }
    Box(
        Modifier
            .background(color, RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    ) {
        Text(level, style = MaterialTheme.typography.labelSmall, color = Color.White, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun DetailRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(96.dp),
        )
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}

private fun profileLabel(profile: String): String = when (profile) {
    "CONSERVATIVE", "C1" -> "保守型"
    "C2" -> "稳健型"
    "BALANCED", "C3" -> "平衡型"
    "C4" -> "进取型"
    "AGGRESSIVE", "C5" -> "激进型"
    else -> profile
}
