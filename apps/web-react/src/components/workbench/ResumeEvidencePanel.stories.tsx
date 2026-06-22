import type { Meta, StoryObj } from "@storybook/react-vite";

function ResumeEvidencePanelFullContent() {
  return (
    <div
      aria-label="简历详情完整内容"
      style={{
        alignItems: "flex-start",
        background: "rgb(137 137 137)",
        display: "block",
        minHeight: "2361px",
        padding: "89px 0 212px 222px",
      }}
    >
      <article
        aria-label="简历详情"
        style={{
          background: "var(--st-bg)",
          borderRadius: 4,
          boxShadow: "0 10px 28px rgb(0 0 0 / 14%)",
          color: "rgb(26 28 31)",
          minHeight: "2060px",
          overflow: "hidden",
          position: "relative",
          width: "726px",
        }}
      >
        <button
          aria-label="关闭"
          style={{
            background: "transparent",
            border: 0,
            color: "rgb(114 116 126)",
            fontSize: 18,
            height: 28,
            lineHeight: 1,
            padding: 0,
            position: "absolute",
            right: 10,
            top: 10,
            width: 28,
          }}
          type="button"
        >
          ×
        </button>
        <header
          style={{
            alignItems: "flex-start",
            background: "#f3f5ff",
            borderBottom: "1px solid rgb(229 232 242)",
            display: "grid",
            gap: 14,
            gridTemplateColumns: "54px minmax(0, 1fr) auto",
            padding: "28px 30px 24px",
          }}
        >
          <span
            aria-hidden="true"
            style={{
              alignItems: "center",
              background: "#6674f6",
              borderRadius: "999px",
              color: "var(--st-action-ink)",
              display: "inline-flex",
              fontSize: 20,
              fontWeight: 800,
              height: 54,
              justifyContent: "center",
              width: 54,
            }}
          >
            吴
          </span>
          <div>
            <strong style={{ display: "block", fontSize: 22 }}>吴所谓</strong>
            <p style={{ color: "rgb(74 77 91)", margin: "7px 0 0" }}>
              资深体验设计工程师 · 平安集团
            </p>
            <div
              aria-label="候选人标签"
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 8,
                marginTop: 16,
              }}
            >
              {["视觉设计", "体验设计", "上海", "本科", "3年", "30-100万"].map(
                (label) => (
                  <span
                    key={label}
                    style={{
                      background: "rgb(245 246 251)",
                      borderRadius: 4,
                      color: "rgb(106 109 124)",
                      fontSize: 12,
                      lineHeight: "22px",
                      padding: "0 9px",
                    }}
                  >
                    {label}
                  </span>
                ),
              )}
            </div>
          </div>
          <button
            style={{
              background: "var(--st-bg)",
              border: "1px solid var(--st-border)",
              borderRadius: 6,
              color: "var(--st-ink)",
              font: "inherit",
              fontWeight: 700,
              minHeight: 32,
              padding: "0 14px",
            }}
            type="button"
          >
            查看来源
          </button>
        </header>

        <div style={{ display: "grid", gap: 24, padding: "24px 30px 30px" }}>
          <ResumeSection
            title="匹配程度"
            items={[
              "推荐理由：可独立主导 0-1 产品体验搭建，擅长拆解复杂业务流程，通过用户调研、行为数据定位核心痛点，输出可量化的体验优化策略。多次通过流程重构提升任务完成率。",
              "候选人满足“能快速通过定性+定量调研确定产品用户真实痛点，搭建可量化体验衡量体系”。具备完整设计系统从 0 到 1 搭建经验，可深度联动前端交付高保真代码同时；面对多角色、长流程、高复杂度业务场景能输出清晰兼顾效率与审美方案。",
              "候选人拥有 AI 产品体验设计相关长周期协作和项目落地经验。",
            ]}
          />
          <ResumeSection
            title="求职意向"
            items={[
              "期望岗位：高级设计师、设计、设计经理/主管",
              "期望行业：互联网、其他",
              "期望城市：上海",
              "期望薪资：20-24K*14薪",
            ]}
          />
          <ResumeSection
            title="工作经历"
            items={[
              "2019.06 - 至今（7年） 平安项目｜用户体验设计专家。工作内容：1. 围绕保险及 CRM 体验设计方案；2. 负责策略、流程、组件规范与视觉小程序设计；3. 通过参与需求、拆解产品目标、竞品分析、制定设计策略；4. 主导交互及视觉改造，评审稿件到交付规范并完成设计闭环。",
              "2017.06 - 2019.06（2年10个月） 国美在线｜资深交互设计师。工作内容：1. 负责智能硬件系统及 APP 交互方案；2. 推动用户研究、信息架构和跨端流程优化；3. 输出可复用组件和页面规范并跟进开发落地。",
              "2014.07 - 2017.08（3年1个月） 天猫视觉体验设计部门｜交互设计师。工作内容：1. 负责复杂交易链路、运营场景及用户使用路径；2. 参与用户研究及任务分析；3. 通过数据复盘持续推进核心流程体验优化。",
            ]}
          />
          <ResumeSection
            title="项目经验"
            items={[
              "2020.05 - 至今（6年1个月） 陆小二业务体验｜项目群落：1. 通过设计调研，搭建企业多业务场景体验资产。2. 通过触点、服务流程和数据反馈持续推动核心任务效率目标。3. 上下游协作：输出 PRD、主流程交互方案、流程策略评审、视觉规范和用研结论，交付可落地的设计方案。",
              "2020.01 - 至今（6年5个月） 旗舰店 CRM｜体验设计负责人。项目内容：1. 账号体系分析、用户画像分层、策略分析、完善功能链路闭环；2. 主导交互及视觉风格设计，评审稿件到高保真设计输出；3. 持续优化大型业务（全流程策略、信息分类、经营录入、详情查看）分阶段设计效果和使用目标。",
            ]}
          />
          <ResumeSection
            title="教育经历"
            items={[
              "2011.09 - 2014.07（3年10个月） 华东师范大学｜工业设计｜硕士",
              "2007.09 - 2011.06（3年9个月） 中国美术大学｜工业设计｜学士",
            ]}
          />
          <ResumeSection
            title="技能标签"
            items={["技能标签1", "技能标签2", "技能标签3", "技能标签4"]}
            variant="tags"
          />
        </div>
      </article>
    </div>
  );
}

