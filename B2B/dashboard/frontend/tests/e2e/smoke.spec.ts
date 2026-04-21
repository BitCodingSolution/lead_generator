import { test, expect } from "@playwright/test"

// -----------------------------------------------------------------------------
// Smoke tests — verify every page renders + core widgets appear.
// These hit the real backend (:8900) and real DB. Safe because they don't
// mutate state.
// -----------------------------------------------------------------------------

test.describe("smoke: pages render", () => {
  test("Overview loads with cross-source card + Marcel KPIs", async ({
    page,
  }) => {
    await page.goto("/")
    await expect(page.getByText("Outreach Overview")).toBeVisible()
    await expect(page.getByText("Campaign activity")).toBeVisible()
    await expect(page.getByText(/across all sources/i).first()).toBeVisible()
    // Uppercase labels are CSS-styled; DOM text is mixed case.
    await expect(page.getByText(/batches/i).first()).toBeVisible()
    await expect(page.getByText(/rows in flight/i)).toBeVisible()
    // Cross-source KPIs
    await expect(page.getByText(/total leads/i)).toBeVisible()
    await expect(page.getByText(/sent today/i)).toBeVisible()
    await expect(page.getByText(/daily send quota/i)).toBeVisible()
  })

  test("Sources page lists YC + Marcel", async ({ page }) => {
    await page.goto("/sources")
    await expect(
      page.getByRole("heading", { name: /sources/i }).first(),
    ).toBeVisible()
    // Both registered sources surface via their display label
    await expect(page.getByText(/y\s*combinator/i).first()).toBeVisible()
    await expect(page.getByText(/marcel/i).first()).toBeVisible()
  })

  test("YC source page shows Lead Pool + filters + auto-run toggle", async ({
    page,
  }) => {
    await page.goto("/sources/ycombinator")
    // Section header
    await expect(page.getByText(/find leads|lead pool/i).first()).toBeVisible()
    // Filters
    await expect(page.getByText(/needs attention/i)).toBeVisible()
    await expect(page.getByText(/starred only/i)).toBeVisible()
    await expect(page.getByText(/hiring only/i)).toBeVisible()
    // Auto-run toggle
    await expect(page.getByText(/Auto-scrape/i)).toBeVisible()
  })

  test("Campaigns page shows All Batches panel above Marcel hero", async ({
    page,
  }) => {
    await page.goto("/campaigns")
    // Header
    await expect(
      page.getByRole("heading", { name: /campaigns/i }).first(),
    ).toBeVisible()
    // Cross-source panel labels
    await expect(page.getByText(/all batches/i).first()).toBeVisible()
    // Marcel hero underneath
    await expect(page.getByText(/run a campaign/i)).toBeVisible()
    await expect(page.getByRole("button", { name: /run pipeline/i })).toBeVisible()
  })
})

// -----------------------------------------------------------------------------
// API-level guards (Playwright uses its request context — fast, no UI)
// -----------------------------------------------------------------------------

const API = "http://127.0.0.1:8900"

test.describe("api: backend endpoints", () => {
  test("cross-source batches aggregator tags each file with source", async ({
    request,
  }) => {
    const r = await request.get(`${API}/api/campaigns/batches`)
    expect(r.ok()).toBe(true)
    const json = await r.json()
    expect(Array.isArray(json.batches)).toBe(true)
    for (const b of json.batches) {
      expect(b.source).toBeTruthy()
      expect(typeof b.total).toBe("number")
      expect(typeof b.sent).toBe("number")
    }
  })

  test("auto-run: GET returns default for YC", async ({ request }) => {
    const r = await request.get(`${API}/api/sources/ycombinator/auto-run`)
    expect(r.ok()).toBe(true)
    const j = await r.json()
    expect(j.source).toBe("ycombinator")
    expect(typeof j.enabled).toBe("boolean")
    expect(j.hour).toBeGreaterThanOrEqual(0)
    expect(j.hour).toBeLessThanOrEqual(23)
  })

  test("auto-run: POST rejects out-of-range hour", async ({ request }) => {
    const r = await request.post(`${API}/api/sources/ycombinator/auto-run`, {
      data: { enabled: true, hour: 25, minute: 0 },
    })
    expect(r.status()).toBe(400)
  })

  test("auto-run: POST rejects non-grab sources", async ({ request }) => {
    const r = await request.post(`${API}/api/sources/marcel/auto-run`, {
      data: { enabled: true, hour: 2, minute: 0 },
    })
    expect(r.status()).toBe(400)
  })

  test("send endpoint: rejects fully-sent batches (400)", async ({
    request,
  }) => {
    const list = await (
      await request.get(`${API}/api/campaigns/batches`)
    ).json()
    const fullySent = (list.batches as any[]).find(
      (b) => b.total > 0 && b.sent === b.total,
    )
    test.skip(!fullySent, "no fully-sent batch to exercise")
    const r = await request.post(
      `${API}/api/sources/${fullySent.source}/batches/${encodeURIComponent(
        fullySent.name,
      )}/send`,
      { data: { count: 5 } },
    )
    expect(r.status()).toBe(400)
    const detail = (await r.json()).detail || ""
    expect(detail).toContain("fully sent")
  })

  test("leads endpoint: attention_only filter works without error", async ({
    request,
  }) => {
    const r = await request.get(
      `${API}/api/sources/ycombinator/leads?attention_only=true&limit=50`,
    )
    expect(r.ok()).toBe(true)
    const j = await r.json()
    expect(Array.isArray(j.rows)).toBe(true)
    for (const row of j.rows) {
      expect(row.needs_attention).toBe(1)
    }
  })

  test("source detail: summary exposes attention_count", async ({
    request,
  }) => {
    const r = await request.get(`${API}/api/sources/ycombinator`)
    expect(r.ok()).toBe(true)
    const j = await r.json()
    expect(j.summary).toBeTruthy()
    expect(typeof j.summary.attention_count).toBe("number")
  })
})

// -----------------------------------------------------------------------------
// Key flows — read-only assertions on data already in the system.
// -----------------------------------------------------------------------------

test.describe("flow: campaigns page source tabs", () => {
  test("source tab filters batch list", async ({ page }) => {
    await page.goto("/campaigns")
    // Wait for initial data load — "All" tab is the always-present anchor.
    await page.getByRole("button", { name: /^All\s+\d+/ }).first().waitFor({
      state: "visible",
      timeout: 10_000,
    })
    const ycTab = page.getByRole("button", { name: /Ycombinator/i })
    const count = await ycTab.count()
    test.skip(count === 0, "no YC batches in system")
    await ycTab.first().click()
    await expect(page.locator("text=YCOMBINATOR").first()).toBeVisible()
  })
})
