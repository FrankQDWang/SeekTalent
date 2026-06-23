import {
  createRootRoute,
  createRoute,
  Outlet,
  useNavigate,
} from "@tanstack/react-router";
import { useEffect } from "react";
import { App } from "../App";

export const rootRoute = createRootRoute({
  component: () => (
    <App>
      <Outlet />
    </App>
  ),
});

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: IndexRedirect,
});

function IndexRedirect() {
  const navigate = useNavigate({ from: "/" });

  useEffect(() => {
    navigate({ to: "/conversations/new" as never, replace: true });
  }, [navigate]);

  return null;
}