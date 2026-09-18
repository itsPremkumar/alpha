# generate_venn_chart — Venn Diagram

## Overview
Illustrates logical set relationships, intersections, and shared characteristics among discrete groups.

## Input Fields
### Required
- sets: array<object>, set definitions with 
ame and size.
- overlaps: array<object>, intersection mappings with sets and size.

### Optional
- 	heme: string, default default.
- 	itle: string, venn diagram title.

## Usage Recommendations
Best suited for 2 to 3 overlapping sets to avoid visual ambiguity.

## Output
- Returns venn diagram image URL and spec in _meta.spec.
