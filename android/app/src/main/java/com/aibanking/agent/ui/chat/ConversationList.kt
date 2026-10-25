package com.aibanking.agent.ui.chat

import androidx.compose.foundation.gestures.animateScrollBy
import androidx.compose.foundation.interaction.collectIsDraggedAsState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import com.aibanking.agent.ui.UiTags
import com.aibanking.agent.ui.anim.LocalBankMotionEnabled
import com.aibanking.agent.ui.components.BankSecondaryButton
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/** Follow new output until the user drags; sending a new message resumes following. */
@Composable
internal fun ConversationList(
    listState: LazyListState,
    contentRevision: Any,
    latestUserMessageId: String?,
    itemCount: Int,
    ensureLastCardVisible: Boolean,
    modifier: Modifier = Modifier,
    latestLabel: String = "回到最新消息",
    followUpdates: Boolean = true,
    content: LazyListScope.() -> Unit,
) {
    var following by rememberSaveable { mutableStateOf(true) }
    var previousUserMessageId by rememberSaveable { mutableStateOf(latestUserMessageId) }
    val dragged by listState.interactionSource.collectIsDraggedAsState()
    val atBottom by remember { derivedStateOf { !listState.canScrollForward } }
    val motion = LocalBankMotionEnabled.current
    val scope = rememberCoroutineScope()

    suspend fun scrollToLatest() {
        if (itemCount == 0) return
        snapshotFlow { listState.layoutInfo.totalItemsCount }.first { it >= itemCount }
        if (motion) listState.animateScrollToItem(itemCount - 1) else listState.scrollToItem(itemCount - 1)
        if (ensureLastCardVisible) {
            // Tall approval/quiz cards must keep their bottom controls reachable.
            repeat(2) {
                if (motion) delay(if (it == 0) 60L else 300L)
                val info = listState.layoutInfo
                val last = info.visibleItemsInfo.lastOrNull { item -> item.index == itemCount - 1 }
                if (last != null) {
                    val overflow = last.offset + last.size - (info.viewportEndOffset - info.afterContentPadding)
                    if (overflow > 0) {
                        if (motion) listState.animateScrollBy(overflow.toFloat()) else listState.scrollToItem(itemCount - 1, overflow)
                    }
                }
            }
        }
    }

    LaunchedEffect(dragged, atBottom, listState.isScrollInProgress) {
        if (dragged) following = false
        else if (!listState.isScrollInProgress && atBottom) following = true
    }
    LaunchedEffect(contentRevision, following, latestUserMessageId, followUpdates) {
        if (latestUserMessageId != previousUserMessageId) {
            previousUserMessageId = latestUserMessageId
            following = true
        }
        if (following && !dragged && followUpdates) scrollToLatest()
    }
    Box(modifier) {
        LazyColumn(
            state = listState, modifier = Modifier.fillMaxSize().testTag(UiTags.CHAT_LIST),
            contentPadding = PaddingValues(horizontal = 20.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp), content = content,
        )
        if (!following && !atBottom) {
            BankSecondaryButton(latestLabel, {
                following = true
                scope.launch { scrollToLatest() }
            }, modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp).testTag("chat_latest"))
        }
    }
}