function ResumeSection({
  items,
  title,
  variant = "list",
}: {
  items: string[];
  title: string;
  variant?: "list" | "tags";
}) {
  return (
    <section>
      <h2
        style={{
          alignItems: "center",
          background: "#f7f8fb",
          color: "rgb(42 43 49)",
          display: "flex",
          fontSize: 16,
          fontWeight: 700,
          lineHeight: 1.4,
          margin: "0 0 14px",
          padding: "8px 10px 8px 12px",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            background: "#7263f2",
            borderRadius: 2,
            display: "inline-block",
            height: 16,
            marginRight: 10,
            width: 4,
          }}
        />
        {title}
      </h2>
      <ul
        style={{
          display: variant === "tags" ? "flex" : "grid",
          flexWrap: "wrap",
          gap: variant === "tags" ? 12 : 10,
          lineHeight: 1.8,
          listStyle: "none",
          margin: 0,
          padding: "0 8px",
        }}
      >
        {items.map((item) => (
          <li
            key={item}
            style={
              variant === "tags"
                ? {
                    background: "rgb(246 247 251)",
                    border: "1px solid rgb(232 234 242)",
                    borderRadius: 4,
                    color: "rgb(98 101 116)",
                    lineHeight: "26px",
                    padding: "0 12px",
                  }
                : undefined
            }
          >
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}

const meta = {
  title: "Workbench/ResumeEvidencePanel",
  component: ResumeEvidencePanelFullContent,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof ResumeEvidencePanelFullContent>;

export default meta;

type Story = StoryObj<typeof meta>;

export const FullContent: Story = {};
