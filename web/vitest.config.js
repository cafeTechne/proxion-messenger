import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        globals: true,
        environment: 'node',
        envFiles: ['.env.test'],
        testTimeout: 30000,
        hookTimeout: 60000,
    },
});
