import './styles.css';

import { RouterProvider } from '@tanstack/react-router';
import { createRoot } from 'react-dom/client';

import { createWorkbenchApi } from './api';
import { createWorkbenchRouter } from './app';

const root = document.querySelector<HTMLDivElement>('#app');

if (!root) {
  throw new Error('App root not found.');
}

const router = createWorkbenchRouter({ api: createWorkbenchApi() });

createRoot(root).render(<RouterProvider router={router} />);
