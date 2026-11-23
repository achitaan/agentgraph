"""How tasks look on screen. Every line of the list is made here."""

TITLE_WIDTH = 30


def shorten(title):
    """Titles longer than TITLE_WIDTH are cut to 27 characters and end in three dots."""
    if len(title) <= TITLE_WIDTH:
        return title
    return title[:TITLE_WIDTH - 3] + "..."


def render_task(task, id_width=1):
    box = "[x]" if task.done else "[ ]"
    mark = "! " if task.priority == "high" else ""
    due = f" (due {task.due})" if task.due else ""
    return f"{box} {task.id:>{id_width}} {mark}{shorten(task.title)}{due}"


def render_list(tasks):
    if not tasks:
        return "(no tasks)"
    id_width = len(str(max(t.id for t in tasks)))
    return "\n".join(render_task(t, id_width) for t in tasks)
