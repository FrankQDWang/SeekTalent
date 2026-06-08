import { defineConfig } from 'vitest/config';
import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';

const backendHost = process.env.SEEKTALENT_DEV_BACKEND_HOST ?? '127.0.0.1';
const backendPort = process.env.SEEKTALENT_DEV_BACKEND_PORT ?? '8012';
const frontendPort = Number(process.env.SEEKTALENT_DEV_FRONTEND_PORT ?? '5178');

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	server: {
		port: frontendPort,
		strictPort: true,
		proxy: {
			'/api': `http://${backendHost}:${backendPort}`
		}
	},
	test: {
		expect: { requireAssertions: true },
		environment: 'jsdom',
		include: ['src/**/*.test.{ts,svelte.ts}'],
		setupFiles: ['./src/test/setup.ts'],
		projects: [
			{
				extends: './vite.config.ts',
				resolve: {
					conditions: ['browser']
				},
				test: {
					name: 'client',
					environment: 'jsdom',
					include: ['src/lib/components/**/*.{test,spec}.ts', 'src/test/**/*.{test,spec}.ts'],
					setupFiles: ['./src/test/setup.ts'],
					expect: { requireAssertions: true }
				}
			},
			{
				extends: './vite.config.ts',
				test: {
					name: 'server',
					environment: 'node',
					include: ['src/**/*.{test,spec}.{js,ts}'],
					exclude: [
						'src/**/*.svelte.{test,spec}.{js,ts}',
						'src/lib/components/**/*.{test,spec}.ts',
						'src/test/**/*.{test,spec}.ts'
					],
					expect: { requireAssertions: true }
				}
			}
		]
	}
});
