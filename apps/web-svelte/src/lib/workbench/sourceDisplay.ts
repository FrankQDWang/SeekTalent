export type SourceKind = 'cts' | 'liepin';

export function sourceLabel(source: SourceKind) {
	return source === 'cts' ? 'CTS' : 'Liepin';
}

export function readinessTone(status: string) {
	if (status === 'ready' || status === 'configured') return 'ready';
	if (status === 'disabled' || status === 'missing' || status === 'needs_setup') return 'warning';
	return 'blocked';
}

export function readinessStatusLabel(status: string) {
	const labels: Record<string, string> = {
		ready: '可用',
		configured: '已配置',
		missing: '缺少配置',
		disabled: '未启用',
		invalid: '配置无效',
		needs_setup: '需要设置',
		safe: '安全',
		warning: '需注意',
		error: '不可用',
		unknown: '待确认'
	};
	return labels[status] ?? '待确认';
}

export function sourceStatusLabel(status: string) {
	const labels: Record<string, string> = {
		pending: '等待启动',
		queued: '排队中',
		running: '检索中',
		completed: '已完成',
		partial: '部分完成',
		blocked: '已阻塞',
		failed: '失败',
		cancelled: '已取消',
		draft: '草稿'
	};
	return labels[status] ?? status;
}

export function sourceReasonLabel(reasonCode: string | null | undefined) {
	const labels: Record<string, string> = {
		blocked_backend_unavailable: 'Liepin 浏览器执行暂不可用。',
		failed_provider_error: '检索源返回错误。',
		liepin_browser_login_required:
			'请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。',
		liepin_browser_probe_unavailable: '浏览器检索通道暂不可用，请确认本机应用和浏览器助手正常后重试。',
		liepin_browser_account_mismatch: '当前 Chrome 中的猎聘账号与此工作台绑定不一致，请切换账号后重试。',
		liepin_pi_disabled: '浏览器检索通道尚未启用。',
		liepin_pi_command_missing: '浏览器检索通道尚未安装或不可用，请先完成本机检索环境设置。',
		liepin_pi_command_invalid: '浏览器检索通道配置无效，请检查本机检索环境设置。',
		liepin_pi_skill_missing: '浏览器检索通道缺少本地执行说明，请重新初始化本机检索环境。',
		liepin_pi_account_secret_missing: '浏览器检索通道缺少本地账号绑定设置，请先完成本机检索环境设置。',
		liepin_pi_mcp_config_missing: '浏览器检索通道缺少本地工具配置，请先完成本机检索环境设置。',
		liepin_pi_mcp_config_invalid: '浏览器检索通道的本地工具配置无效，请修复后重试。',
		liepin_pi_dokobot_mcp_missing: '浏览器检索通道没有检测到网页操作工具，请先完成本机检索环境设置。',
		liepin_pi_dokobot_tool_unobserved: '浏览器检索通道未观察到网页操作能力，请确认本机浏览器助手已启用后重试。',
		login_required: '请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。',
		partial_timeout: '部分结果已返回，检索超时停止。',
		cancelled_by_user: '检索已取消。',
		liepin_connection_not_connected: '本机 Chrome 的猎聘登录态尚未就绪。'
	};
	if (!reasonCode) return null;
	return labels[reasonCode] ?? '检索源需要处理。';
}

export function selectedSourceKinds(input: { cts: boolean; liepin: boolean }): SourceKind[] {
	const result: SourceKind[] = [];
	if (input.cts) result.push('cts');
	if (input.liepin) result.push('liepin');
	return result;
}
