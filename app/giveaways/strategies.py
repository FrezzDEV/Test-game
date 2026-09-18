import random


def choose_number(minimum: int, maximum: int) -> str:
    if minimum > maximum:
        raise ValueError("Invalid number range")
    return str(random.randint(minimum, maximum))
