# generate_spreadsheet — Spreadsheet Data Table

## Overview
Renders formatted tabular data sheets with headers, sorted columns, and formatted cells.

## Input Fields
### Required
- columns: array<object>, column headers and data keys.
- data: array<object>, table row records.

### Optional
- 	itle: string, table title.

## Usage Recommendations
Keep columns concise; format currency, percentages, and dates uniformly.

## Output
- Returns spreadsheet table image URL and spec in _meta.spec.
