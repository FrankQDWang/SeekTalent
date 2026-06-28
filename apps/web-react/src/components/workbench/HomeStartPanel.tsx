import { ArrowUp, Check, CornerDownLeft } from "lucide-react";
import { useRef, useState } from "react";
import { FieldTextarea } from "../primitives/FieldTextarea";
import "./HomeStartPanel.css";

export type HomeStartPanelSubmitInput = {
  message: string;
};

type HomeStartPanelProps = {
  errorMessage?: string | null;
  initialMessage?: string;
  loading?: boolean;
  onSubmit: (input: HomeStartPanelSubmitInput) => Promise<void> | void;
};

export function HomeStartPanel({
  errorMessage = null,
  initialMessage = "",
  loading = false,
  onSubmit,
}: HomeStartPanelProps) {
  const formRef = useRef<HTMLFormElement | null>(null);
  const [message, setMessage] = useState(initialMessage);
  const [fallbackErrorMessage, setFallbackErrorMessage] = useState<
    string | null
  >(null);
  const trimmedMessage = message.trim();
  const displayErrorMessage = errorMessage ?? fallbackErrorMessage;
  const submitDisabled = loading || trimmedMessage.length === 0;

  return (
    <section aria-label="新建招聘任务" className="home-start-panel">
      <div className="home-start-panel__body">
        <div className="home-start-panel__copy">
          <h2>Wide Talent Search</h2>
        </div>
        <form
          className="home-start-panel__form"
          ref={formRef}
          onSubmit={async (event) => {
            event.preventDefault();
            if (submitDisabled) {
              return;
            }
            setFallbackErrorMessage(null);
            try {
              await onSubmit({ message: trimmedMessage });
              setMessage("");
            } catch {
              setFallbackErrorMessage("请求失败，请稍后重试。");
            }
          }}
        >
          <div className="home-start-panel__input-card">
            <FieldTextarea
              disabled={loading}
              hideLabel
              label="消息、JD 或招聘需求"
              onChange={(event) => setMessage(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter" || event.shiftKey) {
                  return;
                }
                event.preventDefault();
                formRef.current?.requestSubmit();
              }}
              placeholder=""
              rows={5}
              value={message}
            />
            {message.length === 0 ? (
              <div aria-hidden="true" className="home-start-panel__placeholder">
                <span>输入 </span>
                <strong>消息 / JD / 招聘需求</strong>
                <span>，开始整理候选人搜索</span>
              </div>
            ) : null}
            <div className="home-start-panel__input-footer">
              <span className="home-start-panel__source-chip">
                <Check aria-hidden="true" size={16} />
                猎聘
              </span>
              <button
                aria-label="开始寻才"
                className="home-start-panel__submit"
                disabled={submitDisabled}
                type="submit"
              >
                <ArrowUp aria-hidden="true" size={18} />
              </button>
            </div>
          </div>
          {displayErrorMessage ? (
            <p className="home-start-panel__error" role="alert">
              {displayErrorMessage}
            </p>
          ) : null}
        </form>
        <div aria-label="快速填充示例" className="home-start-panel__prompts">
          {examplePrompts.map((prompt, index) => (
            <button
              className="home-start-panel__prompt"
              key={`${prompt}-${index.toString()}`}
              onClick={() => setMessage(prompt)}
              type="button"
            >
              <span>{prompt}</span>
              <CornerDownLeft aria-hidden="true" size={20} />
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

const examplePrompts = [
  "你好，先帮我梳理一个招聘需求。",
  "上海 AI Agent 平台工程师，3 年以上 Python 后端经验，熟悉 RAG 和 workflow orchestration。",
  "北京搜索推荐算法负责人，需要多路召回、排序模型和候选人画像系统经验。",
  "杭州 B 端产品设计负责人，负责复杂工作台体验、数据看板和跨团队落地。",
];
