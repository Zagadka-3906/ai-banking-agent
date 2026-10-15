package com.aibanking.agent

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.printToLog
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.chat.ChatRole
import com.aibanking.agent.ui.chat.ChatViewModel
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.FixMethodOrder
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.MethodSorters
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * 第二轮实测问题清单（docs/模拟用户测试问题清单-第二轮.md）的真机回归。
 *
 * 这些用例只能在设备上跑：它们要验证的是"用户看到的东西"——澄清回路是否真的
 * 接住了下一句、回执卡上的时间是本地还是 UTC、返回键是关弹层还是退出应用。
 * Python 侧的对话测试已经覆盖了同一批语义，这里覆盖渲染与交互那一半。
 *
 * 与其它 UI 测试同样的前提：宿主机上 Core(10.0.2.2:8000) 与 Gateway(:8100)
 * 在跑、演示账户已播种、并且**每个测试类用一份干净的核心**——核心账本在内存里，
 * 当日累计额会跨用例叠加，把黄区用例推成红区。
 *
 * 用例按名字顺序执行：转账金额是刻意安排的，全部落在黄区（当日累计 ≤ 1000），
 * 而"申请虚拟卡"会留下第二张卡，从此每笔转账都必须指名付款卡，所以那个用例排最后。
 */
@RunWith(AndroidJUnit4::class)
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class SecondRoundFixesUiTest {

    @get:Rule
    val ui = createAndroidComposeRule<MainActivity>()

    private lateinit var model: ChatViewModel

    @Before
    fun observeModel() {
        ui.activityRule.scenario.onActivity { model = ViewModelProvider(it)[ChatViewModel::class.java] }
        ui.ensureLoggedIn(DEMO_PASSWORD, LOGIN_TIMEOUT_MS)
    }

    /** 发一句话，等助手给出稳定答复（不是占位气泡），返回答复正文。 */
    private fun ask(text: String): String {
        val before = model.state.value.messages.size
        ui.sendChatMessage(text)
        try {
            ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
                val state = model.state.value
                val last = state.messages.lastOrNull()
                !state.busy && state.messages.size > before && last != null &&
                    last.role == ChatRole.ASSISTANT && !last.text.startsWith("正在") &&
                    last.text != "处理中" && last.text != "等待您的确认"
            }
        } catch (timeout: Exception) {
            ui.onRoot().printToLog("R2FIX")
            throw timeout
        }
        return model.state.value.messages.last().text
    }

    private fun awaitApproval() {
        try {
            ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
                ui.onAllNodesWithTag(UiTags.APPROVAL_APPROVE).fetchSemanticsNodes().isNotEmpty()
            }
        } catch (timeout: Exception) {
            ui.onRoot().printToLog("R2FIX")
            throw timeout
        }
    }

    /** 点确认或取消，等审批卡消失（卡片只在核心结算完/撤销后才撤下）。 */
    private fun decide(tag: String) {
        ui.onNodeWithTag(tag).performScrollTo().performClick()
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) {
            ui.onAllNodesWithTag(UiTags.APPROVAL_APPROVE).fetchSemanticsNodes().isEmpty()
        }
    }

    private fun textVisible(needle: String): Boolean =
        ui.onAllNodesWithText(needle, substring = true, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()

    /**
     * 核心按北京时间存"明天上午十点"，回执/待办必须把它渲染成**设备时区**的同一时刻。
     * 用设备自己的时区算出期望串，用例在 GMT 模拟器上和真机上同样成立。
     */
    private fun expectedLocalSchedule(): String {
        val beijing = ZoneId.of("Asia/Shanghai")
        val instant = LocalDate.now(beijing).plusDays(1).atTime(10, 0).atZone(beijing).toInstant()
        return instant.atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"))
    }

    private fun awaitText(needle: String) {
        ui.waitUntil(timeoutMillis = TURN_TIMEOUT_MS) { textVisible(needle) }
    }

    // -- R2-3 持仓查询 --------------------------------------------------------

    @Test
    fun a_holdings_query_reports_positions_not_the_product_shelf() {
        val reply = ask("查看我的持仓")
        assertTrue("空持仓要说清楚，而不是回在售产品：$reply", reply.contains("没有理财持仓"))
        assertFalse("持仓查询不能返回产品货架：$reply", reply.contains("在售理财产品"))
    }

    // -- R2-2 生日联动日期槽位与澄清回填 --------------------------------------

    @Test
    fun b_birthday_lock_accepts_a_bare_month_day() {
        val asked = ask("我爱人生日快到了，帮我锁定1000元，生日前两天提醒我订鲜花和蛋糕")
        assertTrue("缺日期时应当澄清：$asked", asked.contains("生日日期"))
        assertTrue("澄清要给出可接受的日期格式：$asked", asked.contains("10月5号"))
        assertFalse("澄清阶段不得锁定资金", textVisible("已锁定"))

        // 澄清回路必须接住一个光秃秃的日期，而不是把同一句话再问一遍。
        val upcoming = LocalDate.now().plusDays(40)
        val reply = ask("${upcoming.monthValue}月${upcoming.dayOfMonth}号")
        assertFalse("补充日期后不应再要日期：$reply", reply.contains("请补充生日日期"))
        awaitApproval()
        assertTrue("审批卡要回显锁定金额", textVisible("1,000.00"))
        assertTrue("审批卡要回显生日日期", textVisible("${upcoming.monthValue}月${upcoming.dayOfMonth}日") || textVisible(upcoming.toString()))
        decide(UiTags.APPROVAL_REJECT)
    }

    // -- R2-1 量词型金额 ------------------------------------------------------

    @Test
    fun c_measure_word_amount_reaches_the_approval_card() {
        val reply = ask("再转一笔300给李四")
        assertFalse("句中有 300 就不该再要金额：$reply", reply.contains("请补充明确的金额"))
        awaitApproval()
        assertTrue("审批卡金额应为 300", textVisible("300.00"))
        decide(UiTags.APPROVAL_APPROVE)
        awaitText("转账回执")
    }

    @Test
    fun d_card_limit_without_a_unit_shows_the_new_limit() {
        val reply = ask("把尾号3082的卡每日限额调到8000")
        assertFalse("句中有 8000 就不该再要额度：$reply", reply.contains("请补充希望设置的卡片额度"))
        awaitApproval()
        // 红区需要短信验证码与设备签名；这里只验证金额进了审批卡，随后取消。
        assertTrue("审批卡应显示新限额 8,000.00", textVisible("8,000.00"))
        decide(UiTags.APPROVAL_REJECT)
    }

    // -- R2-6 回执与待办的时间显示 --------------------------------------------

    @Test
    fun e_scheduled_receipt_shows_local_time_not_utc() {
        ask("明天上午十点给李四转100元")
        awaitApproval()
        decide(UiTags.APPROVAL_APPROVE)
        awaitText("已登记预约转账")
        assertTrue("回执卡应有执行时间一行", textVisible("执行时间"))
        // 回归信号：核心给的 UTC 原始串（…T02:00:00+00:00）不得再出现。
        assertFalse("回执卡不得显示 UTC 原始串", textVisible("+00:00"))
        assertTrue("执行时间应渲染为设备本地时间", textVisible(expectedLocalSchedule()))
    }

    // -- P3 订阅卡不再暴露结果代码 --------------------------------------------

    @Test
    fun f_subscription_card_does_not_leak_the_status_code() {
        val reply = ask("查看我的订阅")
        assertTrue("应当列出订阅：$reply", reply.contains("订阅"))
        assertFalse("传输层状态码不是给客户看的事实", textVisible("结果代码"))
    }

    // -- P3 弹层返回不退出应用 ------------------------------------------------

    @Test
    fun g_back_closes_the_task_sheet_without_leaving_the_app() {
        ui.waitUntil(timeoutMillis = LOGIN_TIMEOUT_MS) {
            ui.onAllNodesWithTag(UiTags.OPEN_TASKS).fetchSemanticsNodes().isNotEmpty()
        }
        ui.onNodeWithTag(UiTags.OPEN_TASKS).performClick()
        awaitText("任务安排")
        assertFalse("待办任务页中英混排已修：不应再出现 YOUR PLANS", textVisible("YOUR PLANS"))
        assertFalse("待办里的计划时间不得是 UTC 原始串", textVisible("+00:00"))
        assertTrue("待办里的计划时间应渲染为设备本地时间", textVisible(expectedLocalSchedule()))

        ui.activityRule.scenario.onActivity { it.onBackPressedDispatcher.onBackPressed() }
        ui.waitForIdle()
        var finishing = true
        ui.activityRule.scenario.onActivity { finishing = it.isFinishing }
        assertFalse("返回键不应把应用退回桌面", finishing)
        assertFalse("返回键应关掉弹层", textVisible("任务安排"))
        ui.onNodeWithTag(UiTags.CHAT_INPUT).assertIsDisplayed()
    }

    // -- R2-5 / R2-7 多卡时必须能指名付款卡 ----------------------------------

    @Test
    fun h_second_card_must_be_named_and_the_tail_is_honoured() {
        ask("申请一张虚拟卡")
        awaitApproval()
        decide(UiTags.APPROVAL_APPROVE)
        awaitText("虚拟卡已开立")

        val refused = ask("给李四转500元")
        assertTrue("多卡拒绝要给出可用的指定方式：$refused", refused.contains("尾号"))

        // 照着提示做就必须能过——这正是第二轮实测里断裂的那一步。
        val reply = ask("用尾号3082的卡给李四转500元，备注学费")
        assertFalse("指名尾号后不应重复同一条拒绝：$reply", reply.contains("请指定用于支付的卡片"))
        awaitApproval()
        assertTrue("审批卡金额应为 500", textVisible("500.00"))
        decide(UiTags.APPROVAL_APPROVE)
        awaitText("转账回执")
    }

    private companion object {
        const val DEMO_PASSWORD = "Bank@2026"
        const val LOGIN_TIMEOUT_MS = 45_000L

        /** 联网模式下每一轮都要过一次大模型，超时给足。 */
        const val TURN_TIMEOUT_MS = 120_000L
    }
}
