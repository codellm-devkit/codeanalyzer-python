import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import mermaid from "astro-mermaid";
import { pluginCollapsibleSections } from "@expressive-code/plugin-collapsible-sections";
import { pluginLineNumbers } from "@expressive-code/plugin-line-numbers";

// https://astro.build/config
export default defineConfig({
  site: "https://codellm-devkit.github.io",
  base: "/codeanalyzer-python",
  integrations: [
    // Mermaid must run BEFORE Starlight so it can preprocess ```mermaid blocks.
    mermaid({
      theme: "neutral",
      autoTheme: true,
      mermaidConfig: {
        flowchart: { curve: "basis" },
      },
    }),
    starlight({
      title: "codeanalyzer-python",
      tagline: "Static analysis for Python your agents can call.",
      description:
        "codeanalyzer-python turns a Python project into a typed symbol table and call graph — emitted as one analysis JSON artifact or a queryable Neo4j property graph — using Jedi, CodeQL, and Tree-sitter. The Python backend behind CLDK.",
      logo: {
        src: "./src/assets/logo.png",
        replacesTitle: true,
      },
      favicon: "/favicon.png",
      customCss: ["./src/styles/docs.css"],
      expressiveCode: {
        plugins: [pluginCollapsibleSections(), pluginLineNumbers()],
        styleOverrides: {
          borderRadius: "0.4rem",
          frames: {
            shadowColor: "transparent",
          },
        },
        defaultProps: {
          showLineNumbers: false,
        },
      },
      head: [
        {
          tag: "link",
          attrs: { rel: "preconnect", href: "https://fonts.googleapis.com" },
        },
        {
          tag: "link",
          attrs: {
            rel: "preconnect",
            href: "https://fonts.gstatic.com",
            crossorigin: "",
          },
        },
        {
          tag: "link",
          attrs: {
            rel: "stylesheet",
            href: "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap",
          },
        },
      ],
      social: [
        {
          icon: "github",
          label: "codeanalyzer-python on GitHub",
          href: "https://github.com/codellm-devkit/codeanalyzer-python",
        },
        {
          icon: "seti:python",
          label: "codeanalyzer-python on PyPI",
          href: "https://pypi.org/project/codeanalyzer-python",
        },
        {
          icon: "discord",
          label: "CLDK on Discord",
          href: "https://discord.gg/zEjz9YrmqN",
        },
      ],
      editLink: {
        baseUrl:
          "https://github.com/codellm-devkit/codeanalyzer-python/edit/docs/",
      },
      sidebar: [
        {
          label: "Start here",
          items: [
            { label: "What is codeanalyzer-python?", slug: "what-is-codeanalyzer" },
            { label: "Quickstart", slug: "quickstart" },
            { label: "Installation", slug: "installing" },
          ],
        },
        {
          label: "Guides",
          items: [
            { label: "CLI usage", slug: "guides/cli-usage" },
            { label: "Core concepts", slug: "guides/concepts" },
            { label: "CodeQL analysis", slug: "guides/codeql" },
            { label: "Neo4j graph", slug: "guides/neo4j" },
          ],
        },
        {
          label: "Reference",
          items: [
            { label: "CLI options", slug: "reference/cli" },
            { label: "Output schema", slug: "reference/schema" },
          ],
        },
        {
          label: "Extending",
          items: [
            { label: "Overview", slug: "extending/overview" },
            { label: "Entrypoint detection", slug: "guides/entrypoints" },
            { label: "Analysis passes", slug: "extending/analysis-passes" },
          ],
        },
      ],
    }),
  ],
});
