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
			'liepin_pi_command_missing',
			'liepin_pi_command_invalid',
			'liepin_pi_skill_missing',
			'liepin_pi_account_secret_missing',
			'liepin_pi_mcp_config_missing',
			'liepin_pi_mcp_config_invalid',
			'liepin_pi_dokobot_mcp_missing',
			'liepin_pi_dokobot_tool_unobserved',
			'liepin_browser_probe_unavailable'
		];

		for (const reason of browserReasons) {
			const label = sourceReasonLabel(reason) ?? '';
			expect(label).toContain('浏览器');
			expect(label).not.toMatch(/Pi|DokoBot|MCP/i);
		}
		expect(sourceReasonLabel('liepin_browser_login_required')).toContain('本机 Chrome 登录猎聘');
	});
});
