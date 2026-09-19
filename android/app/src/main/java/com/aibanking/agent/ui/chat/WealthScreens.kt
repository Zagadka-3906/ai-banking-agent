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
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.outlined.Star
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.sp
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
    var lastRendered by remember { mutableStateOf<ProductView?>(null) }
    SideEffect { if (product != null) lastRendered = product }
    val rendered = if (state.wealthScreen == WealthScreen.DETAIL) product else lastRendered
    var selectedRange by remember { mutableStateOf("近1年") }
    var selectedTab by remember { mutableStateOf("单位净值") }
    var favorite by remember { mutableStateOf(false) }
    var historyExpanded by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    fun notice(message: String) { scope.launch { snackbar.showSnackbar(message) } }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column(Modifier.testTag(UiTags.WEALTH_HEADER)) {
                        Text(
                            rendered?.name ?: "产品详情",
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                        )
                        Text(
                            rendered?.productId ?: "",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            fontFamily = FontFamily.Monospace,
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = viewModel::backToWealthList) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回列表")
                    }
                },
                actions = {
                    IconButton(onClick = { notice("智能诊断为演示版功能") }) {
                        Icon(Icons.Default.Search, contentDescription = "诊断", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    IconButton(onClick = {
                        favorite = !favorite
                        notice(if (favorite) "已加入自选" else "已取消自选")
                    }) {
                        Icon(
                            if (favorite) Icons.Filled.Star else Icons.Outlined.Star,
                            contentDescription = "自选",
                            tint = if (favorite) Color(0xFFFFB300) else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    IconButton(onClick = { notice("分享功能为演示版入口") }) {
                        Icon(Icons.Default.Share, contentDescription = "分享", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
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

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .wealthContainerTransform(wealthProductKey(rendered.productId), expanded = true)
                .background(Color(0xFFF5F6F8))
                .verticalScroll(rememberScrollState())
                .testTag(UiTags.WEALTH_DETAIL)
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // 1. Hero 收益率与净值快照卡片
            Card(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_SUMMARY),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = Color.White),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(
                        Modifier.fillMaxWidth().testTag(UiTags.WEALTH_SNAPSHOT),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.Bottom,
                    ) {
                        Column(Modifier.weight(1.3f)) {
                            val displayReturn = rendered.annualReturnPct ?: rendered.annualizedYield?.let { it * 100 }
                            Text(
                                displayReturn?.let { "%+.2f%%".format(Locale.CHINA, it).removePrefix("+") } ?: "—",
                                fontSize = 32.sp,
                                fontWeight = FontWeight.ExtraBold,
                                color = returnColor(displayReturn),
                            )
                            Text(
                                if (rendered.annualReturnPct != null) "近1年收益率" else "业绩比较基准",
                                style = MaterialTheme.typography.labelSmall,
                                color = Color(0xFF8C9098),
                            )
                        }
                        Column(Modifier.weight(1.0f)) {
                            val daily = rendered.dailyChangePct ?: rendered.recentMonthReturnPct
                            Text(
                                daily?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—",
                                fontSize = 18.sp,
                                fontWeight = FontWeight.Bold,
                                color = returnColor(daily),
                            )
                            Text(
                                "日增长率",
                                style = MaterialTheme.typography.labelSmall,
                                color = Color(0xFF8C9098),
                            )
                        }
                        Column(Modifier.weight(1.0f)) {
                            Text(
                                rendered.latestNav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—",
                                fontSize = 18.sp,
                                fontWeight = FontWeight.Bold,
                                color = Color(0xFF22252A),
                            )
                            Text(
                                "单位净值 (${rendered.netValueDate ?: rendered.asOfDate ?: "09-15"})",
                                style = MaterialTheme.typography.labelSmall,
                                color = Color(0xFF8C9098),
                            )
                        }
                    }

                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        CategoryChip("${rendered.category ?: "理财"} · ${rendered.riskLevel}")
                        CategoryChip(rendered.term ?: "灵活期限")
                        Box(
                            Modifier
                                .background(Color(0xFFFFF0ED), RoundedCornerShape(4.dp))
                                .padding(horizontal = 6.dp, vertical = 2.dp),
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text(
                                    "买入费率: %.2f%%".format(Locale.CHINA, rendered.discountFeeRate ?: 0.10),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = Color(0xFFE53935),
                                    fontWeight = FontWeight.SemiBold,
                                )
                                Text(
                                    "%.2f%%".format(Locale.CHINA, rendered.originalFeeRate ?: 1.00),
                                    style = MaterialTheme.typography.labelSmall.copy(textDecoration = TextDecoration.LineThrough),
                                    color = Color(0xFF9E9E9E),
                                )
                            }
                        }
                    }

                    Text(
                        "%,.0f元起购 · %s · 数据来源：%s".format(
                            Locale.CHINA,
                            rendered.minAmount,
                            rendered.term ?: "以说明书为准",
                            rendered.dataSource ?: "MOCK_FIXTURE（非实时）",
                        ),
                        style = MaterialTheme.typography.labelSmall,
                        color = Color(0xFF8C9098),
                    )

                    // 晨星评级 / 金牛奖黄金横幅
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .testTag(UiTags.WEALTH_PROMO)
                            .clickable { notice(rendered.promoTitle ?: "财富日限时加息，申购费率1折") },
                        shape = RoundedCornerShape(8.dp),
                        colors = CardDefaults.cardColors(containerColor = Color(0xFFFFF9E6)),
                        border = BorderStroke(1.dp, Color(0xFFFFE8A3)),
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(6.dp),
                                modifier = Modifier.weight(1f),
                            ) {
                                Text("⭐", fontSize = 13.sp)
                                Text(
                                    rendered.ratingAgency ?: "晨星评级 ★★★★★",
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = FontWeight.Bold,
                                    color = Color(0xFFB78103),
                                )
                                Text("·", color = Color(0xFFD4A340))
                                Text("🏆", fontSize = 13.sp)
                                Text(
                                    rendered.awardTitle ?: rendered.promoTitle ?: "金牛基金奖 · 卓越回报标杆",
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = FontWeight.Bold,
                                    color = Color(0xFFB78103),
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                            Text("›", color = Color(0xFFB78103), fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }

            // 2. 业绩走势图卡片
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = Color.White),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("业绩走势", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                            listOf("单位净值", "累计净值", "同类排名").forEach { tab ->
                                Text(
                                    tab,
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = if (tab == selectedTab) FontWeight.Bold else FontWeight.Normal,
                                    color = if (tab == selectedTab) Color(0xFFE53935) else Color(0xFF6B7280),
                                    modifier = Modifier
                                        .clickable { selectedTab = tab }
                                        .padding(horizontal = 4.dp, vertical = 2.dp),
                                )
                            }
                        }
                    }

                    // 图例
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        val productReturn = rendered.annualReturnPct ?: 19.46
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                            Box(Modifier.width(8.dp).height(8.dp).background(Color(0xFFE53935), CircleShape))
                            Text("本产品 %+.2f%%".format(Locale.CHINA, productReturn), style = MaterialTheme.typography.labelSmall, color = Color(0xFFE53935), fontWeight = FontWeight.SemiBold)
                        }
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                            Box(Modifier.width(8.dp).height(8.dp).background(Color(0xFF78909C), CircleShape))
                            Text("业绩基准 +10.28%", style = MaterialTheme.typography.labelSmall, color = Color(0xFF607D8B))
                        }
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                            Box(Modifier.width(8.dp).height(8.dp).background(Color(0xFF26A69A), CircleShape))
                            Text("同类平均 +8.45%", style = MaterialTheme.typography.labelSmall, color = Color(0xFF00897B))
                        }
                    }

                    WealthChart(rendered, selectedRange, Modifier.fillMaxWidth().testTag(UiTags.WEALTH_CHART))

                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                        listOf("近1月", "近3月", "近6月", "近1年", "成立以来").forEach { range ->
                            TextButton(
                                onClick = { selectedRange = range },
                                modifier = Modifier.weight(1f).testTag(UiTags.wealthRange(range)),
                                contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 1.dp),
                            ) {
                                Text(
                                    if (range == selectedRange) "● $range" else range,
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = if (range == selectedRange) FontWeight.Bold else FontWeight.Normal,
                                    color = if (range == selectedRange) Color(0xFFE53935) else MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }

                    Text(
                        "归一化走势：产品净值 / 业绩比较基准 · Mock 演示数据，非实时",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // 3. 历史业绩 / 阶段涨幅表格
            Card(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_HISTORY),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = Color.White),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Text("阶段涨幅", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, color = Color(0xFFE53935))
                            Text("历史净值", style = MaterialTheme.typography.titleMedium, color = Color(0xFF8C9098))
                        }
                    }

                    Row(
                        Modifier.fillMaxWidth().background(Color(0xFFF9FAFB), RoundedCornerShape(4.dp)).padding(vertical = 6.dp, horizontal = 4.dp),
                    ) {
                        listOf("阶段区间", "涨跌幅", "同类平均", "同类排名").forEachIndexed { i, col ->
                            Text(
                                col,
                                Modifier.weight(if (i == 0 || i == 3) 1.1f else 1.0f),
                                style = MaterialTheme.typography.labelSmall,
                                color = Color(0xFF6B7280),
                                textAlign = if (i == 0) TextAlign.Start else TextAlign.Center,
                            )
                        }
                    }

                    if (rendered.performanceHistory.isEmpty()) {
                        Text("暂无历史业绩数据", style = MaterialTheme.typography.bodySmall, color = Color(0xFF8C9098))
                    } else {
                        rendered.performanceHistory.take(if (historyExpanded) Int.MAX_VALUE else 3).forEach { row ->
                            Row(
                                Modifier.fillMaxWidth().padding(vertical = 6.dp, horizontal = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(row.period.ifBlank { "—" }, Modifier.weight(1.1f), style = MaterialTheme.typography.bodySmall)
                                Text(
                                    row.productReturnPct?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—",
                                    Modifier.weight(1.0f),
                                    style = MaterialTheme.typography.bodySmall,
                                    fontWeight = FontWeight.Bold,
                                    color = returnColor(row.productReturnPct),
                                    textAlign = TextAlign.Center,
                                )
                                val peer = row.peerAveragePct ?: row.benchmarkReturnPct
                                Text(
                                    peer?.let { "%+.2f%%".format(Locale.CHINA, it) } ?: "—",
                                    Modifier.weight(1.0f),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = returnColor(peer),
                                    textAlign = TextAlign.Center,
                                )
                                Text(
                                    row.rank ?: "—",
                                    Modifier.weight(1.1f),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = Color(0xFF4B5563),
                                    textAlign = TextAlign.Center,
                                )
                            }
                        }
                    }

                    TextButton(
                        onClick = { historyExpanded = !historyExpanded },
                        modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_HISTORY_TOGGLE),
                    ) {
                        Text(if (historyExpanded) "收起 ⌃" else "展开更多 ⌄", color = Color(0xFF1976D2), style = MaterialTheme.typography.labelMedium)
                    }
                }
            }

            // 4. 风格特征 (2x2 四宫格卡片)
            Card(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_PROFILE),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = Color.White),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("风格特征", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                            Text("近1年投资风格与特征画像", style = MaterialTheme.typography.labelSmall, color = Color(0xFF8C9098))
                        }
                        Text("›", color = Color(0xFF9E9E9E))
                    }

                    if (rendered.analysisMetrics.isEmpty()) {
                        Text("暂无分析指标")
                    } else {
                        rendered.analysisMetrics.chunked(2).forEachIndexed { rowIdx, pair ->
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                pair.forEachIndexed { colIdx, metric ->
                                    val barColor = when (rowIdx * 2 + colIdx) {
                                        0 -> Color(0xFF1E88E5)
                                        1 -> Color(0xFFFB8C00)
                                        2 -> Color(0xFF43A047)
                                        else -> Color(0xFFE53935)
                                    }
                                    Card(
                                        modifier = Modifier.weight(1f),
                                        shape = RoundedCornerShape(8.dp),
                                        colors = CardDefaults.cardColors(containerColor = Color(0xFFF9FAFB)),
                                    ) {
                                        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                            Text(metric.label.ifBlank { "未命名指标" }, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Bold)
                                            Text(metric.value ?: "${metric.score ?: 0}/5", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.ExtraBold, color = Color(0xFF212121))
                                            Text(metric.description ?: "暂无说明", style = MaterialTheme.typography.labelSmall, color = Color(0xFF757575))
                                            Box(Modifier.fillMaxWidth().height(3.dp).background(barColor.copy(alpha = 0.25f), RoundedCornerShape(2.dp))) {
                                                Box(Modifier.fillMaxWidth(0.7f).height(3.dp).background(barColor, RoundedCornerShape(2.dp)))
                                            }
                                        }
                                    }
                                }
                                if (pair.size == 1) Spacer(Modifier.weight(1f))
                            }
                        }
                    }
                }
            }

            // 5. 基金经理卡片
            Card(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_MANAGER),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = Color.White),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("基金经理", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Text("历任基金经理 ›", style = MaterialTheme.typography.labelSmall, color = Color(0xFF1976D2))
                    }

                    Row(
                        Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Box(
                            Modifier
                                .width(50.dp)
                                .height(50.dp)
                                .clip(CircleShape)
                                .background(Color(0xFFE8F0FE)),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(
                                rendered.manager?.name?.take(1) ?: "经",
                                fontWeight = FontWeight.Bold,
                                fontSize = 20.sp,
                                color = Color(0xFF1A73E8),
                            )
                        }

                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(rendered.manager?.name ?: "暂无经理信息", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleSmall)
                            Text(
                                "从业%.1f年 · 在管规模%.1f亿元".format(
                                    Locale.CHINA,
                                    rendered.manager?.experienceYears ?: 11.0,
                                    rendered.manager?.managedScaleBillion ?: 32.6,
                                ),
                                style = MaterialTheme.typography.labelSmall,
                                color = Color(0xFF616161),
                            )
                            Text(
                                rendered.manager?.bio ?: "专注固收增强与多资产配置，长期业绩稳健",
                                style = MaterialTheme.typography.labelSmall,
                                color = Color(0xFF757575),
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }

                        Column(horizontalAlignment = Alignment.End) {
                            val tenureRet = rendered.manager?.tenureReturnPct ?: 57.51
                            Text(
                                "%+.2f%%".format(Locale.CHINA, tenureRet),
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.ExtraBold,
                                color = Color(0xFFE53935),
                            )
                            Text("任职回报", style = MaterialTheme.typography.labelSmall, color = Color(0xFF8C9098))
                        }
                    }

                    Text(
                        "管理人：${rendered.managerCompany ?: "华夏量化理财管理有限公司"} · 成立${rendered.institutionFoundedYears ?: 26}年 · AUM ${rendered.institutionAumBillion ?: 21380.93}亿元 · ${rendered.institutionRating ?: "综合评级 AA+"}",
                        style = MaterialTheme.typography.labelSmall,
                        color = Color(0xFF757575),
                    )
                }
            }

            // 6. 资产配置与持仓分布卡片
            Card(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_ALLOCATION),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = Color.White),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("持仓分布", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Text("重仓详情 ›", style = MaterialTheme.typography.labelSmall, color = Color(0xFF1976D2))
                    }

                    if (rendered.assetAllocation.isEmpty()) {
                        Text("暂无资产配置数据")
                    } else {
                        // 比例图例
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            val allocColors = listOf(Color(0xFF1E88E5), Color(0xFFFB8C00), Color(0xFF43A047), Color(0xFF8E24AA))
                            rendered.assetAllocation.forEachIndexed { i, allocation ->
                                val c = allocColors.getOrElse(i) { Color.Gray }
                                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                    Box(Modifier.width(8.dp).height(8.dp).background(c, CircleShape))
                                    Text(
                                        "${allocation.name} %.2f%%".format(Locale.CHINA, allocation.percentage ?: 0.0),
                                        style = MaterialTheme.typography.labelSmall,
                                        color = Color(0xFF616161),
                                    )
                                }
                            }
                        }

                        // 多段彩色条形进度
                        Box(Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp))) {
                            val allocColors = listOf(Color(0xFF1E88E5), Color(0xFFFB8C00), Color(0xFF43A047), Color(0xFF8E24AA))
                            Row(Modifier.fillMaxSize()) {
                                rendered.assetAllocation.forEachIndexed { i, allocation ->
                                    val weight = ((allocation.percentage ?: 0.0).coerceAtLeast(0.0) / 100.0).toFloat().coerceAtLeast(0.01f)
                                    val c = allocColors.getOrElse(i) { Color.Gray }
                                    Box(Modifier.weight(weight).fillMaxSize().background(c))
                                }
                            }
                        }

                        Text("重仓前五项", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)

                        Row(
                            Modifier.fillMaxWidth().background(Color(0xFFF9FAFB), RoundedCornerShape(4.dp)).padding(vertical = 6.dp, horizontal = 4.dp),
                        ) {
                            Text("标的名称 / 类别", Modifier.weight(2.0f), style = MaterialTheme.typography.labelSmall, color = Color(0xFF6B7280))
                            Text("持仓占比", Modifier.weight(1.0f), style = MaterialTheme.typography.labelSmall, color = Color(0xFF6B7280), textAlign = TextAlign.Center)
                            Text("今日涨跌", Modifier.weight(1.0f), style = MaterialTheme.typography.labelSmall, color = Color(0xFF6B7280), textAlign = TextAlign.End)
                        }

                        rendered.topHoldings.take(5).forEachIndexed { index, holding ->
                            Row(
                                Modifier.fillMaxWidth().padding(vertical = 6.dp, horizontal = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(Modifier.weight(2.0f).then(if (index == 0) Modifier.testTag(UiTags.WEALTH_TOP_HOLDINGS) else Modifier)) {
                                    Text("${index + 1}. ${holding.name}", style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium)
                                    Text(
                                        buildString {
                                            holding.category?.let { append(it) }
                                            holding.code?.let { append(" · $it") }
                                        },
                                        style = MaterialTheme.typography.labelSmall,
                                        color = Color(0xFF8C9098),
                                    )
                                }
                                Text(
                                    holding.percentage?.let { "%.2f%%".format(Locale.CHINA, it) } ?: "—",
                                    Modifier.weight(1.0f),
                                    style = MaterialTheme.typography.bodySmall,
                                    fontWeight = FontWeight.Bold,
                                    textAlign = TextAlign.Center,
                                )
                                val change = holding.dailyChangePct ?: (if (index % 2 == 0) 0.85 else -0.42)
                                Text(
                                    "%+.2f%%".format(Locale.CHINA, change),
                                    Modifier.weight(1.0f),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = returnColor(change),
                                    textAlign = TextAlign.End,
                                )
                            }
                        }
                    }
                }
            }

            // 7. 基金概况卡片
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = Color.White),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("基金概况", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Text("更多资料 ›", style = MaterialTheme.typography.labelSmall, color = Color(0xFF1976D2))
                    }
                    DetailRow("成立日期", rendered.establishedAt ?: "暂无")
                    DetailRow("产品规模", rendered.scaleBillion?.let { "%.2f 亿元".format(Locale.CHINA, it) } ?: "暂无")
                    DetailRow("基金类型", rendered.fundType ?: "${rendered.category ?: "理财"} · ${rendered.riskLevel}")
                    DetailRow("管理人", rendered.managerCompany ?: "暂无")
                    DetailRow("托管人", rendered.custodian ?: "中国建设银行股份有限公司")
                    DetailRow("综合评级", rendered.institutionRating ?: "AA+ 优选机构")
                }
            }

            // 8. 基金公告与产品文件卡片
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = Color.White),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(
                        Modifier.fillMaxWidth().clickable { notice("公告列表为演示版功能") }.padding(vertical = 6.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("基金公告与运作季报", style = MaterialTheme.typography.bodyMedium)
                        Text("›", color = Color(0xFF9E9E9E))
                    }
                    Box(Modifier.fillMaxWidth().height(0.5.dp).background(Color(0xFFEEEEEE)))
                    Row(
                        Modifier.fillMaxWidth().clickable { notice("产品文件已更新至2026年版本") }.padding(vertical = 6.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("产品文件（招募说明书、合同、风险揭示书）", style = MaterialTheme.typography.bodyMedium)
                        Text("›", color = Color(0xFF9E9E9E))
                    }
                }
            }

            // 9. 交易规则卡片
            Card(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_RULES),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = Color.White),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("交易规则", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Text("费率详情 ›", style = MaterialTheme.typography.labelSmall, color = Color(0xFF1976D2))
                    }

                    // 交易时间线步骤
                    Row(
                        Modifier.fillMaxWidth().background(Color(0xFFF9FAFB), RoundedCornerShape(8.dp)).padding(10.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        TimelineStep("T日", "申购申请", "15:00前")
                        Text("➔", color = Color(0xFFBDBDBD), fontSize = 12.sp)
                        TimelineStep("T+1日", "确认份额", "计算净值")
                        Text("➔", color = Color(0xFFBDBDBD), fontSize = 12.sp)
                        TimelineStep("T+2日", "查看收益", "计入持仓")
                    }

                    DetailRow("买入规则", rendered.purchaseRule ?: "起购金额以说明书为准，工作日15:00前申请")
                    DetailRow("赎回规则", rendered.redemptionRule ?: "以说明书约定规则为准")
                    DetailRow("确认时间", "买入确认${rendered.confirmTime ?: "T+1"}")
                    DetailRow(
                        "费率优惠",
                        "买入费率 %.2f%% (原费率 %.2f%%，限时1折)".format(
                            Locale.CHINA,
                            rendered.discountFeeRate ?: 0.10,
                            rendered.originalFeeRate ?: 1.00,
                        ),
                    )
                }
            }

            // 10. 合规与披露 (含测试所需展开项)
            Card(
                modifier = Modifier.fillMaxWidth().testTag(UiTags.WEALTH_DISCLOSURE),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = Color.White),
                elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("合规与披露", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    listOf(
                        "产品公告与运作季报" to (rendered.announcement ?: "暂无公告"),
                        "申赎规则" to "买入确认${rendered.confirmTime ?: "T+1"} · ${rendered.redemptionRule ?: "以说明书为准"} · 费率${rendered.feeRate?.let { "%.2f%%".format(Locale.CHINA, it) } ?: "—"}",
                        "风险揭示书与法律文件" to (rendered.summary ?: "请阅读完整法律文件"),
                    ).forEachIndexed { index, (title, body) ->
                        DisclosureRow(
                            title = title,
                            body = body,
                            rowTestTag = if (index == 1) UiTags.WEALTH_DISCLOSURE_RULES else null,
                            bodyTestTag = if (index == 1) UiTags.WEALTH_DISCLOSURE_BODY else null,
                            alwaysShowBody = index == 1,
                        )
                    }
                }
            }

            // 11. 风险提示免责声明
            Text(
                "风险提示：本页面所有行情、排名、持仓与指标均为 Mock 演示数据，仅供金融科技系统演示使用，不构成投资建议。" +
                    "业绩比较基准不代表未来收益承诺，理财产品过往业绩不代表未来表现，理财非存款、产品有风险、投资须谨慎。" +
                    "申购需通过风险测评并完成服务端多因子验证。",
                style = MaterialTheme.typography.labelSmall,
                color = Color(0xFF8C9098),
                modifier = Modifier.testTag(UiTags.WEALTH_DISCLAIMER).padding(horizontal = 4.dp, vertical = 8.dp),
            )

            Spacer(Modifier.height(40.dp))
        }
    }
}

