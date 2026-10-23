"""The kinds of vehicle the timetables are asked for, as Transitous names them."""
# People movers, funiculars and cable cars count: the link between a terminal and its station is often one of them
# (Bologna, Pisa), and so is the last stretch to a mountain village.
LIFT_MODES = "FUNICULAR,AERIAL_LIFT,OTHER"
GROUND_MODES = "RAIL,SUBURBAN,SUBWAY,TRAM,BUS,COACH,FERRY," + LIFT_MODES   # everything but planes: the planner also flies, and those answers are no use here
NO_COACH_MODES = "RAIL,SUBURBAN,SUBWAY,TRAM,BUS,FERRY," + LIFT_MODES       # ... and but long-distance coaches
ROAD_MODES = "COACH,BUS,TRAM,SUBWAY,FERRY"                   # ... and but trains
