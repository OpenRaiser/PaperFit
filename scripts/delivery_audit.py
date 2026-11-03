#!/usr/bin/env python3
"""Opt-in, read-only delivery file and HTML asset audit; no repair decisions."""

from __future__ import annotations

import argparse
import ast
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlsplit


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: List[str] = []
        self.has_base = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        for key, value in attrs:
            if key in {"src", "href"} and value:
                self.paths.append(value)
                if tag == "base" and key == "href":
                    self.has_base = True


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _member(root: Path, value: str) -> Path:
    """Configuration paths are portable, delivery-relative file names."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Expected a delivery-relative POSIX path: " + str(value))
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Delivery path escapes its root: " + value)
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Delivery path escapes its root: " + value)
    return path


def _dump(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _static_root(node: ast.AST, server: Path) -> Path:
    """Interpret only Path(__file__).resolve(), .parent and / literal strings."""
    base = ast.parse("Path(__file__).resolve()", mode="eval").body
    if _dump(node) == _dump(base):
        return server
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return _static_root(node.value, server).parent
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
        return _static_root(node.left, server) / node.right.value
    raise ValueError("Unsupported HTTP UI_ROOT expression")


def verify_service(root: Path, registration: Dict[str, str]) -> Dict[str, Any]:
    """Verify a declared frozen service binding without importing its source."""
    if not isinstance(registration, dict):
        raise ValueError("Service registration must be a JSON object")
    html = _member(root, registration["html"])
    server = _member(root, registration["server"])
    ui = _member(root, registration["ui_root"])
    expected_sha = registration["server_sha256"]
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha):
        raise ValueError("Service registration requires a trusted server_sha256")
    if not server.is_file() or sha256(server) != expected_sha.lower():
        raise ValueError("Unverified frozen HTTP server SHA: " + registration["server"])
    if not html.is_file() or not ui.is_dir() or not html.is_relative_to(ui):
        raise ValueError("Registered HTML must be an existing file inside its UI root")
    tree = ast.parse(server.read_text(encoding="utf-8"))
    imports = [alias for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "pathlib" and node.level == 0 for alias in node.names if alias.name == "Path" and alias.asname in {None, "Path"}]
    path_stores = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "Path" and isinstance(node.ctx, ast.Store)]
    if len(imports) != 1 or path_stores:
        raise ValueError("Unsupported pathlib.Path binding")
    assignments = [node for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "UI_ROOT" for target in node.targets)]
    stores = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "UI_ROOT" and isinstance(node.ctx, ast.Store)]
    methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_serve_static"]
    if len(assignments) != 1 or len(stores) != 1 or len(methods) != 1:
        raise ValueError("Unsupported HTTP UI_ROOT/static method binding")
    if _static_root(assignments[0].value, server).resolve() != ui:
        raise ValueError("Declared UI root differs from frozen HTTP UI_ROOT")
    method = methods[0]
    prefix = ast.parse('rel = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")\npath = (UI_ROOT / rel).resolve()').body
    if len(method.body) < 3 or [_dump(node) for node in method.body[:2]] != [_dump(node) for node in prefix]:
        raise ValueError("Unsupported HTTP static path mapping")
    guard = method.body[2]
    expected_check = ast.parse("path.relative_to(UI_ROOT)").body[0]
    if not isinstance(guard, ast.Try) or [_dump(node) for node in guard.body] != [_dump(expected_check)] or guard.orelse or guard.finalbody:
        raise ValueError("Missing HTTP UI path containment guard")
    if not any(isinstance(handler.type, ast.Name) and handler.type.id == "ValueError" and any(isinstance(node, ast.Return) for node in handler.body) for handler in guard.handlers):
        raise ValueError("Missing HTTP UI path rejection")
    return {"html": html, "server": server, "ui_root": ui, "server_sha256": expected_sha.lower()}


def _check_html(root: Path, html: Path, binding: Optional[Dict[str, Any]]) -> Tuple[list, list]:
    relative = html.relative_to(root).as_posix()
    parser = _Links()
    parser.feed(html.read_text(encoding="utf-8"))
    broken, served = [], []
    if parser.has_base:
        broken.append({"html": relative, "target": "<base href>", "reason": "unsupported HTML base URL changes local path mapping"})
    for url in parser.paths:
        parts = urlsplit(url)
        if parts.scheme.lower() in {"http", "https", "data", "javascript", "mailto", "tel"}:
            continue
        reason = None
        if parts.scheme or parts.netloc or "\\" in parts.path:
            reason = "unsupported or unregistered URL"
        elif not parts.path:
            continue
        elif parts.path.startswith("/"):
            if binding is None:
                reason = "file-viewed HTML requires relative local links"
            else:
                # The verified server uses request_path.lstrip('/') without
                # URL decoding. Match that actual mapping, including queries.
                ui = binding["ui_root"]
                local = "index.html" if parts.path == "/" else parts.path.lstrip("/")
                target = (ui / local).resolve()
                if not target.is_relative_to(ui) or not target.is_file():
                    reason = "missing or escaped served asset"
                else:
                    served.append({"html": relative, "url": url, "server": binding["server"].relative_to(root).as_posix(), "ui_root": ui.relative_to(root).as_posix(), "asset": target.relative_to(root).as_posix(), "sha256": sha256(target), "binding": "frozen server SHA + AST UI_ROOT/static mapping/containment guard"})
        else:
            # Browsers decode file-viewed relative URLs, unlike the supported
            # service contract above. Resolve after decoding and then contain.
            target = (html.parent / unquote(parts.path)).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                reason = "missing or escaped local link"
        if reason:
            broken.append({"html": relative, "target": url, "reason": reason})
    return broken, served


def check_html_links(root: Path, html: Path, services: Iterable[Dict[str, str]] = ()) -> Tuple[list, list]:
    """Reusable single-page helper; invalid service declarations raise ValueError."""
    root = Path(root).resolve()
    html = Path(html).resolve()
    if not html.is_relative_to(root) or not html.is_file():
        raise ValueError("HTML must be an existing file inside the delivery root")
    bindings = [verify_service(root, registration) for registration in services]
    matches = [binding for binding in bindings if binding["html"] == html]
    if len(matches) > 1:
        raise ValueError("Duplicate HTML service registration")
    return _check_html(root, html, matches[0] if matches else None)


def audit_delivery(root: Path, *, manifest: Optional[Dict[str, Any]] = None, services: Iterable[Dict[str, str]] = ()) -> Dict[str, Any]:
    """Audit all HTML and optional declared file hashes; never write into root."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Delivery root must be an existing directory")
    errors, bindings, file_checks, broken, served = [], {}, [], [], []
    for registration in services:
        try:
            binding = verify_service(root, registration)
            if binding["html"] in bindings:
                raise ValueError("Duplicate HTML service registration")
            bindings[binding["html"]] = binding
        except (KeyError, OSError, SyntaxError, TypeError, ValueError) as error:
            errors.append({"kind": "service", "reason": str(error)})
    if manifest is not None:
        if not isinstance(manifest, dict):
            raise ValueError("Manifest must be a JSON object")
        files = manifest.get("files")
        if isinstance(files, dict):
            files = [{"path": path, "sha256": digest} for path, digest in files.items()]
        if not isinstance(files, list):
            raise ValueError("Manifest must contain a files list or path-to-SHA mapping")
        seen = set()
        for entry in files:
            try:
                name, expected = entry["path"], entry["sha256"]
                if name in seen:
                    raise ValueError("Duplicate manifest path: " + name)
                seen.add(name)
                path = _member(root, name)
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                    raise ValueError("Manifest SHA must contain 64 hexadecimal digits")
                if not path.is_file() or sha256(path) != expected.lower():
                    raise ValueError("Missing file or SHA mismatch: " + name)
                file_checks.append({"path": name, "sha256": expected.lower()})
            except (KeyError, OSError, TypeError, ValueError) as error:
                errors.append({"kind": "manifest", "reason": str(error)})
    html_count = 0
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() != ".html" or not path.is_file():
            continue
        html_count += 1
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            errors.append({"kind": "html", "reason": "HTML escapes delivery root: " + str(path)})
            continue
        try:
            bad, assets = _check_html(root, resolved, bindings.get(resolved))
            broken.extend(bad)
            served.extend(assets)
        except (OSError, UnicodeError, ValueError) as error:
            errors.append({"kind": "html", "reason": str(error)})
    return {"schema_version": 1, "delivery_root": str(root), "status": "failed" if errors or broken else "pass", "html_files_checked": html_count, "manifest_files_checked": len(file_checks), "manifest_checks": file_checks, "service_bindings": [{key: value.relative_to(root).as_posix() if isinstance(value, Path) else value for key, value in binding.items()} for binding in bindings.values()], "broken_links": broken, "served_source_assets": served, "errors": errors}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Existing delivery directory; no fixed PDF set or location")
    parser.add_argument("--manifest", type=Path, help="Optional trusted files path/SHA manifest")
    parser.add_argument("--services", type=Path, help="JSON with a services list of frozen HTML/server/UI-root/SHA registrations")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig")) if args.manifest else None
        config = json.loads(args.services.read_text(encoding="utf-8-sig")) if args.services else {"services": []}
        if not isinstance(config, dict) or not isinstance(config.get("services"), list):
            raise ValueError("Service configuration must contain a services list")
        if manifest is not None and not isinstance(manifest, dict):
            raise ValueError("Manifest must be a JSON object")
        report = audit_delivery(args.root, manifest=manifest, services=config["services"])
    except (OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
