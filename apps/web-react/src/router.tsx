import { createRouter, RouterProvider } from "@tanstack/react-router";
import { conversationRoute } from "./routes/conversation";
import { indexRoute, rootRoute } from "./routes/root";

const routeTree = rootRoute.addChildren([indexRoute, conversationRoute]);

export const router = createRouter({ routeTree });
export { RouterProvider };

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