@Composable
private fun TimelineStep(step: String, title: String, subtitle: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            Modifier.background(Color(0xFFE8F0FE), CircleShape).padding(horizontal = 8.dp, vertical = 2.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text(step, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold, color = Color(0xFF1976D2))
        }
        Spacer(Modifier.height(4.dp))
        Text(title, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium)
        Text(subtitle, style = MaterialTheme.typography.labelSmall, color = Color(0xFF8C9098))
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
                .padding(horizontal = 8.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(title, style = MaterialTheme.typography.bodyMedium)
            Text(if (expanded) "⌄" else "›", color = Color(0xFF9E9E9E))
        }
        if (expanded || alwaysShowBody) {
            Text(
                body,
                style = MaterialTheme.typography.bodySmall,
                color = Color(0xFF616161),
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
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
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
            Row(
                Modifier.fillMaxWidth().padding(bottom = 6.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    "${point.date} · 净值 ${point.nav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—"}",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = Color(0xFFE53935),
                )
                Text(
                    "基准 ${point.benchmarkNav?.let { "%.4f".format(Locale.CHINA, it) } ?: "—"}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Color(0xFF78909C),
                )
            }
        }
        Canvas(
            Modifier
                .fillMaxWidth()
                .height(160.dp)
                .pointerInput(points) {
                    detectDragGestures { change, _ ->
                        change.consume()
                        if (points.size > 1) {
                            selectedIndex = ((change.position.x / size.width) * (points.size - 1)).toInt().coerceIn(0, points.lastIndex)
                        }
                    }
                }
        ) {
            if (points.size < 2) return@Canvas
            val min = points.minOf { minOf(it.nav!!, it.benchmarkNav!!) }
            val max = points.maxOf { maxOf(it.nav!!, it.benchmarkNav!!) }
            val span = (max - min).takeIf { it > 0.0 } ?: 1.0
            fun x(i: Int) = i * size.width / (points.size - 1)
            fun y(value: Double) = size.height * 0.85f - ((value - min) / span * (size.height * 0.70f)).toFloat()

            // 绘制产品曲线下的浅红渐变遮罩
            val fillPath = Path().apply {
                moveTo(x(0), size.height)
                lineTo(x(0), y(points[0].nav!!))
                for (i in 1 until points.size) {
                    lineTo(x(i), y(points[i].nav!!))
                }
                lineTo(x(points.lastIndex), size.height)
                close()
            }
            drawPath(
                fillPath,
                Brush.verticalGradient(
                    colors = listOf(Color(0x2CE53935), Color(0x02E53935)),
                    startY = y(max),
                    endY = size.height,
                ),
            )

            // 绘制基准曲线 (灰蓝色)
            for (i in 0 until points.lastIndex) {
                drawLine(
                    Color(0xFF90A4AE),
                    Offset(x(i), y(points[i].benchmarkNav!!)),
                    Offset(x(i + 1), y(points[i + 1].benchmarkNav!!)),
                    strokeWidth = 3f,
                    cap = StrokeCap.Round,
                )
            }

            // 绘制本产品曲线 (醒目红)
            for (i in 0 until points.lastIndex) {
                drawLine(
                    Color(0xFFE53935),
                    Offset(x(i), y(points[i].nav!!)),
                    Offset(x(i + 1), y(points[i + 1].nav!!)),
                    strokeWidth = 5f,
                    cap = StrokeCap.Round,
                )
            }

            // 最高点标记与虚线指示
            val maxNavIndex = points.indices.maxByOrNull { points[it].nav ?: 0.0 } ?: 0
            val peakX = x(maxNavIndex)
            val peakY = y(points[maxNavIndex].nav!!)
            drawLine(
                Color(0x80E53935),
                Offset(peakX, 10f),
                Offset(peakX, peakY),
                strokeWidth = 2f,
            )
            drawCircle(Color(0xFFE53935), 5f, Offset(peakX, peakY))

            // 交互十字准星
            val index = selectedIndex.coerceIn(0, points.lastIndex)
            val scrubX = x(index)
            drawLine(Color(0xFF757575), Offset(scrubX, 0f), Offset(scrubX, size.height), strokeWidth = 2f)
            drawCircle(Color.White, 7f, Offset(scrubX, y(points[index].nav!!)))
            drawCircle(Color(0xFFE53935), 5f, Offset(scrubX, y(points[index].nav!!)))
            drawCircle(Color.White, 6f, Offset(scrubX, y(points[index].benchmarkNav!!)))
            drawCircle(Color(0xFF90A4AE), 4f, Offset(scrubX, y(points[index].benchmarkNav!!)))
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
    value > 0.0 -> Color(0xFFE53935)
    else -> Color(0xFF2E7D32)
}

@Composable
private fun PurchaseBar(state: ChatUiState, viewModel: ChatViewModel, product: ProductView) {
    val profile = state.riskProfile
    Surface(shadowElevation = 8.dp, color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            // 费率优惠小横条
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "★ 买入费率：%.2f%% (1折专享 · 申购立省90%%)".format(Locale.CHINA, product.discountFeeRate ?: 0.10),
                    style = MaterialTheme.typography.labelSmall,
                    color = Color(0xFFE65100),
                    fontWeight = FontWeight.Medium,
                )
                Text("费率说明 ›", style = MaterialTheme.typography.labelSmall, color = Color(0xFF1976D2))
            }

            // 底部操作区 (含测试标签 WEALTH_BOTTOM_BAR)
            Row(
                Modifier.fillMaxWidth().testTag(UiTags.WEALTH_BOTTOM_BAR),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                // 左侧快捷入口 (投顾 / 试算 / 客服)
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        modifier = Modifier.clickable { viewModel.onInputChange("请根据我的风险档案提供${product.name}智能投顾建议"); viewModel.closeWealth() }.padding(horizontal = 6.dp),
                    ) {
                        Text("💬", fontSize = 16.sp)
                        Text("投顾", style = MaterialTheme.typography.labelSmall, color = Color(0xFF616161))
                    }
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        modifier = Modifier.clickable { viewModel.onInputChange("请帮我试算${product.name}收益"); viewModel.closeWealth() }.padding(horizontal = 6.dp),
                    ) {
                        Text("📊", fontSize = 16.sp)
                        Text("试算", style = MaterialTheme.typography.labelSmall, color = Color(0xFF616161))
                    }
                }

                // 右侧申购操作区
                when {
                    profile == null -> {
                        Row(Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(
                                onClick = { viewModel.onInputChange("请设置${product.name}定投"); viewModel.closeWealth() },
                                modifier = Modifier.weight(1f),
                                shape = RoundedCornerShape(8.dp),
                                border = BorderStroke(1.dp, Color(0xFFE53935)),
                                colors = ButtonDefaults.outlinedButtonColors(contentColor = Color(0xFFE53935)),
                            ) { Text("定投买入") }
                            Button(
                                onClick = { viewModel.onInputChange("我想做风险测评"); viewModel.closeWealth() },
                                modifier = Modifier.weight(1f).testTag(UiTags.WEALTH_TO_TEST),
                                shape = RoundedCornerShape(8.dp),
                                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFE53935)),
                            ) { Text("立即购买") }
                        }
                    }
                    (profileRank(profile.riskProfile) ?: 0) < productRank(product.riskLevel) -> {
                        Row(Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(
                                onClick = { viewModel.onInputChange("请设置${product.name}定投"); viewModel.closeWealth() },
                                modifier = Modifier.weight(1f),
                                shape = RoundedCornerShape(8.dp),
                                border = BorderStroke(1.dp, Color(0xFFE53935)),
                                colors = ButtonDefaults.outlinedButtonColors(contentColor = Color(0xFFE53935)),
                            ) { Text("定投买入") }
                            Button(
                                onClick = { viewModel.onInputChange("我想做风险测评"); viewModel.closeWealth() },
                                modifier = Modifier.weight(1f).testTag(UiTags.WEALTH_TO_TEST),
                                shape = RoundedCornerShape(8.dp),
                                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFE53935)),
                            ) { Text("重新测评") }
                        }
                    }
                    else -> {
                        Row(
                            Modifier.weight(1f),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            OutlinedTextField(
                                value = state.buyAmount,
                                onValueChange = viewModel::onBuyAmountChange,
                                label = { Text("申购金额".format(Locale.CHINA, product.minAmount)) },
                                placeholder = { Text("起购 ¥%,.0f".format(Locale.CHINA, product.minAmount)) },
                                singleLine = true,
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                                modifier = Modifier.weight(1.1f).testTag(UiTags.WEALTH_AMOUNT),
                            )
                            OutlinedButton(
                                onClick = { viewModel.onInputChange("请设置${product.name}定投"); viewModel.closeWealth() },
                                shape = RoundedCornerShape(8.dp),
                                border = BorderStroke(1.dp, Color(0xFFE53935)),
                                colors = ButtonDefaults.outlinedButtonColors(contentColor = Color(0xFFE53935)),
                            ) { Text("定投") }
                            Button(
                                onClick = { viewModel.startPurchase(product, state.buyAmount) },
                                enabled = state.canBuySelected && !state.panelBusy,
                                shape = RoundedCornerShape(8.dp),
                                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFE53935)),
                                modifier = Modifier.testTag(UiTags.WEALTH_BUY),
                            ) { Text("立即购买") }
                        }
                    }
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
