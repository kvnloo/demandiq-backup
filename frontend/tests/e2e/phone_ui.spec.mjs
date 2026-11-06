// Phone UI gates against the real React app, existing Chrome/Playwright harness.
// Requires Vite :3000, FastAPI :8000, and system Chrome (see e2e/README.md).
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { spawnSync } from 'node:child_process'

const PW = '/tmp/node_modules/playwright-core'
if (!existsSync(PW)) {
  const install = spawnSync('npm', ['i', 'playwright-core', '--no-save'], {
    cwd: '/tmp', stdio: 'inherit',
  })
  assert.equal(install.status, 0, 'install playwright-core')
}
const { chromium } = (await import('/tmp/node_modules/playwright-core/index.js')).default
const BASE = 'http://localhost:3000'
const KEY = 'demandiq:project'

const volumes = {
  EBL: { value: 50, _source: 'manual' },
  EBT: { value: 12.5, _source: 'manual' },
  EBR: { value: 30, _source: 'manual' },
}
const intersection = {
  id: 'i1', num: 1, name: 'Intersection 1', control: 'Signalized',
  legs: { N: true, S: true, E: true, W: true },
  approaches: { EB: { exists: true, lanes: [{ movement: 'LTR' }] } },
  volumes: { AM: volumes, PM: volumes },
  balanced: { AM: { EBL: 50, EBT: 13, EBR: 30 }, PM: { EBL: 50, EBT: 13, EBR: 30 } },
}
const state = {
  version: 1, project: { name: 'Phone UI gate fixture', analysisPeriods: ['AM', 'PM'] },
  intersections: [intersection], links: [],
  balancing: { corridorDir: 'EB/WB', maxIterations: 500, linkResolution: 'larger', overrides: { AM: {}, PM: {} } },
  analysis: {
    lastRun: {
      method: 'larger',
      intConverged: { AM: { 1: false }, PM: { 1: true } },
      finalResidual: { AM: { 1: 8 }, PM: { 1: 0 } },
      iterations: { AM: { 1: 500 }, PM: { 1: 24 } },
      movementsReview: { AM: [], PM: [] },
      resolvedLinks: { AM: [], PM: [] },
      qcFlags: [
        { check: 'FRACTIONAL-VOLUME', int: 1, period: 'AM', message: 'Int 1 EBT (AM): raw value 12.5 is not a whole number' },
        { check: 'SOURCE-OUTLIER', int: 1, period: 'AM', message: 'Int 1 EB approach total = 920 veh (AM), verify source count' },
        { check: 'NCHRP-CONV', int: 2, period: 'AM', message: 'Different intersection' },
      ],
      config: { maxIterations: 500 },
    },
  },
}

function rgb(color) {
  const values = color.match(/[\d.]+/g)?.slice(0, 3).map(Number)
  assert.equal(values?.length, 3, 'RGB CSS color')
  return values
}
function luminance(color) {
  const linear = rgb(color).map((v) => {
    const x = v / 255
    return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
}
function contrast(a, b) {
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}
async function gate(page, selectors) {
  const result = await page.evaluate((targets) => {
    const elements = targets.flatMap((selector) => Array.from(document.querySelectorAll(selector)))
    const undersized = elements.filter((el) => {
      const style = getComputedStyle(el)
      if (style.display === 'none' || style.visibility === 'hidden' || el.disabled) return false
      const rect = el.getBoundingClientRect()
      return rect.width < 43.5 || rect.height < 43.5
    }).map((el) => ({ html: el.outerHTML.slice(0, 120), w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height }))
    const clipped = Array.from(document.querySelectorAll(
      '.di-sidebar a span, .di-topbar__new, .di-rm-flag__title, .di-rm-flag__action, .di-vol-th'
    )).filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.outerHTML.slice(0, 120))
    return {
      width: innerWidth,
      docWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      undersized, clipped,
    }
  }, selectors)
  assert.equal(result.width, 390, 'phone viewport')
  assert.ok(result.docWidth <= 391 && result.bodyWidth <= 391, 'horizontal overflow: ' + JSON.stringify(result))
  assert.deepEqual(result.undersized, [], 'touch targets under 44px')
  assert.deepEqual(result.clipped, [], 'clipped action and column labels')
}

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const ctx = await browser.newContext({
  viewport: { width: 390, height: 844 }, deviceScaleFactor: 1,
  isMobile: true, hasTouch: true,
  httpCredentials: { username: 'demo', password: 'demo' },
})
const page = await ctx.newPage()
const consoleErrors = []
page.on('pageerror', (error) => consoleErrors.push(error.message))
page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()) })

try {
  await page.goto(BASE)
  await page.evaluate(({ key, data }) => {
    localStorage.clear()
    localStorage.setItem(key, JSON.stringify(data))
  }, { key: KEY, data: state })

  await page.goto(BASE + '/review-movements')
  await page.locator('.di-rm-flags__list').waitFor()
  assert.equal(await page.locator('.di-rm-flag').count(), 2, 'only current intersection/period flags')
  const titles = await page.locator('.di-rm-flag__title').allTextContents()
  assert.ok(titles.some((s) => s.includes('EBT · FRACTIONAL-VOLUME')), 'movement named')
  assert.ok(titles.some((s) => s.includes('EB approach · SOURCE-OUTLIER')), 'no invented movement')
  await gate(page, ['.di-rm button', '.di-sidebar a', '.di-sidebar__diagnostics', '.di-topbar__new'])
  for (const theme of ['night-traffic', 'sunshine', 'signal-flow', 'signal-flow-day']) {
    const styles = await page.evaluate((name) => {
      document.documentElement.dataset.theme = name
      return {
        text: getComputedStyle(document.querySelector('.di-rm-status')).color,
        label: getComputedStyle(document.querySelector('.di-rm-flag__title')).color,
        background: getComputedStyle(document.body).backgroundColor,
      }
    }, theme)
    assert.ok(contrast(styles.text, styles.background) >= 4.5, theme + ' verdict AA')
    assert.ok(contrast(styles.label, styles.background) >= 4.5, theme + ' flag AA')
  }

  await page.getByRole('button', { name: 'Edit counts' }).first().click()
  assert.equal(new URL(page.url()).pathname, '/volumes', 'fix action next to flag opens existing volume editor')
  await page.locator('.di-vol-input').first().waitFor()
  await gate(page, [
    '.di-sidebar a', '.di-sidebar__diagnostics', '.di-topbar__new',
    '.di-vol-grid input', '.di-up__drop', '.di-up__link', '.di-s4__paste-btn',
  ])
  const cell = page.getByRole('spinbutton', { name: '1 EBT AM volume' })
  await cell.fill('123')
  const value = await page.evaluate((key) => {
    return JSON.parse(localStorage.getItem(key)).intersections[0].volumes.AM.EBT.value
  }, KEY)
  assert.equal(value, 123, 'existing setter still edits the selected movement')
  assert.deepEqual(consoleErrors, [], 'zero browser console/page errors')
  console.log('phone UI: 390px, 44px, AA, flags, fix routing, volume write, console passed')
} finally {
  await ctx.close()
  await browser.close()
}
