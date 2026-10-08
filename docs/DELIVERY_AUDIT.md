# Optional delivery asset audit

`scripts/delivery_audit.py` checks a delivery directory without changing its files, starting an HTTP server, contacting a model, compiling a paper, or creating an archive. It is independent of manuscript repair. Any delivery directory and file set can be used; no username, experiment case or PDF count is required.

Run the HTML check alone, or supply trusted file hashes and service registrations:

```powershell
python scripts/delivery_audit.py --root "<delivery>"
python scripts/delivery_audit.py --root "<delivery>" --manifest "<frozen-manifest.json>" --services "<service-bindings.json>"
```

JSON is printed to stdout; exit status is `0` for pass and `1` for failure. The audit checks every HTML file under the root. `src` and `href` file links must resolve to existing files inside that root. Query strings and fragments do not form part of file names. File-viewed relative URLs are decoded before containment checks, so `%20` works for spaces and encoded traversal cannot escape the directory. HTML `<base href>` is rejected because it changes that path mapping. External HTTP, HTTPS, data, JavaScript, mail and telephone URLs are outside the local-file audit.

## File manifest

The optional manifest verifies each declared file against a SHA-256 recorded at the chosen freeze/export point:

```json
{
  "files": [
    {"path": "viewer.html", "sha256": "<64 hexadecimal characters>"},
    {"path": "pdfs/result.pdf", "sha256": "<64 hexadecimal characters>"}
  ]
}
```

A `files` object mapping relative paths to SHA strings is also accepted. Paths use forward slashes and must remain inside the delivery. Duplicate paths, missing files and hash differences fail. Only declared files are checked against expected hashes; the manifest does not imply the absence of undeclared files. Store expected hashes before the audit rather than recomputing them from the files being checked.

## Declared source services

Ordinary viewer/report HTML cannot use a root-relative local link such as `/styles.css`, even if `styles.css` exists beside the viewer. A frozen source service page may use such a link only after a matching registration is verified:

```json
{
  "services": [
    {
      "html": "tools/preview/web/index.html",
      "server": "tools/preview/local_server.py",
      "ui_root": "tools/preview/web",
      "server_sha256": "<trusted frozen server SHA-256>"
    }
  ]
}
```

Registrations are explicit per HTML page. All registration paths are relative to the delivery root; its HTML must be inside its declared UI root. The server SHA is verified first. Its source is then parsed as Python AST, without importing or executing it. The supported static contract requires:

- One unmodified `from pathlib import Path` binding and one `UI_ROOT` assignment derived solely from `Path(__file__).resolve()`, `.parent` and `/` string literals, resolving to the declared UI root.
- One `_serve_static` method beginning with `rel = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")`, followed by `path = (UI_ROOT / rel).resolve()`.
- A subsequent `try: path.relative_to(UI_ROOT)` and a `ValueError` handler that returns, rejecting paths outside the UI root.

Unknown mappings, source SHA differences and missing containment guards fail. Registered root-relative URLs follow that actual server mapping: query/fragment suffixes are removed, `/` maps to `index.html`, and percent escapes remain literal because this supported server does not decode them. Assets must be existing files inside the UI root. Reports record the verified server identity and each served asset's path and SHA. Supply those asset hashes in the optional manifest when comparison with a frozen asset identity is required.

Python callers can use `audit_delivery(root, manifest=manifest, services=registrations)` for a complete report, or `check_html_links(root, html, services=registrations)` for the `(broken_links, served_assets)` pair. Invalid service declarations raise `ValueError` in the single-page helper; the full audit reports them as failures.

This is a static file/link audit, not browser or HTTP execution validation. It checks HTML `src`/`href` references, not resources discovered through CSS, JavaScript, `srcset`, or dynamic routes. A recorded SHA binds the audit to the supplied snapshot; it does not establish who approved that snapshot.
