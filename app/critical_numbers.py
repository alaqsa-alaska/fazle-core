CRITICAL_NUMBERS_RAW = [
    "8801848144841",
    "8801772274173",
    "8801844836824",
    "8801958122303",
    "8801836743754",
    "8801958122301",
    "8801958122311",
    "8801958122302",
    "8801708314716",
    "8801818989409",
    "8801670535255",
    "8801757622300",
    "8801779415282",
    "8801829366960",
    "8801999330826",
    "8801537443173",
    "8801826532066",
    "8801972694969",
    "8801909956433",
    "8801726393424",
]


def normalize_phone_880(raw: str) -> str:
    from modules.phone_normalizer import normalize_phone
    return normalize_phone(raw) or ""


CRITICAL_NUMBERS = tuple(
    sorted({normalize_phone_880(number) for number in CRITICAL_NUMBERS_RAW if normalize_phone_880(number)})
)
