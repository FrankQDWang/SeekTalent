import { describe, expect, it } from 'vitest';

import {
	readinessStatusLabel,
	readinessTone,
	selectedSourceKinds,
	sourceReasonLabel,
	sourceStatusLabel
} from './sourceDisplay';

describe('source display helpers', () => {
	it('preserves explicit source order', () => {
		expect(selectedSourceKinds({ cts: true, liepin: true })).toEqual(['cts', 'liepin']);
		expect(selectedSourceKinds({ cts: false, liepin: true })).toEqual(['liepin']);
	});

	it('maps statuses to business-facing labels', () => {
		expect(sourceStatusLabel('running')).toBe('检索中');
		expect(sourceStatusLabel('blocked')).toBe('已阻塞');
		expect(readinessStatusLabel('missing')).toBe('缺少配置');
		expect(readinessTone('configured')).toBe('ready');
		expect(readinessTone('missing')).toBe('warning');
		expect(sourceReasonLabel('blocked_backend_unavailable')).toContain('暂不可用');
		expect(sourceReasonLabel('secret-token')).toBe('检索源需要处理。');
	});

	it('maps local browser setup reasons without developer implementation terms', () => {
		const browserReasons = [
			'liepin_opencli_extension_disconnected',
			'liepin_opencli_status_unavailable',
			'liepin_opencli_host_blocked',
			'liepin_opencli_detail_not_opened',
			'liepin_browser_probe_unavailable'
		];

		for (const reason of browserReasons) {
			const label = sourceReasonLabel(reason) ?? '';
			expect(label).toMatch(/浏览器|Chrome/);
			expect(label).not.toMatch(/Pi|DokoBot|MCP/i);
		}
		expect(sourceReasonLabel('liepin_browser_login_required')).toContain('本机 Chrome 登录猎聘');
		expect(sourceReasonLabel('liepin_opencli_login_required')).toContain('登录猎聘');
		expect(sourceReasonLabel('liepin_opencli_identity_intercept')).toContain('招聘身份');
		expect(sourceReasonLabel('liepin_opencli_risk_page')).toContain('人工确认');
		expect(sourceReasonLabel('liepin_opencli_detail_not_opened')).toContain('详情页');
		expect(sourceReasonLabel('liepin_opencli_extension_disconnected')).not.toMatch(
			/OpenCLI|CDP|MCP|DokoBot|风控/i
		);
	});

	it('maps public source reason codes to business-facing labels', () => {
		const publicReasons = [
			'source_login_required',
			'source_account_mismatch',
			'source_browser_timeout',
			'source_browser_backend_unavailable',
			'source_browser_extension_disconnected',
			'source_browser_policy_blocked',
			'source_browser_interaction_required',
			'source_risk_or_verification_required',
			'source_budget_exhausted',
			'source_provider_failed',
			'source_partial',
			'source_unknown'
		];

		for (const reason of publicReasons) {
			const label = sourceReasonLabel(reason) ?? '';
			expect(label.length).toBeGreaterThan(0);
			expect(label).not.toMatch(/OpenCLI|DokoBot|MCP|pi_agent|cookie|authorization/i);
			expect(label).not.toBe('检索源需要处理。');
		}
		expect(sourceReasonLabel('source_login_required')).toContain('登录');
	});
});
