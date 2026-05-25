export type SourceKind = 'cts' | 'liepin';

export function sourceLabel(source: SourceKind) {
	return source === 'cts' ? 'CTS' : '猎聘';
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
		source_login_required: '请先登录对应来源账号并保持会话有效，系统会在检索时使用该登录态。',
		source_account_mismatch: '当前来源账号与此工作台绑定不一致，请切换账号后重试。',
		source_browser_timeout: '浏览器检索等待超时，已停止本次检索。',
		source_browser_backend_unavailable:
			'浏览器检索通道暂不可用，请确认本机应用和浏览器助手正常后重试。',
		source_browser_extension_disconnected:
			'浏览器检索通道未连接，请确认本机浏览器助手已启用后重试。',
		source_browser_policy_blocked: '检索页面或动作不符合来源安全策略，已停止本次检索。',
		source_browser_interaction_required: '来源页面需要人工处理，请完成页面提示后重试。',
		source_risk_or_verification_required: '来源网站需要人工确认，请处理页面提示后重试。',
		source_budget_exhausted: '本轮来源检索额度已用完。',
		source_provider_failed: '检索源返回错误。',
		source_partial: '部分结果已返回，检索源提前停止。',
		source_unknown: '检索源状态需要确认。',
		blocked_backend_unavailable: 'Liepin 浏览器执行暂不可用。',
		failed_provider_error: '检索源返回错误。',
		liepin_browser_login_required:
			'请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。',
		liepin_browser_probe_unavailable:
			'浏览器检索通道暂不可用，请确认本机应用和浏览器助手正常后重试。',
		liepin_browser_account_mismatch:
			'当前 Chrome 中的猎聘账号与此工作台绑定不一致，请切换账号后重试。',
		liepin_pi_disabled: '浏览器检索通道尚未启用。',
		liepin_pi_command_missing: '浏览器检索通道尚未安装或不可用，请先完成本机检索环境设置。',
		liepin_pi_command_invalid: '浏览器检索通道配置无效，请检查本机检索环境设置。',
		liepin_pi_skill_missing: '浏览器检索通道缺少本地执行说明，请重新初始化本机检索环境。',
		liepin_pi_account_secret_missing:
			'浏览器检索通道缺少本地账号绑定设置，请先完成本机检索环境设置。',
		liepin_pi_mcp_config_missing: '浏览器检索通道缺少本地工具配置，请先完成本机检索环境设置。',
		liepin_pi_mcp_config_invalid: '浏览器检索通道的本地工具配置无效，请修复后重试。',
		liepin_pi_mcp_adapter_missing: '浏览器检索通道不可用，请到本机设置检查浏览器助手后重试。',
		liepin_pi_mcp_adapter_unavailable: '浏览器检索通道暂不可用，请到本机设置检查浏览器助手后重试。',
		liepin_pi_dokobot_mcp_command_missing:
			'浏览器检索通道缺少本地工具配置，请先完成本机检索环境设置。',
		liepin_pi_dokobot_mcp_config_mismatch:
			'浏览器检索通道的本地工具配置需要更新，请到本机设置检查后重试。',
		liepin_pi_dokobot_mcp_tool_names_missing:
			'浏览器检索通道缺少网页操作能力配置，请到本机设置检查后重试。',
		liepin_pi_dokobot_mcp_missing:
			'浏览器检索通道没有检测到网页操作工具，请先完成本机检索环境设置。',
		liepin_pi_dokobot_tool_unobserved:
			'浏览器检索通道未观察到网页操作能力，请确认本机浏览器助手已启用后重试。',
		liepin_opencli_backend_disabled: '浏览器检索通道尚未启用。',
		liepin_opencli_command_missing: '浏览器检索通道尚未安装或不可用，请先完成本机检索环境设置。',
		liepin_opencli_extension_disconnected:
			'浏览器检索通道未连接，请确认本机浏览器助手已启用后重试。',
		liepin_opencli_status_unavailable: '浏览器检索通道暂不可用，请确认本机浏览器助手正常后重试。',
		liepin_opencli_forbidden_command: '浏览器检索动作不在允许范围内，已停止本次检索。',
		liepin_opencli_forbidden_text: '检索关键词不符合本机安全策略，已停止本次检索。',
		liepin_opencli_host_blocked: '浏览器当前页面不在可检索范围内，已停止本次检索。',
		liepin_opencli_start_url_blocked: '浏览器当前起始页面不在可检索范围内，已停止本次检索。',
		liepin_opencli_window_policy_blocked: '浏览器窗口策略未满足，已停止本次检索。',
		liepin_opencli_budget_exhausted: '本轮浏览器检索额度已用完。',
		liepin_opencli_timeout: '浏览器检索等待超时，已停止本次检索。',
		liepin_opencli_login_required:
			'请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。',
		liepin_opencli_identity_intercept: '猎聘需要确认招聘身份，请在本机 Chrome 完成确认后重试。',
		liepin_opencli_risk_page: '猎聘需要人工确认当前页面，请在本机 Chrome 处理后重试。',
		liepin_opencli_unknown_modal: '页面出现需要人工确认的提示，请在本机 Chrome 处理后重试。',
		liepin_opencli_source_policy_missing: '浏览器检索缺少来源策略，请检查本机检索环境设置。',
		liepin_opencli_malformed_state: '浏览器页面状态无法安全识别，已停止本次检索。',
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
