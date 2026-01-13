import json
import sys
from pathlib import Path
from jinja2 import Template


def format_hcl_value(value, after_unknown_for_key: bool = False, base_indent: str = "", prefix: str = "+") -> str:
    """Format a single value in HCL style matching Terraform CLI output."""
    if after_unknown_for_key:
        return "(known after apply)"

    if value is None:
        return "null"

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, (int, float)):
        return str(value)

    if isinstance(value, str):
        # Escape quotes and backslashes in strings
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    if isinstance(value, list):
        if len(value) == 0:
            return "[]"

        # Check if it's a list of simple values or objects
        if all(isinstance(item, dict) for item in value):
            # List of objects - will be handled as repeated blocks
            return None  # Signal to handle as blocks

        # List of simple values - format like Terraform CLI
        items = []
        for item in value:
            items.append(f"{base_indent}  {prefix} {format_hcl_value(item)},")
        return "[\n" + "\n".join(items) + f"\n{base_indent}{prefix} ]"

    if isinstance(value, dict):
        # Inline map format like labels = { "key" = "value" }
        if all(isinstance(v, (str, int, float, bool)) or v is None for v in value.values()):
            items = []
            for k, v in sorted(value.items()):
                items.append(f'"{k}" = {format_hcl_value(v)}')
            return "{\n" + "\n".join(f"{base_indent}  {prefix} {item}" for item in items) + f"\n{base_indent}{prefix} }}"
        return None  # Signal to handle as nested block

    return str(value)


def json_to_hcl(data: dict, after_unknown: dict = None, indent: int = 0, prefix: str = "+", resource_header: str = None) -> str:
    """Convert JSON data to HCL-style formatted string matching Terraform CLI output.

    Args:
        data: The resource attributes dictionary
        after_unknown: Dict indicating which keys are computed (known after apply)
        indent: Current indentation level
        prefix: Line prefix (+, -, ~) for the action type
        resource_header: If provided, wraps output in 'resource "type" "name" { ... }'
    """
    if data is None:
        data = {}

    after_unknown = after_unknown or {}
    lines = []
    base_indent = "      "  # 6 spaces like Terraform CLI
    attr_indent = base_indent + "  " * indent

    # Merge keys from both data and after_unknown (for computed-only fields)
    all_keys = set(data.keys()) | set(after_unknown.keys())

    # Sort keys for consistent output, but put simple values first, then blocks
    simple_keys = []
    block_keys = []

    for key in sorted(all_keys):
        value = data.get(key)
        unknown_val = after_unknown.get(key, False)

        # If the key is fully unknown (True), it's a simple computed value
        if unknown_val is True:
            simple_keys.append(key)
        elif isinstance(value, dict) and not all(isinstance(v, (str, int, float, bool)) or v is None for v in value.values()):
            block_keys.append(key)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            block_keys.append(key)
        else:
            simple_keys.append(key)

    # Calculate max key length for alignment (Terraform-style padding)
    all_simple_keys = simple_keys
    max_key_len = max((len(k) for k in all_simple_keys), default=0)

    # Process simple key-value pairs first
    for key in simple_keys:
        value = data.get(key)
        is_unknown = after_unknown.get(key, False) is True

        # Pad key for alignment
        padded_key = key.ljust(max_key_len)
        formatted_value = format_hcl_value(value, is_unknown, attr_indent, prefix)

        if formatted_value is not None:
            lines.append(f"{attr_indent}{prefix} {padded_key} = {formatted_value}")

    # Add blank line before blocks if we had simple values
    if simple_keys and block_keys:
        lines.append("")

    # Process blocks (dicts and lists of dicts)
    for key in block_keys:
        value = data[key]
        unknown_val = after_unknown.get(key, False)
        # If unknown_val is True (boolean), the whole block is unknown
        # If it's a list/dict, it contains nested unknown info, but the block itself is known
        is_fully_unknown = unknown_val is True

        if is_fully_unknown:
            lines.append(f"{attr_indent}{prefix} {key} (known after apply)")
        elif isinstance(value, dict):
            # Single nested block
            lines.append(f"{attr_indent}{prefix} {key} {{")
            nested_hcl = json_to_hcl(value, {}, indent + 1, prefix)
            if nested_hcl.strip():
                lines.append(nested_hcl)
            lines.append(f"{attr_indent}{prefix} }}")
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            # List of blocks - repeat the block for each item
            for item in value:
                lines.append(f"{attr_indent}{prefix} {key} {{")
                nested_hcl = json_to_hcl(item, {}, indent + 1, prefix)
                if nested_hcl.strip():
                    lines.append(nested_hcl)
                lines.append(f"{attr_indent}{prefix} }}")

    result = "\n".join(lines)

    # Wrap in resource header if provided
    if resource_header:
        return f"  {prefix} {resource_header} {{\n{result}\n{prefix}   }}"

    return result


