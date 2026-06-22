import { ArrowUp, Check, CornerDownLeft } from "lucide-react";
import { useState } from "react";
import { FieldTextarea } from "../primitives/FieldTextarea";
import "./HomeStartPanel.css";

export type HomeStartPanelSubmitInput = {
  jobDescription: string;
  jobTitle: string | null;
};

type HomeStartPanelProps = {
  errorMessage?: string | null;
  initialJobDescription?: string;
  loading?: boolean;
  onSubmit: (input: HomeStartPanelSubmitInput) => Promise<void> | void;
};

export function HomeStartPanel({
  errorMessage = null,
  initialJobDescription = "",
  loading = false,
  onSubmit,
}: HomeStartPanelProps) {
  const [jobDescription, setJobDescription] = useState(initialJobDescription);
  const [fallbackErrorMessage, setFallbackErrorMessage] = useState<
    string | null
  >(null);
  const trimmedJobDescription = jobDescription.trim();
  const displayErrorMessage = errorMessage ?? fallbackErrorMessage;
  const submitDisabled = loading || trimmedJobDescription.length === 0;

  return (
    <section aria-label="新建招聘任务" className="home-start-panel">
      <div className="home-start-panel__body">
        <div className="home-start-panel__copy">
          <h2>Wide Talent Search</h2>
        </div>
        <form
          className="home-start-panel__form"
          onSubmit={async (event) => {
            event.preventDefault();
            if (submitDisabled) {
              return;
            }
            setFallbackErrorMessage(null);
            try {
              await onSubmit({
                jobDescription: trimmedJobDescription,
                jobTitle: null,
              });
              setJobDescription("");
            } catch {
              setFallbackErrorMessage("请求失败，请稍后重试。");
            }
          }}
        >
          <div className="home-start-panel__input-card">
            <FieldTextarea
              disabled={loading}
              hideLabel
              label="岗位名称和岗位JD"
              onChange={(event) => setJobDescription(event.currentTarget.value)}
              placeholder=""
              rows={5}
              value={jobDescription}
            />
            {jobDescription.length === 0 ? (
              <div aria-hidden="true" className="home-start-panel__placeholder">
                <span>请粘贴 </span>
                <strong>岗位名称/岗位JD等</strong>
                <span> 信息，快速匹配候选人</span>
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
              onClick={() => setJobDescription(prompt)}
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
  "这是一个标题这是一个标题这是一个标题",
  "这是一个标题这是一个标题这是一个标题",
  "这是一个标题这是一个标题这是一个标题",
  "这是一个标题这是一个标题这是一个标题",
];
