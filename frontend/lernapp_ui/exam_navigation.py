"""Keep display labels out of persisted zero-based question positions."""
import re


def position_index(value, count):
    if isinstance(value, int) and not isinstance(value, bool):
        index = value
    elif isinstance(value, str) and (match := re.match(r"^(\d+)\.\s", value)):
        index = int(match.group(1)) - 1
    else:
        index = 0
    return max(0, min(index, max(0, count - 1)))
