import type { Meta, StoryObj } from "@storybook/react-vite";

function ResumeEvidencePanelFullContent() {
  return (
    <article
      aria-label="简历详情"
      style={{
        background: "var(--st-panel)",
        border: "1px solid var(--st-border)",
        borderRadius: "var(--st-radius-md)",
        maxWidth: 760,
        padding: "20px",
      }}
    >
      <h2>候选人 A 简历证据</h2>
      <section>
        <h3>最近项目</h3>
        <p>建设 Agent 工具调用平台，负责任务编排、检索链路和评测闭环。</p>
      </section>
      <section>
        <h3>匹配证据</h3>
        <ul>
          <li>Python 服务端和异步任务经验。</li>
          <li>RAG 检索、关键词扩展和候选人排序经验。</li>
          <li>上海团队管理经验。</li>
        </ul>
      </section>
    </article>
  );
}

const meta = {
  title: "Workbench/ResumeEvidencePanel",
  component: ResumeEvidencePanelFullContent,
} satisfies Meta<typeof ResumeEvidencePanelFullContent>;

export default meta;

type Story = StoryObj<typeof meta>;

export const FullContent: Story = {};
