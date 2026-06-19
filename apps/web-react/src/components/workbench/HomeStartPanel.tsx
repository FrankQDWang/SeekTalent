import { Search } from "lucide-react";
import { useState } from "react";
import { Button } from "../primitives/Button";
import { FieldInput } from "../primitives/FieldInput";
import { FieldTextarea } from "../primitives/FieldTextarea";
import "./HomeStartPanel.css";

export type HomeStartPanelSubmitInput = {
  jobDescription: string;
  jobTitle: string;
};

type HomeStartPanelProps = {
  errorMessage?: string | null;
  loading?: boolean;
  onSubmit: (input: HomeStartPanelSubmitInput) => Promise<void> | void;
};

export function HomeStartPanel({
  errorMessage = null,
  loading = false,
  onSubmit,
}: HomeStartPanelProps) {
  const [jobTitle, setJobTitle] = useState("");
  const [jobDescription, setJobDescription] = useState("");
  const [fallbackErrorMessage, setFallbackErrorMessage] = useState<
    string | null
  >(null);
  const trimmedJobTitle = jobTitle.trim();
  const trimmedJobDescription = jobDescription.trim();
  const displayErrorMessage = errorMessage ?? fallbackErrorMessage;
  const submitDisabled = loading || trimmedJobDescription.length === 0;

  return (
    <section aria-label="新建招聘任务" className="home-start-panel">
      <div className="home-start-panel__body">
        <div className="home-start-panel__copy">
          <p>新任务</p>
          <h2>粘贴 JD，先确认画像，再开始寻才</h2>
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
                jobTitle: trimmedJobTitle,
              });
              setJobDescription("");
              setJobTitle("");
            } catch {
              setFallbackErrorMessage("请求失败，请稍后重试。");
            }
          }}
        >
          <FieldInput
            autoComplete="off"
            disabled={loading}
            label="职位名称"
            onChange={(event) => setJobTitle(event.currentTarget.value)}
            placeholder="例如：AI Agent 平台工程师"
            value={jobTitle}
          />
          <FieldTextarea
            disabled={loading}
            label="职位描述"
            onChange={(event) => setJobDescription(event.currentTarget.value)}
            placeholder="粘贴 JD、岗位目标、硬性要求或补充背景"
            rows={8}
            value={jobDescription}
          />
          {displayErrorMessage ? (
            <p className="home-start-panel__error" role="alert">
              {displayErrorMessage}
            </p>
          ) : null}
          <div className="home-start-panel__actions">
            <Button
              disabled={submitDisabled}
              icon={<Search aria-hidden="true" size={16} />}
              loading={loading}
              tone="primary"
              type="submit"
            >
              开始寻才
            </Button>
          </div>
        </form>
      </div>
    </section>
  );
}
