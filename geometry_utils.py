def is_valid_positive_number(value):
    try:
        number = float(value)
        return number > 0
    except Exception:
        return False