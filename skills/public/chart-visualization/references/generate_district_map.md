# generate_district_map — District Map

## Overview
Renders choropleth geographic maps visualizing metric distributions across administrative regions, states, or districts.

## Input Fields
### Required
- data: array<object>, items with district or 
egion (string) and alue (number).

### Optional
- mapType: string, map boundary identifier.
- 	heme: string, default default.
- width: number, default 600.
- height: number, default 400.
- 	itle: string, default empty string.

## Usage Recommendations
Ensure regional naming conforms to ISO or standard regional nomenclatures.

## Output
- Returns district map image URL and spec in _meta.spec.
