import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1";
const DEFAULT_MODEL_ID = "deepseek-v4-flash";

export default function registerSeekTalentRuntimeProvider(pi: ExtensionAPI) {
  const modelId = process.env.SEEKTALENT_PI_BAILIAN_MODEL_ID || DEFAULT_MODEL_ID;

  pi.registerProvider("bailian", {
    name: "SeekTalent Runtime Provider",
    baseUrl: process.env.SEEKTALENT_PI_BAILIAN_BASE_URL || DEFAULT_BASE_URL,
    apiKey: "SEEKTALENT_PI_BAILIAN_API_KEY",
    api: "openai-completions",
    models: [
      {
        id: modelId,
        name: `SeekTalent ${modelId}`,
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 128000,
        maxTokens: 8192,
        compat: {
          supportsDeveloperRole: false,
          supportsReasoningEffort: false,
          maxTokensField: "max_tokens",
        },
      },
    ],
  });
}
