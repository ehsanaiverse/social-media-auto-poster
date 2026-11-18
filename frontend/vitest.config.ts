// Frontend tests (Vitest + Testing Library), on top of the app's own Vite config.
// They never reach the network: every test mocks src/services/apiService.ts.
import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config';

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      restoreMocks: true
    }
  })
);
