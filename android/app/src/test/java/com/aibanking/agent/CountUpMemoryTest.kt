package com.aibanking.agent

import com.aibanking.agent.ui.anim.CountUpMemory
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CountUpMemoryTest {
    @Test
    fun a_number_counts_up_only_the_first_time_it_is_shown() {
        assertTrue(CountUpMemory.firstTime("card:TEST-1|15000.0"))
        assertFalse(CountUpMemory.firstTime("card:TEST-1|15000.0"))
        // A changed balance is a new number and counts again.
        assertTrue(CountUpMemory.firstTime("card:TEST-1|14700.0"))
    }
}
