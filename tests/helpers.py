"""Kleine Fabriken für aufbereitete Stunden (Format wie dashboard.UntisFetcher._lesson)."""


def lesson(date=20261012, start=800, end=845, subject="Mat", status="normal", group=None,
           teachers=None, rooms=None, note="", info="", marks=None, subject_old=None,
           subject_full=None, color="#c00000"):
    return {
        "date": date, "start": start, "end": end, "subject": subject,
        "subject_full": subject_full or subject, "subject_old": subject_old, "color": color,
        "teachers": teachers if teachers is not None else [{"t": "Mül", "k": ""}],
        "rooms": rooms if rooms is not None else [{"t": "R204", "k": ""}],
        "status": status, "group": group or subject, "note": note, "info": info, "marks": marks or [],
    }


GRID = [{"name": str(i + 1), "start": s, "end": e} for i, (s, e) in enumerate(
    [(800, 845), (850, 935), (955, 1040), (1045, 1130), (1150, 1235), (1240, 1325)])]
