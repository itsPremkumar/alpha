# generate_path_map — Path / Route Map

## Overview
Renders geographic journey paths, flight routes, logistics tracks, and navigation routes connecting multiple coordinates.

## Input Fields
### Required
- paths: array<object>, list of routes with coordinates (origin, destination, waypoints).

### Optional
- 	heme: string, default default.
- 	itle: string, path map title.

## Usage Recommendations
Highlight directionality and transit checkpoints along routes.

## Output
- Returns path map image URL and spec in _meta.spec.
