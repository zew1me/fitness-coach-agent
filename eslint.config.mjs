import { defineConfig, globalIgnores } from "eslint/config";
import { FlatCompat } from "@eslint/eslintrc";
import js from "@eslint/js";
import importPlugin from "eslint-plugin-import";
import jsxA11y from "eslint-plugin-jsx-a11y";
import tseslint from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import unusedImports from "eslint-plugin-unused-imports";
import globals from "globals";
import path from "node:path";
import { fileURLToPath } from "node:url";

const productionFiles = [
  "app/**/*.{ts,tsx}",
  "components/**/*.{ts,tsx}",
  "lib/**/*.{ts,tsx}",
];
const testFiles = ["tests/web/**/*.{ts,tsx}"];
const compat = new FlatCompat({
  baseDirectory: path.dirname(fileURLToPath(import.meta.url)),
});

export default defineConfig([
  ...compat.extends("next/core-web-vitals"),
  // Generated output, dependency and tool caches, and local agent/worktree state are not source we
  // own. Playwright specs (tests/ui) follow Playwright's async fixture style rather than this
  // app ruleset, and are still type-checked by `tsc` through tsconfig.json.
  globalIgnores([
    ".cache/**",
    ".claude/**",
    ".next/**",
    ".remember/**",
    ".uv-cache/**",
    ".venv/**",
    ".vercel/**",
    ".worktrees/**",
    "node_modules/**",
    "tests/ui/**",
  ]),
  js.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        project: "./tsconfig.json",
      },
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      "@typescript-eslint": tseslint,
      import: importPlugin,
      "jsx-a11y": jsxA11y,
      "unused-imports": unusedImports,
    },
    settings: {
      "import/resolver": {
        typescript: true,
      },
    },
    rules: {
      complexity: ["error", 9],
      "import/no-default-export": "error",
      "import/order": [
        "error",
        {
          "newlines-between": "always",
          alphabetize: { order: "asc", caseInsensitive: true },
        },
      ],
      "no-console": ["error", { allow: ["warn", "error"] }],
      "no-restricted-syntax": [
        "error",
        {
          selector: "TSEnumDeclaration",
          message: "Prefer union literals to enums.",
        },
      ],
      "@typescript-eslint/consistent-type-definitions": ["error", "type"],
      "@typescript-eslint/explicit-function-return-type": "error",
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-misused-promises": "error",
      "@typescript-eslint/no-unnecessary-condition": "error",
      // The core rule below (args: "none") owns unused variables. The TS variant would report each
      // unused local twice and also flag parameters that fakes and callbacks must declare to match
      // a signature.
      "@typescript-eslint/no-unused-vars": "off",
      "no-unused-vars": ["error", { args: "none" }],
      "@typescript-eslint/require-await": "error",
      "unused-imports/no-unused-imports": "error",
    },
  },
  {
    files: productionFiles,
    rules: {
      "@typescript-eslint/no-non-null-assertion": "error",
    },
  },
  {
    // Next.js App Router loads pages, layouts and other route segment files by their default export.
    files: ["app/**/*.tsx", "app/**/layout.tsx", "app/**/page.tsx"],
    rules: {
      "import/no-default-export": "off",
    },
  },
  {
    // Each tool loads its config file by its default export.
    files: [
      "doctor.config.ts",
      "next.config.ts",
      "vitest.config.ts",
      "vitest.oai.config.ts",
      "playwright.config.ts",
    ],
    rules: {
      "import/no-default-export": "off",
    },
  },
  {
    // CSS module declarations mirror `import styles from "./x.module.css"`, which is a default import.
    files: ["**/*.module.css.d.ts"],
    rules: {
      "import/no-default-export": "off",
    },
  },
  {
    files: testFiles,
    rules: {
      complexity: ["error", 12],
      // Tests build partial fakes of SDK and Supabase objects and assert on values a preceding
      // `expect` has already proved present; neither rule protects production behaviour there.
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-non-null-assertion": "off",
    },
  },
]);
