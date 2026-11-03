"""Bounded literal include ownership for source checks, without TeX execution.

File-local checks cannot know the float/group surrounding an ``input``.  This
module reconstructs only an unchanged, literal, uniquely owned include graph.
An exact residual comparison can classify float-wrapper relocation as a source
review issue; it cannot certify numbering, page geometry or visual quality.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any

try:
    from source_map import _comment_syntax, _conditional_ranges, group, masked_tex
    from scientific_integrity import FLOAT_ENVS, _command
except ModuleNotFoundError:
    from .source_map import _comment_syntax, _conditional_ranges, group, masked_tex
    from .scientific_integrity import FLOAT_ENVS, _command

_INPUT = re.compile(r"\\(input|include)(?![A-Za-z@])")
_FLOAT_MARKER = re.compile(r"\\(begin|end)(?![A-Za-z@])")
_SIMPLE_FLOATS = {"table", "table*", "figure", "figure*", "sidewaystable",
                  "sidewaystable*", "sidewaysfigure", "sidewaysfigure*"}
_OPAQUE = re.compile(r"\\(?:if[A-Za-z@]*|else|fi|csname|catcode|includeonly|import|subimport|InputIfFileExists|inputminted)(?![A-Za-z@])")
_DEFINITION = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|"
    r"NewDocumentCommand|RenewDocumentCommand|ProvideDocumentCommand|"
    r"DeclareDocumentCommand|def|gdef|edef|xdef|let)\*?\s*(?:\{\s*)?"
    r"\\(input|include|begin|end|endinput|small|footnotesize|scriptsize|tiny|fontsize|normalsize)(?![A-Za-z@])")
_ENV_DEFINITION = re.compile(r"\\(?:newenvironment|renewenvironment|provideenvironment|NewDocumentEnvironment|RenewDocumentEnvironment)\*?\s*\{([^{}]+)\}")
_LIMITS = ("Unchanged literal input/include graph and exact source residual only; "
           "no macro expansion, conditional evaluation, TeX counter execution, "
           "PDF inspection, readability or final acceptance proof.")


class IncludeContextUnavailable(ValueError):
    pass


def _execution_syntax(source: str) -> str:
    """Respect literal unconditional terminals; endinput keeps its current line."""
    active = masked_tex(source)
    conditions = _conditional_ranges(active)
    for terminal in re.finditer(r"\\(?:end\s*\{document\}|endinput(?![A-Za-z@]))", active):
        if any(start <= terminal.start() < end for start, end in conditions):
            continue
        if re.match(r"\\end\s*\{", terminal[0]):
            return active[:terminal.start()]
        stop = active.find("\n", terminal.end())
        return active[:stop] if stop >= 0 else active
    return active


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or re.search(r"[\\:{}%#^~\x00]", value):
        raise IncludeContextUnavailable("nonliteral_or_unsafe_include_path")
    path = PurePosixPath(value.strip())
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise IncludeContextUnavailable("include_path_outside_project")
    if not path.suffix:
        path = path.with_suffix(".tex")
    if path.suffix.lower() != ".tex":
        raise IncludeContextUnavailable("non_tex_include_requires_review")
    return path.as_posix()


def _graph(sources: dict[str, str], main_tex: str) -> dict[str, list[dict[str, Any]]]:
    graph: dict[str, list[dict[str, Any]]] = {}
    stack: list[str] = []
    parents: dict[str, str] = {}

    def visit(file: str) -> None:
        if file in stack:
            raise IncludeContextUnavailable("include_cycle:" + file)
        if file in graph:
            raise IncludeContextUnavailable("multiply_owned_include:" + file)
        if len(graph) >= 128 or len(stack) >= 32:
            raise IncludeContextUnavailable("include_graph_bound_exceeded")
        if file not in sources:
            raise IncludeContextUnavailable("include_source_unavailable:" + file)
        source = sources[file]
        active = _execution_syntax(source)
        # Inspect definitions before masking their bodies. A nested declaration
        # must not hide a redefined reader/font or float environment.
        declarations = _comment_syntax(source[:len(active)])
        if _DEFINITION.search(declarations) or any(m[1] in FLOAT_ENVS for m in _ENV_DEFINITION.finditer(declarations)):
            raise IncludeContextUnavailable("reader_font_or_float_redefinition:" + file)
        if "^^" in declarations or re.search(r"\\(?:csname|catcode)(?![A-Za-z@])", declarations):
            raise IncludeContextUnavailable("constructed_reader_or_character_rewrite:" + file)
        if _OPAQUE.search(active) or re.search(r"\\begin\s*\{filecontents\*?\}", active):
            raise IncludeContextUnavailable("opaque_include_execution:" + file)
        rows = []
        graph[file] = rows
        stack.append(file)
        for marker in _INPUT.finditer(active):
            argument = group(active, marker.end())
            if not argument:
                raise IncludeContextUnavailable("unbraced_or_dynamic_include:" + file)
            reference = _relative(argument[0])
            local = (PurePosixPath(file).parent / reference).as_posix()
            candidates = sorted({item for item in (reference, local) if item in sources})
            if len(candidates) != 1:
                raise IncludeContextUnavailable("missing_or_ambiguous_include:" + file + ":" + reference)
            child = candidates[0]
            if child in parents:
                raise IncludeContextUnavailable("multiply_owned_include:" + child)
            parents[child] = file
            rows.append({"file": child, "command": marker[1], "start": marker.start(), "end": argument[1]})
            visit(child)
        stack.pop()

    visit(_relative(main_tex))
    return graph


def _expanded(sources: dict[str, str], graph: dict[str, list[dict[str, Any]]], file: str) -> tuple[str, list[str]]:
    source = sources[file][:len(_execution_syntax(sources[file]))]
    chunks = []
    covered = [file]
    offset = 0
    for item in graph[file]:
        child, child_files = _expanded(sources, graph, item["file"])
        chunks.extend((source[offset:item["start"]], "\n", child, "\n"))
        covered.extend(child_files)
        offset = item["end"]
    chunks.append(source[offset:])
    expanded = "".join(chunks)
    if len(expanded) > 128 * 1024:
        raise IncludeContextUnavailable("include_context_size_bound_exceeded")
    return expanded, covered


def float_wrapper_residual(source: str) -> str:
    """Remove ordinary float boundaries; preserve payload tokens/literal bytes."""
    active = masked_tex(source)
    pieces = []
    offset = 0
    for marker in _FLOAT_MARKER.finditer(active):
        env = group(active, marker.end())
        if not env or env[0] not in FLOAT_ENVS:
            continue
        if env[0] not in _SIMPLE_FLOATS:
            # Leave complex wrap/longtable boundaries and their arguments
            # verbatim. Their removal/change cannot match the exact residual;
            # an unchanged neighbour need not invalidate a simple float move.
            continue
        end = env[1]
        if marker[1] == "begin":
            placement = group(active, end, "[", "]")
            if placement:
                if not re.fullmatch(r"[htbpH!\s]*", placement[0]):
                    raise IncludeContextUnavailable("opaque_float_placement")
                end = placement[1]
        pieces.extend((source[offset:marker.start()], " "))
        offset = end
    pieces.append(source[offset:])
    residual = "".join(pieces)
    # Normal TeX whitespace may move with wrappers, but verbatim whitespace is
    # data. Keep literal regions byte-for-byte in an independent identity; do
    # not strip comments inside a verb delimiter or normalize its spacing.
    literals = []
    index = 0
    literal_envs = {"verbatim", "verbatim*", "Verbatim", "lstlisting", "minted", "comment", "filecontents", "filecontents*"}
    while index < len(residual):
        if residual[index] == "%":
            newline = residual.find("\n", index)
            index = len(residual) if newline < 0 else newline + 1
            continue
        if residual[index] != "\\":
            index += 1
            continue
        start = index
        name, index = _command(residual, index)
        if name in {"verb", "lstinline"}:
            if index < len(residual) and residual[index] == "*":
                index += 1
            if name == "lstinline":
                options = group(residual, index, "[", "]")
                if options:
                    index = options[1]
            if index >= len(residual) or residual[index].isspace():
                raise IncludeContextUnavailable("malformed_literal_region")
            stop = residual.find(residual[index], index + 1)
            if stop < 0:
                raise IncludeContextUnavailable("unterminated_literal_region")
            index = stop + 1
            literals.append(residual[start:index])
        elif name == "begin":
            env = group(residual, index)
            if env and env[0] in literal_envs:
                ending = re.search(r"\\end\s*\{" + re.escape(env[0]) + r"\}", residual[env[1]:])
                if not ending:
                    raise IncludeContextUnavailable("unterminated_literal_environment")
                index = env[1] + ending.end()
                literals.append(residual[start:index])
    return re.sub(r"\s+", " ", residual).strip() + "\x00literals:" + json.dumps(literals, ensure_ascii=False)


def compare_include_contexts(before: dict[str, str], after: dict[str, str], *,
                             main_tex: str, files_to_check: list[str]) -> dict[str, Any]:
    """Return the smallest common owning contexts for uncertain file-local checks."""
    result: dict[str, Any] = {"checked": True, "status": "not_applicable", "contexts": [],
                              "reasons": [], "limits": _LIMITS}
    if not files_to_check:
        return result
    try:
        before = {_relative(file): source for file, source in before.items()}
        after = {_relative(file): source for file, source in after.items()}
        old_graph = _graph(before, main_tex)
        new_graph = _graph(after, main_tex)
        identity = lambda graph: {file: [(row["command"], row["file"]) for row in rows] for file, rows in graph.items()}
        if identity(old_graph) != identity(new_graph):
            raise IncludeContextUnavailable("include_graph_changed_requires_review")
        parents = {row["file"]: file for file, rows in new_graph.items() for row in rows}
        roots = set()
        for file in files_to_check:
            if file not in new_graph:
                raise IncludeContextUnavailable("unreachable_changed_source:" + file)
            # A fragment needs its direct lexical parent; an owning file with
            # children already supplies that context. Collapse overlapping roots.
            root = file if new_graph[file] else parents.get(file)
            if root:
                roots.add(root)
        def ancestors(file: str) -> set[str]:
            found = set()
            while file in parents:
                file = parents[file]
                found.add(file)
            return found
        roots = {file for file in roots if not ancestors(file) & roots}
        try:
            from layout_policy import compare_layout_policy
        except ModuleNotFoundError:
            from .layout_policy import compare_layout_policy
        for file in sorted(roots):
            original, covered = _expanded(before, old_graph, file)
            repaired, _ = _expanded(after, new_graph, file)
            if len(covered) <= 1:
                continue
            residual_equal = float_wrapper_residual(original) == float_wrapper_residual(repaired)
            policy = compare_layout_policy(original, repaired, file=file)
            result["contexts"].append({
                "root_file": file, "files": covered, "status": "available",
                "float_wrapper_residual_equal": residual_equal,
                "layout_policy": policy,
                "source_identities": {role: {path: hashlib.sha256(sources[path].encode("utf-8", errors="surrogateescape")).hexdigest() for path in covered}
                                      for role, sources in (("before", before), ("after", after))},
                "expanded_identities": {"before": hashlib.sha256(original.encode("utf-8", errors="surrogateescape")).hexdigest(),
                                        "after": hashlib.sha256(repaired.encode("utf-8", errors="surrogateescape")).hexdigest()},
                "requires_host_review": residual_equal and original != repaired,
                "host_check": "Check caption/label numbering and intended targets, table readability, float placement and all fresh PDF pages; wrapper preservation alone is insufficient.",
            })
        result["status"] = "available" if result["contexts"] else "not_applicable"
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        result.update(status="unavailable", contexts=[], reasons=[str(error)], requires_host_review=True)
    return result
