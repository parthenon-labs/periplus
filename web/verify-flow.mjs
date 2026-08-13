// One-off verification script: drive the real UI against the real backend,
// submit a Tokyo trip, and screenshot each stage of the flow. Not part of the
// test suite — run manually with `node verify-flow.mjs`.
import { chromium } from 'playwright'
import path from 'node:path'
import fs from 'node:fs'

const OUT_DIR = path.resolve('../media/screenshots')
fs.mkdirSync(OUT_DIR, { recursive: true })

function shot(name) {
  return path.join(OUT_DIR, name)
}

async function pickDate(page, triggerName, iso) {
  await page.getByRole('button', { name: triggerName, exact: true }).click()
  const target = new Date(`${iso}T00:00:00`)
  const now = new Date()
  const monthsForward = (target.getFullYear() - now.getFullYear()) * 12 + (target.getMonth() - now.getMonth())
  const nextMonth = page.getByRole('button', { name: 'Next month' })
  for (let i = 0; i < monthsForward; i++) {
    await nextMonth.click()
  }
  const dayLabel = new Intl.DateTimeFormat('en', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(target)
  await page.getByRole('button', { name: dayLabel, exact: true }).click()
}

async function main() {
  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()

  page.on('console', (msg) => console.log('[console]', msg.type(), msg.text()))
  page.on('pageerror', (err) => console.log('[pageerror]', err.message))

  console.log('Navigating to http://127.0.0.1:5173/ ...')
  await page.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
  await page.screenshot({ path: shot('01-trip-input-blank.png'), fullPage: true })

  console.log('Filling destination...')
  await page.getByPlaceholder('Where to?').fill('Tokyo, Japan')

  console.log('Picking dates (3 days / 2 nights, Sep 2026)...')
  await pickDate(page, 'Depart', '2026-09-10')
  await pickDate(page, 'Return', '2026-09-13')

  await page.screenshot({ path: shot('02-trip-input-filled.png'), fullPage: true })

  console.log('Submitting...')
  const submit = page.getByRole('button', { name: /chart this trip/i })
  await submit.click()

  // Wait for navigation to /runs/:id
  await page.waitForURL(/\/runs\/[^/]+$/, { timeout: 30_000 })
  const runUrl = page.url()
  console.log('Run started at', runUrl)
  await page.waitForTimeout(1500)
  await page.screenshot({ path: shot('03-run-progress.png'), fullPage: true })

  // Poll for progress, taking periodic screenshots, until it reaches the
  // result page or fails/cancels, or we time out.
  const deadline = Date.now() + 15 * 60 * 1000 // 15 minutes ceiling for the full pipeline
  let shotCount = 3
  let finalState = 'timeout'
  while (Date.now() < deadline) {
    const url = page.url()
    if (/\/runs\/[^/]+\/result$/.test(url)) {
      finalState = 'succeeded'
      break
    }
    const failedBanner = page.getByText(/the run failed|the run was cancelled/i)
    if (await failedBanner.isVisible().catch(() => false)) {
      finalState = 'failed'
      shotCount += 1
      await page.screenshot({ path: shot(`0${shotCount}-run-failed.png`), fullPage: true })
      break
    }
    await page.waitForTimeout(20_000)
    shotCount += 1
    await page.screenshot({ path: shot(`0${shotCount}-run-progress.png`), fullPage: true })
    console.log('progress check at', new Date().toISOString(), 'url:', url)
  }

  if (finalState === 'succeeded') {
    await page.waitForTimeout(1000)
    await page.screenshot({ path: shot('99-run-result.png'), fullPage: true })
    console.log('SUCCESS: reached result page at', page.url())
  } else {
    console.log('DID NOT REACH RESULT PAGE. finalState =', finalState, 'lastUrl =', page.url())
  }

  await browser.close()
}

main().catch((err) => {
  console.error('FATAL', err)
  process.exit(1)
})
