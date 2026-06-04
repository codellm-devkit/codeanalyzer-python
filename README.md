# codeanalyzer-python — documentation site

This branch holds the documentation site for [**codeanalyzer-python**](https://github.com/codellm-devkit/codeanalyzer-python), the Python static-analysis backend behind [CLDK](https://github.com/codellm-devkit/python-sdk). The tool's source code lives on `main`; this `docs` branch is the [Astro](https://astro.build/) + [Starlight](https://starlight.astro.build/) site that documents it.

## Develop

```shell
npm install        # install dependencies
npm run dev        # local dev server at http://localhost:4321
npm run build      # production build into ./dist
npm run preview    # preview the production build
```

## Structure

```
src/
  content/
    docs/
      index.mdx                     # landing page (splash)
      what-is-codeanalyzer.mdx
      quickstart.mdx
      installing.mdx
      guides/
        cli-usage.mdx
        concepts.mdx
        codeql.mdx
        entrypoints.mdx
      reference/
        cli.mdx                     # CLI option reference
        schema.mdx                  # PyApplication output schema
      extending/
        analysis-passes.mdx         # writing passes / entrypoint finders
  styles/docs.css                   # theme
  assets/                           # logo
astro.config.mjs                    # site + sidebar config
```

## Internal links (important)

The site is served from a **base path** — `https://codellm-devkit.github.io/codeanalyzer-python/` (set via `site` + `base` in `astro.config.mjs`). Astro does **not** rewrite links in page content, so every internal link must include the base prefix:

```md
<!-- correct -->
[Quickstart](/codeanalyzer-python/quickstart/)
<LinkCard href="/codeanalyzer-python/guides/concepts/" ... />

<!-- WRONG — 404s at the base path -->
[Quickstart](/quickstart/)
```

Sidebar `slug` entries in `astro.config.mjs` and assets are based automatically — only authored links in `.mdx` need the prefix. To verify after editing, build and grep `dist/` for any `href="/…"` that doesn't start with `/codeanalyzer-python/`.

## Deploy

Pushing to `docs` triggers `.github/workflows/deploy.yml`, which builds the site and publishes `dist/` to the `gh-pages` branch. GitHub Pages then serves it at `https://codellm-devkit.github.io/codeanalyzer-python/`.

## License

Apache 2.0 — see [LICENSE](./LICENSE).
