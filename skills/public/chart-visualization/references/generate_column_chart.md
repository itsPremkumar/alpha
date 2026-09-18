# generate_column_chart — Column Chart

## Overview
Compares discrete category performance using vertical bars, ideal for periodic metrics, sales volumes, and category totals.

## Input Fields
### Required
- data: array<object>, elements containing category (string) and alue (number). Optional group (string) for grouped or stacked displays.

### Optional
- group: boolean, default alse, displays groups side-by-side.
- stack: boolean, default alse, stacks groups vertically.
- 	heme: string, default default.
- width: number, default 600.
- height: number, default 400.
- 	itle: string, default empty string.
- xisXTitle: string, default empty string.
- xisYTitle: string, default empty string.

## Usage Recommendations
Use column charts when category count is below 15; prefer bar charts for larger counts or long label text.

## Output
- Returns column chart image URL and specification in _meta.spec.
