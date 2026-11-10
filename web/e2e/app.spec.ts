import { expect, test } from "@playwright/test";

test("floor shows a tour and compares the three routes", async ({ page }) => {
  await page.goto("/#floor");
  await expect(page.getByRole("heading", { name: /^Tour \d+$/ })).toBeVisible();
  await expect(page.locator(".tape")).toHaveCount(1);
  const verdict = page.locator(".verdict");
  await page.getByRole("radio", { name: /S-shape/ }).check();
  await expect(verdict).toContainText("S-shape walks every aisle");
  await page.getByRole("radio", { name: /Optimal/ }).check();
  await expect(verdict).toContainText("Optimal walks");
  const before = await page.getByRole("heading", { name: /^Tour \d+$/ }).textContent();
  await page.getByRole("button", { name: "Previous tour" }).click();
  await expect(page.getByRole("heading", { name: /^Tour \d+$/ })).not.toHaveText(before ?? "");
});

test("a planned wave can be released", async ({ page }) => {
  await page.goto("/#waves");
  const row = page
    .locator("tr.row")
    .filter({ has: page.getByRole("button", { name: "Release wave" }) })
    .first();
  const release = row.getByRole("button", { name: "Release wave" });
  await expect(release).toBeVisible();
  const code = (await row.getByRole("button", { name: /^W\d+$/ }).textContent()) ?? "";
  await release.click();
  await expect(page.getByRole("status")).toContainText(`Released ${code}`);
  await expect(page.locator("tr.row", { hasText: code })).toContainText("Released");
});

test("dock timeline has a row for every door", async ({ page, request }) => {
  const layout = await (await request.get("/api/layout")).json();
  const doors = layout.doors.filter((door: { kind: string }) => door.kind !== "stage");
  await page.goto("/#docks");
  await expect(page.locator(".door-label")).toHaveCount(doors.length);
  await expect(page.locator(".actual").first()).toBeVisible();
});

test("stock search lists pallets oldest expiry first", async ({ page }) => {
  await page.goto("/#stock");
  await page.getByLabel("Find a SKU").fill("dish soap");
  const first = page.locator("tbody").first().getByRole("button").first();
  await expect(first).toBeVisible();
  await first.click();
  const dates = page.locator(".stock-page .panel").nth(1).locator("td.figure");
  await expect(dates.first()).toBeVisible();
  const shown = await dates.allTextContents();
  expect([...shown].sort()).toEqual(shown);
});
