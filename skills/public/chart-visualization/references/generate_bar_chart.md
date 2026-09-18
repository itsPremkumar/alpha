# generate_bar_chart — Bar Chart

## Overview
Compares metric performance across categories or groups using horizontal bars, suitable for Top-N rankings, regional comparisons, or channel benchmarks.

## Input Fields
### Required
- data: array<object>, each item must contain category (string) and alue (number). For grouping or stacking, provide group (string).

### Optional
- group: boolean, default alse. When enabled, displays different groups side-by-side (requires stack=false and group field in data).
- stack: boolean, default 	rue. When enabled, stacks different groups on the same bar (requires group=false and group field in data).
- style.backgroundColor: string, custom background color (e.g. #fff).
- style.palette: string[], series color list.
- style.texture: string, default default, options: default/
ough.
- 	heme: string, default default, options: default/cademy/dark.
- width: number, default 600, chart width.
- height: number, default 400, chart height.
- 	itle: string, default empty string, chart title.
- xisXTitle: string, default empty string, X-axis title.
- xisYTitle: string, default empty string, Y-axis title.

## Usage Recommendations
Keep category names concise. When series count is large, use stacking or filter key items to avoid visual clutter.

## Output
- Returns bar chart image URL and provides full configuration in _meta.spec for reuse.
