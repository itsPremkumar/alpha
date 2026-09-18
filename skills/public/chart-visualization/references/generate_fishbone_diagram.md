# generate_fishbone_diagram — Fishbone (Ishikawa) Diagram

## Overview
Renders cause-and-effect root cause diagrams categorizing potential factors contributing to an overall outcome or failure.

## Input Fields
### Required
- data: object, structured root problem statement and branch factor categories.

### Optional
- 	heme: string, default default.
- width: number, default 800.
- height: number, default 500.
- 	itle: string, diagram title.

## Usage Recommendations
Organize root causes into standard categories (e.g. People, Process, Technology, Environment).

## Output
- Returns fishbone diagram image URL with spec in _meta.spec.
