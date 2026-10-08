# Static source navigation

`host_context.py` reads source files and existing diagnosis reports. It does not modify the paper, expand TeX, compile, or call a model API.

```sh
paperfit run scripts/host_context.py --main "<root.tex>" --root "<project-root>" --output data/host_context.json
```

Set `--root` to the directory used for compilation when the main file is in a subdirectory. The working directory is the default if it contains the main file; otherwise the main file's parent is used. Literal `input`/`include` paths resolve against the declared project root first. A source-relative fallback is reported and needs host verification. Commented inputs, literal examples and macro definition bodies do not import source files.

The source map recognizes `figure`, `figure*`, `table`, `table*`, `wrapfigure`, `wraptable`, `sidewaysfigure`, `sidewaysfigure*`, `sidewaystable`, `sidewaystable*`, and `longtable`. The canonical `kind` remains `figure` or `table`; `environment` preserves the actual environment name. Anchors use the object's real file and line numbers, rather than a reference location or flattened include offsets.

Each object's `environment_arguments` contains the literal parameters available in source. Ordinary and sideways floats expose optional `placement`. Wrapped objects distinguish optional `lines`, required `placement`, optional `overhang`, and required `width`. Longtables expose optional `placement` and `column_specification`. Dimensions and macros in these parameters are not evaluated. Missing required parameters set `argument_status` and the anchor to `requires_host_review`.

Graphics records carry their asset path, source line, options, and `in_caption`. Images inside short or long captions are distinguished from body graphics. Compact findings also expose `source_environment` and `environment_arguments` when the source anchor is resolved.

`source_map.object_marker_coverage` is also exposed as `source_navigation_coverage` in the host context. It records supported environments, mapped objects, literal caption marker counts, unsupported or unmapped markers, and uncertain anchors. `partial` means the static navigation has gaps requiring host review. `static_markers_mapped` means the recognized literal markers were mapped; it does **not** prove that all objects produced by TeX expansion were found. Unknown caption carriers, `captionof`, unsupported float environments, malformed or unclosed objects, conditional sources, duplicate labels and nested objects require verification. Unresolved anchors cannot inherit a legacy `executable` assertion.

The host still needs to inspect rendered pages, verify macro-generated content and unsupported environments, and recompile after edits. Navigation coverage is separate from visual diagnosis coverage and does not establish clean layout or scientific equivalence.

## Current applicability of visual evidence

`vision.provider_status` and `vision.provider_clean` describe the provider's recorded diagnosis snapshot. A completed request, including one with zero findings, does not by itself establish that this snapshot applies to the current paper. `vision.evidence_status` is `current`, `stale_or_missing`, or `unknown`; `vision.applicable_to_current_artifacts` is respectively `true`, `false`, or `null`. The effective `vision.clean` is `null` whenever applicability is stale or unknown.

`artifact_freshness` exposes live freshness checks, recorded blocking checks, the selected runtime manifest, and source/PDF/page-image identity matches. The context recomputes identities through the runtime's read-only artifact collector and compares them with the latest local `run_result*.json` snapshot. Changed source, PDF or page images cannot reuse an old clean assertion, even when file timestamps are preserved. A missing or invalid snapshot, or snapshot paths outside the declared project, requires review; external snapshot paths are not followed. Artifact freshness is independent of source-navigation coverage and scientific-content checks.

Current visual evidence also requires a readable PDF with an available page count and a complete, readable set of rendered PNG pages. Missing pages, undecodable images, or an unavailable PDF page count block `vision.clean` even when all recorded hashes still match. The recorded provider status is preserved so a failed request remains distinguishable from an outdated completed diagnosis.

After paper or render changes, run `paperfit check-visual --main "<root.tex>"` to regenerate the diagnosis and runtime snapshot, then rebuild `host_context.json`. Review any remaining blocking checks and inspect the resulting pages. The context command itself does not compile, render, diagnose, or repair the paper.
