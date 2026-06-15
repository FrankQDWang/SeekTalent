import type { Preview } from "@storybook/react-vite";
import "../src/styles/tokens.css";
import "../src/styles/base.css";

const preview: Preview = {
  parameters: {
    a11y: { test: "error" },
    controls: { expanded: true },
  },
};

export default preview;
