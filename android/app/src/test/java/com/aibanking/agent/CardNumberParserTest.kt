package com.aibanking.agent

import com.aibanking.agent.ui.chat.extractCardNumber
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CardNumberParserTest {
    @Test fun parsesSpacedAndHyphenatedValidCard() {
        assertEquals("4111111111111111", extractCardNumber("卡号 4111 1111-1111 1111"))
    }
    @Test fun skipsInvalidCandidates() {
        assertNull(extractCardNumber("1234 5678 9012 3456"))
    }
    @Test fun choosesValidCandidateAmongSeveral() {
        assertEquals("5555555555554444", extractCardNumber("参考 1234 5678 9012 3456，实际 5555 5555 5555 4444"))
    }
    @Test fun rejectsTooLongDigitRun() {
        assertNull(extractCardNumber("41111111111111111111"))
    }
}
