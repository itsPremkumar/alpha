# generate_area_chart — Area Chart

## Overview
Displays metric trends over a continuous independent variable (typically time). Can enable stacking to observe cumulative contributions across different groups, ideal for KPIs, energy metrics, and production time-series.

## Input Fields
### Required
- data: array<object>, elements must include time (string) and value (number). When stacking, group (string) is also required. At least 1 record.

### Optional
- stack: boolean, default false. When enabled, ensure every data item contains a group field.
- style.backgroundColor: string, chart background color (e.g. #fff).
- style.lineWidth: number, custom line width for the area boundary.
- style.palette: string[], color palette array for series coloring.
- style.texture: string, default default, options: default/
ough for hand-drawn texture.
- theme: string, default default, options: default/academy/dark.
- width: number, default 600, chart width.
- height: number, default 400, chart height.
- title: string, default empty string, chart title.
- axisXTitle: string, default empty string, X-axis title.
- axisYTitle: string, default empty string, Y-axis title.

## Usage Recommendations
Ensure consistent time formatting (e.g. YYYY-MM). In stacked mode, ensure all groups cover identical time points with missing values filled.

## Output
- Returns image URL with complete area chart specification in _meta.spec for secondary rendering or tracking.
