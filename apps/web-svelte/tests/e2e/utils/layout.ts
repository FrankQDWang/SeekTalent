import { expect, type Page } from '@playwright/test';

export async function assertNoHorizontalOverflow(page: Page) {
	const overflow = await page.evaluate(() => document.body.scrollWidth - window.innerWidth);
	expect(overflow).toBeLessThanOrEqual(1);
}
