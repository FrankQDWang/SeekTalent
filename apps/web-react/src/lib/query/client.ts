import { QueryClient } from "@tanstack/react-query";

export function createWorkbenchQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 15_000,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export const queryClient = createWorkbenchQueryClient();
