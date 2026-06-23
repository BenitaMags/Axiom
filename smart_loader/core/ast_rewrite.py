"""Shared AST import / usage rewriting for AXIOM agents."""

from __future__ import annotations

import ast


class ImportRewriter(ast.NodeTransformer):
    """Rewrite top-level import statements and module.attribute usages."""

    def __init__(self, replacements: dict[str, str]):
        self.replacements = replacements
        self.seen_imports: set[tuple[str, str | None]] = set()

    def visit_Import(self, node: ast.Import) -> ast.Import | None:
        new_names = []
        for alias in node.names:
            new_name = self.replacements.get(alias.name, alias.name)
            import_key = (new_name, alias.asname)
            if import_key not in self.seen_imports:
                self.seen_imports.add(import_key)
                new_names.append(ast.alias(name=new_name, asname=alias.asname))
        if not new_names:
            return None
        node.names = new_names
        return node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        if node.module in self.replacements:
            node.module = self.replacements[node.module]
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        self.generic_visit(node)
        if isinstance(node.value, ast.Name):
            new_module = self.replacements.get(node.value.id)
            if new_module:
                node.value.id = new_module
        return node


def apply_import_replacements(source_code: str, replacements: dict[str, str]) -> str:
    """Return source with import lines and module usages rewritten."""
    if not replacements:
        return source_code
    tree = ast.parse(source_code)
    new_tree = ImportRewriter(replacements).visit(tree)
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree)
