// Flat config (ESLint 9+). Covers static/js/app.js (plain browser script) and
// static/src/graph/*.jsx (the React Flow island's source, bundled by `npm run build:js` —
// htmx.min.js, htmx-ext-sse.min.js, and the built graph-island.bundle.js itself are vendored,
// not linted).
import js from "@eslint/js";
import react from "eslint-plugin-react";

export default [
  // A bare `ignores` entry (no `files`) excludes globally, from every config object below
  // including js.configs.recommended — vendored/built files aren't ours to lint.
  { ignores: ["static/js/*.min.js", "static/js/graph-island.bundle.js"] },
  js.configs.recommended,
  {
    files: ["static/js/app.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        window: "readonly",
        document: "readonly",
        localStorage: "readonly",
        requestAnimationFrame: "readonly",
        console: "readonly",
        Promise: "readonly",
      },
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    files: ["static/src/graph/**/*.jsx"],
    plugins: { react },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: {
        window: "readonly",
        document: "readonly",
        console: "readonly",
      },
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      // Marks identifiers only referenced inside JSX (e.g. <ReactFlow>, <Handle>) as used —
      // without it, plain no-unused-vars can't see through JSX syntax and flags every imported
      // component as unused.
      "react/jsx-uses-vars": "error",
    },
  },
];
