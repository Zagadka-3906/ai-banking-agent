package com.aibanking.agent.ui

/**
 * Test tags shared by the UI and the instrumented tests.
 *
 * Defined once so the two cannot drift: a renamed tag would otherwise turn a
 * UI test into a silent "node not found" failure that looks like a product bug.
 */
object UiTags {
    const val LOGIN_USER = "login_user"
    const val LOGIN_PASSWORD = "login_password"
    const val LOGIN_OTP = "login_otp"
    const val LOGIN_BUTTON = "login_button"

    const val CHAT_INPUT = "chat_input"
    const val CHAT_SEND = "chat_send"

    const val OPEN_DEVICES = "open_devices"
    const val OPEN_TASKS = "open_tasks"
    const val OPEN_CONTACTS = "open_contacts"
    const val OPEN_CARDS = "open_cards"
    const val CARD_OPEN_ADD = "card_open_add"
    const val CARD_ADD = "card_add"
    const val CARD_NUMBER = "card_number"
    const val CARD_CAMERA = "card_camera"
    const val CARD_CONFIRM = "card_confirm"
    const val CARD_DETAIL = "card_detail"
    const val CARD_REVEAL = "card_reveal"
    const val CARD_PIN = "card_pin"
    const val CARD_REVEALED = "card_revealed"
    const val CARD_TRANSFER = "card_transfer"
    const val CARD_WEALTH = "card_wealth"

    /**
     * 卡片操作按钮的标签前缀。
     *
     * 一张卡同时有多个操作（ACTIVE 有冻结与挂失），所以只带 cardId 的标签仍会
     * 命中多个节点；[cardAction] 把操作名也带上，一张卡的某个操作才唯一可寻址。
     */
    const val CARD_ACTION = "card_action"

    /** 卡片列表本身：列表是懒加载的，测试要滚动才能把视口外的卡面带进组合树。 */
    const val CARD_LIST = "card_list"

    /**
     * Tag of one card's one action button, e.g.
     * `onNodeWithTag(UiTags.cardAction("CARD-002", "freeze"))`.
     *
     * Joined with `:` rather than `_` because action names contain underscores
     * (`report_lost`): an underscore-joined tag cannot be split back into
     * (cardId, action), and a test that tried read `report_lost` as `lost`.
     */
    fun cardAction(cardId: String, action: String) = "$CARD_ACTION:$cardId:$action"

    const val OPEN_WEALTH = "open_wealth"
    const val WEALTH_BUY = "wealth_buy"
    const val WEALTH_TO_TEST = "wealth_to_test"
    const val WEALTH_DETAIL = "wealth_detail"
    const val WEALTH_SNAPSHOT = "wealth_snapshot"
    const val WEALTH_CHART = "wealth_chart"
    const val WEALTH_HISTORY = "wealth_history"
    const val WEALTH_RULES = "wealth_rules"
    const val WEALTH_DISCLAIMER = "wealth_disclaimer"
    const val WEALTH_AMOUNT = "wealth_amount"
    const val WEALTH_LIST = "wealth_list"
    const val WEALTH_HEADER = "wealth_header"
    const val WEALTH_SUMMARY = "wealth_summary"
    const val WEALTH_PROMO = "wealth_promo"
    const val WEALTH_PROFILE = "wealth_profile"
    const val WEALTH_MANAGER = "wealth_manager"
    const val WEALTH_ALLOCATION = "wealth_allocation"
    const val WEALTH_TOP_HOLDINGS = "wealth_top_holdings"
    const val WEALTH_DISCLOSURE = "wealth_disclosure"
    const val WEALTH_BOTTOM_BAR = "wealth_bottom_bar"
    const val WEALTH_HISTORY_TOGGLE = "wealth_history_toggle"
    const val WEALTH_DISCLOSURE_RULES = "wealth_disclosure_rules"
    const val WEALTH_DISCLOSURE_BODY = "wealth_disclosure_body"
    fun wealthRange(period: String) = "wealth_range:$period"
    fun wealthProduct(productId: String) = "wealth_product:$productId"
    const val CONTACT_SEARCH = "contact_search"
    const val CONTACT_OPEN_ADD = "contact_open_add"
    const val CONTACT_TRANSFER = "contact_transfer"
    const val DEVICE_REVOKE = "device_revoke"
    const val TASK_CANCEL = "task_cancel"
    const val CONTACT_ADD = "contact_add"
    const val CONTACT_REMOVE = "contact_remove"

    const val APPROVAL_APPROVE = "approval_approve"
    const val APPROVAL_REJECT = "approval_reject"
    const val APPROVAL_OTP = "approval_otp"

    /** Retry affordance of a panel whose load failed. */
    const val PANEL_RETRY = "panel_retry"
}
