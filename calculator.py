"""Калькулятор баллов. Пороги оценок можно менять под вашу систему."""
from math import ceil

# Процент -> оценка 5-балльной системы (ПРОВЕРЬТЕ пороги по правилам колледжа)
FIVE = [(85, 5), (70, 4), (50, 3), (0, 2)]


def to_scale(percent: float, scale: str) -> str:
    if scale == "5":
        return str(next(g for p, g in FIVE if percent >= p))
    if scale == "12":
        return str(max(1, round(percent * 12 / 100)))  # линейный пересчёт, ПРЕДПОЛОЖЕНИЕ
    return f"{percent:.0f}"  # "100" и по умолчанию: проценты


def percent_of(got: float, max_total: float) -> float:
    return 100 * got / max_total if max_total else 0.0


def needed_points(got: float, done_max: float, total_max: float, target_percent: float) -> dict:
    """Сколько баллов надо набрать за оставшиеся работы, чтобы выйти на цель.

    status: "invalid"    - введённых работ больше, чем максимум за семестр (ошибка в данных)
            "reached"    - цель уже достигнута
            "ok"         - цель достижима, нужно need баллов из remaining
            "impossible" - даже при максимуме за остаток цель недостижима
    """
    remaining = total_max - done_max
    if remaining < 0:
        return {"status": "invalid", "need": 0, "remaining": remaining}
    target_points = target_percent * total_max / 100
    if got >= target_points:
        return {"status": "reached", "need": 0, "remaining": remaining}
    need = ceil(target_points - got)
    return {"status": "ok" if need <= remaining else "impossible", "need": need, "remaining": remaining}


def what_if(got: float, total_max: float, extra_got: float) -> float:
    """Процент итога, если за оставшееся получить extra_got баллов."""
    return percent_of(got + extra_got, total_max)


def scenario(got: float, done_max: float, total_max: float, work_max: float, percent: float) -> dict:
    """Что будет, если следующую работу (макс. work_max б.) сдать на percent%."""
    new_done = done_max + work_max
    if total_max <= 0 or work_max <= 0 or new_done > total_max + 1e-9:
        return {"status": "invalid", "left": max(total_max - done_max, 0)}
    new_got = got + work_max * percent / 100
    targets = []
    for pct, grade in FIVE[:3]:
        targets.append((grade, pct, needed_points(new_got, new_done, total_max, pct)))
    return {"status": "ok", "got": new_got, "done": new_done, "remaining": total_max - new_done,
            "percent_done": percent_of(new_got, new_done), "targets": targets}
