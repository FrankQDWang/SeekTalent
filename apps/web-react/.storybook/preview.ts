import type { Preview } from "@storybook/react-vite";
import { QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { createWorkbenchQueryClient } from "../src/lib/query/client";
import "../src/styles/tokens.css";
import "../src/styles/base.css";

const storybookQueryClient = createWorkbenchQueryClient();

const preview: Preview = {
  decorators: [
    (Story) =>
      createElement(
        QueryClientProvider,
        { client: storybookQueryClient },
        createElement(Story),
      ),
  ],
  parameters: {
    a11y: { test: "error" },
    controls: { expanded: true },
  },
};

export default preview;
