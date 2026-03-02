#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║           ODOO MODULE CODE REVIEW TOOL  (v17.0 / v18.0 / v19.0)            ║
║                                                                              ║
║  Checks Python models, XML views, JS/OWL components and module structure    ║
║  against official Odoo coding guidelines.                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
import re
import sys
import ast
import json
import subprocess
from pathlib import Path
from collections import defaultdict
from xml.etree import ElementTree as ET

# ─────────────────────────────────────────────────────────────────────────────
# ISSUE COLLECTOR
# ─────────────────────────────────────────────────────────────────────────────

SEVERITY_ERROR   = "ERROR"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO    = "INFO"

issues = []   # list of dicts: {file, line, severity, code, message}

def add(file_path, line, severity, code, message):
    issues.append({
        "file":     str(file_path),
        "line":     line,
        "severity": severity,
        "code":     code,
        "message":  message,
    })

def err(file_path, line, code, message):
    add(file_path, line, SEVERITY_ERROR, code, message)

def warn(file_path, line, code, message):
    add(file_path, line, SEVERITY_WARNING, code, message)

def info(file_path, line, code, message):
    add(file_path, line, SEVERITY_INFO, code, message)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def read_file(path):
    """Return (lines_list, full_text) or (None, None) on error."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        return content.splitlines(), content
    except Exception as exc:
        warn(path, 0, "FILE-READ", f"Could not read file: {exc}")
        return None, None


def find_files(root, *extensions):
    """Yield all files under root matching given extensions."""
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            if fname.endswith(extensions):
                yield Path(dirpath) / fname


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL STRUCTURE CHECKS
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_DIRS  = []          # none are strictly required in all modules
RECOMMENDED_DIRS = ["models", "views", "security", "data", "static"]

REQUIRED_FILES = ["__manifest__.py", "__init__.py"]

MANIFEST_REQUIRED_KEYS = [
    "name", "version", "category", "summary", "author",
    "license", "depends", "data",
]

def check_module_structure(module_path):
    """Validate top-level module layout."""
    section = "STRUCT"

    # Required files
    for fname in REQUIRED_FILES:
        fpath = module_path / fname
        if not fpath.exists():
            err(module_path, 0, f"{section}-MISSING",
                f"Missing required file: {fname}")

    # security/ir.model.access.csv
    csv_path = module_path / "security" / "ir.model.access.csv"
    has_models = any((module_path / "models").rglob("*.py")) if (module_path / "models").exists() else False
    if has_models and not csv_path.exists():
        warn(module_path, 0, f"{section}-SECURITY",
             "Module defines models but has no security/ir.model.access.csv")

    # __manifest__.py
    manifest_path = module_path / "__manifest__.py"
    if manifest_path.exists():
        check_manifest(manifest_path)

    # static/src structure
    static_src = module_path / "static" / "src"
    if static_src.exists():
        js_files = list(static_src.rglob("*.js")) + list(static_src.rglob("*.xml"))
        if js_files and not (static_src / "js").exists() and not (static_src / "components").exists():
            info(module_path, 0, f"{section}-STATIC",
                 "static/src should have subdirs: js/, components/, scss/ etc.")


def check_manifest(path):
    lines, content = read_file(path)
    if content is None:
        return

    try:
        manifest = ast.literal_eval(content)
    except Exception:
        err(path, 0, "MANIFEST-PARSE", "Cannot parse __manifest__.py as a Python dict literal")
        return

    for key in MANIFEST_REQUIRED_KEYS:
        if key not in manifest:
            warn(path, 0, "MANIFEST-KEY", f"Missing recommended key in __manifest__.py: '{key}'")

    # Version must follow odoo.x.y.z.w pattern
    version = manifest.get("version", "")
    if version and not re.match(r"^\d+\.\d+\.\d+\.\d+\.\d+$", str(version)):
        warn(path, 0, "MANIFEST-VERSION",
             f"version '{version}' should follow pattern <odoo>.<major>.<minor>.<patch> e.g. 17.0.1.0.0")

    # License should be a recognised Odoo license
    valid_licenses = {"LGPL-3", "GPL-3", "OPL-1", "AGPL-3", "MIT", "Apache-2.0"}
    lic = manifest.get("license", "")
    if lic and lic not in valid_licenses:
        warn(path, 0, "MANIFEST-LICENSE", f"Unusual license '{lic}'. Common Odoo licenses: {valid_licenses}")

    # installable should be True
    if manifest.get("installable") is False:
        info(path, 0, "MANIFEST-INSTALLABLE", "installable is set to False")

    # auto_install warning
    if manifest.get("auto_install") is True:
        info(path, 0, "MANIFEST-AUTO", "auto_install is True — confirm this is intentional")


# ─────────────────────────────────────────────────────────────────────────────
# PYTHON CHECKS
# ─────────────────────────────────────────────────────────────────────────────

PYTHON_STDLIB = {
    "ast", "collections", "contextlib", "copy", "csv", "datetime", "decimal",
    "enum", "functools", "hashlib", "http", "io", "itertools", "json", "logging",
    "math", "operator", "os", "pathlib", "re", "shutil", "string", "sys",
    "tempfile", "threading", "time", "traceback", "unicodedata", "unittest",
    "urllib", "uuid", "warnings", "xml",
}

MODEL_METHOD_ORDER = [
    "_name", "_description", "_inherit", "_inherits", "_table",
    "_order", "_rec_name", "_sql_constraints",
    # fields
    # _compute / _inverse / _search
    # @api.constrains
    # @api.depends / @api.onchange
    # CRUD: create / write / unlink / copy
    # action_ methods
    # other methods
]


def check_python_naming(path, lines, content):
    """
    Check variable, method, class and field naming against official Odoo guidelines.

    Rules from Odoo 17/18/19 docs:
      - Classes          : PascalCase  (e.g. SaleOrder, AccountMove)
      - Methods          : snake_case  (e.g. action_confirm, _compute_total)
      - Variables        : snake_case  (e.g. sale_order, partner_id)
      - Constants        : UPPER_SNAKE (e.g. DEFAULT_MARGIN)
      - Many2one fields  : must end with _id
      - One2many/Many2many: must end with _ids
      - env model vars   : PascalCase  (e.g. SaleOrder = self.env['sale.order'])
      - Record variables : snake_case  (e.g. partner, order, not partnerObj)
      - No camelCase for variables  (that's a JS convention, not Python/Odoo)
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return  # already caught in check_python_model

    # ── Class naming: must be PascalCase ─────────────────────────────────────
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if not re.match(r"^[A-Z][a-zA-Z0-9]*$", node.name):
                warn(path, node.lineno, "NAME-CLASS",
                     f"Class '{node.name}' should use PascalCase (e.g. SaleOrder, AccountMove)")

    # ── Function/method naming: must be snake_case ───────────────────────────
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            name = node.name
            # Allow dunder methods (__init__, __str__ …)
            if name.startswith("__") and name.endswith("__"):
                continue
            # Must be snake_case — no capital letters allowed
            if re.search(r"[A-Z]", name):
                warn(path, node.lineno, "NAME-METHOD",
                     f"Method '{name}' should use snake_case (e.g. action_confirm, _compute_total) "
                     f"not camelCase or PascalCase")

    # ── Field-level naming rules ──────────────────────────────────────────────
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        # Check it's an Odoo model
        base_names = []
        for base in node.bases:
            if isinstance(base, ast.Attribute):
                base_names.append(f"{base.value.id if isinstance(base.value, ast.Name) else '?'}.{base.attr}")
            elif isinstance(base, ast.Name):
                base_names.append(base.id)
        is_model = any(b in (
            "models.Model", "models.TransientModel", "models.AbstractModel",
            "Model", "TransientModel", "AbstractModel"
        ) for b in base_names)
        if not is_model:
            continue

        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            for target in item.targets:
                if not isinstance(target, ast.Name):
                    continue
                field_name = target.id
                if field_name.startswith("_"):
                    continue  # private/magic attrs — skip

                # Detect field type from RHS
                field_type = None
                if isinstance(item.value, ast.Call):
                    func = item.value.func
                    if isinstance(func, ast.Attribute):
                        field_type = func.attr  # e.g. Many2one, One2many, Many2many, Char …
                    elif isinstance(func, ast.Name):
                        field_type = func.id

                if field_type in ("Many2one",):
                    # Must end with _id — hard Odoo convention, not optional
                    if not field_name.endswith("_id"):
                        err(path, item.lineno, "NAME-FIELD-M2O",
                            f"Many2one field '{field_name}' must end with '_id' "
                            f"(e.g. partner_id, product_id) — required Odoo naming convention")

                elif field_type in ("One2many", "Many2many"):
                    # Must end with _ids — hard Odoo convention, not optional
                    if not field_name.endswith("_ids"):
                        err(path, item.lineno, "NAME-FIELD-X2M",
                            f"{field_type} field '{field_name}' must end with '_ids' "
                            f"(e.g. order_line_ids, tag_ids) — required Odoo naming convention")

                # All field names must be snake_case — no camelCase
                if re.search(r"[A-Z]", field_name):
                    err(path, item.lineno, "NAME-FIELD-CASE",
                        f"Field '{field_name}' must use snake_case — Odoo fields are never camelCase")

    # ── Local variable naming: RHS-aware snake_case checks ──────────────────
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for subnode in ast.walk(node):

            # Assignment targets — inspect RHS to determine expected suffix
            if isinstance(subnode, ast.Assign):
                rhs_type = _infer_rhs_type(subnode.value)
                for target in subnode.targets:
                    if isinstance(target, ast.Name):
                        _check_variable_name(path, subnode.lineno, target.id, rhs_type)

            # For loop targets — iterating over a recordset, so each item is a record
            elif isinstance(subnode, ast.For):
                if isinstance(subnode.target, ast.Name):
                    # The loop var is a single record — no suffix required,
                    # just ensure it's snake_case
                    _check_variable_name(path, subnode.lineno, subnode.target.id, "record")

            # Comprehension targets
            elif isinstance(subnode, (ast.ListComp, ast.GeneratorExp, ast.DictComp, ast.SetComp)):
                for gen in subnode.generators:
                    if isinstance(gen.target, ast.Name):
                        if len(gen.target.id) > 1:
                            _check_variable_name(path, subnode.lineno, gen.target.id, "unknown")

            # Function arguments
            elif isinstance(subnode, ast.arguments):
                for arg in subnode.args + subnode.posonlyargs + subnode.kwonlyargs:
                    if arg.arg not in ("self", "cls"):
                        _check_variable_name(path, node.lineno, arg.arg, "unknown")


# ORM methods that return recordsets (multi)
ORM_RECORDSET_METHODS = {
    "search", "browse", "filtered", "sorted", "mapped",
    "with_context", "with_user", "with_company", "sudo",
    "create",        # returns a recordset (single or multi)
    "copy",
}

# ORM methods that return a single record
ORM_SINGLE_METHODS = {
    "ensure_one", "browse",   # browse(id) → single
}

# ORM methods that return a dict
ORM_DICT_METHODS = {
    "read",           # returns list of dicts, but close enough
    "read_group",
    "fields_get",
    "get_metadata",
    "default_get",
    "onchange",
    "fields_view_get",
    "get",            # dict.get
}

# ORM methods that return a scalar (int, float, bool)
ORM_SCALAR_METHODS = {
    "search_count",
}


def _infer_rhs_type(rhs_node):
    """
    Inspect the RHS of an assignment and return one of:
      'recordset'  — multi-record ORM result  → expect _ids suffix
      'record'     — single-record ORM result → expect _id suffix  (or just name)
      'dict'       — dict / read_group result → expect _dict / _vals suffix
      'scalar'     → no suffix required
      'unknown'    → no suffix enforced
    """
    if not isinstance(rhs_node, ast.Call):
        # Check for dict literal
        if isinstance(rhs_node, ast.Dict):
            return "dict"
        return "unknown"

    # Unwrap chained calls: self.env['x'].search([]) → func = search
    method_name = _extract_method_name(rhs_node)

    if method_name in ORM_RECORDSET_METHODS:
        return "recordset"
    if method_name in ORM_SINGLE_METHODS:
        return "record"
    if method_name in ORM_DICT_METHODS:
        return "dict"
    if method_name in ORM_SCALAR_METHODS:
        return "scalar"

    # Dict constructors
    if method_name in ("dict",):
        return "dict"

    return "unknown"


def _extract_method_name(call_node):
    """Extract the final method name from a Call node, handling chains."""
    func = call_node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _check_variable_name(path, lineno, name, rhs_type="unknown"):
    """
    Apply Odoo variable naming rules.

    RHS-aware suffix enforcement:
      recordset  → must end with _ids  (e.g. so_lines, order_ids)
      record     → should end with _id or be a plain name (e.g. order, partner_id)
      dict       → should end with _dict or _vals or _data
    """
    # Skip: single letters, dunders, ALL_CAPS constants, PascalCase env refs
    if len(name) <= 1:
        return
    if name.startswith("__"):
        return
    if re.match(r"^[A-Z][A-Z0-9_]+$", name):
        return  # UPPER_SNAKE constant — valid
    if re.match(r"^[A-Z][a-zA-Z0-9]+$", name) and "_" not in name:
        return  # PascalCase env model var — valid

    # ── camelCase check (applies to everything) ───────────────────────────────
    if re.match(r"^[a-z]", name) and re.search(r"[A-Z]", name):
        info(path, lineno, "NAME-VAR-CAMEL",
             f"Variable '{name}' uses camelCase — Odoo Python uses snake_case "
             f"(e.g. '{_to_snake(name)}' instead of '{name}')")
        return  # don't pile on with suffix errors if already camelCase

    # ── RHS-aware suffix checks ───────────────────────────────────────────────
    if rhs_type == "recordset":
        # Multi-record result: must end with _ids or _lines or _records or similar
        # Accept: _ids, _lines, _orders, _moves, _items — anything that is clearly plural
        # Reject: names that look singular (no plural indicator and no _ids)
        if not (name.endswith("_ids")
                or name.endswith("_lines")
                or name.endswith("_records")
                or name.endswith("_items")
                or name.endswith("_list")
                or name.endswith("s")):      # simple plural e.g. orders, partners, moves
            warn(path, lineno, "NAME-VAR-RECORDSET",
                 f"Variable '{name}' holds a recordset (search/browse result) — "
                 f"use a plural name ending with '_ids' or a plural word "
                 f"(e.g. '{name}_ids' or '{name}s')")

    elif rhs_type == "dict":
        if not (name.endswith("_dict")
                or name.endswith("_vals")
                or name.endswith("_values")
                or name.endswith("_data")
                or name.endswith("_context")
                or name.endswith("_domain")
                or name.endswith("_defaults")
                or name.endswith("_result")
                or name.endswith("s")        # e.g. defaults, values
                or name == "res"             # common Odoo convention: res = {...}
                or name == "ctx"             # common: ctx = self.env.context
                ):
            info(path, lineno, "NAME-VAR-DICT",
                 f"Variable '{name}' holds a dict result — "
                 f"consider a name ending with '_vals', '_dict' or '_data' "
                 f"(e.g. '{name}_vals')")


def _to_snake(name):
    """Convert camelCase to snake_case for suggestion in error messages."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

def check_python_file(path):
    lines, content = read_file(path)
    if content is None:
        return

    # Skip __init__.py and __manifest__.py — different rules
    if path.name in ("__init__.py", "__manifest__.py"):
        return

    check_python_imports(path, lines, content)
    check_python_model(path, lines, content)
    check_python_patterns(path, lines, content)
    check_python_security(path, lines, content)
    check_python_naming(path, lines, content)


def check_python_imports(path, lines, content):
    """Enforce Odoo import grouping: stdlib → odoo → addons."""
    import_lines = [(i + 1, l.strip()) for i, l in enumerate(lines)
                    if l.strip().startswith("import ") or l.strip().startswith("from ")]

    in_stdlib = None
    in_odoo   = None
    last_group = None  # "stdlib" | "odoo" | "addon"

    for lineno, stmt in import_lines:
        if stmt.startswith("from odoo") or stmt.startswith("import odoo"):
            group = "odoo"
        elif stmt.startswith("from odoo.addons") or stmt.startswith("import odoo.addons"):
            group = "addon"
        else:
            # heuristic: if module name is in stdlib, mark as stdlib
            match = re.match(r"(?:import|from)\s+(\w+)", stmt)
            mod = match.group(1) if match else ""
            group = "stdlib" if mod in PYTHON_STDLIB else "addon"

        order = {"stdlib": 1, "odoo": 2, "addon": 3}
        if last_group and order.get(group, 3) < order.get(last_group, 1):
            warn(path, lineno, "PY-IMPORT-ORDER",
                 f"Import group order violation: '{stmt}' ({group}) appears after {last_group} imports. "
                 f"Order should be: stdlib → odoo → addons")
        last_group = group

    # Wildcard imports
    for lineno, stmt in import_lines:
        if re.search(r"import \*", stmt):
            err(path, lineno, "PY-IMPORT-WILDCARD",
                f"Wildcard import not allowed: {stmt}")


def check_python_model(path, lines, content):
    """Check model class conventions using AST."""
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        err(path, exc.lineno or 0, "PY-SYNTAX", f"Syntax error: {exc.msg}")
        return

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        # Only inspect classes that inherit from models.*
        base_names = []
        for base in node.bases:
            if isinstance(base, ast.Attribute):
                base_names.append(f"{base.value.id if isinstance(base.value, ast.Name) else '?'}.{base.attr}")
            elif isinstance(base, ast.Name):
                base_names.append(base.id)

        is_model = any(b in (
            "models.Model", "models.TransientModel", "models.AbstractModel",
            "Model", "TransientModel", "AbstractModel"
        ) for b in base_names)

        if not is_model:
            continue

        class_lineno = node.lineno
        class_name   = node.name

        # Collect class-level attributes
        attrs = {}
        for item in node.body:
            if isinstance(item, ast.Assign):
                for t in item.targets:
                    if isinstance(t, ast.Name):
                        attrs[t.id] = item.lineno

        # _name or _inherit required
        has_name    = "_name" in attrs
        has_inherit = "_inherit" in attrs
        if not has_name and not has_inherit:
            err(path, class_lineno, "PY-MODEL-NAME",
                f"Class '{class_name}' must define _name or _inherit")

        # _description required ONLY when _name is defined (primary model definition)
        # Inherit-only classes (_inherit without _name) don't need _description — same as Odoo base
        if "_description" not in attrs:
            if has_name:
                err(path, class_lineno, "PY-MODEL-DESC",
                    f"Class '{class_name}' defines _name but is missing _description")
            elif has_inherit and not has_name:
                pass  # valid — inherit-only class, no _description needed (matches Odoo base pattern)

        # Check method ordering: fields before methods
        field_linenos   = []
        method_linenos  = []
        for item in node.body:
            if isinstance(item, ast.Assign):
                field_linenos.append(item.lineno)
            elif isinstance(item, ast.FunctionDef):
                method_linenos.append(item.lineno)

        if field_linenos and method_linenos:
            if min(field_linenos) > min(method_linenos):
                warn(path, class_lineno, "PY-MODEL-ORDER",
                     f"Class '{class_name}': field definitions should appear before method definitions")

        # Check each method
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            method_name = item.name
            method_src  = "\n".join(lines[item.lineno - 1 : item.end_lineno])

            # write() inside a @api.depends compute method
            decorators = [
                (d.attr if isinstance(d, ast.Attribute) else d.id if isinstance(d, ast.Name) else "")
                for d in item.decorator_list
            ]
            decorator_ids = []
            for d in item.decorator_list:
                if isinstance(d, ast.Attribute):
                    decorator_ids.append(d.attr)
                elif isinstance(d, ast.Name):
                    decorator_ids.append(d.id)
                elif isinstance(d, ast.Call):
                    if isinstance(d.func, ast.Attribute):
                        decorator_ids.append(d.func.attr)
                    elif isinstance(d.func, ast.Name):
                        decorator_ids.append(d.func.id)

            if "depends" in decorator_ids:
                # look for self.write / self.env[...].write etc.
                for sub in ast.walk(item):
                    if isinstance(sub, ast.Call):
                        if isinstance(sub.func, ast.Attribute) and sub.func.attr == "write":
                            err(path, sub.lineno, "PY-COMPUTE-WRITE",
                                f"Method '{method_name}': do not call write() inside a @api.depends compute; assign to self.field instead")
                            break

            # _compute methods must not use @api.multi (removed in v14+)
            if "multi" in decorator_ids:
                err(path, item.lineno, "PY-API-MULTI",
                    f"Method '{method_name}': @api.multi was removed in Odoo 14+. Methods are now multi by default.")

            # Detect compute method that doesn't assign to any attribute at all
            # Accepts both:  self.field = value
            #           and:  for rec in self: rec.field = value  (correct Odoo pattern)
            if method_name.startswith("_compute_"):
                has_assign = any(
                    isinstance(sub, ast.Assign) and
                    any(
                        isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        for t in sub.targets
                    )
                    for sub in ast.walk(item)
                )
                if not has_assign:
                    warn(path, item.lineno, "PY-COMPUTE-ASSIGN",
                         f"Compute method '{method_name}' does not appear to assign to any field. "
                         f"Expected: self.field = value  or  for rec in self: rec.field = value")

            # @api.onchange preference warning
            if "onchange" in decorator_ids:
                info(path, item.lineno, "PY-ONCHANGE",
                     f"Method '{method_name}': consider a computed field with store=False instead of @api.onchange "
                     f"for stored logic (v17+ preference) — @api.onchange is still valid for UI-only behaviour")

            # Method naming convention: action_ prefix for button methods
            if method_name.startswith("action") and not method_name.startswith("action_"):
                warn(path, item.lineno, "PY-METHOD-NAME",
                     f"Method '{method_name}': action methods should use underscore — 'action_...'")


def check_python_patterns(path, lines, content):
    """Line-by-line pattern checks for anti-patterns."""
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # print() — use logging
        if re.search(r"\bprint\s*\(", stripped) and not stripped.startswith("#"):
            warn(path, lineno, "PY-PRINT",
                 "Use _logger.info/warning/error instead of print()")

        # Bare except
        if re.match(r"^\s*except\s*:", line):
            warn(path, lineno, "PY-BARE-EXCEPT",
                 "Avoid bare 'except:' — catch specific exceptions")

        # == True / == False / == None
        if re.search(r"==\s*(True|False|None)\b", stripped):
            warn(path, lineno, "PY-BOOL-COMPARE",
                 "Use 'is True/False/None' or just truthiness checks, not == True/False/None")

        # String concatenation in SQL (SQL injection risk)
        if re.search(r"(cr|self\.env\.cr)\.execute\s*\(.*['\"].*%s.*['\"].*%.*\)", stripped):
            info(path, lineno, "PY-SQL-PARAM",
                 "SQL uses %s with % formatting — use parameterised queries: cr.execute(sql, (val,))")

        # Avoid mutable default arguments
        if re.search(r"def\s+\w+\s*\(.*=\s*[\[\{]", stripped):
            warn(path, lineno, "PY-MUTABLE-DEFAULT",
                 "Mutable default argument (list/dict) — use None and initialise inside the function")

        # String translation: use _() correctly
        if re.search(r"_\(\s*['\"][^'\"]*%s", stripped):
            warn(path, lineno, "PY-TRANSLATE-FMT",
                 "Don't use %s inside _() translation strings — use _('text %s') % val or f-string after _()")

        # Hardcoded IDs or XML IDs as integers
        if re.search(r"\.browse\(\d+\)", stripped):
            warn(path, lineno, "PY-HARDCODED-ID",
                 "Hardcoded record ID in browse() — use env.ref() or a config parameter instead")

        # Deprecated @api.returns
        if "@api.returns" in stripped:
            info(path, lineno, "PY-API-RETURNS",
                 "@api.returns is rarely needed in v17+; verify it is still required")

        # Field defined without string= for non-obvious names
        field_match = re.match(r"\s+(\w+)\s*=\s*fields\.\w+\(", line)
        if field_match:
            field_name = field_match.group(1)
            # flag if no string= and name is abbreviated or unclear
            has_string = "string=" in line
            if not has_string and len(field_name) <= 3 and field_name not in ("id", "name", "ref"):
                info(path, lineno, "PY-FIELD-STRING",
                     f"Field '{field_name}' is short/abbreviated — consider adding string='...' for clarity")


def check_python_security(path, lines, content):
    """Security-focused checks."""
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # sudo() — flag with context
        if "sudo()" in stripped and not stripped.startswith("#"):
            warn(path, lineno, "PY-SUDO",
                 "sudo() bypasses access control — ensure this is intentional and document why")

        # sudo(user) with a user arg
        if re.search(r"\.sudo\([^)]+\)", stripped) and not stripped.startswith("#"):
            warn(path, lineno, "PY-SUDO-USER",
                 "sudo(user) — verify this privilege escalation is needed and safe")

        # Direct cr.execute with no params (possible injection)
        if re.search(r"(cr|self\.env\.cr)\.execute\s*\(\s*['\"]", stripped):
            warn(path, lineno, "PY-SQL-DIRECT",
                 "Direct SQL execution detected — use ORM methods unless absolutely necessary; "
                 "always use parameterised queries to prevent SQL injection")

        # Shell injection risk
        if re.search(r"os\.system\s*\(|subprocess\.call\s*\(.*shell\s*=\s*True", stripped):
            err(path, lineno, "PY-SHELL-INJECT",
                "Potential shell injection: avoid os.system() and subprocess with shell=True")

        # Unescaped Markup
        if re.search(r"Markup\s*\(.*%.*\)", stripped):
            warn(path, lineno, "PY-MARKUP",
                 "Markup() with format string — ensure the variable is already sanitised to avoid XSS")

        # _logger not declared if logging used
    uses_logger = any("_logger" in l for l in lines)
    imports_logging = any("import logging" in l or "from odoo" in l for l in lines)
    if uses_logger and "getLogger" not in content:
        warn(path, 0, "PY-LOGGER",
             "_logger used but getLogger not called — add: _logger = logging.getLogger(__name__)")


# ─────────────────────────────────────────────────────────────────────────────
# XML CHECKS
# ─────────────────────────────────────────────────────────────────────────────

VIEW_TYPES = {"form", "list", "tree", "kanban", "search", "calendar",
              "pivot", "graph", "activity", "qweb", "gantt"}

def check_xml_file(path):
    lines, content = read_file(path)
    if content is None:
        return

    # Well-formedness
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        err(path, exc.position[0] if hasattr(exc, "position") else 0,
            "XML-PARSE", f"XML parse error: {exc}")
        return

    check_xml_records(path, lines, content, root)
    check_xml_views(path, lines, content, root)
    check_xml_security(path, lines, content, root)
    check_xml_patterns(path, lines, content)


def check_xml_records(path, lines, content, root):
    """Check <record> tags for naming, model, id conventions."""
    for record in root.iter("record"):
        rec_id    = record.get("id", "")
        rec_model = record.get("model", "")
        lineno    = _xml_lineno(lines, f'id="{rec_id}"') if rec_id else 0

        # id must exist
        if not rec_id:
            err(path, lineno, "XML-RECORD-ID",
                "<record> missing 'id' attribute")
            continue

        # id should not start with a digit or contain spaces
        if re.search(r"\s", rec_id):
            err(path, lineno, "XML-RECORD-ID-SPACE",
                f"Record id '{rec_id}' must not contain spaces")

        # View records: id should follow model_name_view_<type> — NO module prefix inside own XML
        if rec_model == "ir.ui.view":
            # Flag if it has a module. prefix (that's wrong inside your own module)
            if "." in rec_id:
                info(path, lineno, "XML-VIEW-ID",
                     f"View id '{rec_id}' should NOT be prefixed with the module name inside your own XML. "
                     f"Use just the id, e.g. 'labure_cost_view_form' not 'my_module.labure_cost_view_form'")
            # Allow inherited view IDs — e.g. view_sale_order_form_inherit_labour
            elif "_inherit_" in rec_id or rec_id.endswith("_inherit"):
                pass  # valid — inherited view
            # Flag if it doesn't end with a known view type
            elif not re.search(r"_(form|list|tree|kanban|search|calendar|pivot|graph|gantt|activity|qweb|report|popup|wizard|dialog)$", rec_id):
                info(path, lineno, "XML-VIEW-ID",
                     f"View id '{rec_id}' should follow pattern 'model_name_view_<type>' "
                     f"(e.g. labure_cost_view_form, sale_order_view_list)")

        # Action records
        if rec_model in ("ir.actions.act_window", "ir.actions.server"):
            if "action" not in rec_id.lower():
                info(path, lineno, "XML-ACTION-ID",
                     f"Action id '{rec_id}' should contain 'action' in the name for clarity")

        # Menu items
        if rec_model == "ir.ui.menu":
            if "menu" not in rec_id.lower():
                info(path, lineno, "XML-MENU-ID",
                     f"Menu id '{rec_id}' should contain 'menu' for clarity")


def check_xml_views(path, lines, content, root):
    """Check view arch quality."""
    for record in root.iter("record"):
        if record.get("model") != "ir.ui.view":
            continue

        # Find arch field
        arch = record.find(".//field[@name='arch']")
        if arch is None:
            continue

        # type attribute on view tag
        view_elem = None
        for child in arch:
            view_elem = child
            break

        if view_elem is not None:
            view_tag = view_elem.tag

            # Check for old <tree> (should be <list> in v17+)
            if view_tag == "tree":
                info(path, 0, "XML-TREE-TAG",
                     "<tree> view tag is renamed to <list> in Odoo 17+ — consider updating")

            # Form views should have a <header> or at least fields
            if view_tag == "form":
                has_header  = view_elem.find("header") is not None
                has_sheet   = view_elem.find("sheet") is not None
                has_fields  = view_elem.find(".//field") is not None

                if not has_sheet and has_fields:
                    warn(path, 0, "XML-FORM-SHEET",
                         "Form view without <sheet> — wrap fields in <sheet> for proper layout")

            # Check for inline styles (not allowed)
            for elem in view_elem.iter():
                style = elem.get("style", "")
                if style:
                    warn(path, 0, "XML-INLINE-STYLE",
                         f"Inline style on <{elem.tag}> — use CSS classes instead")

                # Check attrs format (deprecated in v17)
                attrs = elem.get("attrs", "")
                if attrs:
                    warn(path, 0, "XML-ATTRS-DEPRECATED",
                         f"'attrs' attribute on <{elem.tag}> is deprecated in Odoo 17+ — "
                         f"use 'invisible', 'required', 'readonly' domain attributes directly")

                # class should use o_ prefix for custom classes
                css_classes = elem.get("class", "")
                if css_classes:
                    classes = css_classes.split()
                    for cls in classes:
                        if not cls:
                            continue
                        # Allow Odoo modern (o_), Odoo legacy built-ins (oe_), Bootstrap utilities
                        if (cls.startswith("o_")
                                or cls.startswith("oe_")   # oe_title, oe_avatar, oe_stat_button, oe_inline ...
                                or cls.startswith("text-") or cls.startswith("d-")
                                or cls.startswith("mt-")   or cls.startswith("mb-")
                                or cls.startswith("ms-")   or cls.startswith("me-")
                                or cls.startswith("pt-")   or cls.startswith("pb-")
                                or cls.startswith("p-")    or cls.startswith("m-")
                                or cls.startswith("col")   or cls.startswith("row")
                                or cls.startswith("fw-")   or cls.startswith("fs-")
                                or cls.startswith("bg-")   or cls.startswith("border")
                                or cls.startswith("btn")   or cls.startswith("fa")
                                or cls.startswith("alert") or cls.startswith("badge")
                                or cls.startswith("card")  or cls.startswith("form-")
                                or cls.startswith("nav")   or cls.startswith("tab")
                                or "-" in cls):
                            continue
                        info(path, 0, "XML-CSS-PREFIX",
                             f"Custom CSS class '{cls}' should be prefixed with 'o_' for Odoo modules")
                        break  # one warning per element is enough


def check_xml_security(path, lines, content, root):
    """Check access rights CSV structure when it's an XML security file."""
    fname = path.name
    if fname == "ir.model.access.csv":
        return  # handled separately

    # Look for access records
    for record in root.iter("record"):
        if record.get("model") == "ir.rule":
            # Domain filter should not be empty
            domain_field = record.find(".//field[@name='domain_force']")
            if domain_field is not None:
                domain_text = (domain_field.text or "").strip()
                if domain_text in ("[]", "[(1,'=',1)]", ""):
                    info(path, 0, "XML-IRULE-DOMAIN",
                         f"ir.rule '{record.get('id')}' has a non-restrictive domain — verify this is intentional")


def check_xml_patterns(path, lines, content):
    """Regex-based XML pattern checks."""
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Hardcoded translatable strings without translation marker
        if re.search(r'string="[A-Z][^"]{3,}"', stripped):
            # Check if it's already in a translatable context
            if 'translate="True"' not in stripped and not stripped.startswith("<!--"):
                pass  # Too many false positives — skip for now

        # JavaScript in XML (except OWL templates)
        if "<script" in stripped and 'type="text/javascript"' in stripped:
            warn(path, lineno, "XML-SCRIPT-TAG",
                 "Inline <script> tags in XML views — use static JS files instead")

        # noupdate should be True for core data
        if 'noupdate="0"' in stripped and "security" in str(path):
            info(path, lineno, "XML-NOUPDATE",
                 "Security/access records in data file with noupdate=0 — "
                 "consider noupdate=1 so manual changes aren't overwritten on upgrade")

        # href with http:// (should use https or CDN path)
        if re.search(r'href="http://', stripped):
            warn(path, lineno, "XML-HTTP-LINK",
                 "Non-HTTPS URL in href attribute — use https:// or a relative path")


def _xml_lineno(lines, search_text):
    """Find approximate line number for a text in XML lines."""
    for i, line in enumerate(lines, start=1):
        if search_text in line:
            return i
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# JAVASCRIPT / OWL CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def check_js_file(path):
    lines, content = read_file(path)
    if content is None:
        return

    check_js_imports(path, lines, content)
    check_js_owl(path, lines, content)
    check_js_patterns(path, lines, content)


def check_js_imports(path, lines, content):
    """Check OWL/Odoo import style."""
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Must use ES module imports not require()
        if re.search(r"\brequire\s*\(", stripped) and not stripped.startswith("//"):
            warn(path, lineno, "JS-REQUIRE",
                 "Use ES module imports (import { X } from '@odoo/owl') not require()")

        # No jQuery ($) — deprecated in v17+
        if re.search(r"\$\s*[\.\(]|jQuery\s*[\.\(]", stripped) and not stripped.startswith("//"):
            warn(path, lineno, "JS-JQUERY",
                 "jQuery/$ usage is deprecated in Odoo 17+ OWL components — use native DOM or OWL APIs")

        # Import from correct Odoo paths
        if "import" in stripped:
            if re.search(r'from\s+["\']\.\.\.?/', stripped):
                info(path, lineno, "JS-RELATIVE-IMPORT",
                     "Relative imports like '../' are fragile — prefer '@module/...' absolute imports")


def check_js_owl(path, lines, content):
    """OWL-specific checks."""
    is_component = "Component" in content and ("owl" in content.lower() or "@odoo" in content)
    if not is_component:
        return

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # OWL 1 render() calls — not needed in OWL 2
        if re.search(r"this\.render\s*\(\s*\)", stripped) and not stripped.startswith("//"):
            warn(path, lineno, "JS-OWL-RENDER",
                 "this.render() is not needed in OWL 2 (v18+) — state changes trigger automatic re-renders")

        # Deprecated willPatch / patched lifecycle hooks in favour of onPatched
        if re.search(r"\b(willPatch|patched)\s*\(\s*\)", stripped):
            info(path, lineno, "JS-OWL-HOOKS",
                 "willPatch/patched are OWL 1 hooks — use onPatched() in OWL 2 (v18+)")

        # Static template must be declared
        if re.search(r"class\s+\w+\s+extends\s+Component", stripped):
            # Check if static template is in the class
            class_block_start = lineno
            has_template = any(
                "static template" in lines[j] for j in range(lineno, min(lineno + 20, len(lines)))
            )
            if not has_template:
                warn(path, lineno, "JS-OWL-TEMPLATE",
                     "OWL Component class should declare 'static template = \"Module.TemplateName\"'")

        # useState should be destructured from @odoo/owl, not standalone
        if "useState" in stripped and "import" not in stripped:
            if not re.search(r"this\.\w+\s*=\s*useState", stripped):
                info(path, lineno, "JS-OWL-USESTATE",
                     "Ensure useState is imported from '@odoo/owl' and used in setup()")

        # No direct DOM manipulation inside OWL components
        if re.search(r"document\.(getElementById|querySelector|createElement)", stripped):
            warn(path, lineno, "JS-DOM-MANIP",
                 "Direct DOM manipulation in OWL component — use refs (useRef) or OWL directives instead")


def check_js_patterns(path, lines, content):
    """General JS best practices."""
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # console.log left in
        if re.search(r"\bconsole\.(log|warn|error|dir)\s*\(", stripped) and not stripped.startswith("//"):
            warn(path, lineno, "JS-CONSOLE",
                 "console.log/warn/error should not be in production code")

        # eval() usage
        if re.search(r"\beval\s*\(", stripped) and not stripped.startswith("//"):
            err(path, lineno, "JS-EVAL",
                "eval() is a security risk — never use eval()")

        # var declarations (use const/let)
        if re.match(r"\s*var\s+", line) and not stripped.startswith("//"):
            warn(path, lineno, "JS-VAR",
                 "Use 'const' or 'let' instead of 'var'")

        # == instead of ===
        if re.search(r"[^=!<>]=[^=]|[^=!<>]==(?!=)", stripped):
            # More careful: look for == not ===
            if re.search(r"(?<!=)==(?!=)", stripped) and not stripped.startswith("//"):
                info(path, lineno, "JS-LOOSE-EQ",
                     "Use strict equality '===' instead of loose '=='")

        # alert() / confirm() usage
        if re.search(r"\b(alert|confirm|prompt)\s*\(", stripped):
            warn(path, lineno, "JS-DIALOG",
                 "Use Odoo dialog services instead of native alert()/confirm()/prompt()")

        # Hardcoded URLs
        if re.search(r'["\']https?://(localhost|127\.0\.0\.1)', stripped):
            warn(path, lineno, "JS-HARDCODED-URL",
                 "Hardcoded localhost URL — use relative paths or configuration")


# ─────────────────────────────────────────────────────────────────────────────
# SCSS / CSS CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def check_scss_file(path):
    lines, content = read_file(path)
    if content is None:
        return

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # !important overuse
        if "!important" in stripped and not stripped.startswith("//"):
            warn(path, lineno, "SCSS-IMPORTANT",
                 "Avoid !important — refactor specificity instead")

        # Hardcoded colours instead of variables
        if re.search(r":\s*#[0-9a-fA-F]{3,6}\b", stripped):
            info(path, lineno, "SCSS-HARDCODED-COLOR",
                 "Hardcoded hex colour — use Odoo Bootstrap SCSS variables (e.g. $o-brand-primary)")

        # Pixel font sizes (use rem/em)
        if re.search(r"font-size:\s*\d+px", stripped):
            info(path, lineno, "SCSS-PX-FONT",
                 "px font-size — consider using rem/em for accessibility")

        # ID selectors (high specificity)
        if re.match(r"\s*#[a-zA-Z]", line) and "{" in line:
            warn(path, lineno, "SCSS-ID-SELECTOR",
                 "ID selectors (#id) have very high specificity — use class selectors instead")


# ─────────────────────────────────────────────────────────────────────────────
# ACCESS RIGHTS CSV CHECK
# ─────────────────────────────────────────────────────────────────────────────

def check_access_csv(path):
    lines, content = read_file(path)
    if content is None:
        return

    expected_header = "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink"

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if lineno == 1:
            if stripped != expected_header:
                warn(path, lineno, "CSV-HEADER",
                     f"ir.model.access.csv header doesn't match expected format:\n  "
                     f"Expected: {expected_header}\n  Got:      {stripped}")
            continue

        parts = stripped.split(",")
        if len(parts) != 8:
            err(path, lineno, "CSV-COLUMNS",
                f"Row has {len(parts)} columns, expected 8: {stripped}")
            continue

        rec_id, name, model_id, group_id, r, w, c, u = parts

        if not rec_id:
            err(path, lineno, "CSV-ID", "Access rule missing id")

        if not model_id:
            err(path, lineno, "CSV-MODEL", f"Access rule '{rec_id}' missing model_id")

        # Permissions should be 0 or 1
        for perm_name, perm_val in [("perm_read", r), ("perm_write", w),
                                     ("perm_create", c), ("perm_unlink", u)]:
            if perm_val not in ("0", "1"):
                err(path, lineno, "CSV-PERM",
                    f"Access rule '{rec_id}' has invalid value '{perm_val}' for {perm_name} (must be 0 or 1)")

        # Warn about full access without a group
        if not group_id and r == "1" and w == "1" and c == "1" and u == "1":
            warn(path, lineno, "CSV-FULL-ACCESS",
                 f"Access rule '{rec_id}' grants full CRUD to ALL users (no group) — verify this is intentional")


# ─────────────────────────────────────────────────────────────────────────────
# PYLINT-ODOO INTEGRATION (OPTIONAL)
# ─────────────────────────────────────────────────────────────────────────────

def run_pylint_odoo(module_path):
    """Run pylint-odoo if available; parse and add results."""
    try:
        result = subprocess.run(
            ["pylint", "--load-plugins=pylint_odoo", "--output-format=json",
             "--disable=E0401,W8113,W0212,R0903",  # E0401: import errors (Odoo not installed in review env)
             # W8113: redundant string= attr — valid for UI clarity and searchability
             # W0212: protected member access — single _ methods are internal, not private, in Odoo
             # R0903: too few public methods — Odoo models often have none, logic is in fields/_methods
             str(module_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
        try:
            pylint_issues = json.loads(result.stdout)
        except json.JSONDecodeError:
            return  # no JSON output means pylint-odoo not installed or no issues

        for issue in pylint_issues:
            severity_map = {"error": SEVERITY_ERROR, "warning": SEVERITY_WARNING,
                            "convention": SEVERITY_INFO, "refactor": SEVERITY_INFO,
                            "fatal": SEVERITY_ERROR}
            add(
                file_path=issue.get("path", ""),
                line=issue.get("line", 0),
                severity=severity_map.get(issue.get("type", "warning"), SEVERITY_WARNING),
                code=f"PYLINT-{issue.get('message-id', '?')}",
                message=f"[pylint-odoo] {issue.get('message', '')}",
            )
    except FileNotFoundError:
        info(module_path, 0, "PYLINT-MISSING",
             "pylint not found — install pylint + pylint-odoo for additional checks")
    except subprocess.TimeoutExpired:
        warn(module_path, 0, "PYLINT-TIMEOUT", "pylint timed out after 120s")


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────

def is_odoo_module(path):
    """Return True if path contains a valid Odoo module (has __manifest__.py)."""
    return (path / "__manifest__.py").exists()




def is_git_repo(path):
    """Return True if path is inside a git repository."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-dir"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False  # git not installed


def get_git_root(path):
    """Return the root directory of the git repo."""
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return Path(result.stdout.strip()) if result.returncode == 0 else None


def get_changed_files(git_root):
    """
    Return a set of absolute Paths for every file changed since the last commit.
    Includes:
      - Staged changes        (git diff --cached)
      - Unstaged changes      (git diff)
      - Untracked new files   (git ls-files --others --exclude-standard)
    """
    changed = set()

    # Staged + unstaged tracked changes
    for cmd in (
        ["git", "-C", str(git_root), "diff", "--name-only", "HEAD"],
        ["git", "-C", str(git_root), "diff", "--name-only", "--cached"],
    ):
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                changed.add((git_root / line).resolve())

    # New untracked files
    result = subprocess.run(
        ["git", "-C", str(git_root), "ls-files", "--others", "--exclude-standard"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            changed.add((git_root / line).resolve())

    return changed


def get_git_changed_summary(git_root):
    """Return a human-readable summary of the last commit for display."""
    result = subprocess.run(
        ["git", "-C", str(git_root), "log", "-1", "--pretty=format:%h  %s  (%ar)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"

def discover_modules(addons_path):
    """Return sorted list of all Odoo module paths inside an addons directory."""
    addons_path = Path(addons_path).resolve()
    modules = []
    for entry in sorted(addons_path.iterdir()):
        if entry.is_dir() and is_odoo_module(entry):
            modules.append(entry)
    return modules


def review_module(module_path, show_header=True, changed_files=None):
    """
    Scan a single module.
    changed_files: optional set of Path objects — if provided, only those files
                   are checked (git-diff mode). Structure checks always run.
    """
    module_path = Path(module_path).resolve()

    if show_header:
        print(f"\n{chr(9552)*72}")
        print(f"  ODOO CODE REVIEW  ›  {module_path.name}")
        print(f"  {chr(9552)*72}\n")

    git_mode = changed_files is not None

    print(f"  📦  {module_path.name}" + ("  [git-diff mode]" if git_mode else ""))
    print(f"  {'─'*40}")

    def should_check(f):
        """Return True if this file should be checked."""
        if not git_mode:
            return True
        return Path(f).resolve() in changed_files

    # 1. Module structure — always check (manifest/security don't need git filter)
    print("  📁  Checking module structure …")
    check_module_structure(module_path)

    # 2. Python files
    py_files   = [f for f in find_files(module_path, ".py") if should_check(f)]
    skipped_py = len(list(find_files(module_path, ".py"))) - len(py_files)
    suffix     = f" ({skipped_py} unchanged skipped)" if git_mode and skipped_py else ""
    print(f"  🐍  Checking {len(py_files)} Python file(s){suffix} …")
    for f in py_files:
        check_python_file(f)

    # 3. XML files
    xml_files   = [f for f in find_files(module_path, ".xml") if should_check(f)]
    skipped_xml = len(list(find_files(module_path, ".xml"))) - len(xml_files)
    suffix      = f" ({skipped_xml} unchanged skipped)" if git_mode and skipped_xml else ""
    print(f"  📄  Checking {len(xml_files)} XML file(s){suffix} …")
    for f in xml_files:
        check_xml_file(f)

    # 4. JS files
    js_files   = [f for f in find_files(module_path, ".js") if should_check(f)]
    skipped_js = len(list(find_files(module_path, ".js"))) - len(js_files)
    suffix     = f" ({skipped_js} unchanged skipped)" if git_mode and skipped_js else ""
    print(f"  ⚡  Checking {len(js_files)} JS file(s){suffix} …")
    for f in js_files:
        check_js_file(f)

    # 5. SCSS files
    scss_files   = [f for f in find_files(module_path, ".scss", ".css") if should_check(f)]
    skipped_scss = len(list(find_files(module_path, ".scss", ".css"))) - len(scss_files)
    suffix       = f" ({skipped_scss} unchanged skipped)" if git_mode and skipped_scss else ""
    print(f"  🎨  Checking {len(scss_files)} SCSS/CSS file(s){suffix} …")
    for f in scss_files:
        check_scss_file(f)

    # 6. Access CSV — always check
    csv_path = module_path / "security" / "ir.model.access.csv"
    if csv_path.exists():
        print("  🔒  Checking access rights CSV …")
        check_access_csv(csv_path)

    # 7. pylint-odoo — only on changed files to keep it fast
    if py_files:
        print("  🔬  Running pylint-odoo (if installed) …")
        if git_mode:
            for f in py_files:
                run_pylint_odoo(f)
        else:
            run_pylint_odoo(module_path)

    print()


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

SEVERITY_ICON  = {SEVERITY_ERROR: "🔴", SEVERITY_WARNING: "🟡", SEVERITY_INFO: "🔵"}
SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}


def calculate_score(errors, warnings, infos):
    penalty  = min(len(errors)   * 1.5, 6.0)
    penalty += min(len(warnings) * 0.5, 3.0)
    penalty += min(len(infos)    * 0.1, 1.0)
    return max(round(10.0 - penalty, 1), 0.0)


def score_bar(score):
    filled = round(score)
    bar    = "█" * filled + "░" * (10 - filled)
    if score >= 9:   grade, colour = "Excellent", "✅"
    elif score >= 7: grade, colour = "Good",      "🟢"
    elif score >= 5: grade, colour = "Fair",      "🟡"
    elif score >= 3: grade, colour = "Poor",      "🟠"
    else:            grade, colour = "Critical",  "🔴"
    return bar, grade, colour


def print_module_report(module_path, module_issues, base_path=None):
    """Print report for a single module. Returns (errors, warnings, infos, score)."""
    by_file = defaultdict(list)
    for issue in module_issues:
        by_file[issue["file"]].append(issue)

    errors   = [i for i in module_issues if i["severity"] == SEVERITY_ERROR]
    warnings = [i for i in module_issues if i["severity"] == SEVERITY_WARNING]
    infos    = [i for i in module_issues if i["severity"] == SEVERITY_INFO]
    score    = calculate_score(errors, warnings, infos)
    bar, grade, colour = score_bar(score)
    total    = len(module_issues)

    print(f"{'─'*72}")
    print(f"  {colour}  {module_path.name:<30}  {bar}  {score:.1f}/10  ({grade})")
    print(f"{'─'*72}")
    print(f"  🔴 Errors {len(errors):>3}   🟡 Warnings {len(warnings):>3}   🔵 Info {len(infos):>3}   Total {total:>3}")
    print(f"{'─'*72}\n")

    root = base_path or module_path

    for file_path, file_issues in sorted(by_file.items()):
        try:
            rel = Path(file_path).relative_to(root)
        except ValueError:
            rel = file_path
        print(f"  📂  {rel}")
        print(f"  {'─'*60}")
        sorted_issues = sorted(file_issues, key=lambda x: (SEVERITY_ORDER[x["severity"]], x["line"]))
        for issue in sorted_issues:
            icon     = SEVERITY_ICON[issue["severity"]]
            line_str = f"L{issue['line']}" if issue["line"] else "file"
            print(f"  {icon}  {line_str:<6}  [{issue['code']}]  {issue['message']}")
        print()

    if errors:
        print(f"  🚫  FAILED — {len(errors)} error(s) must be fixed.\n")
    elif warnings:
        print(f"  ⚠️   PASSED WITH WARNINGS — {len(warnings)} warning(s) to address.\n")
    else:
        print(f"  ✅  PASSED — {len(infos)} info note(s) only.\n")

    return score


def print_addons_summary(module_results):
    """Print a summary table for all modules in an addons scan."""
    print(f"\n{'═'*72}")
    print(f"  ADDONS SUMMARY")
    print(f"{'═'*72}")
    print(f"  {'Module':<35} {'Score':>6}  {'Grade':<10}  E    W    I")
    print(f"  {'─'*35} {'─'*6}  {'─'*10}  {'─'*3}  {'─'*3}  {'─'*3}")

    total_e = total_w = total_i = 0
    scores  = []

    for name, (score, e, w, i) in sorted(module_results.items()):
        _, grade, _ = score_bar(score)
        print(f"  {name:<35} {score:>5.1f}  {grade:<10}  {e:<3}  {w:<3}  {i:<3}")
        total_e += e
        total_w += w
        total_i += i
        scores.append(score)

    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    _, avg_grade, avg_colour = score_bar(avg_score)

    print(f"  {'─'*35} {'─'*6}  {'─'*10}  {'─'*3}  {'─'*3}  {'─'*3}")
    print(f"  {'TOTAL  (' + str(len(scores)) + ' modules)':<35} {avg_score:>5.1f}  {avg_grade:<10}  {total_e:<3}  {total_w:<3}  {total_i:<3}")
    print(f"\n  {avg_colour}  ADDONS AVERAGE SCORE :  {avg_score:.1f} / 10  ({avg_grade})")
    print(f"{'═'*72}\n")

    return avg_score, (1 if total_e > 0 else 0)



# ─────────────────────────────────────────────────────────────────────────────
# JSON OUTPUT  (for GitHub Actions integration)
# ─────────────────────────────────────────────────────────────────────────────

def _write_json(output_path, score, module_issues):
    """Write single-module review result as JSON for CI consumption."""
    errors   = [i for i in module_issues if i["severity"] == SEVERITY_ERROR]
    warnings = [i for i in module_issues if i["severity"] == SEVERITY_WARNING]
    infos    = [i for i in module_issues if i["severity"] == SEVERITY_INFO]
    data = {
        "avg_score":      score,
        "total_errors":   len(errors),
        "total_warnings": len(warnings),
        "total_info":     len(infos),
        "passed":         score > 5.0,
        "issues": [
            {
                "file":     i["file"],
                "line":     i["line"],
                "severity": i["severity"],
                "code":     i["code"],
                "message":  i["message"],
            }
            for i in module_issues
        ],
    }
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)


def _write_json_addons(output_path, avg_score, module_results):
    """Write addons review result as JSON for CI consumption."""
    total_e = sum(e for _, (_, e, w, i) in module_results.items())
    total_w = sum(w for _, (_, e, w, i) in module_results.items())
    total_i = sum(i for _, (_, e, w, i) in module_results.items())
    data = {
        "avg_score":      avg_score,
        "total_errors":   total_e,
        "total_warnings": total_w,
        "total_info":     total_i,
        "passed":         avg_score > 5.0,
        "modules": {
            name: {"score": s, "errors": e, "warnings": w, "info": i}
            for name, (s, e, w, i) in module_results.items()
        },
    }
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("\nUsage:")
        print("  Single module :  python3 odoo_code_review.py <module_path>")
        print("  Addons folder :  python3 odoo_code_review.py <addons_path>")
        print()
        print("  Git-aware mode is automatic — if the path is inside a git repo,")
        print("  only files changed since the last commit are checked.")
        print("  Pass --all to force a full scan even inside a git repo.")
        sys.exit(1)

    force_all   = "--all"  in sys.argv
    hook_mode   = "--hook" in sys.argv

    # --min-score 7.0
    min_score = 5.0
    for i, arg in enumerate(sys.argv):
        if arg == "--min-score" and i + 1 < len(sys.argv):
            try:
                min_score = float(sys.argv[i + 1])
            except ValueError:
                pass

    # --output-json /path/result.json
    output_json = None
    for i, arg in enumerate(sys.argv):
        if arg == "--output-json" and i + 1 < len(sys.argv):
            output_json = sys.argv[i + 1]

    clean_args = [a for a in sys.argv[1:]
                  if not a.startswith("--") and
                  not (len(sys.argv) > sys.argv.index(a) - 1 > 0 and
                       sys.argv[sys.argv.index(a) - 1] in ("--min-score", "--output-json"))]

    # simpler clean_args: skip flags and their values
    clean_args = []
    skip_next  = False
    for arg in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg in ("--min-score", "--output-json"):
            skip_next = True
            continue
        if arg.startswith("--"):
            continue
        clean_args.append(arg)

    if not clean_args:
        print("\n❌  No path provided.")
        sys.exit(1)

    scan_path = Path(clean_args[0]).resolve()

    if not scan_path.exists():
        print(f"\n❌  Path does not exist: {scan_path}")
        sys.exit(1)

    # ── Detect git and collect changed files ─────────────────────────────────
    changed_files = None   # None = full scan

    if not force_all and is_git_repo(scan_path):
        git_root     = get_git_root(scan_path)
        changed_files = get_changed_files(git_root)
        last_commit  = get_git_changed_summary(git_root)

        print(f"\n  🔀  Git repo detected: {git_root.name}")
        print(f"  📝  Last commit : {last_commit}")

        if not changed_files:
            print("  ✅  No changed files since last commit — nothing to review.\n")
            sys.exit(0)

        # Filter to only files inside scan_path
        changed_files = {f for f in changed_files if str(f).startswith(str(scan_path))}

        if not changed_files:
            print(f"  ✅  No changed files inside {scan_path.name} since last commit — nothing to review.\n")
            sys.exit(0)

        print(f"  📂  Changed files : {len(changed_files)} file(s) to review")
        for f in sorted(changed_files):
            try:
                rel = f.relative_to(scan_path)
            except ValueError:
                rel = f
            print(f"       • {rel}")
        print()

    # ── Auto-detect: single module or addons directory ────────────────────────
    if is_odoo_module(scan_path):
        # ── SINGLE MODULE MODE ────────────────────────────────────────────────
        mode = "GIT-DIFF" if changed_files is not None else "FULL SCAN"
        print(f"\n{'═'*72}")
        print(f"  ODOO CODE REVIEW  ›  {scan_path.name}  [{mode}]")
        print(f"{'═'*72}\n")

        review_module(scan_path, show_header=False, changed_files=changed_files)
        module_issues = list(issues)

        score = print_module_report(scan_path, module_issues)
        if output_json:
            _write_json(output_json, score, module_issues)

        if hook_mode and score <= min_score:
            print(f"  🚫  COMMIT BLOCKED — score {score:.1f}/10 is below minimum {min_score}/10")
            print(f"       Fix the issues above then try again.\n")
            sys.exit(1)
        sys.exit(1 if any(i["severity"] == SEVERITY_ERROR for i in module_issues) else 0)

    else:
        # ── ADDONS MODE ───────────────────────────────────────────────────────
        # In git mode, only scan modules that have at least one changed file
        all_modules = discover_modules(scan_path)

        if not all_modules:
            print(f"\n❌  No Odoo modules found in: {scan_path}")
            print("    Each module must contain a __manifest__.py file.")
            sys.exit(1)

        if changed_files is not None:
            # Only include modules that have changed files
            modules = [
                m for m in all_modules
                if any(str(cf).startswith(str(m)) for cf in changed_files)
            ]
            skipped = len(all_modules) - len(modules)
        else:
            modules = all_modules
            skipped = 0

        if not modules:
            print(f"  ✅  No modules with changed files — nothing to review.\n")
            sys.exit(0)

        mode = "GIT-DIFF" if changed_files is not None else "FULL SCAN"
        print(f"\n{'═'*72}")
        print(f"  ODOO ADDONS CODE REVIEW  ›  {scan_path.name}  [{mode}]")
        print(f"  Scanning {len(modules)} module(s)" + (f"  ({skipped} unchanged skipped)" if skipped else ""))
        print(f"{'═'*72}")

        module_results = {}

        for module_path in modules:
            issues.clear()

            review_module(module_path, show_header=False, changed_files=changed_files)
            module_issues = list(issues)

            e = sum(1 for i in module_issues if i["severity"] == SEVERITY_ERROR)
            w = sum(1 for i in module_issues if i["severity"] == SEVERITY_WARNING)
            i = sum(1 for i in module_issues if i["severity"] == SEVERITY_INFO)
            score = calculate_score(
                [x for x in module_issues if x["severity"] == SEVERITY_ERROR],
                [x for x in module_issues if x["severity"] == SEVERITY_WARNING],
                [x for x in module_issues if x["severity"] == SEVERITY_INFO],
            )

            print(f"\n{'═'*72}")
            print_module_report(module_path, module_issues, base_path=scan_path)

            module_results[module_path.name] = (score, e, w, i)

        avg_score, exit_code = print_addons_summary(module_results)

        if output_json:
            _write_json_addons(output_json, avg_score, module_results)

        if hook_mode and avg_score <= min_score:
            print(f"  🚫  COMMIT BLOCKED — average score {avg_score:.1f}/10 is below minimum {min_score}/10")
            print(f"       Fix the issues above then try again.\n")
            sys.exit(1)
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
