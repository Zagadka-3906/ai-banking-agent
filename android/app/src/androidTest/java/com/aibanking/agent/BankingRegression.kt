package com.aibanking.agent

/**
 * Focused device regression for UI, platform integration and session isolation.
 * Select with AndroidJUnitRunner's `annotation` argument. Network cases require
 * the isolated scenario backend; credential cases also require the device PIN.
 */
@Target(AnnotationTarget.CLASS)
@Retention(AnnotationRetention.RUNTIME)
annotation class BankingRegression
