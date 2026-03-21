import js from "@eslint/js";
import nextPlugin from "@next/eslint-plugin-next";
import importPlugin from "eslint-plugin-import";
import jsxA11y from "eslint-plugin-jsx-a11y";
import tseslint from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import unusedImports from "eslint-plugin-unused-imports";
import globals from "globals";

const productionFiles = ["app/**/*.{ts,tsx}", "components/**/*.{ts,tsx}", "lib/**/*.{ts,tsx}"];
const testFiles = ["tests/web/**/*.{ts,tsx}"];

export default [
  {
    ignores: [".cache/**", ".next/**", ".uv-cache/**", ".venv/**", "node_modules/**"]
  },
  js.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        project: "./tsconfig.json"
      },
      globals: {
        ...globals.browser,
        ...globals.node
      }
    },
    plugins: {
      "@next/next": nextPlugin,
      "@typescript-eslint": tseslint,
      import: importPlugin,
      "jsx-a11y": jsxA11y,
      "unused-imports": unusedImports
    },
    settings: {
      "import/resolver": {
        typescript: true
      }
    },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      "complexity": ["error", 8],
      "import/no-default-export": "error",
      "import/order": [
        "error",
        {
          "newlines-between": "always",
          alphabetize: { order: "asc", caseInsensitive: true }
        }
      ],
      "no-console": ["error", { allow: ["warn", "error"] }],
      "no-restricted-syntax": [
        "error",
        {
          selector: "TSEnumDeclaration",
          message: "Prefer union literals to enums."
        }
      ],
      "@typescript-eslint/consistent-type-definitions": ["error", "type"],
      "@typescript-eslint/explicit-function-return-type": "error",
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-misused-promises": "error",
      "@typescript-eslint/no-unnecessary-condition": "error",
      "@typescript-eslint/no-unused-vars": "off",
      "@typescript-eslint/require-await": "error",
      "@next/next/no-html-link-for-pages": "off",
      "unused-imports/no-unused-imports": "error"
    }
  },
  {
    files: productionFiles,
    rules: {
      "@typescript-eslint/no-non-null-assertion": "error"
    }
  },
  {
    files: ["app/**/*.tsx", "app/**/layout.tsx", "app/**/page.tsx"],
    rules: {
      "import/no-default-export": "off"
    }
  },
  {
    files: ["next.config.ts", "vitest.config.ts"],
    rules: {
      "import/no-default-export": "off"
    }
  },
  {
    files: testFiles,
    rules: {
      "complexity": ["error", 12],
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-non-null-assertion": "off"
    }
  }
];
