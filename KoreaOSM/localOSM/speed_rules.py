"""OSM passenger-car speed limits and explicitly labeled estimation defaults.

Defaults are model assumptions, not verified legal limits for individual roads.
Conditional/lane limits use the lowest parseable value conservatively, without
claiming to evaluate dates, weather, vehicle weight, or a specific lane.
"""
import math
import re

RULE_VERSION = 'kr-passenger-car-2026-v1'
DEFAULT_DRIVE_KMH = {'motorway': 100, 'motorway_link': 40, 'trunk': 60, 'trunk_link': 40,
                     'primary': 50, 'primary_link': 40, 'secondary': 50, 'secondary_link': 40,
                     'tertiary': 50, 'tertiary_link': 30, 'unclassified': 40, 'residential': 30,
                     'living_street': 20, 'service': 20, 'road': 30, 'track': 15}
MODE_NAMES = ('walk', 'bike', 'drive')
SPEED_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*(km/?h|kph|mph|knots)?$', re.I)


def parse_speed(value):
    """Return km/h for valid numeric/list limits; ambiguous tokens are not numbers."""
    if not isinstance(value, str) or not value.strip():
        return None
    values = []
    for part in re.split(r'[;|]', value.strip()):
        part = part.strip().lower()
        if part == 'walk':
            values.append(6.0)
            continue
        match = SPEED_RE.fullmatch(part)
        if not match:
            return None
        number = float(match[1]) * {'mph': 1.609344, 'knots': 1.852}.get((match[2] or '').lower(), 1)
        if not math.isfinite(number) or not 0 < number <= 200:
            return None
        values.append(number)
    return min(values) if values else None


def directional_limit(tags, direction, mode='motorcar', prefix='maxspeed'):
    """Specific mode/direction overrides generic limits. Lanes constrain the model."""
    scalar_keys = (f'{prefix}:{mode}:{direction}', f'{prefix}:{direction}:{mode}',
                   f'{prefix}:{mode}', f'{prefix}:{direction}', prefix)
    selected = []
    unknown = False
    for key in scalar_keys:
        if key not in tags:
            continue
        value = parse_speed(tags[key])
        if value is not None:
            selected.append((value,key))
            break
        unknown = True
        # An unparseable explicit directional/mode override must not silently
        # inherit an unrelated generic value.
        break
    lane_keys = (f'{prefix}:{mode}:lanes:{direction}', f'{prefix}:lanes:{direction}', f'{prefix}:lanes')
    for key in lane_keys:
        if key in tags:
            value = parse_speed(tags[key])
            if value is not None:
                selected.append((value,key))
            else:
                unknown = True
            break
    conditional = False
    conditional_keys = (f'{prefix}:{mode}:{direction}:conditional', f'{prefix}:{mode}:conditional',
                        f'{prefix}:{direction}:conditional', f'{prefix}:conditional')
    for key in conditional_keys:
        if key not in tags:
            continue
        conditional = True
        raw = tags[key]
        # Capture each speed before @; conditions themselves may contain ';'.
        matches = re.findall(r'(?:^|;)\s*([^;@]+?)\s*@', raw)
        for raw_speed in matches:
            value = parse_speed(raw_speed)
            if value is None:
                unknown = True
            else:
                selected.append((value,key))
        if not matches:
            unknown = True
        break
    if not selected:
        return None, None, conditional, unknown
    value, key = min(selected)
    return value, key, conditional, unknown


def profile(tags, direction):
    highway = tags.get('highway','')
    drive_limit, key, conditional, unknown = directional_limit(tags,direction)
    bike_limit, bike_key, bike_conditional, bike_unknown = directional_limit(tags,direction,'bicycle')
    minimum, _, _, min_unknown = directional_limit(tags,direction,prefix='minspeed')
    default = DEFAULT_DRIVE_KMH.get(highway,20)
    # Without lane information assume the lower one-lane motorway value.
    if highway == 'motorway':
        lanes = parse_speed(tags.get(f'lanes:{direction}', tags.get('lanes','')))
        if lanes is None or lanes < 2:
            default = 80
    drive_speed = drive_limit if drive_limit is not None else default
    if drive_limit is None and minimum is not None:
        drive_speed = max(drive_speed,minimum)
    walk_speed = 2.5 if highway == 'steps' else 4.5
    bike_speed = 15.0 if bike_limit is None else min(15.0,bike_limit)
    conflict = drive_limit is not None and minimum is not None and minimum > drive_limit
    if conflict:
        drive_speed = 0.0  # Contradictory min/max does not create a fictitious fast road.
    return dict(drive_kmh=drive_speed, bike_kmh=bike_speed, walk_kmh=walk_speed,
                drive_limit_kmh=drive_limit, bike_limit_kmh=bike_limit, min_drive_kmh=minimum,
                drive_source='osm' if drive_limit is not None else 'default',
                drive_source_key=key, bike_source_key=bike_key,
                conditional_conservative=conditional or bike_conditional,
                unparsed_tag=unknown or bike_unknown or min_unknown, conflicting_limits=conflict)


def seconds_per_meter(profiles, allowed):
    """Factor lookup [profile * 8 + edge_mode_mask], including mixed-mode choice."""
    factors = []
    for item in profiles:
        for edge_mask in range(8):
            speeds = [item[f'{name}_kmh'] for name,bit in zip(MODE_NAMES,(1,2,4))
                      if edge_mask & allowed & bit and item[f'{name}_kmh'] > 0]
            factors.append(3.6 / max(speeds) if speeds else math.inf)
    return factors
