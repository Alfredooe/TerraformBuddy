# THIS IS VIBECODED SLOP, USE AT YOUR OWN RISK.

A Docker-based tool that converts Terraform plan show JSON output into beautiful, interactive HTML visualizations with Bootstrap 5.

## Features

- **Summary Dashboard**: Visual overview of all resource changes (create, update, delete, replace)
- **Detailed Change View**: Expandable cards showing before/after values for each resource attribute
- **Search & Filter**: Real-time search by resource address or type, with toggleable action filters
- **Bootstrap 5 UI**: Modern, responsive design that works on all devices
- **Zero Dependencies**: Self-contained HTML output with CDN-loaded resources

## Prerequisites

- Docker
- Terraform plan saved as JSON

## Quick Start

### 1. Generate Terraform Plan JSON

First, create a Terraform plan and export it as JSON:

```bash
# Create a plan file
terraform plan -out=tfplan

# Convert to JSON format
terraform show -json tfplan > plan.json
```

### 2. Build the Docker Image

```bash
docker build -t terraformbuddy .
```

### 3. Generate Visualization

Run the container with volume mounts for input and output:

```bash
docker run --rm \
  -v /path/to/your/plan.json:/input/plan.json:ro \
  -v /path/to/output:/output \
  terraformbuddy
```

**Example** (Windows PowerShell):
```powershell
docker run --rm `
  -v ${PWD}/plan.json:/input/plan.json:ro `
  -v ${PWD}/output:/output `
  terraformbuddy
```

**Example** (Linux/Mac):
```bash
docker run --rm \
  -v $(pwd)/plan.json:/input/plan.json:ro \
  -v $(pwd)/output:/output \
  terraformbuddy
```

### 4. View the Result

Open `output/terraform-plan.html` in your web browser.

## How It Works

1. The container reads the JSON plan file from `/input`
2. Parses the Terraform plan and analyzes resource changes
3. Generates an interactive HTML file with:
   - Summary statistics for all change types
   - Color-coded resource cards (green=create, yellow=update, red=delete, orange=replace)
   - Expandable attribute change details with before/after comparison
   - Live search and filter functionality
4. Saves the output to `/output/terraform-plan.html`

## Understanding the Output

### Change Summary Cards

- **Create** (Green): New resources being added
- **Update** (Yellow): Existing resources being modified
- **Delete** (Red): Resources being removed
- **Replace** (Orange): Resources being destroyed and recreated
- **No Change** (Gray): Resources in the plan but not changing

### Resource Cards

Each resource displays:
- Action badge (CREATE, UPDATE, DELETE, REPLACE)
- Resource address (e.g., `aws_instance.example`)
- Resource type (e.g., `aws_instance`)
- Expandable attribute changes showing:
  - Attribute name
  - Before value (red background)
  - After value (green background)
  - Computed values (yellow background with "(computed)" label)

### Search and Filter

- **Search box**: Type to filter resources by address or type
- **Action filters**: Toggle buttons to show/hide specific action types
- Filters work together (resources must match both search and selected actions)

## Development

### Local Testing Without Docker

1. Install dependencies:
```bash
uv sync
```

2. Create test directories:
```bash
mkdir -p input output
```

3. Copy your plan JSON to `input/plan.json`

4. Run the script:
```bash
uv run main.py
```

Note: You'll need to modify [main.py](main.py) to use local paths instead of `/input` and `/output` for local testing.

### Project Structure

```
TerraformBuddy/
├── main.py              # Core application logic
├── pyproject.toml       # Python dependencies (uv format)
├── Dockerfile           # Container definition
└── README.md           # This file
```

## Technical Details

- **Language**: Python 3.12
- **Package Manager**: uv (modern, fast Python package manager)
- **Template Engine**: Jinja2
- **CSS Framework**: Bootstrap 5.3.2
- **Icons**: Bootstrap Icons 1.11.1
- **Output**: Single self-contained HTML file

## Limitations

- Currently focuses on resource changes (doesn't visualize output values or data sources)
- Resource dependency graph visualization planned for future release
- Designed for JSON format only (not raw `terraform plan` text output)

## Future Enhancements

- Resource dependency graph visualization with D3.js
- Export to PDF
- Comparison between multiple plan versions
- Integration with CI/CD pipelines
- Support for Terraform Cloud/Enterprise APIs

## License

MIT

## Contributing

Issues and pull requests welcome!
