import { defineConfig } from 'eslint/config';
import js from '@eslint/js';
import globals from 'globals';
export default defineConfig([
  { ignores: ['.venv/**', '.tools/**', 'node_modules/**', 'dist/**', 'build/**'] },
  {
    files: ['**/*.{js,mjs,cjs,jsx,ts,tsx,mts,cts}'],
    extends: [js.configs.recommended],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
  },
]);
