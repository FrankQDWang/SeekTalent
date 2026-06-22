import type { Meta, StoryObj } from "@storybook/react-vite";
import { ConversationList } from "./ConversationList";
import { ConversationShell } from "./ConversationShell";
import { HomeStartPanel } from "./HomeStartPanel";

function HomeStartPanelStory() {
  return (
    <ConversationShell
      main={<HomeStartPanel onSubmit={() => undefined} />}
      rail={<ConversationList conversations={[]} />}
    />
  );
}

const meta = {
  title: "Workbench/HomeStartPanel",
  component: HomeStartPanelStory,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof HomeStartPanelStory>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Initial: Story = {};
