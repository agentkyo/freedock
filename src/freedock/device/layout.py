"""Physical layout of the MiraBox N4: which control id / image id belongs to which part of the panel.

The FreeDock model uses three kinds of surfaces, always numbered left-to-right, top-to-bottom:

* ``key``   0..9  - the ten LCD keys (two rows of five), 112x112 px each
* ``zone``  0..3  - the four touch zones of the LCD strip, one above each knob
* ``knob``  0..3  - the four rotary encoders (rotate left/right and push)

plus the ``swipe`` gesture on the strip.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    KEY = "key"
    ZONE = "zone"
    KNOB = "knob"
    SWIPE = "swipe"


class EventType(str, Enum):
    DOWN = "down"
    UP = "up"
    TAP = "tap"            # controls that only report one event (strip zones)
    PUSH = "push"          # knob push
    ROTATE = "rotate"      # value = -1 / +1
    SWIPE = "swipe"        # value = -1 (left) / +1 (right)


@dataclass(frozen=True)
class InputEvent:
    kind: Kind
    index: int
    type: EventType
    value: int = 0


@dataclass(frozen=True)
class Model:
    name: str
    vendor_id: int
    product_id: int
    usage_page: int
    key_size: tuple[int, int]
    zone_size: tuple[int, int]
    key_image_ids: tuple[int, ...]      # by key index
    zone_image_ids: tuple[int, ...]     # by zone index
    key_input_ids: tuple[int, ...]
    zone_input_ids: tuple[int, ...]
    knob_push_ids: tuple[int, ...]
    knob_rotate_ids: tuple[tuple[int, int], ...]   # (left, right)
    swipe_ids: tuple[int, int]                     # (left, right)
    rotation: int = 180

    @property
    def key_count(self) -> int:
        return len(self.key_image_ids)

    @property
    def zone_count(self) -> int:
        return len(self.zone_image_ids)

    @property
    def knob_count(self) -> int:
        return len(self.knob_push_ids)

    def image_id(self, kind: Kind, index: int) -> int:
        return (self.key_image_ids if kind == Kind.KEY else self.zone_image_ids)[index]

    def size(self, kind: Kind) -> tuple[int, int]:
        return self.key_size if kind == Kind.KEY else self.zone_size

    def decode(self, control: int, state: int) -> InputEvent | None:
        if control in self.key_input_ids:
            return InputEvent(Kind.KEY, self.key_input_ids.index(control),
                              EventType.DOWN if state else EventType.UP)
        if control in self.zone_input_ids:
            return InputEvent(Kind.ZONE, self.zone_input_ids.index(control), EventType.TAP)
        if control in self.knob_push_ids:
            return InputEvent(Kind.KNOB, self.knob_push_ids.index(control), EventType.PUSH)
        for i, (left, right) in enumerate(self.knob_rotate_ids):
            if control == left:
                return InputEvent(Kind.KNOB, i, EventType.ROTATE, -1)
            if control == right:
                return InputEvent(Kind.KNOB, i, EventType.ROTATE, +1)
        if control == self.swipe_ids[0]:
            return InputEvent(Kind.SWIPE, 0, EventType.SWIPE, -1)
        if control == self.swipe_ids[1]:
            return InputEvent(Kind.SWIPE, 0, EventType.SWIPE, +1)
        return None


N4 = Model(
    name="MiraBox N4",
    vendor_id=0x6602,
    product_id=0x1001,
    usage_page=0xFFA0,
    key_size=(112, 112),
    zone_size=(176, 112),
    # the firmware numbers images strip-first, then the bottom row, then the top row
    key_image_ids=(11, 12, 13, 14, 15, 6, 7, 8, 9, 10),
    zone_image_ids=(1, 2, 3, 4),
    key_input_ids=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
    zone_input_ids=(0x40, 0x41, 0x42, 0x43),
    knob_push_ids=(0x37, 0x35, 0x33, 0x36),
    knob_rotate_ids=((0xA0, 0xA1), (0x50, 0x51), (0x90, 0x91), (0x70, 0x71)),
    swipe_ids=(0x38, 0x39),
)

# Same panel, other USB ids seen in the wild (N4 "E" / N1 rebrands, Ajazz AKP05 clones).
N4_VARIANTS = (
    N4,
    Model(**{**N4.__dict__, "name": "MiraBox N4 (6603:1007)", "vendor_id": 0x6603, "product_id": 0x1007}),
)

MODELS = N4_VARIANTS