def parse_terraform_plan(plan_path: Path) -> dict:
    """Parse Terraform JSON plan file."""
    with open(plan_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_unified_json(before: dict, after: dict, after_unknown: dict, action: str) -> dict:
    """Build unified JSON showing full resource with change indicators."""
    if action == 'create':
        # For creates, show the after state with computed values marked
        unified = {}
        changed_keys = set()
        for key, value in (after or {}).items():
            unified[key] = value
            # Mark as changed (it's new)
            changed_keys.add(key)
        return {'json': unified, 'changed_keys': changed_keys, 'after_unknown': after_unknown or {}}

    elif action == 'delete':
        # For deletes, show the before state
        unified = before or {}
        changed_keys = set(unified.keys())  # Everything is being deleted
        return {'json': unified, 'changed_keys': changed_keys, 'after_unknown': {}}

    else:
        # For updates/replaces, merge and identify changes
        before_dict = before or {}
        after_dict = after or {}
        before_keys = set(before_dict.keys())
        after_keys = set(after_dict.keys())
        all_keys = before_keys | after_keys
        unified = {}
        deleted_keys = set()  # Only in before (RED)
        changed_keys = set()  # In both but different (ORANGE)
        created_keys = set()  # Only in after (GREEN)

        for key in all_keys:
            before_val = before_dict.get(key)
            after_val = after_dict.get(key)

            # Use after value in unified view
            unified[key] = after_val

            # Categorize the key based on presence and value
            in_before = key in before_keys
            in_after = key in after_keys

            # Check if the value is meaningful (not null)
            before_has_value = in_before and before_val is not None
            after_has_value = in_after and after_val is not None

            if before_has_value and not after_has_value:
                # Value exists in before but not in after (deleted)
                deleted_keys.add(key)
            elif not before_has_value and after_has_value:
                # Value exists in after but not in before (created)
                created_keys.add(key)
            elif before_has_value and after_has_value and before_val != after_val:
                # Value exists in both but different (changed)
                changed_keys.add(key)

        return {
            'json': unified,
            'changed_keys': changed_keys,
            'deleted_keys': deleted_keys,
            'created_keys': created_keys,
            'after_unknown': after_unknown or {}
        }


def analyze_changes(plan_data: dict) -> dict:
    """Analyze the plan and extract change statistics and details."""
    resource_changes = plan_data.get('resource_changes', [])

    stats = {
        'create': 0,
        'update': 0,
        'delete': 0,
        'replace': 0,
        'no-op': 0,
    }

    changes_detail = []

    for resource in resource_changes:
        actions = resource.get('change', {}).get('actions', [])
        address = resource.get('address', 'unknown')
        resource_type = resource.get('type', 'unknown')
        resource_name = resource.get('name', 'unknown')

        # Determine primary action
        if actions == ['create']:
            action = 'create'
            stats['create'] += 1
        elif actions == ['delete']:
            action = 'delete'
            stats['delete'] += 1
        elif actions == ['update']:
            action = 'update'
            stats['update'] += 1
        elif 'delete' in actions and 'create' in actions:
            action = 'replace'
            stats['replace'] += 1
        elif actions == ['no-op']:
            action = 'no-op'
            stats['no-op'] += 1
        else:
            action = 'unknown'

        # Get before and after values
        change = resource.get('change', {})
        before = change.get('before', {})
        after = change.get('after', {})
        after_unknown = change.get('after_unknown', {})

        # Build unified JSON view
        unified_json_data = build_unified_json(before, after, after_unknown, action)

        # Determine prefix based on action
        if action == 'create':
            prefix = '+'
        elif action == 'delete':
            prefix = '-'
        elif action == 'replace':
            prefix = '-/+'  # Will use - for before, + for after
        else:  # update
            prefix = '~'

        # Generate HCL-style output with resource header
        resource_header = f'resource "{resource_type}" "{resource_name}"'

        if action in ['update', 'replace']:
            before_hcl = json_to_hcl(before, {}, prefix='-', resource_header=resource_header) if before else None
            after_hcl = json_to_hcl(after, after_unknown, prefix='+', resource_header=resource_header) if after else None
        elif action == 'delete':
            before_hcl = json_to_hcl(before, {}, prefix='-', resource_header=resource_header) if before else None
            after_hcl = None
        else:  # create
            before_hcl = None
            after_hcl = json_to_hcl(after, after_unknown, prefix='+', resource_header=resource_header) if after else None

        changes_detail.append({
            'address': address,
            'type': resource_type,
            'name': resource_name,
            'action': action,
            'unified_json': json.dumps(unified_json_data['json'], indent=2),
            'changed_keys': list(unified_json_data['changed_keys']),
            'deleted_keys': list(unified_json_data.get('deleted_keys', [])),
            'created_keys': list(unified_json_data.get('created_keys', [])),
            'after_unknown': unified_json_data['after_unknown'],
            'before_hcl': before_hcl,
            'after_hcl': after_hcl,
        })

    return {
        'stats': stats,
        'changes': changes_detail,
        'terraform_version': plan_data.get('terraform_version', 'unknown'),
        'format_version': plan_data.get('format_version', 'unknown')
    }


def generate_html(analysis: dict, output_path: Path):
    """Generate HTML visualization using Jinja2 template."""

    template_str = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Terraform Plan Visualization</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css" rel="stylesheet">
    <style>
        .badge-create { background-color: #28a745; }
        .badge-update { background-color: #ffc107; color: #000; }
        .badge-delete { background-color: #dc3545; }
        .badge-replace { background-color: #fd7e14; }
        .badge-no-op { background-color: #6c757d; }

        .card-create { border-left: 4px solid #28a745; }
        .card-update { border-left: 4px solid #ffc107; }
        .card-delete { border-left: 4px solid #dc3545; }
        .card-replace { border-left: 4px solid #fd7e14; }
        .card-no-op { border-left: 4px solid #6c757d; }

        .hcl-viewer {
            background-color: #f8f9fa;
            border-radius: 4px;
            padding: 1rem;
        }

        .diff-container {
            display: flex;
            gap: 0;
            align-items: stretch;
        }

        .diff-panel {
            flex: 1;
            min-width: 0;
            display: flex;
            flex-direction: column;
        }

        .diff-panel-header {
            font-weight: bold;
            padding: 0.5rem;
            border-radius: 4px 4px 0 0;
            font-size: 0.85rem;
        }

        .diff-panel-before .diff-panel-header {
            background-color: #f8d7da;
            color: #842029;
        }

        .diff-panel-after .diff-panel-header {
            background-color: #d4edda;
            color: #0f5132;
        }

        .diff-panel-content {
            background-color: #f8f9fa;
            border: 1px solid #dee2e6;
            border-top: none;
            border-radius: 0 0 4px 4px;
            padding: 0.5rem;
            overflow-x: auto;
            flex: 1;
        }

        .hcl-content {
            font-family: 'Courier New', monospace;
            font-size: 0.85rem;
            margin: 0;
            white-space: pre;
            overflow-x: auto;
            line-height: 1.4;
        }

        /* Highlight changed lines */
        .hcl-content .highlight-create {
            background-color: #d4edda;
            display: inline-block;
            width: 100%;
        }

        .hcl-content .highlight-delete {
            background-color: #f8d7da;
            display: inline-block;
            width: 100%;
        }

        .hcl-content .highlight-update {
            background-color: #fff3cd;
            display: inline-block;
            width: 100%;
        }

        .hcl-content .highlight-replace {
            background-color: #ffe8d1;
            display: inline-block;
            width: 100%;
        }

        .hcl-content .computed-badge {
            display: inline-block;
            background-color: #ffc107;
            color: #000;
            padding: 0.1rem 0.3rem;
            border-radius: 3px;
            font-size: 0.75rem;
            margin-left: 0.5rem;
            font-weight: bold;
        }

        .sticky-top-custom {
            position: sticky;
            top: 0;
            z-index: 1020;
            background-color: white;
            padding: 1rem 0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }

        .clickable-header {
            cursor: pointer;
            user-select: none;
        }

        .clickable-header:hover {
            background-color: #f8f9fa;
        }

        .clickable-header .collapse-icon {
            transition: transform 0.2s;
        }

        .clickable-header[aria-expanded="true"] .collapse-icon {
            transform: rotate(90deg);
        }

        .change-card {
            margin-bottom: 1rem;
            transition: box-shadow 0.2s;
        }

        .change-card:hover {
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
        }

        pre {
            margin: 0;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
    </style>
</head>
<body>
    <nav class="navbar navbar-dark bg-dark">
        <div class="container-fluid">
            <span class="navbar-brand mb-0 h1">
                <i class="bi bi-diagram-3"></i> Terraform Plan Visualization
            </span>
            <span class="text-light">
                <small>Terraform {{ analysis.terraform_version }}</small>
            </span>
        </div>
    </nav>

    <div class="container-fluid py-4">
        <!-- Summary Section -->
        <div class="row mb-4">
            <div class="col-12">
                <h2>Change Summary</h2>
            </div>
            <div class="col-md-2">
                <div class="card text-center">
                    <div class="card-body">
                        <h3 class="card-title text-success">{{ analysis.stats.create }}</h3>
                        <p class="card-text">Create</p>
                    </div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="card text-center">
                    <div class="card-body">
                        <h3 class="card-title text-warning">{{ analysis.stats['update'] }}</h3>
                        <p class="card-text">Update</p>
                    </div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="card text-center">
                    <div class="card-body">
                        <h3 class="card-title text-danger">{{ analysis.stats.delete }}</h3>
                        <p class="card-text">Delete</p>
                    </div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="card text-center">
                    <div class="card-body">
                        <h3 class="card-title" style="color: #fd7e14;">{{ analysis.stats.replace }}</h3>
                        <p class="card-text">Replace</p>
                    </div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="card text-center">
                    <div class="card-body">
                        <h3 class="card-title text-secondary">{{ analysis.stats['no-op'] }}</h3>
                        <p class="card-text">No Change</p>
                    </div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="card text-center bg-primary text-white">
                    <div class="card-body">
                        <h3 class="card-title">{{ analysis.changes|length }}</h3>
                        <p class="card-text">Total Resources</p>
                    </div>
                </div>
            </div>
        </div>

        <!-- Filter and Search -->
        <div class="sticky-top-custom">
            <div class="row">
                <div class="col-md-6">
                    <input type="text" id="searchInput" class="form-control" placeholder="Search by resource address or type...">
                </div>
                <div class="col-md-6">
                    <div class="btn-group w-100" role="group">
                        <input type="checkbox" class="btn-check" id="filter-create" value="create" checked autocomplete="off">
                        <label class="btn btn-outline-success" for="filter-create">Create</label>

                        <input type="checkbox" class="btn-check" id="filter-update" value="update" checked autocomplete="off">
                        <label class="btn btn-outline-warning" for="filter-update">Update</label>

                        <input type="checkbox" class="btn-check" id="filter-delete" value="delete" checked autocomplete="off">
                        <label class="btn btn-outline-danger" for="filter-delete">Delete</label>

                        <input type="checkbox" class="btn-check" id="filter-replace" value="replace" checked autocomplete="off">
                        <label class="btn btn-outline-warning" for="filter-replace" style="border-color: #fd7e14; color: #fd7e14;">Replace</label>

                        <input type="checkbox" class="btn-check" id="filter-no-op" value="no-op" autocomplete="off">
                        <label class="btn btn-outline-secondary" for="filter-no-op">No-op</label>
                    </div>
                </div>
            </div>
        </div>

        <!-- Resource Changes -->
        <div class="row mt-4">
            <div class="col-12">
                <h2>Resource Changes</h2>
                <div id="changesContainer">
                    {% for change in analysis.changes %}
                    <div class="card change-card card-{{ change.action }}"
                         data-action="{{ change.action }}"
                         data-search="{{ change.address }} {{ change.type }}">
                        <div class="card-header d-flex justify-content-between align-items-center clickable-header"
                             data-bs-toggle="collapse"
                             data-bs-target="#collapse-{{ loop.index }}"
                             aria-expanded="false"
                             role="button">
                            <div>
                                <i class="bi bi-chevron-right collapse-icon me-2"></i>
                                <span class="badge badge-{{ change.action }} me-2">{{ change.action|upper }}</span>
                                <strong>{{ change.address }} {% if change.action == 'create' %}will be created{% elif change.action == 'delete' %}will be destroyed{% elif change.action == 'replace' %}must be replaced{% elif change.action == 'update' %}will be updated in-place{% endif %}</strong>
                            </div>
                            <code class="text-muted">{{ change.type }}</code>
                        </div>
                        <div class="card-body p-0">
                            <div class="collapse" id="collapse-{{ loop.index }}">
                                {% if change.action in ['update', 'replace'] and change.before_hcl %}
                                <div class="diff-container">
                                    <div class="diff-panel diff-panel-before">
                                        <div class="diff-panel-header">
                                            <i class="bi bi-dash-circle"></i> Before
                                        </div>
                                        <div class="diff-panel-content">
                                            <pre class="hcl-content" data-deleted-keys='{{ change.deleted_keys|tojson }}' data-changed-keys='{{ change.changed_keys|tojson }}' data-created-keys='[]' data-after-unknown='{}' data-panel="before">{{ change.before_hcl }}</pre>
                                        </div>
                                    </div>
                                    <div class="diff-panel diff-panel-after">
                                        <div class="diff-panel-header">
                                            <i class="bi bi-plus-circle"></i> After
                                        </div>
                                        <div class="diff-panel-content">
                                            <pre class="hcl-content" data-deleted-keys='[]' data-changed-keys='{{ change.changed_keys|tojson }}' data-created-keys='{{ change.created_keys|tojson }}' data-after-unknown='{{ change.after_unknown|tojson }}' data-panel="after">{{ change.after_hcl }}</pre>
                                        </div>
                                    </div>
                                </div>
                                {% elif change.action == 'delete' and change.before_hcl %}
                                <div class="hcl-viewer">
                                    <pre class="hcl-content" data-changed-keys='{{ change.changed_keys|tojson }}' data-after-unknown='{}' data-action="delete">{{ change.before_hcl }}</pre>
                                </div>
                                {% else %}
                                <div class="hcl-viewer">
                                    <pre class="hcl-content" data-changed-keys='{{ change.changed_keys|tojson }}' data-after-unknown='{{ change.after_unknown|tojson }}' data-action="{{ change.action }}">{{ change.after_hcl }}</pre>
                                </div>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            // Filter and search functionality
            const searchInput = document.getElementById('searchInput');
            const filterCheckboxes = document.querySelectorAll('.btn-check');
            const changeCards = document.querySelectorAll('.change-card');

            function filterAndSearch() {
                const searchTerm = searchInput.value.toLowerCase();
                const activeFilters = Array.from(filterCheckboxes)
                    .filter(cb => cb.checked)
                    .map(cb => cb.value);

                changeCards.forEach(card => {
                    const action = card.dataset.action;
                    const searchText = card.dataset.search.toLowerCase();

                    const matchesFilter = activeFilters.includes(action);
                    const matchesSearch = searchText.includes(searchTerm);

                    card.style.display = (matchesFilter && matchesSearch) ? '' : 'none';
                });
            }

            searchInput.addEventListener('input', filterAndSearch);
            filterCheckboxes.forEach(cb => cb.addEventListener('change', filterAndSearch));

            // Initial filter
            filterAndSearch();

            // Highlight HCL lines based on change type
            function highlightHCL(pre) {
                // Skip if already highlighted
                if (pre.dataset.highlighted === 'true') return;

                const deletedKeys = JSON.parse(pre.dataset.deletedKeys || '[]');
                const changedKeys = JSON.parse(pre.dataset.changedKeys || '[]');
                const createdKeys = JSON.parse(pre.dataset.createdKeys || '[]');
                const action = pre.dataset.action;
                const panel = pre.dataset.panel;

                // For create/delete actions, highlight all lines with prefix
                if (action === 'create' || action === 'delete') {
                    const lines = pre.textContent.split(String.fromCharCode(10));
                    const highlightClass = action === 'create' ? 'highlight-create' : 'highlight-delete';
                    const highlightedLines = lines.map(line => {
                        const escapedLine = line.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                        const trimmed = line.trim();
                        if (trimmed.startsWith('+ ') || trimmed.startsWith('- ') || trimmed.startsWith('~ ')) {
                            return '<span class="' + highlightClass + '">' + escapedLine + '</span>';
                        }
                        return escapedLine;
                    }).join(String.fromCharCode(10));
                    pre.innerHTML = highlightedLines;
                    pre.dataset.highlighted = 'true';
                    return;
                }

                // For update/replace, only highlight changed/deleted/created keys
                // Track block nesting to highlight closing braces properly
                const lines = pre.textContent.split(String.fromCharCode(10));
                const blockStack = []; // Stack of {key, highlightClass} for nested blocks

                const highlightedLines = lines.map(line => {
                    const escapedLine = line.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    const trimmed = line.trim();

                    // Check for closing brace/bracket with prefix (e.g., "+ }" or "- ]")
                    const closingMatch = trimmed.match(/^[+\\-~]\\s*[}\\]]$/);
                    if (closingMatch) {
                        if (blockStack.length > 0) {
                            const block = blockStack.pop();
                            if (block.highlightClass) {
                                return '<span class="' + block.highlightClass + '">' + escapedLine + '</span>';
                            }
                        }
                        return escapedLine;
                    }

                    // Extract key name from line (format: "+ key = value" or "+ key {" or "+ key = [")
                    const match = trimmed.match(/^[+\\-~]\\s+(\\w+)\\s*(=\\s*\\[|=|\\{)/);
                    if (!match) return escapedLine;

                    const keyName = match[1];
                    const opener = match[2];
                    const isBlock = opener === '{';
                    const isArray = opener.includes('[');
                    let highlightClass = null;

                    // Check if this key is in any change category
                    if (deletedKeys.includes(keyName)) {
                        highlightClass = 'highlight-delete';
                    } else if (changedKeys.includes(keyName)) {
                        highlightClass = 'highlight-replace';
                    } else if (createdKeys.includes(keyName)) {
                        highlightClass = 'highlight-create';
                    }

                    // If this opens a block or array, push to stack
                    if (isBlock || isArray) {
                        blockStack.push({key: keyName, highlightClass: highlightClass});
                    }

                    if (highlightClass) {
                        return '<span class="' + highlightClass + '">' + escapedLine + '</span>';
                    }

                    return escapedLine;
                }).join(String.fromCharCode(10));

                pre.innerHTML = highlightedLines;
                pre.dataset.highlighted = 'true';
            }

            // Highlight all HCL content immediately
            document.querySelectorAll('.hcl-content').forEach(pre => highlightHCL(pre));
        });
    </script>
</body>
</html>
'''

    template = Template(template_str)
    html_content = template.render(analysis=analysis)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)


def main():
    """Main entry point."""
    input_dir = Path('/input')
    output_dir = Path('/output')

    # Find the plan JSON file in the input directory
    plan_files = list(input_dir.glob('*.json'))

    if not plan_files:
        print("Error: No JSON file found in /input directory")
        print("Please ensure you mount a directory containing a Terraform plan JSON file")
        sys.exit(1)

    if len(plan_files) > 1:
        print(f"Warning: Multiple JSON files found, using {plan_files[0].name}")

    plan_path = plan_files[0]
    print(f"Processing Terraform plan: {plan_path.name}")

    # Parse and analyze the plan
    try:
        plan_data = parse_terraform_plan(plan_path)
        analysis = analyze_changes(plan_data)

        print(f"Found {len(analysis['changes'])} resource changes:")
        print(f"  - Create: {analysis['stats']['create']}")
        print(f"  - Update: {analysis['stats']['update']}")
        print(f"  - Delete: {analysis['stats']['delete']}")
        print(f"  - Replace: {analysis['stats']['replace']}")
        print(f"  - No-op: {analysis['stats']['no-op']}")

        # Generate HTML output
        output_path = output_dir / 'terraform-plan.html'
        generate_html(analysis, output_path)

        print(f"\nVisualization generated: {output_path}")
        print("Open this file in a web browser to view the interactive plan visualization")

    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON file - {e}")
        sys.exit(1)
    except KeyError as e:
        print(f"Error: Missing expected field in plan JSON - {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
