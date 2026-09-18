# generate_pin_map — Pin / Marker Map

## Overview
Plots discrete geographic locations with markers and value callouts on a world or regional map.

## Input Fields
### Required
- data: array<object>, items with 
ame, latitude, longitude, and alue.

### Optional
- 	heme: string, default default.
- 	itle: string, pin map title.

## Usage Recommendations
Verify GPS coordinates match WGS84 standard decimal degrees.

## Output
- Returns pin map image URL and spec in _meta.spec.
