import { mkdirSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { expect, type Page, test } from '@playwright/test';
import { expectNoForbiddenRoutes, mockParityApi, SESSION_IDS } from './parityMockApi';

const FORBIDDEN_PRIMARY_COPY = [
	'Svelte 5 Workbench Spike',
	'Dev mode BYOK',
	'data-root',
	'data root',
	'dataRoots'
];

const RAW_LEAK_STRINGS = [
	'parity-csrf-token',
	'secret-token',
	'raw_provider_payload',
	'Authorization',
	'/private/',
	'/Users/',
	'storage state',
	'Liepin Pi Agent',
	'DokoBot',
	'Pi-first',
	'dokobot_action',
	'live-pi-agent',
	'pi_agent'
];

test.describe('React-parity Workbench shell', () => {
	test('redirects unauthenticated protected routes before protected APIs run', async ({ page }) => {
		const calls = await mockParityApi(page, { authenticated: false });

		await page.goto('/sessions');

		await expect(page, 'unauthenticated /sessions should redirect to /login').toHaveURL(
			/\/login$/,
			{
				timeout: 3_000
			}
		);
		await expect(page.getByRole('heading', { name: /AI Recruiting Platform VP/ })).toHaveCount(0);
		expect(calls.protectedBeforeAuth, 'protected API calls before auth redirect').toEqual([]);
		await expectNoForbiddenRoutes(calls);
	});

	test('renders public login and setup shells', async ({ page }) => {
		const calls = await mockParityApi(page, { authenticated: false });

		await page.goto('/login');
		await expect(page.getByRole('heading', { name: /^登录$/ })).toBeVisible();
		await expect(page.getByLabel(/^邮箱$/)).toBeVisible();
		await expect(page.getByLabel(/^密码$/)).toBeVisible();

		await page.goto('/setup');
		await expect(page.getByRole('heading', { name: /^创建管理员$/ })).toBeVisible();
		await expect(page.getByLabel(/^邮箱$/)).toBeVisible();
		await expect(page.getByLabel(/^显示名称$/)).toBeVisible();
		await expect(page.getByLabel(/^密码$/)).toBeVisible();
		await expectNoForbiddenRoutes(calls);
	});

	test('renders authenticated topbar, source link, logout, session rail, search, collapse, and active highlight', async ({
		page
	}) => {
		const calls = await mockParityApi(page);

		await page.goto(`/sessions/${SESSION_IDS.completed}`);

		await expect(page.getByText('简历智能检索')).toBeVisible();
		await expect(page.getByText('本地招聘工作台')).toBeVisible();
		await expect(page.getByText('3 个会话')).toBeVisible();
		await expect(page.getByText('Parity Recruiter')).toBeVisible();
		await expect(page.getByRole('link', { name: '渠道' })).toHaveAttribute(
			'href',
			'/settings/sources'
		);
		await expect(page.getByRole('button', { name: '退出' })).toBeVisible();

		const rail = page.getByTestId('session-rail');
		await expect(rail).toBeVisible();
		await expect(rail.getByRole('link', { name: '会话' })).toBeVisible();
		await expect(page.getByLabel('搜索会话')).toBeVisible();
		await page.getByLabel('搜索会话').fill('Partial');
		await expect(page.getByRole('link', { name: /Liepin Partial/ })).toBeVisible();
		await expect(page.getByRole('link', { name: /Login Required/ })).toHaveCount(0);

		await page.getByRole('button', { name: 'Collapse session rail' }).click();
		await expect(page.getByRole('button', { name: 'Expand session rail' })).toBeVisible();

		await page.goto(`/sessions/${SESSION_IDS.completed}`);
		const activeSession = page.locator('.rail-item.active', {
			hasText: 'AI Recruiting Platform VP'
		});
		await expect(activeSession).toBeVisible();
		await expectNoForbiddenRoutes(calls);
		expect(calls.unhandled, 'all parity API calls should be explicitly mocked').toEqual([]);
	});

	test('models completed, login-required, and partial source states', async ({ page }) => {
		const calls = await mockParityApi(page);

		await page.goto(`/sessions/${SESSION_IDS.completed}`);
		await expect(page.getByText('CTS', { exact: true }).first()).toBeVisible();
		await expect(page.getByText('猎聘', { exact: true }).first()).toBeVisible();
		await expect(page.getByRole('heading', { name: 'AI Recruiting Platform VP' })).toBeVisible();

		await page.goto(`/sessions/${SESSION_IDS.blocked}`);
		await expect(
			page.getByText('请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。')
		).toBeVisible();
		await expect(page.getByTestId('source-card-liepin').getByText('需登录猎聘')).toBeVisible();
		await expect(page.getByText('最终短名单', { exact: true })).toBeVisible();

		await page.goto(`/sessions/${SESSION_IDS.partial}`);
		await expect(page.getByText('猎聘已返回有效卡片，详情额度仍待审批。')).toBeVisible();
		await expect(page.getByText('最终短名单', { exact: true })).toBeVisible();
		const finalCard = page.getByTestId('candidate-card-identity-parity-1');
		await expect(finalCard.getByText('Candidate A')).toBeVisible();
		await expect(finalCard.getByText('选择理由')).toBeVisible();
		await expect(
			finalCard.getByText('Runtime selected this candidate for agent workflow depth.')
		).toBeVisible();
		await expect(finalCard.getByText('偏好匹配')).toBeVisible();
		await expect(finalCard.getByText('agent tooling')).toBeVisible();
		await expect(finalCard.getByText('弱项')).toBeVisible();
		await expect(finalCard.getByText('Needs leadership calibration')).toBeVisible();
		await expect(finalCard.getByText('风险')).toBeVisible();
		await expect(finalCard.getByText('management scope unclear')).toBeVisible();
		await expect(finalCard.getByText('第 2 轮')).toBeVisible();

		await page.setViewportSize({ width: 390, height: 844 });
		await assertNoHorizontalOverflow(page);
		await expectNoForbiddenRoutes(calls);
		expect(calls.unhandled, 'all parity API calls should be explicitly mocked').toEqual([]);
	});

	test('opens no stream before auth, then switches between global and session EventSource streams', async ({
		page
	}) => {
		await installEventSourceRecorder(page);
		const unauthenticatedCalls = await mockParityApi(page, { authenticated: false });

		await page.goto('/sessions');
		await expect(page).toHaveURL(/\/login$/, { timeout: 3_000 });
		await expect.poll(() => eventSourceEvents(page)).toEqual([]);
		expect(
			unauthenticatedCalls.protectedBeforeAuth,
			'protected API calls before auth redirect'
		).toEqual([]);
		await expectNoForbiddenRoutes(unauthenticatedCalls);

		await page.unroute('**/api/**');
		const authenticatedCalls = await mockParityApi(page);
		await page.goto('/sessions');
		await expect
			.poll(() => eventSourceEvents(page))
			.toContainEqual({
				type: 'open',
				url: '/api/workbench/events/stream'
			});
		const sessionListCallsBeforeEvent = countApiCalls(
			authenticatedCalls.all,
			'GET /api/workbench/sessions'
		);
		await dispatchWorkbenchEvent(page, {
			globalSeq: 99,
			eventName: 'source_connection_status_changed',
			sessionId: null
		});
		await expect
			.poll(() => countApiCalls(authenticatedCalls.all, 'GET /api/workbench/sessions'))
			.toBeGreaterThan(sessionListCallsBeforeEvent);

		await page
			.getByRole('link', { name: /AI Recruiting Platform VP/ })
			.first()
			.click();
		await expect
			.poll(() => eventSourceEvents(page))
			.toContainEqual({
				type: 'close',
				url: '/api/workbench/events/stream'
			});
		await expect
			.poll(() => eventSourceEvents(page))
			.toContainEqual({
				type: 'open',
				url: `/api/workbench/sessions/${SESSION_IDS.completed}/events/stream`
			});

		await page.getByTestId('session-rail').getByRole('link', { name: '会话' }).click();
		await expect
			.poll(() => eventSourceEvents(page))
			.toContainEqual({
				type: 'close',
				url: `/api/workbench/sessions/${SESSION_IDS.completed}/events/stream`
			});
		await expectNoForbiddenRoutes(authenticatedCalls);
	});

	test('does not show spike, BYOK, or data-root posture copy in the primary UI', async ({
		page
	}) => {
		const calls = await mockParityApi(page);

		await page.goto('/sessions');

		for (const copy of FORBIDDEN_PRIMARY_COPY) {
			await expect(page.getByText(copy, { exact: false })).toHaveCount(0);
		}
		await expectNoForbiddenRoutes(calls);
	});

	test('keeps parity routes usable across desktop, tablet, and mobile without leak strings', async ({
		page
	}) => {
		const consoleMessages: string[] = [];
		page.on('console', (message) => consoleMessages.push(message.text()));
		const calls = await mockParityApi(page);
		const screenshotRoot = 'test-results/parity/svelte';
		mkdirSync(screenshotRoot, { recursive: true });

		const routes = [
			{ path: '/login', name: 'login' },
			{ path: '/setup', name: 'setup' },
			{ path: '/sessions', name: 'sessions' },
			{ path: `/sessions/${SESSION_IDS.completed}`, name: 'session-detail' },
			{ path: '/settings/sources', name: 'settings-sources' },
			{ path: '/settings/sources/liepin', name: 'settings-sources-liepin' },
			{
				path: '/connections/liepin/conn-liepin-parity/login',
				name: 'liepin-connection'
			}
		];
		const viewports = [
			{ name: 'desktop', width: 1440, height: 960 },
			{ name: 'tablet', width: 1024, height: 900 },
			{ name: 'mobile', width: 390, height: 844 }
		];

		for (const viewport of viewports) {
			await page.setViewportSize({ width: viewport.width, height: viewport.height });
			for (const route of routes) {
				await page.goto(route.path);
				await expect(page.locator('body')).toBeVisible();
				await expect(page.locator('body')).not.toHaveText(
					/\[object Object\]|Unhandled parity mock route/
				);
				for (const leak of RAW_LEAK_STRINGS) {
					await expect(page.getByText(leak, { exact: false })).toHaveCount(0);
				}
				await page.screenshot({
					path: `${screenshotRoot}/${viewport.name}-${route.name}.png`,
					fullPage: true
				});
			}
		}

		const serializedCalls = JSON.stringify(calls);
		for (const leak of RAW_LEAK_STRINGS) {
			expect(consoleMessages.join('\n'), `console should not leak ${leak}`).not.toContain(leak);
			expect(serializedCalls, `safe call log should not leak ${leak}`).not.toContain(leak);
		}
		await expectNoForbiddenRoutes(calls);
		expect(calls.unhandled, 'all parity API calls should be explicitly mocked').toEqual([]);
	});

	test('renders source settings and Liepin safe status route without legacy browser calls', async ({
		page
	}) => {
		const calls = await mockParityApi(page);

		await page.goto('/settings/sources');
		await expect(page.getByRole('heading', { name: '检索渠道' })).toBeVisible();
		await expect(page.getByText('CTS', { exact: true })).toBeVisible();
		await expect(page.getByText('猎聘', { exact: true })).toBeVisible();
		await expect(page.getByRole('link', { name: '管理猎聘' })).toHaveAttribute(
			'href',
			'/settings/sources/liepin'
		);

		await page.goto('/settings/sources/liepin');
		await expect(page.getByRole('heading', { name: '猎聘连接' })).toBeVisible();
		await expect(page.getByText('Liepin parity connection')).toBeVisible();
		await expect(page.getByRole('link', { name: '查看连接状态' })).toHaveAttribute(
			'href',
			'/connections/liepin/conn-liepin-parity/login'
		);

		await page.goto('/connections/liepin/conn-liepin-parity/login');
		await expect(page.getByRole('heading', { name: 'Liepin browser session' })).toBeVisible();
		await expect(
			page.getByText('Interactive login is not handled in this Workbench route.')
		).toBeVisible();
		await expect(page.getByText(/will not\s+request credentials/)).toBeVisible();

		await expectNoForbiddenRoutes(calls);
		expect(calls.unhandled, 'all parity API calls should be explicitly mocked').toEqual([]);
	});

	test('does not use the old oklch spike theme in primary route CSS', async () => {
		const routeCss = readFileSync('src/routes/layout.css', 'utf8');

		expect(routeCss, 'primary route CSS should use the React hex visual contract').not.toContain(
			'oklch('
		);
	});

	test('keeps handwritten Svelte primary UI free of spike and legacy login relay strings', async () => {
		const forbidden = [
			/Svelte 5 Workbench Spike/,
			/Dev mode BYOK/,
			/data-root|data root|dataRoots/i,
			/login\/frame|login\/snapshot|login\/input|login\/complete/,
			/server_managed_browser|managed_local|external_http|dokobot_action/,
			/login-relay/
		];
		const files = handwrittenSourceFiles([
			'src/routes',
			'src/lib/components',
			'src/lib/workbench',
			'src/lib/api/workbench.ts'
		]);

		for (const file of files) {
			const source = readFileSync(file, 'utf8');
			for (const pattern of forbidden) {
				expect(source, `${file} should not contain ${String(pattern)}`).not.toMatch(pattern);
			}
		}
	});
});

function handwrittenSourceFiles(paths: string[]) {
	return paths.flatMap((path) => {
		const stat = statSync(path);
		if (stat.isFile()) {
			return [path];
		}
		return walk(path).filter(
			(file) =>
				/\.(svelte|ts)$/.test(file) &&
				!file.endsWith('schema.d.ts') &&
				!file.includes('/node_modules/')
		);
	});
}

function walk(path: string): string[] {
	return readdirSync(path).flatMap((entry) => {
		const fullPath = `${path}/${entry}`;
		const stat = statSync(fullPath);
		return stat.isDirectory() ? walk(fullPath) : [fullPath];
	});
}

function countApiCalls(calls: string[], call: string) {
	return calls.filter((item) => item === call).length;
}

async function installEventSourceRecorder(page: Page) {
	await page.addInitScript(() => {
		type EventSourceRecord = { type: 'open' | 'close'; url: string };
		const records: EventSourceRecord[] = [];
		const sources: EventTarget[] = [];

		class MockEventSource extends EventTarget {
			readonly url: string;
			readonly withCredentials = false;
			readyState = 1;
			onopen: ((event: Event) => void) | null = null;
			onmessage: ((event: MessageEvent) => void) | null = null;
			onerror: ((event: Event) => void) | null = null;

			constructor(url: string | URL) {
				super();
				const parsedUrl = new URL(String(url), window.location.origin);
				this.url = `${parsedUrl.pathname}${parsedUrl.search}`;
				sources.push(this);
				records.push({ type: 'open', url: this.url });
				setTimeout(() => this.onopen?.(new Event('open')), 0);
			}

			close() {
				if (this.readyState === 2) {
					return;
				}
				this.readyState = 2;
				records.push({ type: 'close', url: this.url });
			}
		}

		Object.defineProperty(window, '__workbenchEventSourceRecords', {
			value: records,
			configurable: true
		});
		Object.defineProperty(window, '__workbenchEventSources', {
			value: sources,
			configurable: true
		});
		Object.defineProperty(window, 'EventSource', {
			value: MockEventSource,
			configurable: true
		});
	});
}

async function dispatchWorkbenchEvent(page: Page, payload: Record<string, unknown>) {
	await page.evaluate((eventPayload) => {
		const sources = (
			window as Window & {
				__workbenchEventSources?: EventTarget[];
			}
		).__workbenchEventSources;
		const source = sources?.at(-1);
		source?.dispatchEvent(
			new MessageEvent('workbench_event', { data: JSON.stringify(eventPayload) })
		);
	}, payload);
}

async function eventSourceEvents(page: Page) {
	return page.evaluate(() => {
		const records = (
			window as Window & {
				__workbenchEventSourceRecords?: Array<{ type: 'open' | 'close'; url: string }>;
			}
		).__workbenchEventSourceRecords;
		return records ?? [];
	});
}

async function assertNoHorizontalOverflow(page: Page) {
	const overflow = await page.evaluate(() => document.body.scrollWidth - window.innerWidth);
	expect(overflow).toBeLessThanOrEqual(1);
}
